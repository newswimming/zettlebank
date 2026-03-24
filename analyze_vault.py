"""
analyze_vault.py — bulk-analyze all .md files from choracle-remote-v3
by calling POST /analyze on each note and saving results to analyze_results.json.

Usage:
    py analyze_vault.py
"""

import json
import re
import sys
import time
import urllib.request as urlreq
from pathlib import Path

# Windows cp1252 stdout can't encode γ (U+03B3) from Leiden tier labels
sys.stdout.reconfigure(encoding="utf-8")

# ── Config ────────────────────────────────────────────────────────────────────

VAULT   = Path(r"C:\Users\andrea\Documents\choracle-remote-v3")
SERVER  = "http://127.0.0.1:8000"
OUTPUT  = Path(__file__).parent / "analyze_results.json"
MAX_CHARS = 50_000   # server hard limit per note
TIMEOUT   = 120      # seconds per request (pipeline can be slow)

# ── Slug (mirrors server._slugify) ───────────────────────────────────────────

def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")

# ── Wait for server ───────────────────────────────────────────────────────────

def wait_for_server(retries: int = 30, delay: float = 2.0) -> bool:
    print("Waiting for server at", SERVER, "...", flush=True)
    for i in range(retries):
        try:
            with urlreq.urlopen(f"{SERVER}/health", timeout=3) as r:
                if r.status == 200:
                    print("Server is up.\n", flush=True)
                    return True
        except Exception:
            pass
        time.sleep(delay)
        print(f"  [{i+1}/{retries}] not ready yet...", flush=True)
    return False

# ── Gather notes ──────────────────────────────────────────────────────────────

md_files = sorted(
    p for p in VAULT.rglob("*.md")
    if ".obsidian" not in p.parts
)
print(f"Found {len(md_files)} notes in {VAULT}\n", flush=True)

notes = []
skipped = []

for path in md_files:
    slug = slugify(path.stem)
    if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9\-]{0,127}", slug):
        skipped.append((path.name, f"slug '{slug}' fails pattern"))
        continue
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        skipped.append((path.name, str(e)))
        continue
    notes.append({"note_id": slug, "content": content[:MAX_CHARS], "path": str(path)})

if skipped:
    print("Skipped (invalid slug or read error):")
    for name, reason in skipped:
        print(f"  {name}: {reason}")
    print()

# ── Wait for server ───────────────────────────────────────────────────────────

if not wait_for_server():
    sys.exit("Server not reachable at " + SERVER + " after retries. Is it running?")

# ── Analyze each note ─────────────────────────────────────────────────────────

results = []
errors  = []

for i, note in enumerate(notes, 1):
    note_id = note["note_id"]
    payload = json.dumps({"note_id": note_id, "content": note["content"]}).encode("utf-8")
    req = urlreq.Request(
        f"{SERVER}/analyze",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlreq.urlopen(req, timeout=TIMEOUT) as resp:
            result = json.loads(resp.read())
        tags           = result.get("metadata", {}).get("tags", [])
        relations      = result.get("metadata", {}).get("smart_relations", [])
        community_id   = result.get("community_id")
        narrative_act  = result.get("narrative_act", "?")
        bridge         = result.get("bridge_detected", False)
        constraint     = result.get("structural_hole", {}).get("constraint_score", "?")
        tiers          = result.get("community_tiers", [])
        tier_str       = " | ".join(
            f"γ={t['resolution']} [{t['label']}]" for t in tiers
        )
        print(
            f"[{i:>3}/{len(notes)}] {note_id}\n"
            f"         act={narrative_act}  community={community_id}  bridge={bridge}"
            f"  constraint={constraint:.3f}\n"
            f"         tags={tags}\n"
            f"         relations={len(relations)}  tiers: {tier_str or 'none'}",
            flush=True,
        )
        results.append(result)
    except Exception as e:
        print(f"[{i:>3}/{len(notes)}] ERROR {note_id}: {e}", flush=True)
        errors.append({"note_id": note_id, "error": str(e)})

# ── Save results ──────────────────────────────────────────────────────────────

output = {"results": results, "errors": errors}
OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")

print(f"\nDone. {len(results)} analyzed, {len(errors)} errors.")
print(f"Full results saved to: {OUTPUT}")
