# -*- coding: utf-8 -*-
"""
graph_store.py — Lightweight SQLite graph store for screenplay knowledge graphs
"""

from __future__ import annotations
import os
import sqlite3
from typing import Dict, Any, List, Tuple

SCHEMA_SQL = """
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

class GraphStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        dirpath = os.path.dirname(db_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    # -------- persons & aliases --------
    def _get_or_create_person(self, name: str) -> int:
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO persons(canon_name) VALUES (?)", (name,))
        self.conn.commit()
        cur.execute("SELECT id FROM persons WHERE canon_name=?", (name,))
        row = cur.fetchone()
        return row[0]

    def add_aliases(self, canon_name: str, aliases: List[str]):
        pid = self._get_or_create_person(canon_name)
        for a in aliases or []:
            if not a:
                continue
            self.conn.execute(
                "INSERT OR IGNORE INTO aliases(person_id, alias) VALUES (?, ?)",
                (pid, a)
            )
        self.conn.commit()

    # -------- edges: relations --------
    def add_relation(self, src: str, dst: str, rel_type: str, scene_id: str,
                     evidence: str, confidence: float, weight_scale: float = 1.0):
        sid = self._get_or_create_person(src)
        did = self._get_or_create_person(dst)
        cur = self.conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO relations(src_id, dst_id, rel_type, scene_id, evidence, confidence, weight)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (sid, did, rel_type, scene_id, evidence, confidence))
        cur.execute("""
            UPDATE relations SET weight = weight + ?
            WHERE src_id=? AND dst_id=? AND rel_type=? AND scene_id=? AND evidence=?
        """, (confidence * weight_scale, sid, did, rel_type, scene_id, evidence))
        self.conn.commit()

    # -------- edges: interactions --------
    def add_interaction(self, src: str, dst: str, i_type: str, scene_id: str,
                        evidence: str, sentiment: str, power: str,
                        confidence: float, weight_scale: float = 1.0):
        sid = self._get_or_create_person(src)
        did = self._get_or_create_person(dst)
        self.conn.execute("""
            INSERT INTO interactions(src_id, dst_id, i_type, scene_id, evidence,
                                     sentiment, power, confidence, weight)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, did, i_type, scene_id, evidence, sentiment, power, confidence, confidence * weight_scale))
        self.conn.commit()

    # -------- read for visualization --------
    def read_graph(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, canon_name FROM persons")
        id2name = {row[0]: row[1] for row in cur.fetchall()}
        nodes = list(id2name.values())

        cur.execute("""
            SELECT src_id, dst_id, rel_type, SUM(weight) AS w
            FROM relations
            GROUP BY src_id, dst_id, rel_type
        """)
        rel_edges = [(id2name[s], id2name[d], {"type": rt, "weight": float(w or 0)})
                     for s, d, rt, w in cur.fetchall()]

        cur.execute("""
            SELECT src_id, dst_id, i_type, SUM(weight) AS w
            FROM interactions
            GROUP BY src_id, dst_id, i_type
        """)
        inter_edges = [(id2name[s], id2name[d], {"type": it, "weight": float(w or 0)})
                       for s, d, it, w in cur.fetchall()]

        return nodes, rel_edges, inter_edges
