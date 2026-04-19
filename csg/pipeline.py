# -*- coding: utf-8 -*-
"""
pipeline.py — End-to-end pipeline for English screenplay → character/social KG (no Docker)
"""

from __future__ import annotations
import os
import json
import argparse
import traceback
from typing import Dict, Any, List, Optional

import yaml

from parser import smart_load, split_scenes, extract_speaker_turns
from prompts import SCREENPLAY_PROMPT_EN
from llm_providers import build_provider
from graph_store import GraphStore
from visualize import render_graph_html
from readers import read_text

# -------------- Utilities --------------

def _ensure_dir(p: str):
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)

def _to_json_object(maybe_json) -> Dict[str, Any]:
    if isinstance(maybe_json, dict):
        return maybe_json
    if isinstance(maybe_json, str):
        try:
            return json.loads(maybe_json)
        except Exception:
            s = maybe_json
            first = s.find("{")
            last = s.rfind("}")
            if first != -1 and last != -1 and last > first:
                candidate = s[first:last+1]
                try:
                    return json.loads(candidate)
                except Exception:
                    pass
    return {
        "meta": {}, "characters": [], "mentions": [],
        "interactions": [], "relations": [], "scene_summary": {}
    }

def _upper(s: Optional[str], default: str = "") -> str:
    return (s or default).upper().strip()

def _float(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

# enums for validation
REL_ENUM = {
    "FAMILY","ROMANTIC","FRIEND","ALLY","BOSS_OF","SUBORDINATE_OF","TEACHER_OF","STUDENT_OF",
    "RIVAL","ENEMY","BETRAYAL","PROTECTS","BLACKMAILS","OWES_DEBT","COAUTHOR","COCONSPIRATOR","UNKNOWN"
}
INTER_ENUM = {"DIALOGUE_EXCHANGE","CO_OCCURRENCE","PHYSICAL_ACTION","MESSAGE","CALL"}

# --- Safe formatting helper to avoid KeyError from JSON braces in prompt templates ---
PLACEHOLDERS = ["script_id", "chunk_id", "scene_id", "character_list", "speaker_turns", "text"]

def safe_prompt_format(tpl: str, **vals) -> str:
    sentinels = {k: f"@@__{k.upper()}__@@" for k in PLACEHOLDERS}
    for k, sentinel in sentinels.items():
        tpl = tpl.replace("{" + k + "}", sentinel)
    tpl = tpl.replace("{", "{{").replace("}", "}}")
    for k, sentinel in sentinels.items():
        tpl = tpl.replace(sentinel, "{" + k + "}")
    return tpl.format(**vals)

def _validate_and_filter(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize enums and filter malformed items; keep your overall schema."""
    result = result or {}
    result.setdefault("meta", {})
    result.setdefault("characters", [])
    result.setdefault("mentions", [])
    result.setdefault("interactions", [])
    result.setdefault("relations", [])
    result.setdefault("scene_summary", {})

    # relations
    rels = []
    for r in result.get("relations", []):
        if not (r and r.get("src") and r.get("dst")):
            continue
        rtype = _upper(r.get("rel_type"), "UNKNOWN")
        if rtype not in REL_ENUM:
            rtype = "UNKNOWN"
        r["rel_type"] = rtype
        rels.append(r)
    result["relations"] = rels

    # interactions
    its = []
    for it in result.get("interactions", []):
        if not (it and it.get("src") and it.get("dst")):
            continue
        itype = _upper(it.get("type"), "DIALOGUE_EXCHANGE")
        if itype not in INTER_ENUM:
            itype = "DIALOGUE_EXCHANGE"
        it["type"] = itype
        its.append(it)
    result["interactions"] = its

    return result

# -------------- Main Pipeline --------------

def run_pipeline(script_path: str, config_path: str, limit_scenes: Optional[int] = None):
    # Load config
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # Project paths
    script_id   = cfg["project"]["script_id"]
    db_path     = cfg["project"]["db_path"]
    out_dir     = cfg["project"]["out_dir"]
    graph_html  = cfg["project"]["graph_html"]
    _ensure_dir(db_path)
    os.makedirs(out_dir, exist_ok=True)

    # Thresholds
    min_rel_conf  = float(cfg["extract"]["min_relation_confidence"])
    min_int_conf  = float(cfg["extract"]["min_interaction_confidence"])

    # Read screenplay with readers.read_text (pdf/txt), fallback to parser.smart_load (adds .fdx)
    script_path = os.path.expanduser(script_path)
    try:
        raw = read_text(script_path)
    except Exception as e:
        print(f"[pipeline] readers.read_text failed on {script_path}: {e}")
        print("[pipeline] Falling back to parser.smart_load()")
        raw = smart_load(script_path)

    # Guard against scanned PDFs (no selectable text) or empty reads
    if not raw or len(raw.strip()) < 50:
        print(f"[pipeline] WARNING: No extractable text from {script_path}. "
              "This file may be scanned (image-only). Provide a .txt or OCR first.")
        return

    scenes = split_scenes(raw)
    if limit_scenes is not None:
        scenes = scenes[:limit_scenes]
    print(f"[pipeline] Script: {script_path}")
    print(f"[pipeline] Scenes detected: {len(scenes)}")

    # Build model provider
    provider = build_provider(cfg)
    print(f"[pipeline] Provider: {cfg.get('model',{}).get('provider','dummy')} | model_name={cfg.get('model',{}).get('model_name','')}")

    # Graph store
    store = GraphStore(db_path)

    # Rolling character roster
    character_roster: List[str] = []

    # Output JSONL of per-scene extractions
    out_jsonl = os.path.join(out_dir, "extractions.jsonl")
    _ensure_dir(out_jsonl)
    with open(out_jsonl, "w", encoding="utf-8") as fout:
        # Iterate scenes
        for i, sc in enumerate(scenes):
            scene_id = sc["scene_id"]
            text     = sc["text_with_index"]
            print(f"\n[pipeline] === Scene {i+1}/{len(scenes)} :: {scene_id} ===")

            # Parse speaker turns
            speaker_turns = extract_speaker_turns(text)

            # Build prompt
            prompt = safe_prompt_format(
                SCREENPLAY_PROMPT_EN,
                script_id=script_id,
                chunk_id=str(i),
                scene_id=scene_id,
                character_list=json.dumps(character_roster, ensure_ascii=False),
                speaker_turns=json.dumps(speaker_turns, ensure_ascii=False),
                text=text
            )

            # Call model
            try:
                result = provider.complete(prompt)
                result = _to_json_object(result)
                result = _validate_and_filter(result)
            except Exception as e:
                print(f"[WARN] Provider error at scene {scene_id}: {e}")
                traceback.print_exc()
                continue

            # Write JSONL line
            fout.write(json.dumps(result, ensure_ascii=False) + "\n")
            fout.flush()

            # Ingest characters → aliases
            for ch in result.get("characters", []) or []:
                name = ch.get("canon_name")
                if not name:
                    continue
                aliases = ch.get("aliases") or []
                store.add_aliases(name, aliases)
                if name not in character_roster:
                    character_roster.append(name)

            # Ingest relations
            for r in result.get("relations", []) or []:
                src   = r.get("src")
                dst   = r.get("dst")
                rtype = _upper(r.get("rel_type"), "UNKNOWN")
                conf  = _float(r.get("confidence"), 0.0)
                evid  = r.get("evidence", "")
                sid   = r.get("scene_id", scene_id)
                if not src or not dst:
                    continue
                if conf < min_rel_conf:
                    continue
                store.add_relation(src, dst, rtype, sid, evid, conf, weight_scale=1.0)

            # Ingest interactions
            for it in result.get("interactions", []) or []:
                src   = it.get("src")
                dst   = it.get("dst")
                itype = _upper(it.get("type"), "DIALOGUE_EXCHANGE")
                conf  = _float(it.get("confidence"), 0.0)
                evid  = it.get("evidence", "")
                sid   = it.get("scene_id", scene_id)
                sent  = (it.get("sentiment") or "neutral").lower()
                power = (it.get("power_dynamics") or "unclear").lower()
                if not src or not dst:
                    continue
                if conf < min_int_conf:
                    continue
                store.add_interaction(src, dst, itype, sid, evid, sent, power, conf, weight_scale=0.5)

    # Read back graph and render
    nodes, rel_edges, inter_edges = store.read_graph()
    store.close()

    out_path = render_graph_html(
        nodes, rel_edges, inter_edges,
        out_html=graph_html,
        edge_colors=cfg["visual"]["edge_colors"]
    )
    print(f"\n✅ Done. Graph saved to: {out_path}")
    print(f"   Raw per-scene JSON saved to: {out_jsonl}")
    print(f"   SQLite graph DB: {db_path}")

# -------------- CLI --------------

def main():
    ap = argparse.ArgumentParser(description="Screenplay → Social/Knowledge Graph pipeline (no Docker)")
    ap.add_argument("--script", help="Path to screenplay file (.txt/.pdf/.fdx)")
    ap.add_argument("--input_glob", help="Glob of scripts, e.g., 'data/*.pdf' (optional batch mode)")
    ap.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    ap.add_argument("--limit_scenes", type=int, default=None, help="Debug: process only the first N scenes")
    args = ap.parse_args()

    if args.input_glob:
        import glob
        paths = glob.glob(os.path.expanduser(args.input_glob))
        if not paths:
            print(f"[pipeline] No files matched: {args.input_glob}")
            return
        for path in paths:
            run_pipeline(path, args.config, limit_scenes=args.limit_scenes)
    elif args.script:
        run_pipeline(os.path.expanduser(args.script), args.config, limit_scenes=args.limit_scenes)
    else:
        ap.error("Provide either --script or --input_glob")

if __name__ == "__main__":
    main()
