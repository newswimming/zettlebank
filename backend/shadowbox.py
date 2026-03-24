"""ShadowBox — ChromaDB semantic index for ZettleBank vault notes.

Responsibilities
----------------
1. **Ingest** — parse Smart Connections `.ajson` files (or fall back to raw
   vault `.md` files) and upsert vectors + structured metadata into a
   ChromaDB `PersistentClient` collection keyed by note_id.

2. **Metadata filters** — every document is stored with 16-beat Kishōtenketsu
   payload fields: ``narrative_beat``, ``narrative_act``, ``community_id``,
   ``constraint``, ``is_ten_candidate``, ``tags``, and ``provenance``
   (CLAUDE.md Rules 2 & 4).

3. **Hybrid search** — ``hybrid_search()`` merges two ranked lists via
   Reciprocal Rank Fusion (RRF):
     • ChromaDB cosine-distance rank  (semantic contrast — high distance first)
     • NetworkX Burt-constraint rank  (topological pivot — low constraint first)

4. **Ten-contrast query** — ``query_ten_contrasts()`` wraps hybrid_search to
   surface notes that are both semantically contrasting AND structurally bridging
   (Kishōtenketsu Ten-pivot candidates).

Embedding model contract
------------------------
A single sentence-transformer model is used for **all** ingestion and query
calls within a ShadowBox instance.  Mixing models produces meaningless cosine
distances.  If you change the model delete the ChromaDB directory and re-ingest.

Default model: ``all-MiniLM-L6-v2`` (384-dim, already cached by BERTopic).
When Smart Connections `.ajson` files supply ``TaylorAI/bge-micro-v2`` vectors
(also 384-dim), those are used directly **only** when the ShadowBox model is set
to match; otherwise note content is re-embedded with the configured model so the
entire collection remains in one semantic space.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chromadb
import networkx as nx
import numpy as np

logger = logging.getLogger("shadowbox")

# ---------------------------------------------------------------------------
# Kishōtenketsu constants (CLAUDE.md Rule 2)
# ---------------------------------------------------------------------------

#: Maps each of the 16 beat slugs to its parent act.
BEAT_TO_ACT: dict[str, str] = {
    **{f"ki-{i}":    "ki"    for i in range(1, 5)},
    **{f"sho-{i}":   "sho"   for i in range(5, 9)},
    **{f"ten-{i}":   "ten"   for i in range(9, 13)},
    **{f"ketsu-{i}": "ketsu" for i in range(13, 17)},
    "unplaced": "unknown",
}

ALL_BEATS: list[str] = list(BEAT_TO_ACT.keys())

# Smart Connections embedding model key (must match .ajson data)
SC_EMBED_MODEL_KEY = "TaylorAI/bge-micro-v2"

# ChromaDB settings
COLLECTION_NAME = "vault_notes"

# RRF rank-smoothing constant — standard value from Cormack et al. (2009)
RRF_K = 60

# Burt's constraint threshold for Ten-candidate classification
TEN_CONSTRAINT_THRESHOLD = 0.5

# Cosine-distance window for "Ten-style contrast".
# Below MIN → notes are near-identical (same act, no pivot potential).
# Above MAX → notes are completely unrelated (noise, not a pivot).
TEN_DISTANCE_MIN = 0.20
TEN_DISTANCE_MAX = 1.50

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AJsonNote:
    """Parsed entry from a Smart Connections ``.ajson`` file."""

    note_id: str
    path: str
    vec: Optional[list[float]]         # 384-dim vector; None if absent in file
    outlinks: list[str] = field(default_factory=list)


@dataclass
class HybridResult:
    """Single result from ``hybrid_search`` / ``query_ten_contrasts``."""

    note_id:       str
    rrf_score:     float    # higher = better fusion rank
    chroma_rank:   int      # 1-indexed position in ChromaDB contrast list
    nx_rank:       int      # 1-indexed position in NetworkX constraint list
    distance:      float    # cosine distance from query embedding
    constraint:    float    # Burt's constraint (-1.0 = not computed)
    narrative_act: str      # "ki" | "sho" | "ten" | "ketsu" | "unknown"
    snippet:       str      # first ~120 chars of note body


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block (``---`` … ``---``) from note text."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].lstrip("\n")
    return text


def _snippet(text: str, length: int = 120) -> str:
    body = _strip_frontmatter(text).strip().replace("\n", " ")
    body = body.encode("ascii", errors="replace").decode("ascii")
    return body[:length] + ("..." if len(body) > length else "")


def _tags_to_str(tags: list | str) -> str:
    """Serialise tags list → comma-joined string for ChromaDB metadata."""
    if isinstance(tags, list):
        return ",".join(str(t) for t in tags)
    return str(tags) if tags else ""


def _beat_from_tags(tags_str: str) -> tuple[str, str]:
    """Return ``(narrative_beat, narrative_act)`` from a comma-joined tag string.

    Scans for ``code/<beat>`` tags.  Returns ``("unplaced", "unknown")`` if none
    of the 16 beat slugs is present.
    """
    for tag in tags_str.split(","):
        tag = tag.strip()
        if tag.startswith("code/"):
            beat = tag[5:]
            if beat in BEAT_TO_ACT:
                return beat, BEAT_TO_ACT[beat]
    return "unplaced", "unknown"


# ---------------------------------------------------------------------------
# .ajson parser
# ---------------------------------------------------------------------------


def parse_ajson_dir(ajson_dir: Path) -> dict[str, AJsonNote]:
    """Read all ``*.ajson`` files in *ajson_dir* and return parsed entries.

    Each ``.ajson`` file contains newline-delimited JSON entries of the form::

        "smart_sources:notes/note-stem.md": { "path": ..., "embeddings": {...}, ... }

    Notes without a valid ``path`` field are silently skipped.
    Notes without embeddings are included with ``vec=None`` so their metadata
    can still populate ChromaDB filters.

    Returns:
        ``{note_id: AJsonNote}`` keyed by the note stem (filename without ``.md``).
    """
    results: dict[str, AJsonNote] = {}

    if not ajson_dir.exists():
        logger.info("parse_ajson_dir: %s does not exist — skipping", ajson_dir)
        return results

    for ajson_file in sorted(ajson_dir.glob("*.ajson")):
        try:
            raw = ajson_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", ajson_file, exc)
            continue

        for line in raw.strip().split("\n"):
            line = line.strip().rstrip(",")
            if not line or not line.startswith('"smart_sources:'):
                continue

            try:
                obj = json.loads("{" + line + "}")
            except json.JSONDecodeError:
                continue

            for key, val in obj.items():
                if not key.startswith("smart_sources:"):
                    continue

                path = val.get("path", "")
                if not path:
                    continue

                note_id = Path(path).stem

                # Extract vector from the first available model
                vec: Optional[list[float]] = None
                embeds = val.get("embeddings", {})
                for _model_key, model_data in embeds.items():
                    v = model_data.get("vec")
                    if v and isinstance(v, list) and len(v) > 0:
                        vec = [float(x) for x in v]
                        break

                outlinks = [
                    ol.get("target", "")
                    for ol in val.get("outlinks", [])
                    if ol.get("target")
                ]

                results[note_id] = AJsonNote(
                    note_id=note_id,
                    path=path,
                    vec=vec,
                    outlinks=outlinks,
                )

    logger.info(
        "parse_ajson_dir: parsed %d entries from %s", len(results), ajson_dir
    )
    return results


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def rrf_merge(
    ranked_lists: list[list[str]],
    k: int = RRF_K,
) -> dict[str, float]:
    """Standard Reciprocal Rank Fusion over an arbitrary number of ranked lists.

    Formula (Cormack et al. 2009)::

        RRF_score(d) = Σ_{i=1}^{n} 1 / (k + rank_i(d))

    where ``rank_i(d)`` is the 1-indexed position of document *d* in list *i*.
    Documents absent from a list contribute nothing for that list.

    Args:
        ranked_lists: Each inner list is ordered **best-first** (rank 1 = index 0).
                      An empty inner list is silently skipped.
        k: Rank-smoothing constant.  The canonical value is 60.

    Returns:
        ``{note_id: rrf_score}`` — higher score means better fusion rank.
        Only documents appearing in at least one list are included.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_zero, doc_id in enumerate(ranked):
            rank = rank_zero + 1                          # 1-indexed
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


# ---------------------------------------------------------------------------
# NetworkX contrast ranking
# ---------------------------------------------------------------------------


def nx_contrast_ranking(
    query_note_id: str,
    graph: nx.DiGraph,
    candidate_ids: list[str],
    query_community_id: int = -1,
) -> list[str]:
    """Rank *candidate_ids* by Ten-pivot potential using NetworkX topology.

    Algorithm
    ---------
    1. Convert the DiGraph to an undirected projection (Burt's constraint is
       direction-agnostic; see PLAN_NETWORKX.md ADR-004).
    2. Compute Burt's constraint for all eligible nodes via ``nx.constraint``.
    3. Sort candidates: cross-community notes first, then by constraint
       ascending (0 = pure structural hole = strongest Ten candidate).

    Falls back to a deterministic alphabetical ordering when the graph has
    fewer than 3 nodes or ``nx.constraint`` raises.

    Args:
        query_note_id:       The note being queried (excluded from output).
        graph:               Current vault DiGraph.
        candidate_ids:       Pool of note IDs to rank.
        query_community_id:  Leiden macro community of the query note
                             (−1 = unknown → cross-community preference disabled).

    Returns:
        *candidate_ids* re-ordered, best contrast first.
    """
    if graph.number_of_nodes() < 3:
        return sorted(c for c in candidate_ids if c != query_note_id)

    undirected = graph.to_undirected(reciprocal=False)

    # Promote `confidence` → `weight` for constraint calculation
    for _u, _v, data in undirected.edges(data=True):
        data["weight"] = data.get("confidence", data.get("weight", 1.0))

    eligible = [n for n in undirected.nodes() if undirected.degree(n) > 0]
    if not eligible:
        return sorted(c for c in candidate_ids if c != query_note_id)

    try:
        constraint_map: dict[str, float] = nx.constraint(
            undirected, nodes=eligible, weight="weight"
        )
    except (nx.NetworkXError, ZeroDivisionError) as exc:
        logger.warning("nx.constraint failed: %s", exc)
        return sorted(c for c in candidate_ids if c != query_note_id)

    ranked: list[tuple[str, float, bool]] = []
    for nid in candidate_ids:
        if nid == query_note_id:
            continue
        c = constraint_map.get(nid, 1.0)   # isolated nodes get max constraint
        same_community = False
        if query_community_id != -1:
            node_data = graph.nodes.get(nid, {})
            try:
                node_community = int(node_data.get("community_id", -1))
            except (TypeError, ValueError):
                node_community = -1
            same_community = node_community == query_community_id

        ranked.append((nid, c, same_community))

    # Cross-community first (same_community=False → False < True → sorts first),
    # then by constraint ascending within each group.
    ranked.sort(key=lambda x: (x[2], x[1]))
    return [nid for nid, _c, _sc in ranked]


# ---------------------------------------------------------------------------
# ShadowBox
# ---------------------------------------------------------------------------


class ShadowBox:
    """ChromaDB-backed semantic index with RRF hybrid search.

    Typical usage::

        sb = ShadowBox.from_vault(vault_dir, graph)
        results = sb.query_ten_contrasts("the-mask-ceremony", content, graph)

    The ChromaDB collection is stored inside *vault_dir* at ``.chroma/``.
    Add ``vault/choracle-remote-00/.chroma/`` to ``.gitignore``.
    """

    #: Output dimension shared by both ``all-MiniLM-L6-v2`` and bge-micro-v2
    EMBED_DIM = 384

    def __init__(
        self,
        chroma_path: Path,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        """Initialise ChromaDB client and lazily prepare the embedder.

        Args:
            chroma_path: Directory where ChromaDB persists its SQLite + HNSW
                         index.  Created if it does not exist.
            model_name:  Sentence-transformer model used for ingestion **and**
                         query.  Must be consistent across all calls.
        """
        self._model_name = model_name
        self._embedder = None                       # lazy-loaded on first embed()

        chroma_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(chroma_path))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ShadowBox: path=%s  model=%s  collection='%s'  count=%d",
            chroma_path, model_name, COLLECTION_NAME, self._collection.count(),
        )

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(self._model_name)
            logger.info("ShadowBox: loaded embedder '%s'", self._model_name)
        return self._embedder

    def embed(self, texts: list[str]) -> np.ndarray:
        """Encode *texts* → L2-normalised float32 array of shape (n, EMBED_DIM).

        Texts are truncated to 512 tokens internally by the model.  Pass note
        bodies with frontmatter already stripped for best results.
        """
        if not texts:
            return np.zeros((0, self.EMBED_DIM), dtype=np.float32)
        embedder = self._get_embedder()
        vecs = embedder.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 20,
            batch_size=32,
        )
        return np.asarray(vecs, dtype=np.float32)

    # ------------------------------------------------------------------
    # Internal metadata builder
    # ------------------------------------------------------------------

    def _compute_constraints(self, graph: nx.DiGraph) -> dict[str, float]:
        """Return ``{note_id: burt_constraint}`` for all non-isolated nodes."""
        if graph.number_of_nodes() < 3:
            return {}
        undirected = graph.to_undirected(reciprocal=False)
        for _u, _v, data in undirected.edges(data=True):
            data["weight"] = data.get("confidence", data.get("weight", 1.0))
        eligible = [n for n in undirected.nodes() if undirected.degree(n) > 0]
        if not eligible:
            return {}
        try:
            return dict(
                nx.constraint(undirected, nodes=eligible, weight="weight")
            )
        except (nx.NetworkXError, ZeroDivisionError) as exc:
            logger.warning("_compute_constraints: %s", exc)
            return {}

    def _build_metadata(
        self,
        note_id: str,
        graph: nx.DiGraph,
        constraint_map: dict[str, float],
        extra_tags: str = "",
        has_sc_embedding: bool = False,
    ) -> dict:
        """Assemble the ChromaDB metadata dict for a single note.

        ChromaDB only supports ``str | int | float | bool`` metadata values.
        All complex types (tag lists, etc.) are serialised to strings.
        """
        node_data = graph.nodes.get(note_id, {}) if graph else {}

        # Prefer caller-supplied tags (e.g. from .ajson outlinks analysis),
        # then fall back to graph node data set by /analyze
        tags_str = extra_tags or _tags_to_str(node_data.get("tags", []))
        beat, act = _beat_from_tags(tags_str)

        constraint = float(constraint_map.get(note_id, -1.0))
        is_ten = constraint >= 0.0 and constraint < TEN_CONSTRAINT_THRESHOLD

        community_id = -1
        raw_community = node_data.get("community_id")
        if raw_community is not None:
            try:
                community_id = int(raw_community)
            except (TypeError, ValueError):
                pass

        return {
            "note_id":          note_id,
            "narrative_beat":   beat,
            "narrative_act":    act,
            "community_id":     community_id,
            "tags":             tags_str,
            "constraint":       constraint,
            "is_ten_candidate": is_ten,
            "has_sc_embedding": has_sc_embedding,
            "model_name":       self._model_name,
        }

    # ------------------------------------------------------------------
    # Public ingestion API
    # ------------------------------------------------------------------

    def ingest_from_ajson(
        self,
        ajson_dir: Path,
        notes_content: dict[str, str],
        graph: nx.DiGraph,
    ) -> int:
        """Parse Smart Connections ``.ajson`` files and upsert into ChromaDB.

        Embedding strategy
        ~~~~~~~~~~~~~~~~~~
        * If a note's ``.ajson`` entry supplies a ``vec`` with the correct
          dimension (384) **and** the configured model matches
          ``SC_EMBED_MODEL_KEY``, the stored vector is used directly.
        * Otherwise the note body is re-embedded with ``self._model_name`` so
          the entire collection remains in a single semantic space.

        Safe to call multiple times — upsert is idempotent.

        Args:
            ajson_dir:     Path to ``.smart-env/multi/`` directory.
            notes_content: ``{note_id: raw_markdown}`` for every vault note.
            graph:         Current vault DiGraph (for community_id + constraint).

        Returns:
            Number of notes upserted.
        """
        ajson_data = parse_ajson_dir(ajson_dir)
        constraint_map = self._compute_constraints(graph)
        use_sc_vecs = (self._model_name == SC_EMBED_MODEL_KEY)

        ids, embeddings, documents, metadatas = [], [], [], []
        need_generation: list[tuple[int, str]] = []   # (idx_in_batch, body)

        for note_id, content in notes_content.items():
            body = _strip_frontmatter(content).strip()
            if not body:
                continue

            sc = ajson_data.get(note_id)
            vec_usable = (
                use_sc_vecs
                and sc is not None
                and sc.vec is not None
                and len(sc.vec) == self.EMBED_DIM
            )

            meta = self._build_metadata(
                note_id, graph, constraint_map,
                has_sc_embedding=vec_usable,
            )

            ids.append(note_id)
            documents.append(body[:2000])
            metadatas.append(meta)

            if vec_usable:
                embeddings.append(sc.vec)
            else:
                embeddings.append(None)
                need_generation.append((len(ids) - 1, body))

        # Generate embeddings for notes without usable .ajson vectors
        if need_generation:
            texts = [body for _, body in need_generation]
            generated = self.embed(texts)
            for (idx, _), vec in zip(need_generation, generated):
                embeddings[idx] = vec.tolist()

        if ids:
            self._collection.upsert(
                ids=ids,
                embeddings=[
                    e if isinstance(e, list) else list(e)
                    for e in embeddings
                ],
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(
                "ingest_from_ajson: upserted %d notes "
                "(%d from .ajson, %d re-embedded)",
                len(ids), len(ids) - len(need_generation), len(need_generation),
            )

        return len(ids)

    def ingest_from_vault(
        self,
        notes_dir: Path,
        graph: nx.DiGraph,
    ) -> int:
        """Embed vault ``.md`` files and upsert into ChromaDB.

        Fallback ingestion path when Smart Connections ``.ajson`` data is absent.
        All embeddings are generated with ``self._model_name``.

        Args:
            notes_dir: Path to the vault notes directory (``*.md`` files).
            graph:     Current vault DiGraph.

        Returns:
            Number of notes upserted.
        """
        if not notes_dir.exists():
            logger.warning("ingest_from_vault: %s not found", notes_dir)
            return 0

        notes_content: dict[str, str] = {
            md.stem: md.read_text(encoding="utf-8", errors="replace")
            for md in sorted(notes_dir.glob("*.md"))
        }
        if not notes_content:
            logger.warning("ingest_from_vault: no .md files found in %s", notes_dir)
            return 0

        constraint_map = self._compute_constraints(graph)

        ids, bodies, documents, metadatas = [], [], [], []

        for note_id, content in notes_content.items():
            body = _strip_frontmatter(content).strip()
            if not body:
                continue
            meta = self._build_metadata(note_id, graph, constraint_map)
            ids.append(note_id)
            bodies.append(body[:2000])
            documents.append(body[:2000])
            metadatas.append(meta)

        if not ids:
            return 0

        vecs = self.embed(bodies)
        self._collection.upsert(
            ids=ids,
            embeddings=vecs.tolist(),
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(
            "ingest_from_vault: upserted %d notes from %s", len(ids), notes_dir
        )
        return len(ids)

    def update_constraints(self, graph: nx.DiGraph) -> int:
        """Recompute Burt's constraint for all nodes and refresh ChromaDB metadata.

        Call this after any significant graph mutation (bulk ingest, /analyze).
        Does **not** re-embed — only the ``constraint`` and ``is_ten_candidate``
        metadata fields are updated.

        Returns:
            Number of notes updated.
        """
        constraint_map = self._compute_constraints(graph)
        if not constraint_map:
            return 0

        all_ids = self._collection.get(include=[])["ids"]
        if not all_ids:
            return 0

        existing = self._collection.get(ids=all_ids, include=["metadatas"])
        update_ids, update_metas = [], []

        for nid, meta in zip(existing["ids"], existing["metadatas"]):
            if nid not in constraint_map:
                continue
            c = float(constraint_map[nid])
            meta = dict(meta)
            meta["constraint"] = c
            meta["is_ten_candidate"] = c < TEN_CONSTRAINT_THRESHOLD
            # Re-derive act if it wasn't set during ingestion
            if meta.get("narrative_act", "unknown") == "unknown" and meta.get("tags"):
                _, act = _beat_from_tags(meta["tags"])
                meta["narrative_act"] = act
            update_ids.append(nid)
            update_metas.append(meta)

        if update_ids:
            self._collection.update(ids=update_ids, metadatas=update_metas)
            logger.info("update_constraints: updated %d notes", len(update_ids))

        return len(update_ids)

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        query_embedding: list[float],
        query_note_id: str,
        graph: nx.DiGraph,
        n: int = 5,
        n_fetch: int = 50,
        where: dict | None = None,
    ) -> list[HybridResult]:
        """Hybrid search via Reciprocal Rank Fusion.

        Two ranked lists are fused:

        **ChromaDB contrast list**
            Fetch up to *n_fetch* nearest neighbours by cosine similarity.
            Filter to the ``[TEN_DISTANCE_MIN, TEN_DISTANCE_MAX]`` distance
            window (close enough to be relevant, far enough to be a pivot).
            Re-rank by distance **descending** (most contrasting first).

        **NetworkX constraint list**
            All candidates from the ChromaDB pool, ranked by Burt's constraint
            ascending (0 = pure structural hole = strongest Ten candidate).
            Cross-community notes are sorted before same-community notes.

        RRF merges both lists.  A note ranked highly in both (semantically
        contrasting AND structurally bridging) receives the highest fused score.

        Args:
            query_embedding: Pre-computed embedding of the query note body.
            query_note_id:   ID of the query note (excluded from results).
            graph:           Current vault DiGraph.
            n:               Number of results to return.
            n_fetch:         ChromaDB over-fetch count before filtering/re-ranking.
            where:           Optional ChromaDB ``where`` filter dict.

        Returns:
            Up to *n* ``HybridResult`` objects sorted by ``rrf_score`` descending.
        """
        count = self._collection.count()
        if count == 0:
            logger.warning("hybrid_search: collection is empty")
            return []

        actual_fetch = min(n_fetch, count)

        # ── ChromaDB query ────────────────────────────────────────────
        query_kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results":        actual_fetch,
            "include":          ["distances", "metadatas", "documents"],
        }
        if where:
            query_kwargs["where"] = where

        chroma_result = self._collection.query(**query_kwargs)
        raw_ids       = chroma_result["ids"][0]
        raw_distances = chroma_result["distances"][0]
        raw_metadatas = chroma_result["metadatas"][0]
        raw_documents = chroma_result["documents"][0]

        # Filter: remove query note and apply distance window
        pool: list[tuple[str, float, dict, str]] = [
            (nid, dist, meta, doc)
            for nid, dist, meta, doc
            in zip(raw_ids, raw_distances, raw_metadatas, raw_documents)
            if nid != query_note_id
            and TEN_DISTANCE_MIN <= dist <= TEN_DISTANCE_MAX
        ]

        if not pool:
            # Relax distance filter if nothing passes (small vault edge-case)
            pool = [
                (nid, dist, meta, doc)
                for nid, dist, meta, doc
                in zip(raw_ids, raw_distances, raw_metadatas, raw_documents)
                if nid != query_note_id
            ]

        # Re-rank ChromaDB pool by distance DESCENDING (most contrasting = rank 1)
        pool.sort(key=lambda x: x[1], reverse=True)
        chroma_ranked = [nid for nid, _, _, _ in pool]

        # ── NetworkX constraint ranking ───────────────────────────────
        candidate_ids = [nid for nid, _, _, _ in pool]
        query_community = -1
        if graph and query_note_id in graph:
            try:
                query_community = int(
                    graph.nodes[query_note_id].get("community_id", -1)
                )
            except (TypeError, ValueError):
                pass

        nx_ranked = nx_contrast_ranking(
            query_note_id, graph, candidate_ids,
            query_community_id=query_community,
        )

        # ── RRF merge ─────────────────────────────────────────────────
        rrf_scores = rrf_merge([chroma_ranked, nx_ranked], k=RRF_K)
        sorted_ids = sorted(
            rrf_scores, key=lambda x: rrf_scores[x], reverse=True
        )

        # Build fast lookup maps
        chroma_rank_map = {nid: i + 1 for i, nid in enumerate(chroma_ranked)}
        nx_rank_map     = {nid: i + 1 for i, nid in enumerate(nx_ranked)}
        distance_map    = {nid: dist   for nid, dist, _, _  in pool}
        meta_map        = {nid: meta   for nid, _, meta, _  in pool}
        doc_map         = {nid: doc    for nid, _, _, doc   in pool}

        results: list[HybridResult] = []
        for nid in sorted_ids[:n]:
            meta = meta_map.get(nid, {})
            results.append(HybridResult(
                note_id=nid,
                rrf_score=rrf_scores[nid],
                chroma_rank=chroma_rank_map.get(nid, 9999),
                nx_rank=nx_rank_map.get(nid, 9999),
                distance=distance_map.get(nid, -1.0),
                constraint=float(meta.get("constraint", -1.0)),
                narrative_act=str(meta.get("narrative_act", "unknown")),
                snippet=_snippet(doc_map.get(nid, ""), length=120),
            ))

        return results

    def query_ten_contrasts(
        self,
        note_id: str,
        note_content: str,
        graph: nx.DiGraph,
        n: int = 5,
    ) -> list[HybridResult]:
        """Find the top-*n* Ten-style pivoting/contrasting notes for *note_id*.

        Embeds the stripped body of *note_content* then delegates to
        ``hybrid_search``.  No act filter is applied on the ChromaDB side —
        cross-domain contrast is discovered through RRF, not pre-filtering.

        Args:
            note_id:      Identifier of the query note (excluded from results).
            note_content: Full raw note text (may contain YAML frontmatter).
            graph:        Current vault DiGraph.
            n:            Number of results to return.

        Returns:
            Up to *n* ``HybridResult`` objects, RRF score descending.
        """
        body = _strip_frontmatter(note_content).strip()
        if not body:
            logger.warning("query_ten_contrasts: empty body for '%s'", note_id)
            return []

        vec = self.embed([body])[0].tolist()
        return self.hybrid_search(
            query_embedding=vec,
            query_note_id=note_id,
            graph=graph,
            n=n,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_vault(
        cls,
        vault_dir: Path,
        graph: nx.DiGraph,
        model_name: str = "all-MiniLM-L6-v2",
        force_reingest: bool = False,
    ) -> "ShadowBox":
        """Construct a fully initialised ShadowBox for the given vault.

        ChromaDB is stored at ``vault_dir/.chroma/``.
        Add this directory to ``.gitignore`` (it is large and regenerable).

        Ingestion priority:
        1. Smart Connections ``.ajson`` files (metadata-rich; re-embeds if
           model mismatch).
        2. Raw vault ``notes/*.md`` files (pure generation, always available).

        If the collection already contains documents and *force_reingest* is
        ``False``, ingestion is skipped and only ``update_constraints()`` runs
        (cheap: only metadata writes, no re-embedding).

        Args:
            vault_dir:      Root vault directory (contains ``notes/``,
                            ``.smart-env/``, ``.chroma/``).
            graph:          Current vault DiGraph.
            model_name:     Sentence-transformer model name.
            force_reingest: If ``True``, wipe and rebuild the collection.

        Returns:
            Ready-to-query ``ShadowBox`` instance.
        """
        chroma_path = vault_dir / ".chroma"
        sb = cls(chroma_path, model_name=model_name)

        if sb._collection.count() > 0 and not force_reingest:
            logger.info(
                "ShadowBox.from_vault: reusing existing index (%d docs)",
                sb._collection.count(),
            )
            sb.update_constraints(graph)
            return sb

        if force_reingest and sb._collection.count() > 0:
            sb._client.delete_collection(COLLECTION_NAME)
            sb._collection = sb._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("ShadowBox.from_vault: collection wiped for re-ingest")

        ajson_dir  = vault_dir / ".smart-env" / "multi"
        notes_dir  = vault_dir / "notes"
        notes_content: dict[str, str] = {}

        if notes_dir.exists():
            for md in sorted(notes_dir.glob("*.md")):
                notes_content[md.stem] = md.read_text(
                    encoding="utf-8", errors="replace"
                )

        if ajson_dir.exists() and any(ajson_dir.glob("*.ajson")):
            sb.ingest_from_ajson(ajson_dir, notes_content, graph)
        elif notes_content:
            sb.ingest_from_vault(notes_dir, graph)
        else:
            logger.warning(
                "ShadowBox.from_vault: no .ajson files and no .md notes found"
            )

        sb.update_constraints(graph)
        return sb
