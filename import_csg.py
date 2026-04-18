#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_csg.py — CLI to import a Character-Social-Graph pipeline run
into the ZettleBank vault graph.

Reads CSG output (SQLite + JSONL) and POSTs to the server's
POST /graph/ingest-character-graph endpoint.

Usage:
    python import_csg.py \\
        --db path/to/out/graph.db \\
        --jsonl path/to/out/jsonl/extractions.jsonl \\
        [--server http://127.0.0.1:8000] \\
        [--overwrite]

The server must be running before this script is invoked.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


# ---------------------------------------------------------------------------
# SQLite readers
# ---------------------------------------------------------------------------

def _read_characters(conn: sqlite3.Connection) -> list[dict]:
    """Read all characters and their aliases from the CSG SQLite database."""
    rows = conn.execute(
        """
        SELECT p.canon_name,
               GROUP_CONCAT(a.alias, '|||') AS aliases_concat
        FROM persons p
        LEFT JOIN aliases a ON a.person_id = p.id
        GROUP BY p.id, p.canon_name
        """
    ).fetchall()

    characters: list[dict] = []
    for row in rows:
        canon_name = row["canon_name"]
        aliases_concat = row["aliases_concat"]
        if aliases_concat:
            aliases = [a for a in aliases_concat.split("|||") if a.strip()]
        else:
            aliases = []
        characters.append({"canon_name": canon_name, "aliases": aliases})
    return characters


def _read_relations(conn: sqlite3.Connection) -> list[dict]:
    """Read all directed social relations from the CSG SQLite database."""
    rows = conn.execute(
        """
        SELECT p1.canon_name AS src,
               p2.canon_name AS dst,
               r.rel_type,
               r.scene_id,
               COALESCE(r.evidence, '') AS evidence,
               r.confidence
        FROM relations r
        JOIN persons p1 ON r.src_id = p1.id
        JOIN persons p2 ON r.dst_id = p2.id
        """
    ).fetchall()

    return [
        {
            "src":        row["src"],
            "dst":        row["dst"],
            "rel_type":   row["rel_type"],
            "scene_id":   row["scene_id"],
            "evidence":   row["evidence"],
            "confidence": row["confidence"],
        }
        for row in rows
    ]


def _read_interactions(conn: sqlite3.Connection) -> list[dict]:
    """Read all directed interaction instances from the CSG SQLite database.

    Note: the column is named 'power' in graph_store.py's schema.
    It is aliased to 'power_dynamics' in the output dict to match
    the CSGInteraction model on the server side.
    """
    rows = conn.execute(
        """
        SELECT p1.canon_name AS src,
               p2.canon_name AS dst,
               i.i_type,
               COALESCE(i.scene_id, '') AS scene_id,
               COALESCE(i.sentiment, 'neutral') AS sentiment,
               COALESCE(i.power, 'unclear') AS power_dynamics,
               i.confidence
        FROM interactions i
        JOIN persons p1 ON i.src_id = p1.id
        JOIN persons p2 ON i.dst_id = p2.id
        """
    ).fetchall()

    return [
        {
            "src":            row["src"],
            "dst":            row["dst"],
            "i_type":         row["i_type"],
            "scene_id":       row["scene_id"],
            "sentiment":      row["sentiment"],
            "power_dynamics": row["power_dynamics"],
            "confidence":     row["confidence"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------

def _read_jsonl(
    jsonl_path: str,
) -> tuple[list[dict], int, dict[str, str], dict[str, str], list[str]]:
    """Parse CSG extractions.jsonl and return derived metadata.

    Returns:
        turning_points       — list of {scene_id, who, description} dicts
        total_scenes         — number of lines successfully parsed
        first_appearances    — canon_name → first scene_id where character appeared
        descriptions         — canon_name → description string (first non-empty wins)
        place_time_scene_ids — scene IDs where scene_summary.where or .when was set
    """
    turning_points: list[dict] = []
    total_scenes: int = 0
    first_appearances: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    place_time_scene_ids: list[str] = []

    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue

            total_scenes += 1

            # Determine scene_id for this line
            meta = result.get("meta") or {}
            scene_id: str = meta.get("scene_id") or f"SCENE_{i:03d}"

            # Collect turning points from scene_summary
            scene_summary = result.get("scene_summary") or {}

            # Collect scene IDs with explicit place or time for mirror/locus detection
            if scene_summary.get("where") or scene_summary.get("when"):
                place_time_scene_ids.append(scene_id)

            who: list[str] = scene_summary.get("who") or []
            for tp_raw in scene_summary.get("turning_points") or []:
                if tp_raw and isinstance(tp_raw, str) and tp_raw.strip():
                    turning_points.append({
                        "scene_id":    scene_id,
                        "who":         who,
                        "description": tp_raw.strip(),
                    })

            # Record first appearances and descriptions
            for ch in result.get("characters") or []:
                canon_name = ch.get("canon_name")
                if not canon_name:
                    continue
                if canon_name not in first_appearances:
                    first_appearances[canon_name] = scene_id
                if canon_name not in descriptions:
                    desc = ch.get("description")
                    if desc and isinstance(desc, str) and desc.strip():
                        descriptions[canon_name] = desc.strip()

    return turning_points, total_scenes, first_appearances, descriptions, place_time_scene_ids


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict) -> dict:
    """POST a JSON payload to url and return the parsed response dict.

    Raises SystemExit(1) on any non-2xx HTTP status.
    Uses only stdlib urllib — no third-party requests package.
    """
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        print(f"[import_csg] HTTP {exc.code} from {url}")
        print(raw[:500])
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Import a Character-Social-Graph pipeline run into ZettleBank."
    )
    ap.add_argument("--db",     required=True, help="Path to CSG SQLite output (out/graph.db)")
    ap.add_argument("--jsonl",  required=True, help="Path to CSG extractions JSONL (out/jsonl/extractions.jsonl)")
    ap.add_argument("--server", default="http://127.0.0.1:8000", help="ZettleBank server base URL")
    ap.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing .md files in VAULT_NOTES_DIR")
    args = ap.parse_args()

    # ── Step 1: Read SQLite ──────────────────────────────────────────────────
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    characters    = _read_characters(conn)
    relations     = _read_relations(conn)
    interactions  = _read_interactions(conn)
    conn.close()

    # ── Step 2: Read JSONL ───────────────────────────────────────────────────
    turning_points, total_scenes, first_appearances, descriptions, place_time_scene_ids = _read_jsonl(args.jsonl)

    # ── Step 3: Merge JSONL metadata into characters ─────────────────────────
    for char in characters:
        name = char["canon_name"]
        char["first_appearance_scene"] = first_appearances.get(name)
        char["description"] = descriptions.get(name)

    # ── Step 4: Build request payload ────────────────────────────────────────
    # Drop i_type from each interaction — CSGInteraction has no i_type field.
    interactions_payload = [
        {k: v for k, v in iact.items() if k != "i_type"}
        for iact in interactions
    ]

    payload = {
        "total_scenes":             total_scenes,
        "characters":               characters,
        "relations":                relations,
        "interactions":             interactions_payload,
        "turning_points":           turning_points,
        "place_time_scene_ids":     place_time_scene_ids,
        "overwrite_existing_files": args.overwrite,
    }

    # ── Step 5: Report and POST ───────────────────────────────────────────────
    url = args.server.rstrip("/") + "/graph/ingest-character-graph"
    print(f"POSTing to {url} ...")
    print(f"  characters:   {len(characters)}")
    print(f"  relations:    {len(relations)}")
    print(f"  interactions: {len(interactions)}")
    print(f"  total_scenes: {total_scenes}")
    print(f"  place_time_scenes: {len(place_time_scene_ids)}")

    # ── Step 6: Display response ──────────────────────────────────────────────
    result = _post_json(url, payload)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
