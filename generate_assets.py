"""
generate_assets.py — Run /graph/generate-arc and consolidate all outputs
into the generated_assets/ folder.

Outputs:
  generated_assets/arc.txt            — the 8-sentence narrative arc
  generated_assets/cluster_ki.txt     — raw note excerpts fed to Ki LLM
  generated_assets/cluster_sho.txt    — raw note excerpts fed to Sho LLM
  generated_assets/cluster_ten.txt    — raw note excerpts fed to Ten LLM
  generated_assets/cluster_ketsu.txt  — raw note excerpts fed to Ketsu LLM
  generated_assets/arc_manifest.json  — full API response + metadata

Usage:
    python generate_assets.py
"""

import json
import re
import sys
import urllib.request as urlreq
from datetime import datetime, timezone
from pathlib import Path

SERVER      = "http://127.0.0.1:8000"
VAULT_NOTES = Path(__file__).parent / "choracle-remote-00" / "notes"
OUT_DIR     = Path(__file__).parent / "generated_assets"
CLUSTER_CAP = 12_000   # mirrors server _extract_cluster_text limit

sys.stdout.reconfigure(encoding="utf-8")

ACT_LABELS = {
    "ki":    "起  Ki  — Introduction",
    "sho":   "承  Shō — Development",
    "ten":   "転  Ten — Twist",
    "ketsu": "結  Ketsu — Resolution",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(path: str) -> dict:
    with urlreq.urlopen(f"{SERVER}{path}", timeout=10) as r:
        return json.loads(r.read())

def _post(path: str, body: dict) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = urlreq.Request(
        f"{SERVER}{path}", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlreq.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip()
    return text

def _build_cluster_text(note_ids: list[str]) -> str:
    parts, total = [], 0
    for nid in note_ids:
        note_path = VAULT_NOTES / f"{nid}.md"
        if not note_path.exists():
            continue
        try:
            raw = note_path.read_text(encoding="utf-8", errors="replace")
            excerpt = _strip_frontmatter(raw)[:1500].strip()
        except OSError:
            continue
        if not excerpt:
            continue
        block = f"Title: {nid}\nText: {excerpt}\n---"
        if total + len(block) > CLUSTER_CAP:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)

# ── 1. Health check ───────────────────────────────────────────────────────────

print("Checking server...", flush=True)
health = _get("/health")
print(f"  nodes={health['nodes']}  edges={health['edges']}  "
      f"ollama={health['ollama_alive']}\n", flush=True)

if not health["ollama_alive"]:
    sys.exit("Ollama is offline — cannot generate arc.")

# ── 2. Community membership ───────────────────────────────────────────────────

print("Fetching community map...", flush=True)
comm_data   = _get("/graph/communities/multi")
macro_map   = comm_data["macro"]["communities"]   # note_id -> comm_id

# Invert: comm_id -> [note_ids]
comm_notes: dict[int, list[str]] = {}
for nid, cid in macro_map.items():
    comm_notes.setdefault(cid, []).append(nid)

# ── 3. Call generate-arc ──────────────────────────────────────────────────────

print("Calling /graph/generate-arc (this may take ~30–90 seconds)...\n", flush=True)
arc = _post("/graph/generate-arc", {"locked_acts": []})

beats         = {a: arc[a] for a in ("ki", "sho", "ten", "ketsu")}
clusters_used = arc.get("clusters_used", {})

# ── 4. Print arc to console ───────────────────────────────────────────────────

print("=" * 72)
print("NARRATIVE ARC")
print("=" * 72)
for act in ("ki", "sho", "ten", "ketsu"):
    print(f"\n{ACT_LABELS[act]}")
    print("-" * 50)
    print(beats[act] or "(not generated)")
print("\n" + "=" * 72 + "\n")

# ── 5. Write generated_assets/ ───────────────────────────────────────────────

OUT_DIR.mkdir(exist_ok=True)
timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")

# arc.txt
arc_lines = [f"Generated: {timestamp}\n"]
for act in ("ki", "sho", "ten", "ketsu"):
    cids = clusters_used.get(act, [])
    arc_lines.append(f"{ACT_LABELS[act]}  [clusters: {cids}]")
    arc_lines.append("-" * 50)
    arc_lines.append(beats[act] or "(not generated)")
    arc_lines.append("")

(OUT_DIR / "arc.txt").write_text("\n".join(arc_lines), encoding="utf-8")
print(f"Wrote arc.txt", flush=True)

# cluster_<act>.txt
for act in ("ki", "sho", "ten", "ketsu"):
    cids = clusters_used.get(act, [])
    if not cids:
        continue
    note_ids = []
    for cid in cids:
        note_ids.extend(comm_notes.get(cid, []))
    cluster_text = _build_cluster_text(note_ids)
    out_file = OUT_DIR / f"cluster_{act}.txt"
    header = (
        f"Act:      {ACT_LABELS[act]}\n"
        f"Clusters: {cids}\n"
        f"Notes:    {len(note_ids)}\n"
        f"Generated:{timestamp}\n"
        + "=" * 72 + "\n\n"
    )
    out_file.write_text(header + cluster_text, encoding="utf-8")
    print(f"Wrote cluster_{act}.txt  ({len(note_ids)} notes, {len(cluster_text):,} chars)", flush=True)

# arc_manifest.json
manifest = {
    "generated": timestamp,
    "health":    health,
    "arc":       beats,
    "clusters_used": clusters_used,
    "cluster_note_counts": {
        act: len([n for cid in cids for n in comm_notes.get(cid, [])])
        for act, cids in clusters_used.items()
    },
}
(OUT_DIR / "arc_manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"Wrote arc_manifest.json\n", flush=True)
print(f"All assets saved to: {OUT_DIR}")
