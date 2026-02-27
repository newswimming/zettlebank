# CLAUDE.md

## Technology Stack
- **Frontend**: TypeScript / React — Obsidian plugin at `choracle-remote/.obsidian/plugins/zettlebank/`
- **Backend**: Python / FastAPI — `server.py` intelligence layer
- **Graph**: NetworkX (persistent DiGraph) + igraph (Leiden community detection via `leidenalg`)
- **Embeddings**: Smart Connections plugin data (TaylorAI/bge-micro-v2, 384-dim) at `choracle-remote/.smart-env/multi/`
- **Validation**: Pydantic (server) ↔ Zod (client) — schemas must stay in sync

## Build & Test Commands
- Build Frontend: `cd choracle-remote/.obsidian/plugins/zettlebank && npm run build`
- Start Backend: `uvicorn server:app --reload`
- Test Suite: `python test_workflow.py`
- Lint: `eslint . --ext .ts,.tsx`

## Rules

### Strict YAML Template
All frontmatter extraction and writing MUST produce only these fields, in this order. No other top-level keys are permitted.

```yaml
---
aliases:          # text | null
description:      # text | null
note created:     # date (Obsidian-managed)
last updated:     # date (written on approve)
tags:             # list[str] — Obsidian path syntax: topic/, aspect/, affect/, code/
source:           # text | null
citationID:       # text | null — Zotero / BibTeX key (camelCase, not snake_case)
smart_relations:  # list[{link: str, type: RelationType, confidence: float}]
community_id:     # int | null
updated:          # ISO timestamp (written by plugin on every approve)
---
```

- `smart_relations` entries use controlled vocabulary for `type`: contradicts, supports, potential_to, kinetic_to, motivates, hinders, related
- `tags` use forward-slash path syntax for Obsidian's collapsible tag tree. Allowed prefixes: `topic/`, `aspect/`, `affect/`, `code/`
- `citationID` is camelCase — do not rename to `citation_id`
- Do NOT add fields like Topics, Emotions, Character Arc, or Story Beat as top-level frontmatter keys

### Data Integrity
- Never propose direct text edits to frontmatter. All metadata updates must use `app.fileManager.processFrontMatter()` (Safe Frontmatter Manipulation pattern). Read `docs/patterns.md` for the required implementation.
- Nothing writes to frontmatter until explicit user approval in the staging area.

### Graph Logic
- Narrative clustering uses the **Leiden Algorithm** (not Louvain) at two resolutions: macro (y=0.5) for themes, micro (y=2.0) for scene beats. Review `docs/decisions.md` (ADR-001) before modifying.
- Relation type classification is discriminative, not generative (ADR-002). Default fallback is `related`.

### Multi-Stage Pipeline
The `/analyze` endpoint runs a multi-stage extraction pipeline:
- **Stage A** (first, sequential): Graph topology + dual-resolution Leiden (γ=0.5 macro, γ=2.0 micro)
- **Stage B** (parallel, after A): BERTopic community keywords → Ollama LLM → `topic/<Label>` tag
- **Stage C** (parallel with B): spaCy NER → `aspect/place`, `aspect/character`, etc.
- **Stage D** (parallel with B+C): Ollama tinyllama → `affect/<value>` + `code/<value>` tags
- **Assembly**: `_assemble_tags()` merges, deduplicates, and caps at 10 tags

Tag prefixes: `topic/` (BERTopic keywords), `aspect/` (spaCy NER entities), `affect/` (positive/negative/mu), `code/` (qi/law/mu)

### Architecture
- The system is strictly split between Obsidian frontend and local Python backend. Read `docs/architecture.md` before changing the API bridge or Data Contract.
- Pydantic models in `server.py` and Zod schemas in `schema.ts` must mirror each other exactly.
- Before implementing new schemas, check `docs/bugs.md` for known failure modes (Metadata Drift, Label Drift).
