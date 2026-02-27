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
	type AnalyzeResponse,
	type NarrativeMetadata,
	type SmartRelation,
} from "./schema";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIEW_TYPE = "zettlebank-sidebar";

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

		// 3. Merge smart_relations by link::type key (Shadow Database, ADR-003)
		const existingRels: SmartRelation[] =
			frontmatter.smart_relations || [];
		const relMap = new Map(
			existingRels.map((r) => [`${r.link}::${r.type}`, r])
		);
		for (const rel of approved.metadata.smart_relations) {
			relMap.set(`${rel.link}::${rel.type}`, rel);
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
// React Sidebar Mounting (architecture.md – custom ItemView)
// ---------------------------------------------------------------------------

type SidebarState =
	| { phase: "idle" }
	| { phase: "loading" }
	| { phase: "staging"; data: AnalyzeResponse }
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
					onApprove: (p) => this.plugin.approveAndWrite(p),
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
	 * Reads the active note and sends it to the backend for analysis.
	 * Transitions the sidebar: idle/error → loading → staging (on success) or error (on failure).
	 */
	async analyzeActiveNote(): Promise<void> {
		const file = this.app.workspace.getActiveFile();
		if (!file) return;

		this.view?.setSidebarState({ phase: "loading" });

		try {
			const content = await this.app.vault.cachedRead(file);
			const response = await analyzeNote(
				file.basename,
				content,
				this.settings.backendUrl
			);
			// Stage for user approval — do NOT write to frontmatter yet
			this.view?.setSidebarState({ phase: "staging", data: response });
		} catch (err) {
			const message =
				err instanceof Error ? err.message : "Analysis failed";
			console.error("ZettleBank analysis failed:", err);
			this.view?.setSidebarState({ phase: "error", message });
		}
	}

	/** Writes the user-approved payload to the active note's frontmatter and resets the sidebar to idle. */
	async approveAndWrite(approved: ApprovedPayload): Promise<void> {
		const file = this.app.workspace.getActiveFile();
		if (!file) return;

		// Safe Frontmatter Manipulation: only after explicit user approval
		writeFrontmatter(this.app, file, approved);
		this.view?.setSidebarState({ phase: "idle" });
	}

	/** Detaches all open sidebar leaves when the plugin is disabled or unloaded. */
	async onunload(): Promise<void> {
		this.app.workspace.detachLeavesOfType(VIEW_TYPE);
	}
}
