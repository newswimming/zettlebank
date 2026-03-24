import { useState, useCallback, useEffect, type FC } from "react";
import type { ApprovedPayload } from "./main";
import type {
	AnalyzeResponse,
	SmartRelation,
	NarrativeMetadata,
	RelationType,
} from "./schema";
import { validateGenerateArcResponse } from "./schema";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type SidebarState =
	| { phase: "idle" }
	| { phase: "loading" }
	| { phase: "staging"; data: AnalyzeResponse }
	| { phase: "error"; message: string };

interface SidebarProps {
	state: SidebarState;
	onAnalyze: () => void;
	onApprove: (payload: ApprovedPayload) => void;
}

// ---------------------------------------------------------------------------
// Relation type → display color (controlled vocabulary, architecture.md)
// ---------------------------------------------------------------------------

const RELATION_COLORS: Record<RelationType, string> = {
	contradicts: "var(--text-error)",
	supports: "var(--text-success)",
	potential_to: "var(--text-faint)",
	kinetic_to: "var(--text-accent)",
	motivates: "var(--text-success)",
	hinders: "var(--text-error)",
	related: "var(--text-muted)",
};

// ---------------------------------------------------------------------------
// Narrative act configuration
// ---------------------------------------------------------------------------

const ACT_CONFIG = {
	ki:    { label: "Ki",    kanji: "起", sub: "Introduction", color: "#34d399" },
	sho:   { label: "Sho",   kanji: "承", sub: "Development",  color: "#60a5fa" },
	ten:   { label: "Ten",   kanji: "転", sub: "Twist",        color: "#f59e0b" },
	ketsu: { label: "Ketsu", kanji: "結", sub: "Resolution",   color: "#c084fc" },
} as const;

type Act = keyof typeof ACT_CONFIG;
const ACTS: Act[] = ["ki", "sho", "ten", "ketsu"];

// ---------------------------------------------------------------------------
// useHealth — pings /health on mount, surfaces ollama_alive
// ---------------------------------------------------------------------------

function useHealth(): boolean {
	const [ollamaAlive, setOllamaAlive] = useState(true);

	useEffect(() => {
		fetch("http://localhost:8000/health", {
			signal: AbortSignal.timeout(4000),
		})
			.then((r) => r.json())
			.then((d) => {
				if (d.ollama_alive === false) setOllamaAlive(false);
			})
			.catch(() => {
				// Server unreachable — leave enabled, generation will surface the error
			});
	}, []);

	return ollamaAlive;
}

// ---------------------------------------------------------------------------
// Tag Toggle Card
//
// Each tag gets an interactive chip the user can toggle on/off before
// approving. Tags use prefix/value format (topic/, aspect/, affect/, code/).
// ---------------------------------------------------------------------------

const TagToggle: FC<{
	value: string;
	accepted: boolean;
	onToggle: () => void;
}> = ({ value, accepted, onToggle }) => (
	<button
		className={`zettlebank-tag-toggle ${accepted ? "is-accepted" : "is-rejected"}`}
		onClick={onToggle}
		type="button"
	>
		{value}
	</button>
);

// ---------------------------------------------------------------------------
// Tag Group Card — groups tags by prefix (topic/, aspect/, affect/, code/)
// ---------------------------------------------------------------------------

const TAG_PREFIXES = ["topic", "aspect", "affect", "code"] as const;

function groupTagsByPrefix(tags: string[]): Record<string, string[]> {
	const groups: Record<string, string[]> = {};
	for (const prefix of TAG_PREFIXES) {
		groups[prefix] = [];
	}
	for (const tag of tags) {
		const slash = tag.indexOf("/");
		if (slash === -1) continue;
		const prefix = tag.slice(0, slash);
		if (prefix in groups) {
			groups[prefix].push(tag);
		}
	}
	return groups;
}

const TagGroupCard: FC<{
	prefix: string;
	tags: string[];
	accepted: Set<string>;
	onToggle: (tag: string) => void;
}> = ({ prefix, tags, accepted, onToggle }) => (
	<div className="zettlebank-card">
		<h4 className="zettlebank-card-label">{prefix}/</h4>
		{tags.length > 0 ? (
			<div className="zettlebank-tags">
				{tags.map((t) => (
					<TagToggle
						key={t}
						value={t}
						accepted={accepted.has(t)}
						onToggle={() => onToggle(t)}
					/>
				))}
			</div>
		) : (
			<span className="zettlebank-empty">None detected</span>
		)}
	</div>
);

// ---------------------------------------------------------------------------
// Description Card — editable text field
// ---------------------------------------------------------------------------

const DescriptionCard: FC<{
	value: string;
	onChange: (next: string) => void;
}> = ({ value, onChange }) => (
	<div className="zettlebank-card">
		<h4 className="zettlebank-card-label">Description</h4>
		<textarea
			className="zettlebank-description-input"
			value={value}
			onChange={(e) => onChange(e.target.value)}
			rows={3}
			placeholder="One-line summary..."
		/>
	</div>
);

// ---------------------------------------------------------------------------
// Relation Card — accept / reject each suggested edge
// ---------------------------------------------------------------------------

const RelationCard: FC<{
	rel: SmartRelation;
	accepted: boolean;
	onToggle: () => void;
}> = ({ rel, accepted, onToggle }) => (
	<div
		className={`zettlebank-relation-card ${accepted ? "is-accepted" : "is-rejected"}`}
		onClick={onToggle}
	>
		<span
			className="zettlebank-relation-type"
			style={{ color: RELATION_COLORS[rel.type] }}
		>
			{rel.type}
		</span>
		<span className="zettlebank-relation-target">{rel.link}</span>
		<span className="zettlebank-relation-confidence">
			{(rel.confidence * 100).toFixed(0)}%
		</span>
		<span className="zettlebank-relation-status">
			{accepted ? "accepted" : "rejected"}
		</span>
	</div>
);

// ---------------------------------------------------------------------------
// Staging Area — the full approval interface
//
// The user reviews AI suggestions as interactive cards, toggles individual
// tags / relations, edits the description, then hits "Approve & Write".
// Nothing touches frontmatter until the user confirms.
// ---------------------------------------------------------------------------

const StagingArea: FC<{
	data: AnalyzeResponse;
	onApprove: (payload: ApprovedPayload) => void;
	onReanalyze: () => void;
}> = ({ data, onApprove, onReanalyze }) => {
	// -- Tag toggles (all accepted by default) --
	const [acceptedTags, setAcceptedTags] = useState<Set<string>>(
		() => new Set(data.metadata.tags)
	);

	const toggleTag = useCallback((tag: string) => {
		setAcceptedTags((prev) => {
			const next = new Set(prev);
			if (next.has(tag)) next.delete(tag);
			else next.add(tag);
			return next;
		});
	}, []);

	const tagGroups = groupTagsByPrefix(data.metadata.tags);

	// -- Description editing --
	const [description, setDescription] = useState(
		data.metadata.description ?? ""
	);

	// -- Relation toggles (all accepted by default) --
	const [acceptedRels, setAcceptedRels] = useState<Set<string>>(
		() =>
			new Set(
				data.metadata.smart_relations.map(
					(r) => `${r.link}::${r.type}`
				)
			)
	);

	const toggleRelation = useCallback((key: string) => {
		setAcceptedRels((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key);
			else next.add(key);
			return next;
		});
	}, []);

	// -- Build ApprovedPayload and hand to plugin --
	const handleApprove = useCallback(() => {
		const approvedRelations = data.metadata.smart_relations.filter((r) =>
			acceptedRels.has(`${r.link}::${r.type}`)
		);

		const metadata: NarrativeMetadata = {
			aliases: data.metadata.aliases,
			description: description.trim() || null,
			tags: Array.from(acceptedTags),
			smart_relations: approvedRelations,
			source: data.metadata.source,
			citationID: data.metadata.citationID,
		};

		onApprove({
			metadata,
			community_id: data.community_id,
		});
	}, [data, acceptedTags, description, acceptedRels, onApprove]);

	return (
		<div className="zettlebank-staging">
			{/* Description card */}
			<DescriptionCard value={description} onChange={setDescription} />

			{/* Tag group cards (topic/, aspect/, affect/, code/) */}
			{TAG_PREFIXES.map((prefix) => (
				<TagGroupCard
					key={prefix}
					prefix={prefix}
					tags={tagGroups[prefix] ?? []}
					accepted={acceptedTags}
					onToggle={toggleTag}
				/>
			))}

			{/* Smart relations */}
			{data.metadata.smart_relations.length > 0 && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">Relations</h4>
					{data.metadata.smart_relations.map((rel) => {
						const key = `${rel.link}::${rel.type}`;
						return (
							<RelationCard
								key={key}
								rel={rel}
								accepted={acceptedRels.has(key)}
								onToggle={() => toggleRelation(key)}
							/>
						);
					})}
				</div>
			)}

			{/* Community badge */}
			{data.community_id !== null && (
				<div className="zettlebank-community">
					<span className="zettlebank-community-label">Community</span>
					<span className="zettlebank-community-id">
						{data.community_id}
					</span>
				</div>
			)}

			{/* Action bar */}
			<div className="zettlebank-actions">
				<button
					className="zettlebank-btn-approve"
					onClick={handleApprove}
					type="button"
				>
					Approve &amp; Write
				</button>
				<button
					className="zettlebank-btn-secondary"
					onClick={onReanalyze}
					type="button"
				>
					Re-analyze
				</button>
			</div>
		</div>
	);
};

// ---------------------------------------------------------------------------
// NarrativeArcPanel — 4-act beat generator
// ---------------------------------------------------------------------------

const NarrativeArcPanel: FC<{ ollamaAlive: boolean }> = ({ ollamaAlive }) => {
	const [beats, setBeats] = useState<Record<Act, string>>({
		ki: "", sho: "", ten: "", ketsu: "",
	});
	const [locked, setLocked] = useState<Record<Act, boolean>>({
		ki: false, sho: false, ten: false, ketsu: false,
	});
	const [loading, setLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);

	const toggleLock = (act: Act) => {
		if (loading) return;
		setLocked((prev) => ({ ...prev, [act]: !prev[act] }));
	};

	const generate = async () => {
		if (!ollamaAlive || loading) return;
		setLoading(true);
		setError(null);

		const locked_acts = ACTS.filter((a) => locked[a]);

		try {
			const resp = await fetch(
				"http://localhost:8000/graph/generate-arc",
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ locked_acts }),
					signal: AbortSignal.timeout(180_000),
				}
			);
			if (!resp.ok) throw new Error(`Server error ${resp.status}`);
			const data = validateGenerateArcResponse(await resp.json());

			setBeats((prev) => ({
				ki:    locked.ki    ? prev.ki    : data.ki    || prev.ki,
				sho:   locked.sho   ? prev.sho   : data.sho   || prev.sho,
				ten:   locked.ten   ? prev.ten   : data.ten   || prev.ten,
				ketsu: locked.ketsu ? prev.ketsu : data.ketsu || prev.ketsu,
			}));
		} catch (e) {
			setError(
				e instanceof Error ? e.message : "Generation failed."
			);
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="zettlebank-staging">
			{ACTS.map((act) => {
				const cfg = ACT_CONFIG[act];
				const isLocked = locked[act];
				const beat = beats[act];
				const isPending = loading && !isLocked;

				return (
					<div
						key={act}
						className="zettlebank-card"
						onClick={() => toggleLock(act)}
						style={{
							cursor: loading ? "default" : "pointer",
							borderColor: isLocked ? cfg.color : undefined,
							boxShadow: isLocked
								? `0 0 0 1px ${cfg.color}55, inset 0 0 14px ${cfg.color}0d`
								: undefined,
							background: isLocked ? "#13161f" : undefined,
							transition:
								"border-color 0.18s, box-shadow 0.18s, background 0.18s",
						}}
					>
						{/* Block header */}
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "7px",
								marginBottom: "6px",
							}}
						>
							<span
								style={{
									width: 8,
									height: 8,
									borderRadius: "50%",
									background: cfg.color,
									flexShrink: 0,
									display: "inline-block",
								}}
							/>
							<span
								style={{
									flex: 1,
									fontSize: "11px",
									fontWeight: 700,
									color: "var(--text-normal)",
									letterSpacing: "0.02em",
								}}
							>
								{cfg.label}{" "}
								<span
									style={{
										color: "var(--text-faint)",
										fontWeight: 400,
									}}
								>
									{cfg.kanji} · {cfg.sub}
								</span>
							</span>
							<span style={{ fontSize: "11px", opacity: 0.75 }}>
								{isLocked ? "🔒" : "🔓"}
							</span>
						</div>

						{/* Beat text */}
						<p
							style={{
								fontSize: "11px",
								lineHeight: 1.6,
								margin: 0,
								paddingLeft: "15px",
								color: beat
									? "var(--text-muted)"
									: "var(--text-faint)",
								fontStyle: beat ? "normal" : "italic",
								opacity: isPending ? 0.4 : 1,
								transition: "opacity 0.2s",
							}}
						>
							{isPending
								? "· · ·"
								: beat || "Click generate to draft..."}
						</p>
					</div>
				);
			})}

			{/* Error state */}
			{error && (
				<div
					className="zettlebank-error"
					style={{ marginBottom: "8px" }}
				>
					<p>{error}</p>
				</div>
			)}

			{/* Generate button */}
			<div className="zettlebank-actions">
				<button
					className="zettlebank-btn-approve"
					onClick={generate}
					disabled={!ollamaAlive || loading}
					type="button"
					style={{ width: "100%" }}
				>
					{loading ? "Drafting..." : "Generate Arc"}
				</button>
			</div>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Root sidebar component
// ---------------------------------------------------------------------------

export const ZettleBankSidebar: FC<SidebarProps> = ({
	state,
	onAnalyze,
	onApprove,
}) => {
	const ollamaAlive = useHealth();
	const [activeTab, setActiveTab] = useState<"analysis" | "arc">(
		"analysis"
	);

	const tabStyle = (tab: "analysis" | "arc"): React.CSSProperties => ({
		flex: 1,
		padding: "8px 0",
		background: "none",
		border: "none",
		borderBottom:
			activeTab === tab
				? "2px solid var(--interactive-accent)"
				: "2px solid transparent",
		color:
			activeTab === tab
				? "var(--text-normal)"
				: "var(--text-faint)",
		cursor: "pointer",
		fontSize: "12px",
		fontWeight: activeTab === tab ? 600 : 400,
		transition: "color 0.15s, border-color 0.15s",
	});

	return (
		<div className="zettlebank-container">
			{/* Header */}
			<div className="zettlebank-header">
				<h3>ZettleBank</h3>
				{activeTab === "analysis" &&
					state.phase !== "loading" &&
					state.phase !== "staging" && (
						<button
							className="zettlebank-analyze-btn"
							onClick={onAnalyze}
							type="button"
						>
							Analyze
						</button>
					)}
			</div>

			{/* Tab navigation */}
			<div
				style={{
					display: "flex",
					borderBottom:
						"1px solid var(--background-modifier-border)",
					padding: "0 12px",
					flexShrink: 0,
				}}
			>
				<button
					type="button"
					onClick={() => setActiveTab("analysis")}
					style={tabStyle("analysis")}
				>
					Analysis
				</button>
				<button
					type="button"
					onClick={() => setActiveTab("arc")}
					style={tabStyle("arc")}
				>
					Arc Generator
				</button>
			</div>

			{/* Ollama offline warning */}
			{!ollamaAlive && (
				<div
					style={{
						margin: "8px 12px 0",
						padding: "7px 9px",
						background: "var(--background-modifier-error)",
						border: "1px solid var(--text-error)",
						borderRadius: "5px",
						fontSize: "11px",
						color: "var(--text-error)",
						display: "flex",
						gap: "5px",
						alignItems: "flex-start",
						lineHeight: 1.5,
					}}
				>
					<span style={{ flexShrink: 0 }}>⚠️</span>
					<span>
						Ollama is offline. Arc generation is disabled.
					</span>
				</div>
			)}

			{/* Tab content */}
			{activeTab === "analysis" && (
				<>
					{state.phase === "idle" && (
						<div className="zettlebank-empty-state">
							<p>
								Select a note and click Analyze to extract
								narrative metadata.
							</p>
						</div>
					)}

					{state.phase === "loading" && (
						<div className="zettlebank-loading">
							Analyzing note...
						</div>
					)}

					{state.phase === "error" && (
						<div className="zettlebank-error">
							<p>{state.message}</p>
							<button
								className="zettlebank-btn-secondary"
								onClick={onAnalyze}
								type="button"
							>
								Retry
							</button>
						</div>
					)}

					{state.phase === "staging" && (
						<StagingArea
							data={state.data}
							onApprove={onApprove}
							onReanalyze={onAnalyze}
						/>
					)}
				</>
			)}

			{activeTab === "arc" && (
				<NarrativeArcPanel ollamaAlive={ollamaAlive} />
			)}
		</div>
	);
};
