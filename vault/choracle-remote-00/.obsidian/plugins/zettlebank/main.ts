import {
	App,
	Plugin,
	ItemView,
	WorkspaceLeaf,
	TFile,
	requestUrl,
	PluginSettingTab,
	Setting,
} from "obsidian";
import { StrictMode, createElement } from "react";
import { createRoot, Root } from "react-dom/client";
import { ZettleBankSidebar } from "./ZettleBankView";
import {
	validateAnalyzeResponse,
	validateSyncNoteResponse,
	SyncNoteRequestSchema,
	type AnalyzeResponse,
	type NarrativeMetadata,
	type EdgeMatrix,
} from "./schema";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIEW_TYPE = "zettlebank-sidebar";

/**
 * Convert an arbitrary string to a valid backend note_id slug.
 * Steps (in order):
 *  1. Lowercase
 *  2. Trim leading/trailing whitespace
 *  3. Replace spaces and underscores with hyphens
 *  4. Remove any character that is not alphanumeric or a hyphen
 *  5. Collapse consecutive hyphens into one
 *  6. Strip leading/trailing hyphens
 * Falls back to "note" if the result is empty.
 */
function slugify(text: string): string {
	return text
		.toLowerCase()
		.trim()
		.replace(/[\s_]+/g, "-")
		.replace(/[^a-z0-9-]/g, "")
		.replace(/-{2,}/g, "-")
		.replace(/^-+|-+$/g, "")
		|| "note";
}

// ---------------------------------------------------------------------------
// Content Utilities (auto-analysis helpers)
// ---------------------------------------------------------------------------

/**
 * Returns the note body with the YAML frontmatter block stripped.
 * Handles both LF and CRLF line endings.
 */
function extractBody(content: string): string {
	const match = content.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?([\s\S]*)$/);
	return match ? match[1].trim() : content.trim();
}

/** Counts whitespace-delimited words in a string. */
function wordCount(text: string): number {
	return text.split(/\s+/).filter(Boolean).length;
}

/**
 * Non-cryptographic djb2 hash — used only to detect body changes between
 * saves. Not stored anywhere persistent; collisions are acceptable.
 */
function simpleHash(text: string): string {
	let h = 5381;
	for (let i = 0; i < text.length; i++) {
		h = ((h << 5) + h) ^ text.charCodeAt(i);
		h |= 0; // keep 32-bit
	}
	return (h >>> 0).toString(36);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

interface ZettleBankSettings {
	backendUrl: string;
}

const DEFAULT_SETTINGS: ZettleBankSettings = {
	backendUrl: "http://localhost:8000",
};

// ---------------------------------------------------------------------------
// Client-Server Bridge (architecture.md)
//
// Raw JSON is validated through the Zod schema before it enters the UI.
// This catches Metadata Drift at the boundary instead of inside React.
// ---------------------------------------------------------------------------

/**
 * Posts note content to the backend `/analyze` endpoint and validates the
 * response through the Zod schema before returning it to the caller.
 * Throws a `ZodError` if the response shape doesn't match the contract.
 */
async function analyzeNote(
	noteId: string,
	content: string,
	backendUrl: string
): Promise<AnalyzeResponse> {
	const res = await requestUrl({
		url: `${backendUrl}/analyze`,
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			note_id: noteId,
			content,
		}),
	});
	return validateAnalyzeResponse(res.json);
}

// ---------------------------------------------------------------------------
// Safe Frontmatter Manipulation (patterns.md)
//
// All metadata writes go through app.fileManager.processFrontMatter to
// prevent race conditions and file corruption. Never edit YAML directly.
//
// This is ONLY called after user approval — never on raw backend output.
// ---------------------------------------------------------------------------

export interface ApprovedPayload {
	metadata: NarrativeMetadata;
	community_id: number | null;
}

/**
 * Merges an approved payload into the note's frontmatter via Obsidian's
 * `processFrontMatter` API. Tags and `smart_relations` are merged additively
 * (never overwritten). Only called after explicit user approval in the staging area.
 */
function writeFrontmatter(
	app: App,
	file: TFile,
	approved: ApprovedPayload
): void {
	app.fileManager.processFrontMatter(file, (frontmatter) => {
		// 1. Write approved metadata scalars
		if (approved.metadata.aliases !== null) {
			frontmatter.aliases = approved.metadata.aliases;
		}
		if (approved.metadata.description !== null) {
			frontmatter.description = approved.metadata.description;
		}
		if (approved.metadata.source !== null) {
			frontmatter.source = approved.metadata.source;
		}
		if (approved.metadata.citationID !== null) {
			frontmatter.citationID = approved.metadata.citationID;
		}

		// 2. Merge approved tags (deduplicated)
		const existingTags: string[] = frontmatter.tags || [];
		frontmatter.tags = [
			...new Set([...existingTags, ...approved.metadata.tags]),
		];

		// 3. Merge smart_relations by target_id::relation_type key (Shadow Database, ADR-003)
		const existingRels: EdgeMatrix[] =
			frontmatter.smart_relations || [];
		const relMap = new Map(
			existingRels.map((r) => [`${r.target_id}::${r.relation_type}`, r])
		);
		for (const rel of approved.metadata.smart_relations) {
			relMap.set(`${rel.target_id}::${rel.relation_type}`, rel);
		}
		frontmatter.smart_relations = Array.from(relMap.values());

		// 4. Write community_id
		if (approved.community_id !== null) {
			frontmatter.community_id = approved.community_id;
		}

		// 5. Timestamp — ISO 8601, platform-agnostic
		frontmatter.updated = new Date().toISOString();
	});
}

// ---------------------------------------------------------------------------
// Graph Sync (background — triggered by vault watcher on frontmatter edits)
// ---------------------------------------------------------------------------

async function syncNoteToGraph(
	app: App,
	file: TFile,
	backendUrl: string
): Promise<void> {
	const cache = app.metadataCache.getFileCache(file);
	const fm    = cache?.frontmatter;
	if (!fm) return;

	const noteId = slugify(file.basename);

	const payload = SyncNoteRequestSchema.parse({
		note_id:         noteId,
		tags:            fm.tags          ?? [],
		smart_relations: fm.smart_relations ?? [],
		community_id:    fm.community_id  ?? null,
	});

	const res = await requestUrl({
		url:    `${backendUrl}/graph/sync-note`,
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body:   JSON.stringify(payload),
	});
	validateSyncNoteResponse(res.json);
}

// ---------------------------------------------------------------------------
// React Sidebar Mounting (architecture.md – custom ItemView)
// ---------------------------------------------------------------------------

type SidebarState =
	| { phase: "idle" }
	| { phase: "loading" }
	| { phase: "results"; data: AnalyzeResponse }
	| { phase: "error"; message: string };

class ZettleBankView extends ItemView {
	private root: Root | null = null;
	private plugin: ZettleBankPlugin;
	private state: SidebarState = { phase: "idle" };

	constructor(leaf: WorkspaceLeaf, plugin: ZettleBankPlugin) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType(): string {
		return VIEW_TYPE;
	}

	getDisplayText(): string {
		return "ZettleBank";
	}

	getIcon(): string {
		return "book-open";
	}

	/** Mounts the React root into the sidebar container when the panel is opened. */
	async onOpen(): Promise<void> {
		const container = this.containerEl.children[1];
		container.empty();

		const mountPoint = container.createEl("div", {
			attr: { id: "zettlebank-root" },
		});

		this.root = createRoot(mountPoint);
		this.render();
	}

	/** Transitions the sidebar to a new state and triggers a React re-render. */
	setSidebarState(next: SidebarState): void {
		this.state = next;
		this.render();
	}

	/** Re-renders the React tree into the sidebar root with the current state. */
	private render(): void {
		if (!this.root) return;
		this.root.render(
			createElement(
				StrictMode,
				null,
				createElement(ZettleBankSidebar, {
					state: this.state,
					onAnalyze: () => this.plugin.analyzeActiveNote(),
				})
			)
		);
	}

	/** Unmounts the React root when the sidebar panel is closed. */
	async onClose(): Promise<void> {
		this.root?.unmount();
		this.root = null;
	}
}

// ---------------------------------------------------------------------------
// Settings Tab
// ---------------------------------------------------------------------------

class ZettleBankSettingTab extends PluginSettingTab {
	plugin: ZettleBankPlugin;

	constructor(app: App, plugin: ZettleBankPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	/** Renders the backend URL input field in Obsidian's Settings panel. */
	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		new Setting(containerEl)
			.setName("Backend URL")
			.setDesc(
				"Address of the ZettleBank FastAPI server. Change this if you use a non-default port or a remote host."
			)
			.addText((text) =>
				text
					.setPlaceholder(DEFAULT_SETTINGS.backendUrl)
					.setValue(this.plugin.settings.backendUrl)
					.onChange(async (value) => {
						this.plugin.settings.backendUrl =
							value.trim() || DEFAULT_SETTINGS.backendUrl;
						await this.plugin.saveSettings();
					})
			);
	}
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

export default class ZettleBankPlugin extends Plugin {
	settings: ZettleBankSettings = { ...DEFAULT_SETTINGS };
	private view: ZettleBankView | null = null;
	private _suppressedPaths = new Set<string>();
	private _pendingSync     = new Map<string, ReturnType<typeof setTimeout>>();
	/**
	 * Tracks the djb2 hash of each note's body (frontmatter stripped) as of
	 * its last analysis. Used to distinguish body edits (→ re-analyze) from
	 * pure frontmatter edits (→ graph sync only).
	 */
	private _bodyHashes      = new Map<string, string>();

	private _suppressSyncFor(path: string): void {
		this._suppressedPaths.add(path);
		setTimeout(() => this._suppressedPaths.delete(path), 3000);
	}

	/** Registers the sidebar view, ribbon icon, command palette entry, and settings tab. */
	async onload(): Promise<void> {
		await this.loadSettings();

		this.registerView(VIEW_TYPE, (leaf) => {
			this.view = new ZettleBankView(leaf, this);
			return this.view;
		});

		this.addRibbonIcon("book-open", "ZettleBank", () => {
			this.activateView();
		});

		this.addCommand({
			id: "analyze-current-note",
			name: "Analyze current note",
			callback: () => this.analyzeActiveNote(),
		});

		this.addSettingTab(new ZettleBankSettingTab(this.app, this));

		// Vault watchers: auto-analyze new notes and sync frontmatter edits back to graph.
		// Both create and modify route through _scheduleFileChange with different debounce
		// windows. The suppress set prevents a post-write modify from re-triggering analysis.
		const scheduleChange = (file: TFile, debounceMs: number) => {
			if (!(file instanceof TFile)) return;
			if (file.extension !== "md") return;
			if (file.path.startsWith(".obsidian/")) return;
			if (this._suppressedPaths.has(file.path)) return;

			const existing = this._pendingSync.get(file.path);
			if (existing) clearTimeout(existing);

			const timer = setTimeout(async () => {
				this._pendingSync.delete(file.path);
				try {
					await this._handleFileChange(file);
				} catch (err) {
					console.warn("[ZettleBank] background change handler failed:", file.path, err);
				}
			}, debounceMs);

			this._pendingSync.set(file.path, timer);
		};

		// create — longer debounce because Obsidian fires the event before content
		// is fully committed to disk (e.g. template insertion, drag-drop)
		this.registerEvent(
			this.app.vault.on("create", (file: TFile) => scheduleChange(file, 2500))
		);

		// modify — standard debounce; fires on every keystroke inside Obsidian
		this.registerEvent(
			this.app.vault.on("modify", (file: TFile) => scheduleChange(file, 1500))
		);
	}

	/** Reads persisted data from Obsidian's store, falling back to `DEFAULT_SETTINGS` for any missing keys. */
	async loadSettings(): Promise<void> {
		this.settings = Object.assign(
			{},
			DEFAULT_SETTINGS,
			await this.loadData()
		);
	}

	/** Persists current settings to Obsidian's data store. */
	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}

	/** Opens the ZettleBank sidebar in the right panel, or focuses it if already open. */
	async activateView(): Promise<void> {
		const { workspace } = this.app;

		let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
		if (!leaf) {
			const rightLeaf = workspace.getRightLeaf(false);
			if (!rightLeaf) return;
			leaf = rightLeaf;
			await leaf.setViewState({ type: VIEW_TYPE, active: true });
		}
		workspace.revealLeaf(leaf);
	}

	/**
	 * Central routing method called by both the create and modify vault watchers.
	 *
	 * Decision tree:
	 *   • No plugin frontmatter (no `updated` field) + body ≥ 300 words → auto-analyze
	 *   • Has plugin frontmatter, first time seen since plugin load:
	 *       – mtime > updated + 60 s → body likely changed → auto-analyze
	 *       – otherwise             → metadata edit only  → graph sync
	 *   • Has plugin frontmatter, hash tracked:
	 *       – body hash changed  → auto-analyze
	 *       – body hash same     → frontmatter edit only → graph sync
	 */
	private async _handleFileChange(file: TFile): Promise<void> {
		const content = await this.app.vault.cachedRead(file);
		const body    = extractBody(content);
		const newHash = simpleHash(body);
		const words   = wordCount(body);

		const cache = this.app.metadataCache.getFileCache(file);
		const fm    = cache?.frontmatter;

		// `updated` is written by this plugin on every analysis — its presence
		// indicates the note has been through the pipeline at least once.
		const hasPluginFrontmatter = typeof fm?.updated === "string";

		const prevHash = this._bodyHashes.get(file.path);

		// ── Branch 1: no plugin frontmatter ─────────────────────────────────
		if (!hasPluginFrontmatter) {
			if (words >= 300) {
				this._bodyHashes.set(file.path, newHash);
				await this._autoAnalyzeFile(file, content);
			}
			// Below threshold: do nothing — wait for more content
			return;
		}

		// ── Branch 2: plugin frontmatter present, first seen this session ───
		if (prevHash === undefined) {
			this._bodyHashes.set(file.path, newHash);

			const lastUpdated = new Date(fm.updated as string).getTime();
			const staleness   = file.stat.mtime - lastUpdated; // ms

			if (words >= 300 && staleness > 60_000) {
				// File was edited more than 60 s after last analysis → re-analyze
				await this._autoAnalyzeFile(file, content);
			} else {
				// Freshly opened or only frontmatter touched → sync metadata
				await syncNoteToGraph(this.app, file, this.settings.backendUrl);
			}
			return;
		}

		// ── Branch 3: hash already known ────────────────────────────────────
		if (newHash !== prevHash) {
			// Body changed
			this._bodyHashes.set(file.path, newHash);
			if (words >= 300) {
				await this._autoAnalyzeFile(file, content);
			} else {
				// Dropped below threshold (e.g. user deleted content) → sync only
				await syncNoteToGraph(this.app, file, this.settings.backendUrl);
			}
		} else {
			// Body unchanged — only frontmatter was edited → sync to graph
			await syncNoteToGraph(this.app, file, this.settings.backendUrl);
		}
	}

	/**
	 * Runs the full analysis pipeline on `file` in the background (no loading
	 * spinner). Writes frontmatter immediately on success and updates the
	 * sidebar if the file happens to be the active one.
	 *
	 * Errors are logged but not surfaced to the user — background analysis is
	 * best-effort. The user can always trigger manual analysis to retry.
	 */
	private async _autoAnalyzeFile(file: TFile, content?: string): Promise<void> {
		try {
			const text = content ?? await this.app.vault.cachedRead(file);
			const response = await analyzeNote(
				slugify(file.basename),
				text.slice(0, 50000),
				this.settings.backendUrl
			);

			this._suppressSyncFor(file.path);
			writeFrontmatter(this.app, file, {
				metadata:     response.metadata,
				community_id: response.community_id,
			});

			// Keep hash in sync so the next modify doesn't re-trigger analysis
			this._bodyHashes.set(file.path, simpleHash(extractBody(text)));

			// Update sidebar only when this is the currently viewed note
			if (this.app.workspace.getActiveFile()?.path === file.path) {
				this.view?.setSidebarState({ phase: "results", data: response });
			}
		} catch (err) {
			console.warn("[ZettleBank] auto-analysis failed:", file.path, err);
		}
	}

	/**
	 * Reads the active note and sends it to the backend for analysis.
	 * Transitions the sidebar: idle/error → loading → results (on success) or error (on failure).
	 * Auto-writes frontmatter immediately after a successful analysis.
	 */
	async analyzeActiveNote(): Promise<void> {
		const file = this.app.workspace.getActiveFile();
		if (!file) return;

		this.view?.setSidebarState({ phase: "loading" });

		try {
			const content  = await this.app.vault.cachedRead(file);
			const response = await analyzeNote(
				slugify(file.basename),
				content.slice(0, 50000),
				this.settings.backendUrl
			);

			// Auto-write: suppress watcher for this file, then write frontmatter
			this._suppressSyncFor(file.path);
			writeFrontmatter(this.app, file, {
				metadata:     response.metadata,
				community_id: response.community_id,
			});

			// Keep hash in sync so the next modify event doesn't re-trigger analysis
			this._bodyHashes.set(file.path, simpleHash(extractBody(content)));

			this.view?.setSidebarState({ phase: "results", data: response });
		} catch (err) {
			const message = err instanceof Error ? err.message : "Analysis failed";
			console.error("[ZettleBank] analysis failed:", err);
			this.view?.setSidebarState({ phase: "error", message });
		}
	}

	/** Detaches all open sidebar leaves when the plugin is disabled or unloaded. */
	async onunload(): Promise<void> {
		for (const timer of this._pendingSync.values()) {
			clearTimeout(timer);
		}
		this._pendingSync.clear();
		this._bodyHashes.clear();
		this.app.workspace.detachLeavesOfType(VIEW_TYPE);
	}
}
