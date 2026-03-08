"""
ZettleBank end-to-end test workflow.

1. Start uvicorn in background.
2. Bulk-ingest real vault notes to build a meaningful graph.
3. POST a note to /analyze.
4. Validate: multi-resolution Leiden, nested Obsidian-path tags,
   graph persistence, wiki-link edge extraction, Data Contract.
"""

import subprocess
import sys
import time
import json
import signal
import socket
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Controlled vocabulary — copied verbatim from architecture.md
# ---------------------------------------------------------------------------

ALLOWED_RELATION_TYPES = {
    "contradicts",
    "supports",
    "potential_to",
    "kinetic_to",
    "motivates",
    "hinders",
    "related",  # ADR-002 fallback
}

BASE_URL = "http://localhost:8000"
PROJECT_DIR = Path(__file__).resolve().parent.parent   # project root
VAULT_DIR   = PROJECT_DIR / "vault" / "choracle-remote-00" / "notes"
MOCK_NOTE   = PROJECT_DIR / "test_mock_note.md"
GRAPH_PATH  = PROJECT_DIR / "vault_graph.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


def port_in_use(port: int) -> bool:
    """Return True if something is already listening on *port*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for_server(url: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=3)
            if r.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, OSError):
            pass
        time.sleep(1)
    return False


def _slugify(name: str) -> str:
    """Normalise a filename stem to a valid note_id slug (lowercase, hyphens only)."""
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "note"


def load_vault_notes() -> list[dict]:
    """Read every .md file from the vault notes directory."""
    notes = []
    if not VAULT_DIR.exists():
        return notes
    for md in VAULT_DIR.glob("*.md"):
        notes.append({
            "note_id": _slugify(md.stem),
            "content": md.read_text(encoding="utf-8", errors="replace"),
        })
    return notes


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def main():
    global passed, failed

    # Clean stale graph file so we start fresh
    if GRAPH_PATH.exists():
        GRAPH_PATH.unlink()

    # ── Step 1: Start uvicorn ─────────────────────────────────────────
    print("\n[Step 1] Starting uvicorn server...")
    if port_in_use(8000):
        print("  ERROR  Port 8000 is already in use — kill the existing server "
              "before running the test.")
        sys.exit(1)
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(PROJECT_DIR),   # server.py lives in project root
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not wait_for_server(BASE_URL):
            _, stderr = server_proc.communicate(timeout=3)
            print("  FAIL  Server did not start within timeout.")
            print(f"  stderr:\n{stderr.decode()}")
            server_proc.terminate()
            sys.exit(1)
        print("  Server is up.\n")

        # ── Step 2: Bulk-ingest vault notes ───────────────────────────
        print("[Step 2] Ingesting vault notes...")
        vault_notes = load_vault_notes()
        print(f"  Found {len(vault_notes)} notes in vault.")

        r_ingest = httpx.post(
            f"{BASE_URL}/graph/ingest",
            json=vault_notes,
            timeout=15,
        )
        check("POST /graph/ingest returns 200", r_ingest.status_code == 200)
        ingest_data = r_ingest.json()
        print(f"  Graph: {ingest_data.get('nodes')} nodes, "
              f"{ingest_data.get('edges')} edges\n")

        check("Graph has nodes from vault",
              ingest_data.get("nodes", 0) >= len(vault_notes))
        check("Graph has wiki-link edges",
              ingest_data.get("edges", 0) > 0)
        check("BERTopic is ready after ingest",
              ingest_data.get("bertopic_ready") is True,
              f"got {ingest_data.get('bertopic_ready')}")

        # ── Step 3: POST a note to /analyze ───────────────────────────
        print("[Step 3] Analyzing mock note...")
        content = MOCK_NOTE.read_text(encoding="utf-8")
        payload = {
            "note_id": "the-mask-ceremony",
            "content": content,
        }
        r = httpx.post(f"{BASE_URL}/analyze", json=payload, timeout=90)
        check("HTTP status is 200", r.status_code == 200, f"got {r.status_code}")

        data = r.json()
        print(f"\n  Response:\n{json.dumps(data, indent=2)}\n")

        # ── Step 4: Validate Data Contract ────────────────────────────
        print("[Step 4] Validating Data Contract...\n")

        # 4a. Top-level shape
        check("Has 'note_id'",          "note_id"          in data)
        check("No 'fields' key (removed)", "fields"        not in data)
        check("Has 'metadata'",         "metadata"         in data)
        check("Has 'community_id'",     "community_id"     in data)
        check("Has 'community_tiers'",  "community_tiers"  in data)
        check("Has 'bridge_detected'",  "bridge_detected"  in data)
        check("Has 'narrative_audit'",  "narrative_audit"  in data)
        check("bridge_detected is bool",
              isinstance(data.get("bridge_detected"), bool))
        check("note_id matches", data.get("note_id") == "the-mask-ceremony")

        # 4b. NarrativeMetadata shape
        meta = data.get("metadata", {})
        TEMPLATE_KEYS = {"aliases", "description", "tags",
                         "smart_relations", "source", "citationID"}
        check("metadata has all template keys",
              TEMPLATE_KEYS.issubset(meta.keys()), f"got {set(meta.keys())}")

        # 4d. Tags use new pipeline prefixes
        tags = meta.get("tags", [])
        check("metadata.tags is a list", isinstance(tags, list))
        check("Tags are non-empty", len(tags) > 0, f"got {len(tags)} tags")

        all_path_syntax = all("/" in t for t in tags)
        check("All tags use Obsidian path syntax (contain '/')",
              all_path_syntax, f"tags: {tags}")

        ALLOWED_PREFIXES = {"topic/", "aspect/", "affect/", "code/"}
        all_allowed = all(
            any(t.startswith(p) for p in ALLOWED_PREFIXES)
            for t in tags
        )
        check("All tags use allowed prefixes (topic/, aspect/, affect/, code/)",
              all_allowed, f"tags: {tags}")

        has_topic  = any(t.startswith("topic/")  for t in tags)
        has_affect = any(t.startswith("affect/") for t in tags)
        has_code   = any(t.startswith("code/")   for t in tags)
        check("Has topic/ tag (BERTopic)", has_topic, f"tags: {tags}")
        check("Has code/ tag (16-beat slug)", has_code, f"tags: {tags}")

        # affect/ only emitted when Narrative Auditor fires (bridge_detected=True)
        if data.get("bridge_detected"):
            check("Has affect/ tag (Narrative Auditor, bridge detected)",
                  has_affect, f"tags: {tags}")
            audit = data.get("narrative_audit") or {}
            check("narrative_audit.beat_position is set",
                  isinstance(audit.get("beat_position"), str)
                  and len(audit.get("beat_position", "")) > 0)
            check("narrative_audit.narrative_summary is set",
                  isinstance(audit.get("narrative_summary"), str))
        else:
            print("  INFO  bridge_detected=False: Narrative Auditor not triggered; "
                  "affect/ tag omitted (expected)")
            check("narrative_audit is null when no bridge",
                  data.get("narrative_audit") is None)

        # aspect/ is a soft check — spaCy may find no entities in short notes
        has_aspect = any(t.startswith("aspect/") for t in tags)
        if has_aspect:
            check("Has aspect/ tag (spaCy NER)", True)
        else:
            print("  INFO  No aspect/ tags found (spaCy found no entities — OK for short notes)")

        # 4e. Community tiers (multi-resolution)
        tiers = data.get("community_tiers", [])
        check("community_tiers has 2 entries (macro + micro)",
              len(tiers) == 2, f"got {len(tiers)}")

        if len(tiers) == 2:
            resolutions = sorted([t["resolution"] for t in tiers])
            check("Tier resolutions are 0.5 and 2.0",
                  resolutions == [0.5, 2.0], f"got {resolutions}")

            for tier in tiers:
                check(f"Tier y={tier['resolution']} has label",
                      isinstance(tier.get("label"), str) and len(tier["label"]) > 0)
                check(f"Tier y={tier['resolution']} has community_id",
                      isinstance(tier.get("community_id"), int))

        # 4f. Relation types (controlled vocabulary)
        relations = meta.get("smart_relations", [])
        all_valid = True
        for rel in relations:
            if rel.get("type") not in ALLOWED_RELATION_TYPES:
                all_valid = False
                check(f"Relation type '{rel.get('type')}' is allowed",
                      False, f"not in {ALLOWED_RELATION_TYPES}")
        check("All relation types use controlled vocabulary", all_valid)

        # ── Step 5: Graph persistence ─────────────────────────────────
        print("\n[Step 5] Verifying graph persistence...")
        check("vault_graph.json exists on disk", GRAPH_PATH.exists())

        if GRAPH_PATH.exists():
            graph_data = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
            check("Persisted graph has nodes",
                  len(graph_data.get("nodes", [])) > 0)
            # NetworkX 3.4+ saves edges under "edges"; older versions used "links"
            edges = graph_data.get("links") or graph_data.get("edges", [])
            check("Persisted graph has edges", len(edges) > 0)

        # ── Step 6: Verify /graph/communities/multi ───────────────────
        print("\n[Step 6] Verifying multi-resolution endpoint...")
        r_multi = httpx.get(f"{BASE_URL}/graph/communities/multi", timeout=10)
        check("GET /graph/communities/multi returns 200",
              r_multi.status_code == 200)

        multi = r_multi.json()
        check("Has 'macro' tier", "macro" in multi)
        check("Has 'micro' tier", "micro" in multi)

        if "macro" in multi and "micro" in multi:
            check("Macro resolution is 0.5",
                  multi["macro"]["resolution"] == 0.5)
            check("Micro resolution is 2.0",
                  multi["micro"]["resolution"] == 2.0)
            check("Macro has labels dict",
                  isinstance(multi["macro"].get("labels"), dict))
            check("Micro has labels dict",
                  isinstance(multi["micro"].get("labels"), dict))

            n_macro = len(set(multi["macro"]["communities"].values()))
            n_micro = len(set(multi["micro"]["communities"].values()))
            print(f"\n  Macro communities (y=0.5): {n_macro}")
            print(f"  Micro communities (y=2.0): {n_micro}")
            check("Micro produces >= macro communities",
                  n_micro >= n_macro,
                  f"micro={n_micro}, macro={n_macro}")

        # ── Summary ──────────────────────────────────────────────────
        print(f"\n{'=' * 50}")
        print(f"  Results: {passed} passed, {failed} failed")
        print(f"{'=' * 50}\n")

    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)
        print("  Server stopped.")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
