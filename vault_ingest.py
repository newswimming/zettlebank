#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vault_ingest.py — Ingest vault character .md files into CSG SQLite and ZettleBank.

Reads vault character notes from choracle-remote-00, inserts them into the
Character-Social-Graph SQLite database so they participate alongside screenplay
characters, then POSTs to the ZettleBank /graph/ingest-character-graph endpoint
so they receive archetype assignments and appear in arc generation.

Usage:
    python vault_ingest.py \\
        [--vault-dir choracle-remote-00/dayfly-angel-island/characters] \\
        [--csg-db ../Character-social-graph/out/graph.db] \\
        [--server http://127.0.0.1:8000] \\
        [--overwrite]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


VAULT_SCENE_ID = "VAULT_SCENE_000"

# Relations derived from vault .md content — edit to add more as the vault grows
_VAULT_RELATIONS: list[dict] = [
    {
        "src": "Motome Kimura",
        "dst": "Yasuda Kitano",
        "rel_type": "ROMANTIC",
        "scene_id": VAULT_SCENE_ID,
        "evidence": "She emigrated to California in 1917 and married Yasuda Kitano upon her arrival",
        "confidence": 0.95,
    },
    {
        "src": "Yasuda Kitano",
        "dst": "Motome Kimura",
        "rel_type": "ROMANTIC",
        "scene_id": VAULT_SCENE_ID,
        "evidence": "Yasuda was 38-years-old when he married 20-year-old Motome Kimura",
        "confidence": 0.95,
    },
]

# Turning points mark characters as narrative pivots (boosts Ten-act score)
_VAULT_TURNING_POINTS: list[dict] = [
    {
        "scene_id": VAULT_SCENE_ID,
        "who": ["Motome Kimura", "Yasuda Kitano"],
        "description": (
            "Motome Kimura emigrates to California and marries Yasuda Kitano "
            "through a fraudulent picture-bride registry"
        ),
    }
]


# ---------------------------------------------------------------------------
# Frontmatter parser (stdlib only)
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter block from markdown. Returns (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 3:].strip()

    fm: dict = {}
    current_key: str | None = None
    list_items: list[str] = []
    in_list = False

    for line in fm_block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and in_list:
            list_items.append(stripped[2:].strip().strip('"'))
        elif ":" in stripped and not stripped.startswith("-"):
            if in_list and current_key is not None:
                fm[current_key] = list(list_items)
                list_items = []
            parts = stripped.split(":", 1)
            current_key = parts[0].strip()
            val = parts[1].strip()
            if val == "[]":
                in_list = False
                fm[current_key] = []
            elif val == "":
                in_list = True
                fm[current_key] = None
            else:
                in_list = False
                fm[current_key] = val.strip('"')

    if in_list and current_key is not None:
        fm[current_key] = list(list_items)

    return fm, body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Lowercase, spaces/underscores → hyphens, strip non-alnum."""
    s = text.lower().strip()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s


def _canon_from_stem(stem: str) -> str:
    """'motome-kimura' → 'Motome Kimura'."""
    return " ".join(w.capitalize() for w in stem.replace("-", " ").split())


def _aliases_from_tags(tags: list[str], note_id: str) -> list[str]:
    """Extract alias display names from aspect/character/* tags, excluding own ID."""
    aliases: list[str] = []
    for tag in tags:
        if tag.startswith("aspect/character/"):
            alias_slug = tag.split("/", 2)[2]
            if alias_slug and alias_slug != note_id:
                alias_name = _canon_from_stem(alias_slug)
                if alias_name not in aliases:
                    aliases.append(alias_name)
    return aliases


# ---------------------------------------------------------------------------
# Vault reader
# ---------------------------------------------------------------------------

def _read_vault_characters(vault_dir: Path) -> list[dict]:
    """Read all .md files in vault_dir, return list of character dicts."""
    characters: list[dict] = []
    for md_file in sorted(vault_dir.glob("*.md")):
        canon_name = _canon_from_stem(md_file.stem)
        text = md_file.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)

        tags: list[str] = fm.get("tags") or []
        note_id = _slug(canon_name)
        aliases = _aliases_from_tags(tags, note_id)

        # Use first non-empty body line as description
        desc_lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        description = desc_lines[0] if desc_lines else None

        has_place = any(t.startswith("aspect/place/") for t in tags)

        characters.append({
            "canon_name": canon_name,
            "aliases": aliases,
            "description": description,
            "first_appearance_scene": VAULT_SCENE_ID,
            "has_place_tag": has_place,
        })
        print(f"  vault char: {canon_name} | aliases={aliases} | place_tag={has_place}")

    return characters


# ---------------------------------------------------------------------------
# CSG SQLite insertion
# ---------------------------------------------------------------------------

_CSG_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canon_name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    alias TEXT,
    UNIQUE(person_id, alias),
    FOREIGN KEY(person_id) REFERENCES persons(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id INTEGER NOT NULL,
    dst_id INTEGER NOT NULL,
    rel_type TEXT NOT NULL,
    scene_id TEXT,
    evidence TEXT,
    confidence REAL,
    weight REAL DEFAULT 0,
    UNIQUE(src_id, dst_id, rel_type, scene_id, evidence),
    FOREIGN KEY(src_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY(dst_id) REFERENCES persons(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id INTEGER NOT NULL,
    dst_id INTEGER NOT NULL,
    i_type TEXT NOT NULL,
    scene_id TEXT,
    evidence TEXT,
    sentiment TEXT,
    power TEXT,
    confidence REAL,
    weight REAL DEFAULT 0,
    FOREIGN KEY(src_id) REFERENCES persons(id) ON DELETE CASCADE,
    FOREIGN KEY(dst_id) REFERENCES persons(id) ON DELETE CASCADE
);
"""


def _get_or_create_person(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO persons(canon_name) VALUES (?)", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM persons WHERE canon_name=?", (name,)).fetchone()
    return row[0]


def _insert_into_csg(
    csg_db: Path,
    characters: list[dict],
    relations: list[dict],
) -> None:
    """Upsert vault characters and relations into the CSG SQLite database."""
    csg_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(csg_db))
    conn.executescript(_CSG_SCHEMA)
    conn.commit()

    chars_written = 0
    for char in characters:
        _get_or_create_person(conn, char["canon_name"])
        for alias in char.get("aliases") or []:
            if alias:
                pid = _get_or_create_person(conn, char["canon_name"])
                conn.execute(
                    "INSERT OR IGNORE INTO aliases(person_id, alias) VALUES (?, ?)",
                    (pid, alias),
                )
        conn.commit()
        chars_written += 1

    rels_written = 0
    for rel in relations:
        sid = _get_or_create_person(conn, rel["src"])
        did = _get_or_create_person(conn, rel["dst"])
        conn.execute(
            """
            INSERT OR IGNORE INTO relations
                (src_id, dst_id, rel_type, scene_id, evidence, confidence, weight)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, did, rel["rel_type"], rel["scene_id"],
             rel["evidence"], rel["confidence"], rel["confidence"]),
        )
        conn.commit()
        rels_written += 1

    conn.close()
    print(f"  CSG SQLite: {chars_written} chars, {rels_written} relations -> {csg_db}")


# ---------------------------------------------------------------------------
# ZettleBank POST
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        print(f"[vault_ingest] HTTP {exc.code} from {url}")
        print(raw[:500])
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ingest vault character .md files into CSG SQLite and ZettleBank."
    )
    ap.add_argument(
        "--vault-dir",
        default="choracle-remote-00/dayfly-angel-island/characters",
        help="Directory containing vault character .md files",
    )
    ap.add_argument(
        "--csg-db",
        default="../Character-social-graph/out/graph.db",
        help="Path to the CSG SQLite database (graph.db)",
    )
    ap.add_argument(
        "--server",
        default="http://127.0.0.1:8000",
        help="ZettleBank server base URL",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .md stubs in VAULT_NOTES_DIR",
    )
    args = ap.parse_args()

    vault_dir = Path(args.vault_dir)
    csg_db = Path(args.csg_db)

    if not vault_dir.is_dir():
        print(f"[vault_ingest] vault-dir not found: {vault_dir}")
        raise SystemExit(1)

    print(f"[vault_ingest] Reading vault characters from: {vault_dir}")
    characters = _read_vault_characters(vault_dir)
    if not characters:
        print("[vault_ingest] No .md files found — nothing to do.")
        raise SystemExit(0)

    # ── Step 1: Insert into CSG SQLite ──────────────────────────────────────
    print(f"\n[vault_ingest] Writing to CSG SQLite: {csg_db}")
    _insert_into_csg(csg_db, characters, _VAULT_RELATIONS)

    # ── Step 2: Build ZettleBank payload ────────────────────────────────────
    # place_time_scene_ids: include VAULT_SCENE_ID if any character has a place tag
    place_time_scene_ids: list[str] = (
        [VAULT_SCENE_ID] if any(c["has_place_tag"] for c in characters) else []
    )

    zb_chars = [
        {
            "canon_name": c["canon_name"],
            "aliases": c["aliases"],
            "description": c["description"],
            "first_appearance_scene": c["first_appearance_scene"],
        }
        for c in characters
    ]

    payload = {
        "total_scenes": 1,
        "characters": zb_chars,
        "relations": _VAULT_RELATIONS,
        "interactions": [],
        "turning_points": _VAULT_TURNING_POINTS,
        "place_time_scene_ids": place_time_scene_ids,
        "overwrite_existing_files": args.overwrite,
    }

    # ── Step 3: POST to ZettleBank ───────────────────────────────────────────
    url = args.server.rstrip("/") + "/graph/ingest-character-graph"
    print(f"\n[vault_ingest] POSTing to {url}")
    print(f"  characters:        {len(zb_chars)}")
    print(f"  relations:         {len(_VAULT_RELATIONS)}")
    print(f"  turning_points:    {len(_VAULT_TURNING_POINTS)}")
    print(f"  place_time_scenes: {place_time_scene_ids}")

    result = _post_json(url, payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
