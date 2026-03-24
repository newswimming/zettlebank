"""Self-audit: top-5 Ten-style contrasting notes against test_mock_note.md.

Verifies
--------
1. ``ShadowBox`` ingests all vault notes without a running server.
2. A NetworkX DiGraph is built from wiki-link extraction.
3. Burt's constraint is computed and stored in ChromaDB metadata.
4. ``rrf_merge`` satisfies the rank-combination invariants.
5. ``query_ten_contrasts`` returns ranked results with valid scoring fields.
6. RRF scores are monotonically decreasing in the returned list.
7. Top result has cosine distance >= 0.2 (not near-identical - it's a contrast).

Usage (from project root, venv activated)::

    venv/Scripts/python backend/audit_ten_contrast.py

Expected output: 5 ranked notes with note_id, rrf_score, chroma_rank,
nx_rank, cosine distance, Burt constraint, narrative act, and a snippet.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import networkx as nx

# ---------------------------------------------------------------------------
# Path bootstrap - make the project root importable as a package
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.shadowbox import (   # noqa: E402
    ShadowBox,
    HybridResult,
    rrf_merge,
    nx_contrast_ranking,
    TEN_CONSTRAINT_THRESHOLD,
    RRF_K,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("audit")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VAULT_DIR    = PROJECT_ROOT / "vault" / "choracle-remote-00"
NOTES_DIR    = VAULT_DIR / "notes"
AJSON_DIR    = VAULT_DIR / ".smart-env" / "multi"
MOCK_NOTE    = PROJECT_ROOT / "test_mock_note.md"
MOCK_NOTE_ID = "the-mask-ceremony"

#: Always rebuild the ChromaDB index for a clean audit.
FORCE_REINGEST = True

# ---------------------------------------------------------------------------
# Wiki-link graph builder
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]")


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def build_graph_from_vault(notes_dir: Path) -> nx.DiGraph:
    """Build a DiGraph from vault wiki-links.

    Mirrors ``server.py``'s ``_extract_wikilinks`` + ``_upsert_wikilink_edges``.
    Edge attributes use the full EdgeMatrix schema (PLAN_NETWORKX.md S3.2) so
    ``nx.constraint`` can read the ``confidence`` weight.
    """
    graph = nx.DiGraph()
    if not notes_dir.exists():
        logger.warning("build_graph_from_vault: %s not found", notes_dir)
        return graph

    for md in sorted(notes_dir.glob("*.md")):
        note_id = md.stem
        graph.add_node(note_id)
        content = md.read_text(encoding="utf-8", errors="replace")
        for raw_target in _WIKILINK_RE.findall(content):
            slug = _slugify(raw_target)
            if slug and slug != note_id:
                graph.add_edge(
                    note_id, slug,
                    relation_type="related",
                    narrative_act="ki",
                    confidence=0.5,
                    provenance="wikilink",
                    weight=0.5,
                )

    logger.info(
        "Graph: %d nodes, %d edges", graph.number_of_nodes(), graph.number_of_edges()
    )
    return graph


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        suffix = f"  - {detail}" if detail else ""
        print(f"  FAIL  {label}{suffix}")


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def main() -> None:
    print()
    print("=" * 62)
    print("  ZettleBank ShadowBox - Ten-Contrast Self-Audit")
    print("=" * 62)

    # - Step 1: Build vault graph -------------------
    print("\n[1] Building vault graph from wiki-links ...")
    graph = build_graph_from_vault(NOTES_DIR)
    check("Graph has >= 10 nodes",  graph.number_of_nodes() >= 10,
          f"got {graph.number_of_nodes()}")
    check("Graph has directed edges", graph.number_of_edges() > 0,
          f"got {graph.number_of_edges()}")

    # - Step 2: Initialise ShadowBox -----------------
    print("\n[2] Initialising ShadowBox (force_reingest=True) ...")
    sb = ShadowBox.from_vault(
        vault_dir=VAULT_DIR,
        graph=graph,
        force_reingest=FORCE_REINGEST,
    )
    count = sb._collection.count()
    print(f"    Collection count after ingest: {count}")
    check("Collection is non-empty",       count > 0,  f"got {count}")
    check("Collection has >= 10 documents", count >= 10, f"got {count}")

    # - Step 3: Verify ChromaDB metadata fields ------------
    print("\n[3] Verifying metadata fields on sampled documents ...")
    sample = sb._collection.get(limit=min(10, count), include=["metadatas"])
    metas = sample["metadatas"]

    required_fields = {
        "note_id", "narrative_beat", "narrative_act", "community_id",
        "tags", "constraint", "is_ten_candidate", "has_sc_embedding",
    }
    for field_name in sorted(required_fields):
        present = all(field_name in m for m in metas)
        check(f"All sampled docs have '{field_name}'", present)

    ten_candidates = [m for m in metas if m.get("is_ten_candidate")]
    print(f"    Ten candidates in sample (n={len(metas)}): {len(ten_candidates)}")

    constraint_values = [m.get("constraint", -1.0) for m in metas]
    valid_constraints = [c for c in constraint_values if c >= 0.0]
    check(
        "At least some nodes have computed constraint",
        len(valid_constraints) > 0,
        f"valid={len(valid_constraints)}/{len(metas)}",
    )

    # - Step 4: Unit-test rrf_merge ------------------
    print("\n[4] Unit-testing rrf_merge ...")

    # 4a - A note ranked 1 in both lists should beat a note ranked last in one
    la = ["alpha", "beta",    "gamma", "delta"]
    lb = ["gamma", "alpha",   "epsilon", "zeta"]
    scores = rrf_merge([la, lb], k=RRF_K)

    alpha_score   = scores.get("alpha",   0.0)   # rank 1 + rank 2
    gamma_score   = scores.get("gamma",   0.0)   # rank 3 + rank 1
    epsilon_score = scores.get("epsilon", 0.0)   # rank 3 in lb only
    delta_score   = scores.get("delta",   0.0)   # rank 4 in la only

    check(
        "alpha (r1+r2) > epsilon (r3 in one list only)",
        alpha_score > epsilon_score,
        f"alpha={alpha_score:.5f}  epsilon={epsilon_score:.5f}",
    )
    check(
        "gamma (r3+r1) > delta (r4 in one list only)",
        gamma_score > delta_score,
        f"gamma={gamma_score:.5f}  delta={delta_score:.5f}",
    )
    check(
        "All six source documents received a score",
        all(d in scores for d in ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]),
    )

    # 4b - Empty list must not break fusion
    scores_b = rrf_merge([[], ["x", "y"]], k=RRF_K)
    check("Empty ranked list does not break fusion", scores_b.get("x", 0) > 0)

    # 4c - Single list: rank 1 > rank 2 > rank 3
    scores_c = rrf_merge([["a", "b", "c"]], k=RRF_K)
    check(
        "Single list: rank-1 > rank-2 > rank-3",
        scores_c.get("a", 0) > scores_c.get("b", 0) > scores_c.get("c", 0),
        f"a={scores_c['a']:.5f}  b={scores_c['b']:.5f}  c={scores_c['c']:.5f}",
    )

    # 4d - Verify the exact RRF formula at k=60
    expected_rank1 = 1.0 / (RRF_K + 1)
    check(
        f"Rank-1 score == 1/(k+1) = 1/{RRF_K+1} ~ {expected_rank1:.5f}",
        abs(scores_c["a"] - expected_rank1) < 1e-9,
        f"got {scores_c['a']:.9f}",
    )

    # - Step 5: Unit-test nx_contrast_ranking -------------
    print("\n[5] Unit-testing nx_contrast_ranking ...")
    check("Graph has >= 3 nodes for constraint computation",
          graph.number_of_nodes() >= 3)

    vault_note_ids = list(graph.nodes())
    candidates = [n for n in vault_note_ids if n != MOCK_NOTE_ID]
    ranked = nx_contrast_ranking(
        MOCK_NOTE_ID, graph, candidates, query_community_id=-1
    )
    check("nx_contrast_ranking returns all candidates",
          set(ranked) == set(candidates),
          f"expected {len(candidates)}, got {len(ranked)}")
    check("Query note is excluded from ranking",
          MOCK_NOTE_ID not in ranked)
    check("nx_contrast_ranking is deterministic",
          ranked == nx_contrast_ranking(MOCK_NOTE_ID, graph, candidates))

    # - Step 6: Load mock note --------------------
    print("\n[6] Loading mock note ...")
    check("test_mock_note.md exists", MOCK_NOTE.exists(), str(MOCK_NOTE))
    mock_content = MOCK_NOTE.read_text(encoding="utf-8")
    check("Mock note is non-empty", len(mock_content.strip()) > 0)
    print(f"    Query: \"{mock_content.strip()[:80]}...\"")

    # - Step 7: query_ten_contrasts ------------------
    print(f"\n[7] query_ten_contrasts('{MOCK_NOTE_ID}', graph, n=5) ...")
    results: list[HybridResult] = sb.query_ten_contrasts(
        note_id=MOCK_NOTE_ID,
        note_content=mock_content,
        graph=graph,
        n=5,
    )

    check("Returns >= 1 result",             len(results) >= 1,
          f"got {len(results)}")
    check("Returns <= 5 results",            len(results) <= 5)
    check("No result is the query note",
          all(r.note_id != MOCK_NOTE_ID for r in results))
    check("All RRF scores are positive",
          all(r.rrf_score > 0 for r in results),
          str([round(r.rrf_score, 6) for r in results]))
    check("All distances are non-negative",
          all(r.distance >= 0 for r in results),
          str([round(r.distance, 4) for r in results]))
    check("RRF scores are in descending order",
          results == sorted(results, key=lambda r: r.rrf_score, reverse=True),
          str([round(r.rrf_score, 6) for r in results]))
    check("All chroma_rank values are positive integers",
          all(isinstance(r.chroma_rank, int) and r.chroma_rank > 0
              for r in results))
    check("All nx_rank values are positive integers",
          all(isinstance(r.nx_rank, int) and r.nx_rank > 0
              for r in results))

    # - Step 8: Cross-validation -------------------
    print("\n[8] Cross-validation ...")

    if results:
        top = results[0]
        check(
            "Top result has distance >= 0.2 (not near-identical)",
            top.distance >= 0.2,
            f"distance={top.distance:.4f}",
        )
        check(
            "Top result note_id is non-empty string",
            isinstance(top.note_id, str) and len(top.note_id) > 0,
        )

    if len(results) >= 2:
        score_diffs = [
            results[i].rrf_score - results[i + 1].rrf_score
            for i in range(len(results) - 1)
        ]
        check(
            "RRF scores are strictly non-increasing",
            all(d >= 0 for d in score_diffs),
            str([round(d, 8) for d in score_diffs]),
        )

    # Verify that RRF actually integrates both signals:
    # a note with a high chroma_rank AND high nx_rank should score higher than
    # a note appearing in only one list.
    if len(results) >= 2:
        # Find a note that appears in both lists vs one that appears in just one
        dual_presence = [
            r for r in results
            if r.chroma_rank < 9999 and r.nx_rank < 9999
        ]
        single_presence = [
            r for r in results
            if r.chroma_rank == 9999 or r.nx_rank == 9999
        ]
        if dual_presence and single_presence:
            best_dual   = max(dual_presence,   key=lambda r: r.rrf_score)
            best_single = max(single_presence, key=lambda r: r.rrf_score)
            check(
                "Dual-list note scores >= single-list-only note",
                best_dual.rrf_score >= best_single.rrf_score,
                f"dual={best_dual.rrf_score:.5f}  single={best_single.rrf_score:.5f}",
            )
        else:
            print("    INFO  All results appear in both lists (full dual coverage).")

    # - Step 9: Print ranked results -----------------
    print()
    print("-" * 62)
    print(f"  Top-{len(results)} Ten-style contrasts for: '{MOCK_NOTE_ID}'")
    print("-" * 62)

    for i, r in enumerate(results, start=1):
        ten_flag = " [Ten-candidate]" if r.constraint >= 0 and r.constraint < TEN_CONSTRAINT_THRESHOLD else ""
        print()
        print(f"  #{i}  {r.note_id}{ten_flag}")
        print(f"       RRF score   : {r.rrf_score:.6f}")
        print(f"       Chroma rank : {r.chroma_rank:>3d}   distance  = {r.distance:.4f}")
        print(f"       NX rank     : {r.nx_rank:>3d}   constraint= {r.constraint:.4f}   act={r.narrative_act}")
        print(f"       Snippet     : {r.snippet}")

    # - Summary ----------------------------
    print()
    print("=" * 62)
    print(f"  {_passed} passed   {_failed} failed")
    print("=" * 62)
    print()

    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
