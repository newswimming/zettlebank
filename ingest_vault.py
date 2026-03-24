"""
ingest_vault.py — bulk-ingest all .md files from choracle-remote-01
into the ZettleBank server at http://127.0.0.1:8000/graph/ingest.

Usage:
    py ingest_vault.py
"""

import json
import re
import sys
from pathlib import Path

try:
    import urllib.request as urlreq
except ImportError:
    sys.exit("stdlib urllib not available — this should never happen.")

# ── Config ────────────────────────────────────────────────────────────────────

VAULT   = Path(r"C:\Users\andrea\Downloads\zettlebank-4.1.1\zettlebank-4.1.1\choracle-remote-00")
SERVER  = "http://127.0.0.1:8000"
BATCH   = 20          # notes per POST (keep requests manageable)
MAX_CHARS = 50_000    # server hard limit per note

# ── Slug (mirrors server._slugify) ───────────────────────────────────────────

def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")

# ── Gather notes ─────────────────────────────────────────────────────────────

md_files = sorted(VAULT.rglob("*.md"))
print(f"Found {len(md_files)} .md files in {VAULT}\n")

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
    notes.append({"note_id": slug, "content": content[:MAX_CHARS]})

if skipped:
    print("Skipped (invalid slug or read error):")
    for name, reason in skipped:
        print(f"  {name}: {reason}")
    print()

# ── POST in batches ───────────────────────────────────────────────────────────

total_ingested = 0

for i in range(0, len(notes), BATCH):
    batch = notes[i : i + BATCH]
    payload = json.dumps(batch).encode("utf-8")

    req = urlreq.Request(
        f"{SERVER}/graph/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlreq.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        total_ingested += result.get("ingested", len(batch))
        print(
            f"Batch {i // BATCH + 1:>3}: ingested {result.get('ingested'):>3} notes "
            f"| graph now {result.get('nodes'):>4} nodes / {result.get('edges'):>4} edges"
            + (" | BERTopic ready" if result.get("bertopic_ready") else "")
        )
    except Exception as e:
        print(f"Batch {i // BATCH + 1:>3}: ERROR — {e}")
        sys.exit(1)

print(f"\nDone. {total_ingested} notes ingested total.")
