# -*- coding: utf-8 -*-
"""
parser.py — English Screenplay Parser (No Docker, No Admin Required)

Features:
- Load screenplay from .txt / .pdf (PyPDF2) / .fdx (Final Draft 8+)
- Split scenes by common headings (INT./EXT./INT/EXT.) with robust fallback chunking
- Extract speaker turns (CHARACTER in ALL CAPS, optional parenthetical, dialogue),
  returning approximate character indices relative to the scene text
- Ready to be used with LLM extraction prompts (evidence + char_span)

Usage (CLI demo):
    python parser.py --script data/sample_en.txt
"""

from __future__ import annotations
import os
import re
import json
import argparse
from typing import List, Dict, Any, Tuple, Optional

# ---- PDF Loader (PyPDF2 / pypdf2) ----
try:
    from PyPDF2 import PdfReader  # Most installations use this import name
except Exception:  # Fallback just in case
    from pypdf2 import PdfReader  # Some environments expose the module as pypdf2

# ---- XML for FDX ----
from xml.etree import ElementTree as ET

# =========================
# Scene Heading Recognition
# =========================
SCENE_HEADING_RE = re.compile(
    r"""^\s*(?:I\/E\.|INT\/EXT\.|INT\.|EXT\.)        # INT./EXT./INT/EXT prefixes
         [\sA-Z0-9.,'"\-()#/]*                       # location slug (lenient)
         (?:\s-\s[A-Z0-9 .,'()"]+)?\s*$              # optional " - TIME"
    """, re.IGNORECASE | re.VERBOSE,
)

NUMBERED_SCENE_HEADING_RE = re.compile(
    r"""^\s*\d+\s+(?:I\/E\.|INT\/EXT\.|INT\.|EXT\.)  # leading number + heading
         [\sA-Z0-9.,'"\-()#/]*                       # location slug
         (?:\s-\s[A-Z0-9 .,'()"]+)?\s*$              # optional " - TIME"
    """, re.IGNORECASE | re.VERBOSE,
)

# Transitions (not scenes); we skip them when parsing turns.
TRANSITION_RE = re.compile(
    r'^\s*(?:CUT TO:|DISSOLVE TO:|SMASH CUT TO:|MATCH CUT TO:|FADE IN:|FADE OUT:|WIPE TO:|CUT TO BLACK:|FADE TO BLACK:)\s*$',
    re.IGNORECASE
)

# ==========================
# Character Line Recognition
# ==========================
CHARACTER_LINE_RE = re.compile(
    r'^[A-Z0-9 .\'\-()]+(?:\(V\.O\.\)|\(O\.S\.\)|\(CONT['\']?D\))?\s*$'
)
PARENTHETICAL_RE = re.compile(r'^\s*\(.*\)\s*$')

MAX_CHAR_NAME_CHARS = 40
MAX_CHAR_NAME_SPACES = 4

# ============
# Loaders
# ============
def load_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def load_pdf(path: str) -> str:
    reader = PdfReader(path)
    texts: List[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        texts.append(t)
    return "\n".join(texts)

def load_fdx(path: str) -> str:
    xml = ET.parse(path)
    root = xml.getroot()
    ns = {"fdx": root.tag.split("}")[0].strip("{")}
    lines: List[str] = []
    for p in root.findall(".//fdx:Paragraph", ns):
        ptype = p.get("Type", "")
        text = "".join((t.text or "") for t in p.findall(".//fdx:Text", ns)).strip()
        if ptype in ("Scene Heading", "Action", "Character", "Parenthetical", "Dialogue", "Transition"):
            if text:
                if ptype == "Transition" and not text.endswith(":"):
                    text += ":"
                lines.append(text)
    return "\n".join(lines)

def smart_load(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        return load_text(path)
    elif ext == ".pdf":
        return load_pdf(path)
    elif ext == ".fdx":
        return load_fdx(path)
    else:
        raise ValueError(f"Unsupported screenplay format: {ext}")

# ==================
# Scene Splitting
# ==================
def split_scenes(
    raw_text: str,
    fallback_chunk_lines: int = 400,
    min_scene_len_chars: int = 30
) -> List[Dict[str, Any]]:
    """
    Returns: [{"scene_id": "SCENE_000", "text": "...", "text_with_index": "..."}]
    """
    lines = raw_text.splitlines()
    scenes: List[Dict[str, Any]] = []
    curr: List[str] = []
    scene_idx = 0

    def is_scene_heading(ln: str) -> bool:
        return bool(SCENE_HEADING_RE.match(ln) or NUMBERED_SCENE_HEADING_RE.match(ln))

    def flush():
        nonlocal scene_idx, curr
        if not curr:
            return
        text = "\n".join(curr).strip()
        if len(text) >= min_scene_len_chars:
            sid = f"SCENE_{scene_idx:03d}"
            scenes.append({"scene_id": sid, "text": text, "text_with_index": text})
            scene_idx += 1
        curr = []

    # Primary pass: split by scene headings
    for ln in lines:
        if is_scene_heading(ln):
            flush()
            curr.append(ln)
        else:
            curr.append(ln)
    flush()

    # Fallback chunking
    if len(scenes) <= 1:
        chunks: List[str] = []
        buf: List[str] = []
        for i, ln in enumerate(lines):
            buf.append(ln)
            if (i + 1) % fallback_chunk_lines == 0:
                chunks.append("\n".join(buf))
                buf = []
        if buf:
            chunks.append("\n".join(buf))
        scenes = [
            {"scene_id": f"SCENE_{i:03d}", "text": t.strip(), "text_with_index": t.strip()}
            for i, t in enumerate(chunks)
            if len(t.strip()) >= min_scene_len_chars
        ]
    return scenes

# ===========================
# Speaker Turns (English)
# ===========================
def extract_speaker_turns(scene_text: str) -> List[Dict[str, Any]]:
    """
    Returns a list of dicts:
    {
      "speaker": "ALAN",
      "parenthetical": "(whispers)" | None,
      "dialogue": "...",
      "start": <char_index_start_in_scene_text>,
      "end": <char_index_end_in_scene_text>
    }
    """
    lines_with_nl = scene_text.splitlines(keepends=True)
    line_starts: List[int] = []
    pos = 0
    for ln in lines_with_nl:
        line_starts.append(pos)
        pos += len(ln)

    def norm_speaker(raw: str) -> str:
        s = raw.strip()
        s = re.sub(r'\s*\((V\.O\.|O\.S\.|CONT['\']?D)\)\s*$', '', s).strip()
        s = re.sub(r'\s+', ' ', s)
        return s

    def looks_like_character_line(raw_no_nl: str, stripped: str) -> bool:
        # Exclude scene headings or transitions (common false positives)
        if SCENE_HEADING_RE.match(stripped) or NUMBERED_SCENE_HEADING_RE.match(stripped):
            return False
        if TRANSITION_RE.match(stripped):
            return False
        if not CHARACTER_LINE_RE.match(raw_no_nl):
            return False
        if len(stripped) > MAX_CHAR_NAME_CHARS:
            return False
        if stripped.count(" ") > MAX_CHAR_NAME_SPACES:
            return False
        # Enforce "mostly uppercase" heuristic
        letters = re.sub(r'[^A-Za-z]+', '', stripped)
        return bool(letters) and letters.upper() == letters

    turns: List[Dict[str, Any]] = []
    i = 0
    n = len(lines_with_nl)

    while i < n:
        raw_line = lines_with_nl[i]
        raw_no_nl = raw_line.rstrip("\n")
        stripped = raw_no_nl.strip()

        # Skip empties or transitions
        if not stripped or TRANSITION_RE.match(stripped):
            i += 1
            continue

        # Candidate character line
        if looks_like_character_line(raw_no_nl, stripped):
            speaker = norm_speaker(raw_no_nl)
            start_idx = line_starts[i]
            i += 1

            # Optional parenthetical
            parenthetical: Optional[str] = None
            if i < n:
                nxt_stripped = lines_with_nl[i].strip()
                if PARENTHETICAL_RE.match(nxt_stripped):
                    parenthetical = nxt_stripped
                    i += 1

            # Collect dialogue lines
            dialog_lines: List[str] = []
            last_line_end_char = start_idx
            while i < n:
                nxt = lines_with_nl[i]
                nxt_no_nl = nxt.rstrip("\n")
                nxt_stripped2 = nxt_no_nl.strip()

                # Stop conditions
                if not nxt_stripped2:
                    last_line_end_char = line_starts[i] + len(nxt)
                    i += 1
                    break
                if TRANSITION_RE.match(nxt_stripped2):
                    break
                if looks_like_character_line(nxt_no_nl, nxt_stripped2):
                    break

                dialog_lines.append(nxt_no_nl)
                last_line_end_char = line_starts[i] + len(nxt)
                i += 1

            dialogue = "\n".join(dialog_lines).strip()
            if dialogue:
                end_idx = last_line_end_char
                turns.append({
                    "speaker": speaker,
                    "parenthetical": parenthetical,
                    "dialogue": dialogue,
                    "start": int(start_idx),
                    "end": int(end_idx)
                })
            continue

        i += 1

    return turns

# ==============
# CLI for debug
# ==============
def _demo_cli(script_path: str, max_show_turns: int = 5):
    raw = smart_load(script_path)
    scenes = split_scenes(raw)
    print(f"[parser] Detected scenes: {len(scenes)}")
    if not scenes:
        return

    first_scene = scenes[0]
    print(f"[parser] First scene id: {first_scene['scene_id']}, length: {len(first_scene['text'])} chars")

    turns = extract_speaker_turns(first_scene["text"])
    print(f"[parser] Speaker turns in first scene: {len(turns)} (showing up to {max_show_turns})")
    for t in turns[:max_show_turns]:
        compact = {
            "speaker": t["speaker"],
            "parenthetical": t["parenthetical"],
            "dialogue": (t["dialogue"][:60] + "…") if len(t["dialogue"]) > 60 else t["dialogue"],
            "span": [t["start"], t["end"]],
        }
        print(json.dumps(compact, ensure_ascii=False))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="English screenplay parser (scenes + speaker turns)")
    ap.add_argument("--script", required=True, help="Path to screenplay (.txt/.pdf/.fdx)")
    args = ap.parse_args()
    _demo_cli(args.script)
