"""ZettleBank backend – FastAPI intelligence layer.

Maintains a persistent NetworkX graph, runs multi-resolution Leiden
community detection, and exposes /analyze for the Obsidian frontend.

Smart Connections integration: reads .smart-env/multi/*.ajson for
TaylorAI/bge-micro-v2 embeddings to find top-5 similar notes, then
uses graph topology + neighbor data to propose tags and relations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import uuid
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx as httpx_client
import numpy as np
import networkx as nx
import spacy
from bertopic import BERTopic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from leidenalg import find_partition, RBConfigurationVertexPartition
from pydantic import BaseModel, Field
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer
import igraph as ig

logger = logging.getLogger("zettlebank")

# ---------------------------------------------------------------------------
# Constants – Controlled vocabularies from architecture.md
# ---------------------------------------------------------------------------

RELATION_TYPES = {
    "contradicts",
    "supports",
    "potential_to",
    "kinetic_to",
    "motivates",
    "hinders",
    "related",  # default fallback per ADR-002
}

# Leiden resolution tiers (ADR-001)
#   γ ≈ 2.0  → Micro-clusters: scene beats, local motifs
#   γ ≈ 1.0  → Mid-level (default)
#   γ ≈ 0.5  → Macro-clusters: global themes, acts
RESOLUTION_MICRO = 2.0
RESOLUTION_MACRO = 1.0

# ---------------------------------------------------------------------------
# Environment config – override any value via .env or shell environment
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv optional; fall back to os.environ / defaults

_VAULT_DIR = os.environ.get("VAULT_DIR", "choracle-remote-00")

# H-2: Guard against path-traversal via VAULT_DIR env variable.
# Absolute paths are permitted so that vaults outside the server directory
# (e.g. C:\Users\...\Documents\choracle-remote-01) can be specified in .env.
_vault_path = Path(_VAULT_DIR.strip())
if ".." in _vault_path.parts:
    raise ValueError(
        f"VAULT_DIR must not contain '..', got: {_VAULT_DIR!r}"
    )

# Graph persistence path
GRAPH_PATH = Path(__file__).parent / "vault_graph.json"

# Smart Connections data path
SMART_ENV_DIR   = Path(__file__).parent / _VAULT_DIR / ".smart-env" / "multi"
EMBED_MODEL_KEY = os.environ.get("EMBED_MODEL_KEY", "TaylorAI/bge-micro-v2")

# Vault notes directory (for BERTopic fitting)
VAULT_NOTES_DIR = Path(__file__).parent / _VAULT_DIR / "notes"

# BERTopic persistence path
BERTOPIC_PATH = Path(__file__).parent / "bertopic_model"

# Ollama settings
OLLAMA_BASE_URL         = os.environ.get("OLLAMA_BASE_URL",         "http://localhost:11434")
OLLAMA_MODEL            = os.environ.get("OLLAMA_MODEL",            "llama3.2")
NARRATIVE_AUDITOR_MODEL = os.environ.get("NARRATIVE_AUDITOR_MODEL", "llama3.1")

# H-1: Guard against SSRF via OLLAMA_BASE_URL.
_parsed_ollama = urlparse(OLLAMA_BASE_URL)
if _parsed_ollama.scheme not in ("http", "https"):
    raise ValueError(
        f"OLLAMA_BASE_URL must use http or https, got scheme: {_parsed_ollama.scheme!r}"
    )

# spaCy model
SPACY_MODEL = os.environ.get("SPACY_MODEL", "en_core_web_trf")

# spaCy entity type → aspect category mapping
SPACY_TO_ASPECT: dict[str, str] = {
    "GPE": "place",
    "LOC": "place",
    "FAC": "place",
    "PERSON": "character",
    "ORG": "character",
    "NORP": "character",
    "DATE": "time",
    "TIME": "time",
    "EVENT": "time",
    "PRODUCT": "object",
    "WORK_OF_ART": "object",
    "LAW": "object",
}

ASPECT_TYPES = {"place", "time", "character", "object"}
AFFECT_VALUES = {"positive", "negative", "mu"}
CODE_VALUES = {"qi", "law", "mu"}

# Structural bridge threshold: Burt constraint below this → Ten-pivot candidate
# M-3: Defensive parse — bad env value falls back to 0.4 rather than crashing at import.
try:
    BURT_BRIDGE_THRESHOLD = float(os.environ.get("BURT_BRIDGE_THRESHOLD", "0.4"))
except ValueError:
    logging.getLogger("zettlebank").warning(
        "Invalid BURT_BRIDGE_THRESHOLD env value; using default 0.4"
    )
    BURT_BRIDGE_THRESHOLD = 0.4

# Constraint threshold for Ten-candidate identification in _assign_macro_acts.
# Nodes with Burt constraint below this are counted as structural-hole candidates.
try:
    TEN_CONSTRAINT_THRESHOLD = float(os.environ.get("TEN_CONSTRAINT_THRESHOLD", "0.4"))
except ValueError:
    logger.warning("Invalid TEN_CONSTRAINT_THRESHOLD env value; using default 0.4")
    TEN_CONSTRAINT_THRESHOLD = 0.4

# 16-beat Kishōtenketsu codes (CLAUDE.md Rule 2) — used by the Narrative Auditor
BEAT_CODES = {
    "ki-1",     "ki-2",     "ki-3",     "ki-4",
    "sho-5",    "sho-6",    "sho-7",    "sho-8",
    "ten-9",    "ten-10",   "ten-11",   "ten-12",
    "ketsu-13", "ketsu-14", "ketsu-15", "ketsu-16",
    "unplaced",
}


def _slugify(text: str) -> str:
    """Lowercase, replace spaces/underscores with hyphens, strip non-alnum."""
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (between opening and closing ---) from note text."""
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3:].lstrip("\n")


def _spacy_tokenizer(text: str) -> list[str]:
    """Lemmatize and filter tokens using the loaded spaCy model.

    Used as the CountVectorizer tokenizer so BERTopic keyword extraction
    operates on lemmas rather than raw surface forms (e.g. 'ritual' not
    'rituals', 'mediat' not 'mediating').  Falls back to whitespace split
    if spaCy has not loaded yet.
    """
    if _nlp is None:
        return text.split()
    doc = _nlp(text[:50_000])
    return [
        token.lemma_.lower()
        for token in doc
        if not token.is_stop
        and not token.is_punct
        and token.is_alpha
        and len(token.lemma_) > 2
    ]


# ---------------------------------------------------------------------------
# App & graph state
# ---------------------------------------------------------------------------

app = FastAPI(title="ZettleBank Intelligence Layer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["app://obsidian.md", "capacitor://localhost", "http://localhost"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

graph = nx.DiGraph()

# Pipeline models (loaded at startup)
_nlp: spacy.language.Language | None = None
_bertopic_model: BERTopic | None = None
_bertopic_ready: bool = False
# note stem → topic id, populated during fit
_note_topics: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Pydantic models – the Data Contract
#
# SCHEMA CONTRACT v1.0
# Mirror: schema.ts – Zod schemas must stay in sync with these models.
# When adding/removing/renaming a field here, update schema.ts to match.
# ---------------------------------------------------------------------------


class RelationType(str, Enum):
    contradicts = "contradicts"
    supports = "supports"
    potential_to = "potential_to"
    kinetic_to = "kinetic_to"
    motivates = "motivates"
    hinders = "hinders"
    related = "related"


class NarrativeActEnum(str, Enum):
    """Kishōtenketsu macro-act assignment for a community."""
    ki    = "ki"
    sho   = "sho"
    ten   = "ten"
    ketsu = "ketsu"


class ProvenanceEnum(str, Enum):
    """How the edge was generated."""
    sc_embedding = "sc_embedding"   # cosine similarity via Smart Connections
    wikilink     = "wikilink"       # regex-extracted [[wiki-link]]
    llm          = "llm"            # LLM-inferred relation


class EdgeMatrix(BaseModel):
    """A single typed, provenanced edge in the narrative graph.

    The YAML key in frontmatter stays `smart_relations` (ADR-003 Shadow
    Database pattern) so existing Dataview queries are not broken.
      - target_id:     slug of the target note
      - relation_type: controlled vocabulary edge label
      - narrative_act: macro-act of the target note's community
      - confidence:    float [0, 1]
      - provenance:    source of the edge
    """
    target_id:     str
    relation_type: RelationType    = RelationType.related
    narrative_act: NarrativeActEnum = NarrativeActEnum.sho
    confidence:    float           = Field(default=1.0, ge=0.0, le=1.0)
    provenance:    ProvenanceEnum  = ProvenanceEnum.sc_embedding


class NarrativeMetadata(BaseModel):
    """Strict 1:1 mirror of choracle-remote-00/templates/frontmatter-template.md.

    Every field matches the template's YAML key exactly so the Obsidian
    frontend can round-trip it via processFrontMatter without drift.
    """
    aliases: Optional[str] = Field(
        default=None,
        description="Alternate display name for the note.",
    )
    description: Optional[str] = Field(
        default=None,
        description="One-line summary of the note's content.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Hierarchical tag list in Obsidian path syntax (e.g. 'topic/ritual_mask', 'affect/positive').",
    )
    smart_relations: list[EdgeMatrix] = Field(
        default_factory=list,
        description="Typed edges to other notes (target_id + relation_type + narrative_act + confidence + provenance).",
    )
    source: Optional[str] = Field(
        default=None,
        description="Origin reference (URL, book title, archive ID).",
    )
    citationID: Optional[str] = Field(
        default=None,
        description="Zotero / BibTeX citation key.",
    )


# C-1, C-2: note_id slug pattern enforced at the Pydantic boundary.
# H-4: content length capped at the API boundary (50 000 chars ≈ ~40 KB UTF-8).
_NOTE_ID_PATTERN = r"^[a-z0-9][a-z0-9\-]{0,127}$"
_CONTENT_MAX = 50_000


class AnalyzeRequest(BaseModel):
    note_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=_NOTE_ID_PATTERN,
        description="Slugified note filename stem (lowercase, hyphens, no traversal).",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=_CONTENT_MAX,
        description="Note markdown body (frontmatter included). Max 50 000 chars.",
    )


class IngestItem(BaseModel):
    """One item for POST /graph/ingest — M-6: typed model replaces list[dict]."""
    note_id: str = Field(..., min_length=1, max_length=128, pattern=_NOTE_ID_PATTERN)
    content: str = Field(default="", max_length=_CONTENT_MAX)


class CommunityTier(BaseModel):
    """One resolution tier of the Leiden partition."""
    resolution: float
    label: str
    community_id: int


class NarrativeAudit(BaseModel):
    """Narrative bridge analysis produced by the Narrative Auditor agent.

    Only populated when bridge_detected=True (Burt constraint < BURT_BRIDGE_THRESHOLD).
    Mirrors schema.ts NarrativeAuditSchema — update both together.
    """
    beat_position: str = Field(
        default="unplaced",
        description="16-beat Kishōtenketsu position (ki-1..ketsu-16 or unplaced).",
    )
    bridge_note_ids: list[str] = Field(
        default_factory=list,
        description="Neighbouring notes this note bridges structurally.",
    )
    narrative_summary: str = Field(
        default="",
        description="LLM summary of the note's narrative bridge function.",
    )


class StructuralHole(BaseModel):
    """Burt's constraint data for the analyzed note.

    Mirrors schema.ts StructuralHoleSchema — update both together.
    """
    constraint_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Burt's constraint value (0=open structural hole, 1=fully constrained).",
    )
    is_ten_candidate: bool = Field(
        default=False,
        description="True when constraint_score < TEN_CONSTRAINT_THRESHOLD.",
    )


class AnalyzeResponse(BaseModel):
    note_id: str
    metadata: NarrativeMetadata
    community_id: Optional[int] = None
    community_tiers: list[CommunityTier] = Field(default_factory=list)
    bridge_detected: bool = False
    narrative_audit: Optional[NarrativeAudit] = None
    narrative_act: str = Field(
        default="sho",
        description="Macro-act assignment for this note's community: ki, sho, ten, or ketsu.",
    )
    structural_hole: StructuralHole = Field(default_factory=StructuralHole)


# ---------------------------------------------------------------------------
# Graph persistence – save / load the vault graph to disk
# ---------------------------------------------------------------------------


def _save_graph() -> None:
    """Persist the NetworkX graph as node-link JSON."""
    data = nx.node_link_data(graph)
    GRAPH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _migrate_graph_v1_to_v2() -> None:
    """Backfill legacy graph edges that pre-date the EdgeMatrix schema.

    Any edge lacking a 'provenance' or 'narrative_act' attribute is assigned
    conservative defaults so the rest of the pipeline can treat all edges
    uniformly without version-checking.

    Heuristic for provenance:
      - relation_type == "related" and no prior provenance → "wikilink"
      - anything else                                      → "sc_embedding"
    """
    updated = 0
    for _u, _v, data in graph.edges(data=True):
        changed = False
        if "provenance" not in data:
            rel_type = data.get("relation_type", "related")
            data["provenance"] = (
                ProvenanceEnum.wikilink.value
                if rel_type == "related"
                else ProvenanceEnum.sc_embedding.value
            )
            changed = True
        if "narrative_act" not in data:
            data["narrative_act"] = NarrativeActEnum.sho.value
            changed = True
        if changed:
            updated += 1
    if updated:
        logger.info("_migrate_graph_v1_to_v2: backfilled %d legacy edges", updated)


def _load_graph() -> None:
    """Restore the graph from disk on server startup."""
    global graph
    if not GRAPH_PATH.exists():
        return
    try:
        raw = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        # L-5: Validate structure before deserialization to catch corrupted files.
        if raw.get("multigraph", False):
            raise ValueError("vault_graph.json must not be a multigraph")
        if not raw.get("directed", True):
            raise ValueError("vault_graph.json must be a directed graph")
        # NetworkX 3.4+ uses "edges" key; older persisted files use "links".
        edges_key = "links" if "links" in raw else "edges"
        graph = nx.node_link_graph(raw, directed=True, edges=edges_key)
        _migrate_graph_v1_to_v2()
    except Exception as exc:
        logger.error("_load_graph: failed to load vault_graph.json (%s); starting empty", exc)


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def _upsert_node(note_id: str, metadata: dict) -> None:
    """Add or update a node in the persistent graph."""
    graph.add_node(note_id, **metadata)


def _extract_wikilinks(content: str) -> list[str]:
    """Parse [[wiki-links]] from note content to build edges."""
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]", content)


def _upsert_edges(source: str, relations: list[EdgeMatrix]) -> None:
    """Add or update directed edges from EdgeMatrix objects."""
    for rel in relations:
        graph.add_edge(
            source,
            rel.target_id,
            relation_type=rel.relation_type.value,
            weight=rel.confidence,
            provenance=rel.provenance.value,
            narrative_act=rel.narrative_act.value,
        )


def _upsert_wikilink_edges(source: str, targets: list[str]) -> None:
    """Create 'related' edges for every wiki-link found in content."""
    for target in targets:
        if target != source:
            graph.add_edge(
                source,
                target,
                relation_type="related",
                weight=0.5,
                provenance=ProvenanceEnum.wikilink.value,
                narrative_act=NarrativeActEnum.sho.value,
            )


# ---------------------------------------------------------------------------
# Smart Connections integration – load embeddings, find top-K neighbors
# ---------------------------------------------------------------------------

# In-memory cache: note_id → 384-dim numpy vector
_embeddings: dict[str, np.ndarray] = {}
# note_id → list of outlink target strings
_sc_outlinks: dict[str, list[str]] = {}


def _note_id_from_path(path: str) -> str:
    """Convert a Smart Connections path like 'notes/khmer-tiger-spirit.md' to 'khmer-tiger-spirit'."""
    return Path(path).stem


def _load_smart_env() -> None:
    """Parse all .ajson files from Smart Connections multi/ dir.

    Each .ajson file contains one or more newline-delimited JSON entries
    keyed as "smart_sources:<path>". We extract the embedding vector
    and outlinks for each note.
    """
    _embeddings.clear()
    _sc_outlinks.clear()

    if not SMART_ENV_DIR.exists():
        return

    for ajson_file in SMART_ENV_DIR.glob("*.ajson"):
        try:
            raw = ajson_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in raw.strip().split("\n"):
            line = line.strip().rstrip(",")
            if not line:
                continue

            # Each line is: "smart_sources:path": { ... } or "smart_blocks:path#heading": { ... }
            # We only want smart_sources entries (full-note embeddings)
            if not line.startswith('"smart_sources:'):
                continue

            try:
                # Parse as a single-key JSON object
                obj = json.loads("{" + line + "}")
            except json.JSONDecodeError:
                continue

            for key, val in obj.items():
                if not key.startswith("smart_sources:"):
                    continue

                path = val.get("path", "")
                if not path:
                    continue

                note_id = _note_id_from_path(path)

                # Extract embedding vector
                embeds = val.get("embeddings", {})
                model_data = embeds.get(EMBED_MODEL_KEY, {})
                vec = model_data.get("vec")
                if vec and isinstance(vec, list):
                    _embeddings[note_id] = np.array(vec, dtype=np.float32)

                # Extract outlinks
                outlinks = val.get("outlinks", [])
                targets = [ol.get("target", "") for ol in outlinks if ol.get("target")]
                if targets:
                    _sc_outlinks[note_id] = targets


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _find_top_k_neighbors(note_id: str, k: int = 5) -> list[tuple[str, float]]:
    """Find the top-k most similar notes by cosine similarity of embeddings.

    Returns list of (neighbor_note_id, similarity_score) sorted descending.
    """
    if note_id not in _embeddings:
        return []

    query_vec = _embeddings[note_id]
    scores: list[tuple[str, float]] = []

    for other_id, other_vec in _embeddings.items():
        if other_id == note_id:
            continue
        sim = _cosine_similarity(query_vec, other_vec)
        scores.append((other_id, sim))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:k]


def _infer_relation_type(
    source: str, target: str, similarity: float
) -> RelationType:
    """Infer a relation type from graph topology and similarity score.

    Heuristics (ADR-002 discriminative approach):
    - If bidirectional edge exists → supports
    - If only one-way edge → potential_to
    - If target has outlink to source via SC data → kinetic_to
    - If high similarity (>0.8) → supports
    - If moderate similarity (>0.5) → motivates
    - Default → related
    """
    has_forward = graph.has_edge(source, target)
    has_backward = graph.has_edge(target, source)

    if has_forward and has_backward:
        return RelationType.supports
    if has_forward:
        return RelationType.potential_to

    # Check SC outlinks for reverse connection
    target_outlinks = _sc_outlinks.get(target, [])
    if source in target_outlinks or source.replace("-", " ") in target_outlinks:
        return RelationType.kinetic_to

    if similarity > 0.8:
        return RelationType.supports
    if similarity > 0.5:
        return RelationType.motivates

    return RelationType.related


# ---------------------------------------------------------------------------
# Multi-resolution Leiden (ADR-001)
#
# Two tiers produce a hierarchical community structure:
#   Macro (γ=0.5)  → coarse global themes  (Acts)
#   Micro (γ=2.0)  → fine scene beats      (Scenes)
#
# Community labels are derived from the most-connected node in each
# cluster, then combined into Obsidian path-syntax tags:
#   Macro/Micro  →  "Theme/Ritual"  or  "Act/Identity"
# ---------------------------------------------------------------------------


def _nx_to_igraph() -> tuple[ig.Graph, dict[int, str]]:
    """Convert the persistent NetworkX DiGraph to an igraph Graph."""
    mapping = {node: idx for idx, node in enumerate(graph.nodes())}
    reverse = {idx: node for node, idx in mapping.items()}

    ig_graph = ig.Graph(directed=True)
    ig_graph.add_vertices(len(mapping))
    ig_graph.vs["name"] = list(mapping.keys())

    edges = [(mapping[u], mapping[v]) for u, v in graph.edges()]
    weights = [graph[u][v].get("weight", 1.0) for u, v in graph.edges()]

    if edges:
        ig_graph.add_edges(edges)
        ig_graph.es["weight"] = weights

    return ig_graph, reverse


def _run_leiden(
    ig_graph: ig.Graph,
    reverse: dict[int, str],
    resolution: float,
) -> dict[str, int]:
    """Run Leiden at a single resolution and return node → community_id."""
    if ig_graph.vcount() < 2:
        return {reverse[i]: 0 for i in range(ig_graph.vcount())}

    has_edges = ig_graph.ecount() > 0

    partition = find_partition(
        ig_graph,
        RBConfigurationVertexPartition,
        weights="weight" if has_edges else None,
        resolution_parameter=resolution,
    )

    return {reverse[idx]: mem for idx, mem in enumerate(partition.membership)}


def _community_label(community_id: int, membership: dict[str, int]) -> str:
    """Derive a human-readable label for a community.

    Strategy: pick the node with the highest degree inside the community,
    title-case its note_id slug, and append the cluster size so the user
    can see at a glance that the label represents a group of notes.
    """
    members = [n for n, c in membership.items() if c == community_id]
    if not members:
        return f"Cluster-{community_id} (0 notes)"

    # Highest-degree node as representative
    best = max(members, key=lambda n: graph.degree(n) if n in graph else 0)
    # Title-case the note_id slug
    label = best.replace("-", " ").replace("_", " ").strip().title()
    return f"{label} Cluster ({len(members)} notes)"


def _detect_multi_resolution() -> tuple[
    dict[str, int],   # macro membership
    dict[str, int],   # micro membership
    dict[int, str],   # macro labels
    dict[int, str],   # micro labels
]:
    """Run Leiden at both macro and micro resolutions."""
    if graph.number_of_nodes() < 2:
        trivial = {n: 0 for n in graph.nodes()}
        return trivial, trivial, {0: "Vault"}, {0: "Vault"}

    ig_graph, reverse = _nx_to_igraph()

    macro = _run_leiden(ig_graph, reverse, RESOLUTION_MACRO)
    micro = _run_leiden(ig_graph, reverse, RESOLUTION_MICRO)

    macro_labels = {
        cid: _community_label(cid, macro)
        for cid in set(macro.values())
    }
    micro_labels = {
        cid: _community_label(cid, micro)
        for cid in set(micro.values())
    }

    return macro, micro, macro_labels, micro_labels



# ---------------------------------------------------------------------------
# Structural bridge detection — Burt's constraint
# ---------------------------------------------------------------------------


def _compute_bridge_score(note_id: str) -> float:
    """Burt's constraint for *note_id* on the undirected graph projection.

    Returns 1.0 (maximum constraint, no structural hole) when:
    - the node has fewer than 2 neighbours (constraint undefined),
    - the graph has < 3 nodes, or
    - nx.constraint() raises an error.

    Values below BURT_BRIDGE_THRESHOLD indicate structural holes:
    the note bridges otherwise disconnected communities (Ten-pivot candidate).

    Uses nx.Graph(graph) rather than graph.to_undirected() so that parallel
    directed edges are collapsed into a single undirected edge.  Edge weights
    are sanitized to at least 0.001 to prevent ZeroDivisionErrors inside
    nx.constraint().
    """
    if note_id not in graph or graph.number_of_nodes() < 3:
        return 1.0
    ug = nx.Graph(graph)
    for u, v, data in ug.edges(data=True):
        data["weight"] = max(float(data.get("weight", 1.0)), 0.001)
    if ug.degree(note_id) < 2:
        return 1.0
    try:
        constraints = nx.constraint(ug, nodes=[note_id])
        return float(constraints.get(note_id, 1.0))
    except Exception:
        return 1.0


def _get_bridge_neighbors(note_id: str, macro: dict[str, int]) -> list[str]:
    """Return immediate neighbours of *note_id* that belong to different macro communities.

    Used to populate NarrativeAudit.bridge_note_ids so the Narrative Auditor
    knows which communities the note bridges.
    """
    if note_id not in graph:
        return []
    note_community = macro.get(note_id, -1)
    all_neighbors = set(graph.successors(note_id)) | set(graph.predecessors(note_id))
    return [n for n in all_neighbors if macro.get(n, -2) != note_community]


def _build_constraint_map() -> dict[str, float]:
    """Burt's constraint for every node in the undirected graph projection.

    Returns {node_id: constraint_value}.  Nodes with fewer than 2 neighbours
    receive 1.0 (fully constrained, no structural hole).

    Uses nx.Graph(graph) and sanitizes weights to avoid ZeroDivisionErrors.
    """
    if graph.number_of_nodes() < 3:
        return {n: 1.0 for n in graph.nodes()}
    ug = nx.Graph(graph)
    for u, v, data in ug.edges(data=True):
        data["weight"] = max(float(data.get("weight", 1.0)), 0.001)
    try:
        raw = nx.constraint(ug)
        return {n: float(v) for n, v in raw.items()}
    except Exception:
        return {n: 1.0 for n in graph.nodes()}


def _store_node_tags(note_id: str, tags: list[str]) -> None:
    """Persist aspect/topic tags on the graph node for cross-note heuristic scoring."""
    if note_id in graph:
        graph.nodes[note_id]["tags"] = tags
    else:
        graph.add_node(note_id, tags=tags)


def _get_all_node_tags() -> dict[str, list[str]]:
    """Return {note_id: [tags]} for every node that has stored tags."""
    return {
        n: data["tags"]
        for n, data in graph.nodes(data=True)
        if data.get("tags")
    }


def _assign_macro_acts(
    macro_membership: dict[str, int],
    g: nx.DiGraph,
    constraint_map: dict[str, float],
    node_tags: dict[str, list[str]],
) -> dict[int, str]:
    """Score macro-communities and assign Ki / Sho / Ten / Ketsu acts.

    Priority order:
    1. Ten   — community with highest concentration of low-constraint nodes
               (constraint < TEN_CONSTRAINT_THRESHOLD).
    2. Ki    — community with highest (aspect/place + aspect/time tag count)
               divided by mean out-degree.
    3. Ketsu — community with highest in-degree originating from Ki and Ten nodes.
    4. Sho   — all remaining communities.

    Returns {community_id: act_name}.
    """
    community_ids = sorted(set(macro_membership.values()))
    if not community_ids:
        return {}

    # Group nodes by community
    comm_nodes: dict[int, list[str]] = {c: [] for c in community_ids}
    for node, cid in macro_membership.items():
        comm_nodes[cid].append(node)

    assigned: dict[int, str] = {}

    # ── Ten: highest concentration of structural-hole nodes ──────────────
    def _ten_score(cid: int) -> float:
        nodes = comm_nodes[cid]
        if not nodes:
            return 0.0
        bridge_count = sum(
            1 for n in nodes if constraint_map.get(n, 1.0) < TEN_CONSTRAINT_THRESHOLD
        )
        return bridge_count / len(nodes)

    ten_id = max(community_ids, key=_ten_score)
    assigned[ten_id] = "ten"

    # ── Ki: highest place/time tag density relative to out-degree ────────
    def _ki_score(cid: int) -> float:
        if cid in assigned:
            return -1.0
        nodes = comm_nodes[cid]
        if not nodes:
            return 0.0
        tag_count = sum(
            sum(
                1 for t in node_tags.get(n, [])
                if t.startswith("aspect/place") or t.startswith("aspect/time")
            )
            for n in nodes
        )
        mean_outdeg = sum(g.out_degree(n) for n in nodes) / len(nodes)
        return tag_count / (mean_outdeg + 1.0)

    remaining = [c for c in community_ids if c not in assigned]
    if remaining:
        ki_id = max(remaining, key=_ki_score)
        assigned[ki_id] = "ki"

        # ── Ketsu: highest in-degree from Ki and Ten nodes ────────────────
        ki_ten_nodes = set(comm_nodes.get(ten_id, []) + comm_nodes.get(ki_id, []))

        def _ketsu_score(cid: int) -> int:
            if cid in assigned:
                return -1
            target_nodes = set(comm_nodes[cid])
            return sum(
                1 for u, v in g.edges()
                if u in ki_ten_nodes and v in target_nodes
            )

        remaining2 = [c for c in community_ids if c not in assigned]
        if remaining2:
            ketsu_id = max(remaining2, key=_ketsu_score)
            assigned[ketsu_id] = "ketsu"

    # ── Sho: all remaining ────────────────────────────────────────────────
    for cid in community_ids:
        if cid not in assigned:
            assigned[cid] = "sho"

    return assigned


# Legacy single-resolution helper (used by /graph/communities)
def _detect_communities(resolution: float = 1.0) -> dict[str, int]:
    """Single-resolution Leiden for backward compatibility."""
    if graph.number_of_nodes() < 2:
        return {n: 0 for n in graph.nodes()}
    ig_graph, reverse = _nx_to_igraph()
    return _run_leiden(ig_graph, reverse, resolution)


# ---------------------------------------------------------------------------
# Relation + tag generation via Smart Connections embeddings
# ---------------------------------------------------------------------------


def _graph_neighbors_fallback(
    note_id: str, wikilink_targets: list[str], k: int = 5
) -> list[tuple[str, float]]:
    """Build a neighbor list from graph topology when SC embeddings are absent.

    Priority order:
      1. Direct wiki-link targets parsed from note content   (score 0.7)
      2. Existing graph successors/predecessors by degree    (score ∝ degree)

    Returns list of (neighbor_id, score) capped at k, deduplicated.
    """
    seen: set[str] = set()
    neighbors: list[tuple[str, float]] = []

    # 1. Wiki-link targets from content (highest priority)
    for raw_target in wikilink_targets:
        slug = raw_target.lower().strip().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        if slug and slug != note_id and slug not in seen and slug in graph:
            seen.add(slug)
            neighbors.append((slug, 0.7))

    if len(neighbors) >= k:
        return neighbors[:k]

    # 2. Graph topology: successors then predecessors, ranked by degree
    topology_candidates: list[tuple[str, float]] = []
    if note_id not in graph:
        return neighbors[:k]
    for nid in list(graph.successors(note_id)) + list(graph.predecessors(note_id)):
        if nid == note_id or nid in seen:
            continue
        seen.add(nid)
        degree = graph.degree(nid)
        score = round(min(0.6, 0.1 + degree * 0.05), 3)
        topology_candidates.append((nid, score))

    # Sort by score descending
    topology_candidates.sort(key=lambda x: x[1], reverse=True)
    neighbors.extend(topology_candidates[: k - len(neighbors)])

    return neighbors[:k]


def _generate_smart_relations_with_provenance(
    note_id: str, neighbors: list[tuple[str, float]], limit: int = 5
) -> list[EdgeMatrix]:
    """Generate EdgeMatrix edges, detecting provenance from actual graph edges."""
    relations: list[EdgeMatrix] = []

    for neighbor_id, sim_score in neighbors[:limit]:
        rel_type = _infer_relation_type(note_id, neighbor_id, sim_score)

        # Detect provenance: check if a graph edge exists with wikilink provenance
        edge_data = graph.get_edge_data(note_id, neighbor_id) or {}
        edge_prov = edge_data.get("provenance", "")
        if edge_prov == ProvenanceEnum.wikilink.value:
            prov = ProvenanceEnum.wikilink
        elif edge_prov == ProvenanceEnum.llm.value:
            prov = ProvenanceEnum.llm
        elif note_id in _embeddings and neighbor_id in _embeddings:
            prov = ProvenanceEnum.sc_embedding
        else:
            prov = ProvenanceEnum.wikilink  # graph-topology fallback

        relations.append(EdgeMatrix(
            target_id=neighbor_id,
            relation_type=rel_type,
            confidence=round(min(sim_score, 1.0), 3),
            provenance=prov,
        ))

    return relations


def _classify_relations(
    note_id: str, content: str
) -> tuple[list[EdgeMatrix], list[tuple[str, float]]]:
    """Extract EdgeMatrix edges using SC embeddings; falls back to graph topology.

    Returns (relations, neighbors) so the caller can reuse neighbors for tags.
    Strategy:
      1. SC embeddings available → cosine top-K (original behaviour)
      2. No embeddings          → wiki-link targets + graph successors/predecessors
    """
    neighbors = _find_top_k_neighbors(note_id, k=5)

    if not neighbors:
        # Fallback: parse wiki-links from content + use graph topology
        wikilink_targets = _extract_wikilinks(content)
        neighbors = _graph_neighbors_fallback(note_id, wikilink_targets, k=5)

    if not neighbors:
        return [], []

    relations = _generate_smart_relations_with_provenance(note_id, neighbors, limit=5)
    return relations, neighbors


# ---------------------------------------------------------------------------
# Cross-act edge generation (beat orchestration support)
#
# Intra-cluster edges (above) capture semantic similarity within a community.
# Cross-act edges reach deliberately across community boundaries so the beat
# orchestrator can traverse a ki → sho → ten → ketsu sequence along typed
# graph paths rather than being stranded inside a single theme cluster.
# ---------------------------------------------------------------------------


def _find_best_neighbor_in_act(
    note_id: str,
    target_act: str,
    macro: dict[str, int],
    community_act_map: dict[int, str],
    exclude_ids: set[str],
) -> tuple[str, float] | None:
    """Return the highest-cosine note whose macro-community maps to target_act.

    Falls back to graph-topology (highest-degree node in the target act) when
    SC embeddings are absent for the source note.  Returns None if no qualifying
    candidate exists.
    """
    if note_id not in _embeddings:
        # Topology fallback: pick the highest-degree graph node in target_act.
        candidates = [
            n for n in graph.nodes()
            if n != note_id
            and n not in exclude_ids
            and community_act_map.get(macro.get(n)) == target_act
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda n: graph.degree(n))
        return (best, 0.3)  # synthetic low-confidence score for topology fallback

    query_vec = _embeddings[note_id]
    best_id: str | None = None
    best_score = -1.0

    for other_id, other_vec in _embeddings.items():
        if other_id == note_id or other_id in exclude_ids:
            continue
        other_cid = macro.get(other_id)
        if community_act_map.get(other_cid) != target_act:
            continue
        sim = _cosine_similarity(query_vec, other_vec)
        if sim > best_score:
            best_score = sim
            best_id = other_id

    if best_id is None:
        return None
    return (best_id, best_score)


def _infer_cross_act_relation_type(source_act: str, target_act: str) -> RelationType:
    """Act-pair heuristic for cross-community edges.

    Encodes the narrative logic of Kishōtenketsu transitions:
      ki  → sho  : motivates    (introduction energises development)
      sho → ten  : potential_to (development latently enables the twist)
      ten → ketsu: kinetic_to   (twist actively drives resolution)
      ki  → ten  : contradicts  (foundation held against its pivot — juxtaposition)
      ten → ki   : contradicts  (reverse — pivot reframes the foundation)
      *   → ki   : supports     (anything feeding back into the foundation)
      sho → ketsu: supports     (development feeds directly into resolution)
      ki  → ketsu: supports     (foundation underlies resolution)
      default    : related
    """
    mapping: dict[tuple[str, str], RelationType] = {
        ("ki",    "sho"):   RelationType.motivates,
        ("sho",   "ten"):   RelationType.potential_to,
        ("ten",   "ketsu"): RelationType.kinetic_to,
        ("ki",    "ten"):   RelationType.contradicts,
        ("ten",   "ki"):    RelationType.contradicts,
        ("sho",   "ki"):    RelationType.supports,
        ("ketsu", "ki"):    RelationType.supports,
        ("sho",   "ketsu"): RelationType.supports,
        ("ki",    "ketsu"): RelationType.supports,
    }
    return mapping.get((source_act, target_act), RelationType.related)


def _build_cross_act_edges(
    note_id: str,
    source_act: str,
    macro: dict[str, int],
    community_act_map: dict[int, str],
    intra_neighbor_ids: set[str],
) -> list[EdgeMatrix]:
    """Generate up to 3 cross-act EdgeMatrix edges — one per foreign act.

    For each Kishōtenketsu act that differs from source_act, finds the
    highest-similarity note in that act and creates a typed edge using
    _infer_cross_act_relation_type.  narrative_act is set to target_act
    so the beat orchestrator knows which act each cross-cluster edge reaches.

    Already-selected intra-cluster neighbors are excluded to avoid duplicates.
    """
    cross_edges: list[EdgeMatrix] = []
    exclude = intra_neighbor_ids | {note_id}

    for target_act in ("ki", "sho", "ten", "ketsu"):
        if target_act == source_act:
            continue

        result = _find_best_neighbor_in_act(
            note_id, target_act, macro, community_act_map, exclude
        )
        if result is None:
            continue

        neighbor_id, sim_score = result
        rel_type = _infer_cross_act_relation_type(source_act, target_act)
        prov = (
            ProvenanceEnum.sc_embedding
            if note_id in _embeddings and neighbor_id in _embeddings
            else ProvenanceEnum.wikilink
        )

        cross_edges.append(EdgeMatrix(
            target_id=neighbor_id,
            relation_type=rel_type,
            narrative_act=NarrativeActEnum(target_act),
            confidence=round(min(sim_score, 1.0), 3),
            provenance=prov,
        ))
        exclude.add(neighbor_id)

    return cross_edges


# ---------------------------------------------------------------------------
# Multi-stage pipeline helpers (Stages B, C, D)
# ---------------------------------------------------------------------------


def _load_pipeline_models() -> None:
    """Load spaCy and BERTopic models at startup.

    Attempts to load SPACY_MODEL (default: en_core_web_trf). Falls back to
    en_core_web_sm if the trf model is not installed (e.g. Python 3.13 on
    Windows where curated-tokenizers fails to compile). Set SPACY_MODEL=en_core_web_sm
    in .env to use the smaller model explicitly.
    """
    global _nlp
    try:
        _nlp = spacy.load(SPACY_MODEL)
        logger.info("spaCy model loaded: %s", SPACY_MODEL)
    except OSError:
        fallback = "en_core_web_sm"
        logger.warning(
            "spaCy model '%s' not found — falling back to '%s'. "
            "For full transformer NER run: python -m spacy download %s",
            SPACY_MODEL, fallback, SPACY_MODEL,
        )
        _nlp = spacy.load(fallback)
    _fit_bertopic_on_vault()


def _fit_bertopic_on_vault() -> None:
    """Fit BERTopic on all vault .md files. Safe for small/empty vaults.

    Improvements applied:
    - Frontmatter stripped before fitting so YAML schema words don't pollute
      keyword extraction.
    - Smart Connections embeddings (bge-micro-v2, 384-dim) passed directly as
      pre-computed embeddings when available, keeping BERTopic's semantic space
      consistent with the Smart Relations embeddings.
    - TruncatedSVD replaces PCA for dimensionality reduction (better suited to
      sparse/dense text embedding matrices; avoids numba/LLVM issues on Py3.13).
    - spaCy lemmatization tokenizer in CountVectorizer so keyword extraction
      collapses inflected forms (ritual/rituals → ritual).
    - n_clusters ceiling raised to 15 for finer-grained topic coverage.
    - Stores note_stem → topic_id mapping for per-note lookup at analyze-time.
    """
    global _bertopic_model, _bertopic_ready, _note_topics

    docs: list[str] = []
    stems: list[str] = []
    if VAULT_NOTES_DIR.exists():
        for md in sorted(VAULT_NOTES_DIR.glob("*.md")):
            raw = md.read_text(encoding="utf-8", errors="replace").strip()
            if raw:
                docs.append(_strip_frontmatter(raw))
                stems.append(md.stem)

    if len(docs) < 2:
        logger.warning("BERTopic: <2 docs in vault, model not fitted")
        _bertopic_model = None
        _bertopic_ready = False
        _note_topics = {}
        return

    # Align Smart Connections embeddings with the doc list.
    # Notes that have SC embeddings use them directly; the rest are dropped
    # from the pre-computed matrix path and BERTopic falls back to its own
    # sentence-transformer embedding for the full corpus.
    embedding_matrix: np.ndarray | None = None
    if _embeddings:
        aligned_docs, aligned_stems, aligned_vecs = [], [], []
        for doc, stem in zip(docs, stems):
            if stem in _embeddings:
                aligned_docs.append(doc)
                aligned_stems.append(stem)
                aligned_vecs.append(_embeddings[stem])
        if len(aligned_docs) >= 2:
            docs = aligned_docs
            stems = aligned_stems
            embedding_matrix = np.stack(aligned_vecs).astype(np.float32)
            logger.info(
                f"BERTopic: using {len(aligned_docs)} pre-computed SC embeddings"
            )

    n_clusters = max(2, min(15, len(docs) // 3))
    n_components = min(5, len(docs) - 1)

    # spaCy lemmatization tokenizer (uses the already-loaded _nlp model).
    # stop_words=None because _spacy_tokenizer already filters stop words.
    vectorizer = CountVectorizer(tokenizer=_spacy_tokenizer, stop_words=None)

    _bertopic_model = BERTopic(
        umap_model=TruncatedSVD(n_components=n_components),
        hdbscan_model=KMeans(n_clusters=n_clusters, random_state=42),
        vectorizer_model=vectorizer,
        verbose=False,
    )
    topics, _ = _bertopic_model.fit_transform(docs, embeddings=embedding_matrix)

    _note_topics = {stem: int(tid) for stem, tid in zip(stems, topics)}
    _bertopic_ready = True
    logger.info(
        f"BERTopic fitted on {len(docs)} docs, "
        f"{len(_bertopic_model.get_topic_info())} topics, "
        f"{n_clusters} clusters"
    )


def _get_community_keywords(community_id: int, membership: dict[str, int], top_n: int = 8) -> list[str]:
    """Return the top-N BERTopic keywords for a macro community.

    Finds the plurality topic_id across all community members via Counter,
    then returns keyword strings from that dominant topic.
    Returns [] if BERTopic not ready, community is empty, or all members are outliers.
    """
    if _bertopic_model is None or not _bertopic_ready:
        return []

    members = [stem for stem, cid in membership.items() if cid == community_id]
    if not members:
        return []

    topic_ids = [_note_topics[stem] for stem in members if stem in _note_topics]
    # Filter out outlier topic (-1)
    topic_ids = [tid for tid in topic_ids if tid != -1]
    if not topic_ids:
        return []

    dominant_id, _ = Counter(topic_ids).most_common(1)[0]
    topic_info = _bertopic_model.get_topic(dominant_id)
    if not topic_info:
        return []

    return [word for word, _ in topic_info[:top_n]]


async def _llm_topic_label(keywords: list[str], note_content: str) -> str:
    """Call Ollama to produce a concise Title_Case label from community keywords.

    Returns a 2–5 word label with underscores (e.g. "Logistics_of_the_Siege").
    Falls back to slugified top-3 keywords on any error or empty result.
    """
    fallback = "_".join(_slugify(w) for w in keywords[:3] if _slugify(w)) or "unassigned"

    keyword_str = ", ".join(keywords)
    excerpt = note_content[:500].replace("\n", " ")
    prompt = (
        "Given these topic keywords and a note excerpt, produce a concise 2-5 word "
        "label in Title_Case with underscores instead of spaces (e.g. Siege_Logistics, "
        "Ritual_Mask_Ceremony, Character_Identity_Crisis). "
        "Output ONLY the label, no punctuation, no explanation.\n\n"
        f"Keywords: {keyword_str}\n"
        f"Excerpt: {excerpt}\n\n"
        "Label:"
    )

    try:
        raw = await _ollama_complete(prompt, json_mode=False)
        if not isinstance(raw, str):
            return fallback
        # Post-process: strip surrounding quotes/whitespace, normalise spaces to _
        label = raw.strip().strip('"\'')
        label = re.sub(r"\s+", "_", label)
        # Keep only alnum, underscore, hyphen
        label = re.sub(r"[^a-zA-Z0-9_\-]", "", label)
        if not label:
            return fallback
        return label
    except Exception as exc:
        logger.warning(f"Stage B LLM label error: {exc}")
        return fallback


async def _run_stage_b_topic(
    content: str,
    note_id: str,
    macro_id: int | None,
    macro_membership: dict[str, int],
) -> list[str]:
    """Stage B: BERTopic → LLM → up to two topic/<HumanLabel> tags.

    Priority order (note-specific first, community second):
    1. If BERTopic not ready → ["topic/unassigned"]
    2. Resolve this note's own BERTopic topic_id from the pre-fitted lookup;
       if the note is new (not in vault at fit time) run transform() live.
    3. Resolve the Leiden macro community's dominant topic_id; if it differs
       from the note-level topic, generate a second label (up to 2 tags total).
    4. Each topic_id → top-8 lemmatised keywords → Ollama Title_Case label.
    """
    if _bertopic_model is None or not _bertopic_ready:
        return ["topic/unassigned"]

    async def _label_for_topic_id(topic_id: int) -> str | None:
        """Return a Title_Case label string for a BERTopic topic_id, or None."""
        info = _bertopic_model.get_topic(topic_id)
        if not info:
            return None
        kws = [w for w, _ in info[:8]]
        return await _llm_topic_label(kws, content)

    tags: list[str] = []

    # --- Priority 1: note-level topic (specific to this document) ---
    note_topic_id = _note_topics.get(note_id)
    if note_topic_id is None:
        # New note not in vault at fit time — run transform() live
        try:
            raw_topics, _ = await asyncio.to_thread(
                _bertopic_model.transform, [_strip_frontmatter(content)]
            )
            note_topic_id = int(raw_topics[0])
        except Exception as exc:
            logger.warning(f"Stage B transform() error: {exc}")
            note_topic_id = None

    if note_topic_id is not None and note_topic_id != -1:
        label = await _label_for_topic_id(note_topic_id)
        if label:
            tags.append(f"topic/{label}")

    # --- Priority 2: community-level topic (if distinct from note topic) ---
    if macro_id is not None and len(tags) < 2:
        members = [
            s for s, cid in macro_membership.items() if cid == macro_id
        ]
        topic_ids = [
            _note_topics[s]
            for s in members
            if s in _note_topics and _note_topics[s] != -1
        ]
        if topic_ids:
            community_topic_id, _ = Counter(topic_ids).most_common(1)[0]
            if community_topic_id != note_topic_id:
                label2 = await _label_for_topic_id(community_topic_id)
                if label2:
                    candidate = f"topic/{label2}"
                    if candidate not in tags:
                        tags.append(candidate)

    return tags if tags else ["topic/unassigned"]


def _run_stage_c_aspects(content: str) -> list[str]:
    """Stage C: spaCy NER → aspect/category/entity tags.

    For each detected entity, records its slugified text under the
    appropriate aspect category.  Uses a Counter per category so that
    entities mentioned more than once rank higher; the top-3 by frequency
    are kept per category to cap tag explosion.  Slug deduplication means
    surface variants ("Mexico"/"Mexican" under different labels) don't
    produce duplicate slugs within the same category.
    """
    if _nlp is None:
        return []

    doc = _nlp(content[:100000])
    found: dict[str, Counter] = {}
    for ent in doc.ents:
        aspect = SPACY_TO_ASPECT.get(ent.label_)
        if aspect:
            slug = _slugify(ent.text)
            if slug:
                found.setdefault(aspect, Counter())[slug] += 1

    tags: list[str] = []
    for category in sorted(found):
        for slug, _ in found[category].most_common(3):
            tags.append(f"aspect/{category}/{slug}")
    return tags


async def _ollama_complete(prompt: str, model: str = OLLAMA_MODEL,
                           json_mode: bool = True) -> dict | str:
    """Async Ollama HTTP call via httpx."""
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    # H-1: follow_redirects=False prevents redirect-based SSRF chaining.
    async with httpx_client.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        raw = body.get("response", "")

    if json_mode:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"affect": "mu", "code": "mu"}
    return raw


def _default_beat_from_community(
    note_id: str, macro_id: int | None, macro_labels: dict[int, str]
) -> str:
    """Infer a default Kishōtenketsu beat from macro community label.

    Used when the Narrative Auditor is NOT triggered (bridge_detected=False).
    Maps community label keywords to the opening beat of each act.
    """
    if macro_id is None:
        return "ki-1"
    label = macro_labels.get(macro_id, "").lower()
    if any(w in label for w in ("ten", "twist", "pivot", "turn", "revers")):
        return "ten-9"
    if any(w in label for w in ("sho", "develop", "continu", "elabor", "deepen")):
        return "sho-5"
    if any(w in label for w in ("ketsu", "resol", "synthes", "conclus", "integr")):
        return "ketsu-13"
    return "ki-1"


async def _run_stage_d_llm_classify(content: str) -> list[str]:
    """Stage D — fast affect scoring, unconditional on every note.

    Uses OLLAMA_MODEL (lightweight) to classify valence only: positive,
    negative, or mu (neutral/ambiguous).  Returns a single-element list
    e.g. ``["affect/positive"]``.

    Kept intentionally minimal — the prompt asks for one field so the LLM
    has no opportunity to hallucinate unrelated output.  The heavy Narrative
    Auditor is reserved for Ten-candidates (bridge_detected=True).
    """
    prompt = (
        "You are a valence classifier for a knowledge graph.\n"
        "Read the note excerpt and respond with valid JSON only:\n"
        '{"affect": "<positive|negative|mu>"}\n\n'
        f"Text:\n{content[:1500]}\n\nJSON:"
    )
    try:
        result = await _ollama_complete(prompt, model=OLLAMA_MODEL, json_mode=True)
        if not isinstance(result, dict):
            return ["affect/mu"]
        affect = str(result.get("affect", "mu")).strip()
        if affect not in AFFECT_VALUES:
            affect = "mu"
        return [f"affect/{affect}"]
    except Exception as exc:
        logger.warning("Stage D affect error: %s", exc)
        return ["affect/mu"]


async def _run_narrative_auditor(
    note_id: str,
    content: str,
    bridge_nodes: list[str],
) -> tuple[list[str], NarrativeAudit]:
    """Agent 3 — Narrative Auditor (Llama 3.1), conditional on bridge detection.

    Classifies the note's 16-beat Kishōtenketsu position and summarises its
    structural bridge function.  Returns (combined_tags, NarrativeAudit).
    Only called when Burt constraint < BURT_BRIDGE_THRESHOLD.
    """
    fallback_beat = "ten-9"  # Bridge notes typically occupy the Ten (pivot) act
    # M-4, C-1: Sanitize bridge node IDs before embedding in the LLM prompt.
    bridge_str = (
        ", ".join(_slugify(n) for n in bridge_nodes[:6] if _slugify(n))
        if bridge_nodes else "none identified"
    )
    beat_list = (
        "ki-1 ki-2 ki-3 ki-4 (Introduction/Ki), "
        "sho-5 sho-6 sho-7 sho-8 (Development/Sho), "
        "ten-9 ten-10 ten-11 ten-12 (Twist-Pivot/Ten), "
        "ketsu-13 ketsu-14 ketsu-15 ketsu-16 (Resolution/Ketsu), "
        "unplaced"
    )
    prompt = (
        "You are a narrative analyst specialising in Kishōtenketsu (起承転結 — "
        "Introduction, Development, Twist, Resolution).\n"
        "This knowledge-graph note has been flagged as a structural bridge "
        "(low Burt constraint: it connects otherwise separate clusters).\n\n"
        f"Bridged communities contain these notes: {bridge_str}\n\n"
        "Classify this note using the 16-beat matrix and explain its bridge role.\n"
        "Respond with valid JSON only — no prose before or after:\n"
        "{\n"
        '  "beat_position": "<one beat slug from: ' + beat_list + '>",\n'
        '  "narrative_summary": "<2-3 sentences on this note\'s bridge function>"\n'
        "}\n\n"
        f"Text:\n{content[:2000]}\n\nJSON:"
    )

    fallback_audit = NarrativeAudit(
        beat_position=fallback_beat,
        bridge_note_ids=bridge_nodes,
        narrative_summary="Structural bridge detected; narrative function unresolved.",
    )

    try:
        result = await _ollama_complete(
            prompt, model=NARRATIVE_AUDITOR_MODEL, json_mode=True
        )
        if not isinstance(result, dict):
            return [f"code/{fallback_beat}"], fallback_audit

        beat = str(result.get("beat_position", fallback_beat)).strip()
        if beat not in BEAT_CODES:
            beat = fallback_beat

        # M-1: Sanitize narrative_summary to prevent YAML-breaking content.
        summary = str(result.get("narrative_summary", "")).strip()
        summary = re.sub(r"[\r\n]+", " ", summary)[:500]
        summary = summary.replace("---", "- - -")
        audit = NarrativeAudit(
            beat_position=beat,
            bridge_note_ids=[_slugify(n) for n in bridge_nodes if _slugify(n)],
            narrative_summary=summary,
        )
        return [f"code/{beat}"], audit

    except Exception as exc:
        logger.warning("Narrative Auditor error: %s", exc)
        return [f"code/{fallback_beat}"], fallback_audit


def _assemble_tags(
    topic_tags: list[str],
    aspect_tags: list[str],
    affect_tags: list[str],
    code_tags: list[str],
    limit: int = 10,
) -> list[str]:
    """Merge all pipeline tags, dedup, cap at limit."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in topic_tags + aspect_tags + affect_tags + code_tags:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)
        if len(result) >= limit:
            break
    return result


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup():
    _load_graph()
    # L-1: offload synchronous I/O off the event loop.
    await asyncio.to_thread(_load_smart_env)
    # H-3: offload heavy transformer + BERTopic load off the event loop.
    await asyncio.to_thread(_load_pipeline_models)


@app.on_event("shutdown")
async def on_shutdown():
    _save_graph()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "graph_persisted": GRAPH_PATH.exists(),
        "smart_connections": {
            "embeddings_loaded": len(_embeddings),
            "outlinks_loaded": len(_sc_outlinks),
        },
        "bertopic_ready": _bertopic_ready,
    }


@app.post("/smart-env/reload")
async def reload_smart_env():
    """Reload Smart Connections embeddings from disk."""
    # L-1: offload file I/O off the event loop.
    await asyncio.to_thread(_load_smart_env)
    return {
        "embeddings_loaded": len(_embeddings),
        "outlinks_loaded": len(_sc_outlinks),
    }


@app.post("/bertopic/refit")
async def refit_bertopic():
    """Refit the BERTopic model on the current vault notes without restarting."""
    await asyncio.to_thread(_fit_bertopic_on_vault)
    return {
        "bertopic_ready": _bertopic_ready,
        "note_topics": len(_note_topics),
    }


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    """Asynchronous Tri-Agent pipeline.

    Agent 1 — Structural Agent (NetworkX + Leiden + Burt constraint):
      Sequential. Builds/updates graph, runs dual-resolution Leiden, computes
      Burt constraint to detect low-modularity bridges (Ten-pivot candidates).

    Agent 2 — Semantic Agent (spaCy en_core_web_trf, low VRAM):
      Sequential after Agent 1 to prevent VRAM contention with Agent 3.
      Stage B (BERTopic CPU-only) runs concurrently with Agent 2.

    Agent 3 — Narrative Auditor (Llama 3.1, conditional):
      Triggered only when bridge_score < BURT_BRIDGE_THRESHOLD.
      Produces full 16-beat Kishōtenketsu classification + NarrativeAudit.
      When not triggered, a default beat is inferred from the community label.

    Final: _assemble_tags() → up to 10 tags.
    """
    if not req.content.strip():
        raise HTTPException(status_code=422, detail="Content must not be empty.")

    # --- Agent 1: Structural Agent ---
    relations, _neighbors = _classify_relations(req.note_id, req.content)
    wikilinks = _extract_wikilinks(req.content)
    _upsert_node(req.note_id, {})
    _upsert_edges(req.note_id, relations)
    _upsert_wikilink_edges(req.note_id, wikilinks)
    _save_graph()
    macro, micro, macro_labels, micro_labels = _detect_multi_resolution()

    macro_id = macro.get(req.note_id)
    micro_id  = micro.get(req.note_id)

    # Bridge detection: low Burt constraint = structural hole = Ten-pivot candidate
    bridge_score    = _compute_bridge_score(req.note_id)
    bridge_detected = bridge_score < BURT_BRIDGE_THRESHOLD
    bridge_nodes    = _get_bridge_neighbors(req.note_id, macro) if bridge_detected else []

    logger.info(
        "Structural Agent: note=%s  constraint=%.3f  bridge=%s  bridge_nodes=%d",
        req.note_id, bridge_score, bridge_detected, len(bridge_nodes),
    )

    # --- Agent 2: Semantic Agent + Stage D affect (all parallel) ---
    # Stage B (BERTopic), Stage C (spaCy NER), and Stage D (affect scoring)
    # run concurrently.  Stage D is unconditional — every note gets an
    # affect tag.  The Narrative Auditor (Agent 3) is reserved for
    # Ten-candidates (bridge_detected=True) and handles only beat position.
    topic_tags, aspect_tags, affect_tags = await asyncio.gather(
        _run_stage_b_topic(req.content, req.note_id, macro_id, macro),
        asyncio.to_thread(_run_stage_c_aspects, req.content),
        _run_stage_d_llm_classify(req.content),
    )

    # --- Macro-act assignment — runs after Stage C so aspect tags are available ---
    _store_node_tags(req.note_id, aspect_tags)
    constraint_map = _build_constraint_map()
    node_tags = _get_all_node_tags()
    community_act_map = _assign_macro_acts(macro, graph, constraint_map, node_tags)
    narrative_act = community_act_map.get(macro_id, "sho") if macro_id is not None else "sho"

    # Backfill narrative_act on EdgeMatrix objects using the resolved act map.
    # Also refresh the stored graph edge attribute so persistence is accurate.
    for edge in relations:
        target_cid = macro.get(edge.target_id)
        act_str = community_act_map.get(target_cid, "sho") if target_cid is not None else "sho"
        edge.narrative_act = NarrativeActEnum(act_str)
        if graph.has_edge(req.note_id, edge.target_id):
            graph[req.note_id][edge.target_id]["narrative_act"] = act_str

    # --- Cross-act edges: one best neighbor per foreign act ------------------
    # Runs after community_act_map is resolved so _find_best_neighbor_in_act
    # can filter candidates by their true act assignment.  These edges cross
    # community boundaries so the beat orchestrator can walk a full
    # ki → sho → ten → ketsu sequence in a single graph traversal.
    intra_ids = {e.target_id for e in relations}
    cross_relations = _build_cross_act_edges(
        req.note_id, narrative_act, macro, community_act_map, intra_ids
    )
    if cross_relations:
        _upsert_edges(req.note_id, cross_relations)
        _save_graph()
        relations = relations + cross_relations
        logger.info(
            "Cross-act edges added: note=%s  source_act=%s  targets=%s",
            req.note_id,
            narrative_act,
            [(e.target_id, e.relation_type, e.narrative_act) for e in cross_relations],
        )

    structural_hole = StructuralHole(
        constraint_score=round(bridge_score, 6),
        is_ten_candidate=bridge_detected,
    )

    # --- Agent 3: Narrative Auditor — conditional on bridge detection ---
    if bridge_detected:
        logger.info("Narrative Auditor triggered for %s", req.note_id)
        llm_tags, narrative_audit = await _run_narrative_auditor(
            req.note_id, req.content, bridge_nodes
        )
    else:
        default_beat    = _default_beat_from_community(req.note_id, macro_id, macro_labels)
        llm_tags        = [f"code/{default_beat}"]
        narrative_audit = None

    # Assemble final tag list
    # affect_tags: from Stage D (unconditional valence, e.g. ["affect/positive"])
    # llm_tags:    code/ beat from Narrative Auditor or community-label fallback
    tags = _assemble_tags(topic_tags, aspect_tags, affect_tags, llm_tags, limit=10)

    # Build community tier info
    tiers: list[CommunityTier] = []
    if macro_id is not None:
        tiers.append(CommunityTier(
            resolution=RESOLUTION_MACRO,
            label=macro_labels.get(macro_id, "Unknown"),
            community_id=macro_id,
        ))
    if micro_id is not None:
        tiers.append(CommunityTier(
            resolution=RESOLUTION_MICRO,
            label=micro_labels.get(micro_id, "Unknown"),
            community_id=micro_id,
        ))

    narrative = NarrativeMetadata(
        smart_relations=relations,
        tags=tags,
    )

    return AnalyzeResponse(
        note_id=req.note_id,
        metadata=narrative,
        community_id=macro_id if macro_id is not None else 0,
        community_tiers=tiers,
        bridge_detected=bridge_detected,
        narrative_audit=narrative_audit,
        narrative_act=narrative_act,
        structural_hole=structural_hole,
    )


@app.get("/graph/communities")
async def get_communities(resolution: float = 1.0):
    """Return community assignments at a single resolution.

    The resolution parameter γ tunes granularity:
      - γ > 1 → more, smaller communities (micro-clusters / Scenes)
      - γ < 1 → fewer, larger communities (macro-themes / Acts)
    """
    communities = _detect_communities(resolution)
    return {"resolution": resolution, "communities": communities}


@app.get("/graph/communities/multi")
async def get_multi_communities():
    """Return community assignments at both macro (γ=0.5) and micro (γ=2.0)."""
    macro, micro, macro_labels, micro_labels = _detect_multi_resolution()
    return {
        "macro": {
            "resolution": RESOLUTION_MACRO,
            "communities": macro,
            "labels": macro_labels,
        },
        "micro": {
            "resolution": RESOLUTION_MICRO,
            "communities": micro,
            "labels": micro_labels,
        },
    }


@app.get("/graph/node/{note_id}")
async def get_node(note_id: str):
    # C-2: Normalise path parameter through the same slug convention as AnalyzeRequest.
    note_id = _slugify(note_id)
    if note_id not in graph:
        raise HTTPException(status_code=404, detail="Node not found.")
    neighbors = {
        target: graph[note_id][target]
        for target in graph.successors(note_id)
    }
    return {
        "note_id": note_id,
        "metadata": dict(graph.nodes[note_id]),
        "outgoing_relations": neighbors,
    }


@app.post("/graph/ingest")
async def ingest_vault(notes: list[IngestItem]):
    """Bulk-ingest notes to build the vault graph before analysis.

    M-6: typed IngestItem model replaces unvalidated list[dict].
    This populates the graph with wiki-link edges so that Leiden
    partitions are meaningful from the first /analyze call.
    """
    for item in notes:
        _upsert_node(item.note_id, {})
        links = _extract_wikilinks(item.content)
        _upsert_wikilink_edges(item.note_id, links)

    _save_graph()

    # Re-fit BERTopic after bulk ingest — offloaded off the event loop.
    await asyncio.to_thread(_fit_bertopic_on_vault)

    return {
        "ingested": len(notes),
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "bertopic_ready": _bertopic_ready,
    }
