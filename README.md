# ZettleBank

Narrative intelligence sidebar for Obsidian. Analyzes note content via a local FastAPI backend and proposes tags, smart relations, and community IDs for user review before writing to frontmatter.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Node.js | 18+ | For the plugin build |
| Python | 3.11–3.13 | **Not 3.14+** — breaks spaCy |
| Ollama | any | Must be running locally with a model pulled |
| Obsidian | 1.0+ | Desktop only (`isDesktopOnly: true`) |

---

## Plugin: Install & Build

The plugin source lives in:

```
vault/choracle-remote-00/.obsidian/plugins/zettlebank/
```

**Install dependencies:**

```bash
cd vault/choracle-remote-00/.obsidian/plugins/zettlebank
npm install
```

**Developer mode** — watches source files and rebuilds `main.js` on every save:

```bash
npm run dev
```

Reload the plugin in Obsidian after each rebuild: `Ctrl/Cmd+P` → *Reload app without saving*.

**Production build** — runs TypeScript type-checking first, then bundles:

```bash
npm run build
```

> `npm run build` will fail on type errors. `npm run dev` skips type checking for faster iteration.

---

## Backend: Install & Run

```bash
# From the project root
cp .env.example .env          # fill in VAULT_DIR and model settings
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Start the server
py -3.13 -m uvicorn server:app --host 127.0.0.1 --port 8000
```

The server must be running before you click **Analyze** in the sidebar. Check liveness at `http://localhost:8000/health`.

**Environment variables** (`.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_DIR` | `choracle-remote-00` | Name of the vault folder relative to `server.py` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2` | Model used for topic labelling and affect/code classification |
| `SPACY_MODEL` | `en_core_web_sm` | spaCy NER model |
| `EMBED_MODEL_KEY` | `TaylorAI/bge-micro-v2` | Must match what the Smart Connections plugin used |

---

## Testing via BRAT

[BRAT](https://github.com/TfTHacker/obsidian42-brat) (Beta Reviewers Auto-update Tool) lets you install a plugin directly from a GitHub repository without submitting to the community list.

1. Install **BRAT** from Obsidian → Settings → Community Plugins.
2. Open the command palette (`Ctrl/Cmd+P`) → **BRAT: Add a beta plugin for testing**.
3. Paste the repository URL:
   ```
   https://github.com/<your-org>/km-test-0
   ```
4. BRAT clones the repo, copies `main.js` + `manifest.json` + `styles.css` into your vault's plugin folder, and enables the plugin.
5. To update later: command palette → **BRAT: Update all beta plugins**.

> BRAT reads the compiled `main.js` from the repository — it does **not** build from source. Run `npm run build` and commit `main.js` before asking collaborators to install via BRAT.

---

## Architecture

ZettleBank is split across two processes that communicate over localhost HTTP.

```
Obsidian (Electron)                     Python process
┌─────────────────────────────┐         ┌──────────────────────────┐
│  ZettleBankPlugin           │  POST   │  FastAPI /analyze        │
│  ├─ ZettleBankView          │────────▶│  ├─ Stage A: Leiden      │
│  │  └─ ZettleBankSidebar    │         │  ├─ Stage B: BERTopic+LLM│
│  │     (React)              │◀────────│  ├─ Stage C: spaCy NER   │
│  └─ ZettleBankSettingTab    │  JSON   │  └─ Stage D: Ollama LLM  │
└─────────────────────────────┘         └──────────────────────────┘
```

### Plugin classes (`main.ts`)

**`ZettleBankPlugin`** (`extends Plugin`)
Entry point. Runs on Obsidian load/unload. Owns:
- Plugin settings (`loadData` / `saveData`) — persists `backendUrl` across sessions.
- View registration and ribbon icon.
- The `analyze-current-note` command.
- Coordinates the two side-effect operations: `analyzeActiveNote()` and `approveAndWrite()`.

**`ZettleBankView`** (`extends ItemView`)
The sidebar panel. Owns a React root (`createRoot`) and a local `SidebarState` machine:

```
idle ──[Analyze]──▶ loading ──[response]──▶ staging ──[Approve]──▶ idle
                                    └──[error]──▶ error ──[Retry]──▶ loading
```

On every state transition it calls `this.render()` which re-renders the React tree. State never lives in React — it lives here, in the Obsidian `ItemView`, and flows down as props.

**`ZettleBankSettingTab`** (`extends PluginSettingTab`)
Renders the Settings tab inside Obsidian → Settings → ZettleBank. Currently exposes one field: **Backend URL** (default `http://localhost:8000`).

**`analyzeNote()`** (module-level async function)
The HTTP bridge. Sends note content to `/analyze`, receives raw JSON, and validates it through the Zod `AnalyzeResponseSchema` before returning. Any schema violation throws here, at the boundary, rather than surfacing as a render error inside React.

**`writeFrontmatter()`** (module-level function)
Wraps `app.fileManager.processFrontMatter()`. Only called after explicit user approval. Merges tags and `smart_relations` additively (never overwrites existing data) and writes an ISO 8601 timestamp to `updated`.

### React component (`ZettleBankView.tsx`)

**`ZettleBankSidebar`** (root FC)
Stateless shell. Receives `state`, `onAnalyze`, and `onApprove` from the `ItemView` and switches between the idle, loading, error, and staging views.

**`StagingArea`** (FC)
The approval interface. Holds its own local React state for tag toggles, description edits, and relation toggles. On confirm it assembles an `ApprovedPayload` and hands it up to `onApprove` — the only path that triggers a frontmatter write.

### Schema contract (`schema.ts` ↔ `server.py`)

Pydantic models in `server.py` and Zod schemas in `schema.ts` must stay in sync. The shared types are:

| Type | Description |
|------|-------------|
| `SmartRelation` | `{ link, type, confidence }` — a single graph edge |
| `NarrativeMetadata` | Frontmatter payload: aliases, description, tags, smart_relations, source, citationID |
| `AnalyzeResponse` | Full `/analyze` response: `note_id`, `metadata`, `community_id`, `community_tiers` |

If you add or rename a field in one, update the other. The `validateAnalyzeResponse()` function in `schema.ts` will throw a `ZodError` at runtime if they drift.

---

## Project layout

```
km-test-0/
├── server.py                          # FastAPI backend (intelligence layer)
├── requirements.txt
├── .env.example                       # Copy to .env and fill in values
├── vault/
│   └── choracle-remote-00/
│       └── .obsidian/
│           └── plugins/
│               └── zettlebank/        # ← plugin root (build from here)
│                   ├── main.ts        # Plugin entry point
│                   ├── ZettleBankView.tsx  # React sidebar
│                   ├── schema.ts      # Zod validation schemas
│                   ├── styles.css     # Sidebar CSS (Obsidian CSS vars)
│                   ├── main.js        # Compiled output — committed for BRAT/releases
│                   ├── manifest.json  # Plugin metadata
│                   ├── package.json
│                   ├── tsconfig.json
│                   └── esbuild.config.mjs
├── plugin/                            # Mirror of plugin source (dev reference)
├── architecture.md
├── decisions.md                       # ADR-001 (Leiden), ADR-002 (discriminative LLM), ADR-003 (Shadow DB)
└── CLAUDE.md                          # AI assistant context and invariants
```
