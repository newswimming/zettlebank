# CLAUDE.md

## Technology Stack
- **Frontend**: TypeScript / React — Obsidian plugin at `vault/choracle-remote-00/.obsidian/plugins/zettlebank/`
- **Backend**: Python / FastAPI — `server.py` intelligence layer
- **Graph**: NetworkX (persistent DiGraph) + igraph (Leiden community detection via `leidenalg`)
- **Embeddings**: Smart Connections plugin data (TaylorAI/bge-micro-v2, 384-dim) at `vault/choracle-remote-00/.smart-env/multi/`
- **Validation**: Pydantic (server) ↔ Zod (client) — schemas must stay in sync

## Build & Test Commands
- Build Frontend: `cd vault/choracle-remote-00/.obsidian/plugins/zettlebank && npm run build`
- Start Backend: `uvicorn server:app --reload` (use venv: `venv/Scripts/python -m uvicorn server:app`)
- Test Suite: `python backend/test_workflow.py`
- Lint: `eslint . --ext .ts,.tsx`

---

## Rule 1 — Python Runtime: 3.11–3.13 Only

**Constraint**: The backend MUST run on Python 3.11, 3.12, or 3.13. Python 3.14+ is prohibited.

**Reason**: spaCy depends on `pydantic.v1` internally. Python 3.14 breaks this compatibility layer. Uvicorn on Windows also triggers a numba/LLVM crash when spawned from bash directly — always launch via `cmd.exe` or `start.sh` on Windows.

**Enforcement**:
- All `requirements.txt` pins must be tested against 3.11–3.13.
- `setup.sh` and `start.sh` detect Python by probing `py -3.13`, `py -3.12`, `py -3.11` in order — do not remove this detection chain.
- Never add a dependency that requires Python ≥ 3.14.
- If spaCy, leidenalg, or numba version bumps are needed, verify 3.11–3.13 compatibility first.

---

## Rule 2 — Primary Narrative Framework: 16-Beat Kishōtenketsu Matrix

The **Kishōtenketsu (起承転結)** 4-act structure, expanded to 16 beats, is the canonical narrative model for all note analysis. Every analysis pipeline, tag label, and community structure must be interpretable within this matrix.

### The 16-Beat Matrix

| Act | Kanji | Function | Beats | Description |
|-----|-------|----------|-------|-------------|
| Ki  | 起    | Introduction | 1–4 | World/context established; no conflict yet |
| Shō | 承    | Development  | 5–8 | Continuation and elaboration of Ki |
| Ten | 転    | Twist        | 9–12 | Unexpected turn unrelated to prior conflict — the pivot |
| Ketsu | 結  | Resolution   | 13–16 | Synthesis that recontextualizes Ki and Shō through Ten |

**Beat definitions** (used as `code/` tag values):

```
code/ki-1    — World establishment: the context or setting is introduced
code/ki-2    — Subject emergence: the primary entity/concept appears
code/ki-3    — Status quo: the stable condition before change
code/ki-4    — Threshold: first signal that the stable state will shift

code/sho-5   — Engagement: deepening of the introduced subject
code/sho-6   — Complication: elaboration that raises stakes
code/sho-7   — Interweaving: themes from Ki begin to interact
code/sho-8   — Suspension: the development reaches maximum density

code/ten-9   — Pivot: the unexpected turn (not a conflict resolution — a reframe)
code/ten-10  — Revelation: a new perspective that recontextualizes prior beats
code/ten-11  — Cascade: consequences of the pivot propagate
code/ten-12  — Inversion: the original framing is held up against its opposite

code/ketsu-13 — Synthesis: Ki and Shō are re-read through the lens of Ten
code/ketsu-14 — Integration: disparate elements cohere
code/ketsu-15 — Resolution: the new stable state is established
code/ketsu-16 — Echo: a transformed return to the opening image or theme
```

### Mapping to the Pipeline

- **Stage D (Ollama `affect/` + `code/`)**: The LLM must classify beat position using the 16 labels above. The discriminative prompt must offer all 16 `code/ki-*` through `code/ketsu-*` values as choices. The fallback is `code/ki-1` (unplaced introduction), not omission.
- **Macro Leiden (γ=0.5)**: Macro communities map to Kishōtenketsu **acts** (Ki, Shō, Ten, Ketsu). Community labels should reflect which act dominates the cluster.
- **Micro Leiden (γ=2.0)**: Micro communities map to **individual beats** within an act. Beat granularity at this resolution is expected and correct.
- **`smart_relations` edge types**: Relation type selection must consider beat position. Notes in the same act are `supports`; notes in Ten that reframe Ki-act notes are `kinetic_to`; notes that contradict a prior state are `contradicts`.

### What Is NOT Kishōtenketsu
- Do not model this as a Western 3-act or Hero's Journey structure. Kishōtenketsu has **no inherent conflict** — the Ten pivot is a juxtaposition, not a crisis.
- Do not conflate `ten` with antagonism. The pivot beat reframes; it does not destroy.
- Do not add beats outside the 16-beat matrix. If a note resists classification, assign the closest beat and add `code/unplaced` as a secondary tag.

---

## Rule 3 — Schema Contract: Pydantic ↔ Zod Synchronization

The Pydantic models in `server.py` and the Zod schemas in `schema.ts` define a single shared contract. They must be **identical in structure** at all times. A mismatch is a breaking bug, not a style issue.

### The Contract (Schema Contract v1.0)

| Pydantic model (`server.py`) | Zod schema (`schema.ts`) |
|-----------------------------|--------------------------|
| `RelationType` (str Enum)   | `RelationTypeEnum`       |
| `SmartRelation`             | `SmartRelationSchema`    |
| `NarrativeMetadata`         | `NarrativeMetadataSchema`|
| `CommunityTier`             | `CommunityTierSchema`    |
| `AnalyzeResponse`           | `AnalyzeResponseSchema`  |

### Synchronization Rules

1. **Field names**: Python uses `snake_case`; TypeScript uses `camelCase` for display only. The wire format is always `snake_case` (FastAPI default). Zod schemas must match the wire names exactly — `citationID` is the single exception (already camelCase in both languages per the frontmatter template; do not normalize it).
2. **Adding a field**: Add to the Pydantic model first, then immediately update the Zod schema in the same commit. Never merge a PR where one side is updated without the other.
3. **Removing a field**: Mark deprecated in both files simultaneously. Remove from both in the same commit.
4. **Renaming a field**: Treat as remove + add. Both files, same commit.
5. **Type changes**: `Optional[str]` in Pydantic → `z.string().nullable().default(null)` in Zod. `list[str]` → `z.array(z.string())`. `float` with ge/le bounds → `z.number().min().max()`.
6. **Enums**: Every value in `RelationType` must appear in `RelationTypeEnum` and vice versa. The Kishōtenketsu beat codes (`code/ki-1` … `code/ketsu-16`) are **tag string values**, not enum members — do not add them to `RelationType`.
7. **Validation boundary**: The frontend must call `validateAnalyzeResponse(raw)` (the helper in `schema.ts`) on every `/analyze` response before touching any field. Never access raw response fields directly.

### Known Failure Modes (see also `docs/bugs.md`)
- **Metadata Drift**: Frontend writes a field name that doesn't match the Pydantic model. Result: silent YAML corruption. Prevention: always use `processFrontMatter()` with keys taken from the validated `AnalyzeResponse`, never from hardcoded strings.
- **Label Drift**: A new relation type is added to `RelationType` but not to `RelationTypeEnum`. Result: Zod parse failure at runtime. Prevention: the synchronization rule above.

---

## Rule 4 — Strict YAML Frontmatter Template

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
- `code/` tag values must be one of the 16 Kishōtenketsu beat slugs (Rule 2) or `code/unplaced`
- Do NOT add fields like Topics, Emotions, Character Arc, or Story Beat as top-level frontmatter keys

---

## Rule 5 — Data Integrity

- Never propose direct text edits to frontmatter. All metadata updates must use `app.fileManager.processFrontMatter()` (Safe Frontmatter Manipulation pattern). Read `docs/patterns.md` for the required implementation.
- Nothing writes to frontmatter until explicit user approval in the staging area.

---

## Rule 6 — Graph Logic

- Narrative clustering uses the **Leiden Algorithm** (not Louvain) at two resolutions: macro (γ=0.5) for Kishōtenketsu acts, micro (γ=2.0) for individual scene beats. Review `docs/decisions.md` (ADR-001) before modifying.
- Relation type classification is discriminative, not generative (ADR-002). Default fallback is `related`.
- Beat position (Rule 2) must inform edge type selection — see mapping in Rule 2 § Mapping to the Pipeline.

---

## Rule 7 — Multi-Stage Pipeline

The `/analyze` endpoint runs a multi-stage extraction pipeline:
- **Stage A** (first, sequential): Graph topology + dual-resolution Leiden (γ=0.5 macro → act, γ=2.0 micro → beat)
- **Stage B** (parallel, after A): BERTopic community keywords → Ollama LLM → `topic/<Label>` tag
- **Stage C** (parallel with B): spaCy NER → `aspect/place`, `aspect/character`, `aspect/time`, `aspect/object`
- **Stage D** (parallel with B+C): Ollama tinyllama → `affect/<value>` (positive/negative/mu) + `code/<beat>` (one of the 16 Kishōtenketsu beats)
- **Assembly**: `_assemble_tags()` merges, deduplicates, and caps at 10 tags

Tag prefixes: `topic/` (BERTopic), `aspect/` (spaCy NER), `affect/` (valence), `code/` (Kishōtenketsu beat)

---

## Rule 8 — Architecture Boundaries

- The system is strictly split between Obsidian frontend and local Python backend. Read `docs/architecture.md` before changing the API bridge or Data Contract.
- Pydantic models in `server.py` and Zod schemas in `schema.ts` must mirror each other exactly (Rule 3).
- Before implementing new schemas, check `docs/bugs.md` for known failure modes (Metadata Drift, Label Drift).
- The Shadow Database pattern (ADR-003) must be preserved: `smart_relations` are stored as valid YAML lists but rendered exclusively via the React sidebar.
