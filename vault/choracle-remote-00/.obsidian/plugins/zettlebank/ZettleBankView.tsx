import React, { useState, useEffect, type FC } from "react";
import type {
	AnalyzeResponse,
	EdgeMatrix,
	NarrativeAudit,
	RelationType,
	StructuralHole,
	CommunityTier,
} from "./schema";
import { validateGenerateArcResponse } from "./schema";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type SidebarState =
	| { phase: "idle" }
	| { phase: "loading" }
	| { phase: "results"; data: AnalyzeResponse }
	| { phase: "error"; message: string };

interface SidebarProps {
	state: SidebarState;
	onAnalyze: () => void;
}

// ---------------------------------------------------------------------------
// Display maps
// ---------------------------------------------------------------------------

const RELATION_COLORS: Record<RelationType, string> = {
	contradicts:  "var(--text-error)",
	supports:     "var(--text-success)",
	potential_to: "var(--text-faint)",
	kinetic_to:   "var(--text-accent)",
	motivates:    "var(--text-success)",
	hinders:      "var(--text-error)",
	related:      "var(--text-muted)",
};

const ACT_LABELS: Record<string, { label: string; kanji: string }> = {
	ki:    { label: "Act 1: Ki · Introduction",  kanji: "起" },
	sho:   { label: "Act 2: Shō · Development",  kanji: "承" },
	ten:   { label: "Act 3: Ten · Pivot",         kanji: "転" },
	ketsu: { label: "Act 4: Ketsu · Resolution", kanji: "結" },
};

const ACT_SHORT: Record<string, string> = {
	ki: "起", sho: "承", ten: "転", ketsu: "結",
};

const ACT_ORDER: Record<string, number> = { ki: 1, sho: 2, ten: 3, ketsu: 4 };

const PROVENANCE_LABEL: Record<string, string> = {
	sc_embedding: "~ Embedding",
	wikilink:     "↗ Wiki-link",
	llm:          "✦ LLM",
};

// ---------------------------------------------------------------------------
// Arc Generator constants
// ---------------------------------------------------------------------------

const ARC_CFG = {
	ki:    { label: "Ki",    kanji: "起", sub: "Introduction", color: "#34d399" },
	sho:   { label: "Sho",   kanji: "承", sub: "Development",  color: "#60a5fa" },
	ten:   { label: "Ten",   kanji: "転", sub: "Twist",        color: "#f59e0b" },
	ketsu: { label: "Ketsu", kanji: "結", sub: "Resolution",   color: "#c084fc" },
} as const;

type ArcAct = keyof typeof ARC_CFG;
const ARC_ACTS: ArcAct[] = ["ki", "sho", "ten", "ketsu"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Groups tags by prefix. affect/ is excluded — rendered as ValenceBadge. */
function groupTagsByPrefix(tags: string[]): Record<string, string[]> {
	const groups: Record<string, string[]> = { topic: [], aspect: [], code: [] };
	for (const tag of tags) {
		const slash = tag.indexOf("/");
		if (slash === -1) continue;
		const prefix = tag.slice(0, slash);
		if (prefix in groups) groups[prefix].push(tag);
	}
	return groups;
}

/** Forward = later in Ki→Shō→Ten→Ketsu. Regression = earlier. Lateral = same. */
function getMomentumVector(fromAct: string, toAct: string): { arrow: string; label: string } {
	const from = ACT_ORDER[fromAct] ?? 0;
	const to   = ACT_ORDER[toAct]   ?? 0;
	if (from < to) return { arrow: "➔", label: "Forward" };
	if (from > to) return { arrow: "⟲", label: "Regression" };
	return { arrow: "↔", label: "Lateral" };
}

// ---------------------------------------------------------------------------
// Shared micro-styles
// ---------------------------------------------------------------------------

const LABEL_STYLE: React.CSSProperties = {
	fontSize: "var(--font-ui-smaller)",
	color: "var(--text-muted)",
	textTransform: "uppercase",
	letterSpacing: "0.05em",
	flexShrink: 0,
};

const CHIP_STYLE: React.CSSProperties = {
	display: "inline-block",
	padding: "1px 7px",
	borderRadius: "var(--radius-s)",
	border: "1px solid var(--background-modifier-border)",
	fontFamily: "var(--font-monospace)",
	fontSize: "var(--font-ui-smaller)",
	color: "var(--text-accent)",
	lineHeight: 1.5,
};

// ---------------------------------------------------------------------------
// Emotional Valence Badge
// ---------------------------------------------------------------------------

const VALENCE_STYLES: Record<string, { color: string; bg: string; label: string }> = {
	positive: { color: "var(--color-green, #3dba6f)", bg: "rgba(61,186,111,0.12)",  label: "Positive"    },
	negative: { color: "var(--color-red,  #e05252)",  bg: "rgba(224,82,82,0.12)",   label: "Negative"    },
	mu:       { color: "var(--text-muted)",            bg: "var(--background-modifier-hover)", label: "Mu (Neutral)" },
};

const ValenceBadge: FC<{
	affectTag: string | undefined;
}> = ({ affectTag }) => {
	if (!affectTag) return null;
	const value = affectTag.slice("affect/".length);
	const style = VALENCE_STYLES[value] ?? VALENCE_STYLES["mu"];
	return (
		<div
			style={{
				display: "flex", alignItems: "center", gap: "8px",
				width: "100%", padding: "8px 12px", boxSizing: "border-box",
				borderRadius: "var(--radius-m)", border: `1px solid ${style.color}`,
				background: style.bg,
			}}
		>
			<span style={LABEL_STYLE}>Emotional Valence</span>
			<span style={{ fontWeight: 700, fontSize: "var(--font-ui-medium)", color: style.color }}>
				{style.label}
			</span>
			<span style={{ marginLeft: "auto", fontFamily: "var(--font-monospace)", fontSize: "var(--font-ui-smaller)", color: "var(--text-faint)" }}>
				{affectTag}
			</span>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Structural Analysis Card  (act, communities, Burt constraint — no pivot section)
// ---------------------------------------------------------------------------

const StructuralCard: FC<{
	narrativeAct: string;
	communityTiers: CommunityTier[];
	structuralHole: StructuralHole;
}> = ({ narrativeAct, communityTiers, structuralHole }) => {
	const act   = ACT_LABELS[narrativeAct] ?? { label: narrativeAct, kanji: "?" };
	const macro = communityTiers.find((t) => t.resolution === 0.5);
	const micro = communityTiers.find((t) => t.resolution === 2.0);

	return (
		<div className="zettlebank-card zettlebank-structural-card">
			<h4 className="zettlebank-card-label">Structural Analysis</h4>

			<div className="zettlebank-structural-row">
				<span className="zettlebank-structural-key">Narrative Act</span>
				<span className="zettlebank-structural-val">
					<span className="zettlebank-act-kanji">{act.kanji}</span>{" "}{act.label}
				</span>
			</div>

			{macro && (
				<div className="zettlebank-structural-row">
					<span className="zettlebank-structural-key">Macro (γ=0.5)</span>
					<span className="zettlebank-structural-val">{macro.label}</span>
				</div>
			)}

			{micro && (
				<div className="zettlebank-structural-row">
					<span className="zettlebank-structural-key">Micro (γ=2.0)</span>
					<span className="zettlebank-structural-val">{micro.label}</span>
				</div>
			)}

			<div className="zettlebank-structural-row">
				<span className="zettlebank-structural-key">Burt Constraint</span>
				<span className="zettlebank-structural-val">
					{structuralHole.constraint_score.toFixed(3)}
					{structuralHole.is_ten_candidate && (
						<span className="zettlebank-pivot-badge"> ⚠ High Pivot Potential</span>
					)}
				</span>
			</div>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Pivot Analysis Card  (shown whenever is_ten_candidate=true)
// ---------------------------------------------------------------------------

/**
 * Standalone card for Ten-pivot candidates. Shows pivot freedom (1 − constraint)
 * as a visual bar, the inferred beat position, and the LLM's bridge explanation
 * when the Narrative Auditor fired.
 */
const PivotAnalysisCard: FC<{
	structuralHole: StructuralHole;
	audit: NarrativeAudit | null;
}> = ({ structuralHole, audit }) => {
	// pivot_freedom = how much structural freedom this note has (0=none, 1=total)
	const freedom = Math.max(0, 1 - structuralHole.constraint_score);
	const freedomPct = (freedom * 100).toFixed(1);

	return (
		<div className="zettlebank-card" style={{ borderColor: "var(--text-accent)", borderWidth: "1px" }}>
			<h4 className="zettlebank-card-label" style={{ color: "var(--text-accent)" }}>
				転 Pivot Analysis
			</h4>

			{/* Pivot freedom bar */}
			<div style={{ marginBottom: "8px" }}>
				<div style={{ display: "flex", justifyContent: "space-between", marginBottom: "3px" }}>
					<span style={LABEL_STYLE}>Structural Freedom</span>
					<span style={{ fontSize: "var(--font-ui-smaller)", color: "var(--text-accent)", fontWeight: 600 }}>
						{freedomPct}%
					</span>
				</div>
				<div style={{
					height: "6px", borderRadius: "3px",
					background: "var(--background-modifier-border)",
					overflow: "hidden",
				}}>
					<div style={{
						height: "100%",
						width: `${freedomPct}%`,
						borderRadius: "3px",
						background: "var(--text-accent)",
						transition: "width 0.3s ease",
					}} />
				</div>
				<div style={{ display: "flex", justifyContent: "space-between", marginTop: "2px" }}>
					<span style={{ fontSize: "10px", color: "var(--text-faint)" }}>Constrained</span>
					<span style={{ fontSize: "10px", color: "var(--text-faint)" }}>Free Bridge</span>
				</div>
			</div>

			{/* Beat position from Narrative Auditor */}
			{audit?.beat_position && (
				<div className="zettlebank-structural-row">
					<span className="zettlebank-structural-key">Beat Position</span>
					<span className="zettlebank-structural-val" style={{ fontFamily: "var(--font-monospace)" }}>
						code/{audit.beat_position}
					</span>
				</div>
			)}

			{/* Narrative summary */}
			{audit?.narrative_summary && (
				<p className="zettlebank-bridge-summary" style={{ marginTop: "8px" }}>
					{audit.narrative_summary}
				</p>
			)}

			{/* Fallback when auditor hasn't fired yet */}
			{!audit && (
				<p style={{ fontSize: "var(--font-ui-smaller)", color: "var(--text-faint)", margin: "4px 0 0" }}>
					Narrative Auditor analysis pending — constraint score qualifies this note as a Ten-pivot candidate.
				</p>
			)}
		</div>
	);
};

// ---------------------------------------------------------------------------
// Bridge Network Card  (shown when bridge_detected=true)
// ---------------------------------------------------------------------------

/**
 * Shows the cross-community bridge network when bridge_detected=true.
 * Displays bridged note IDs as chips and the LLM's bridge explanation.
 */
const BridgeCard: FC<{
	audit: NarrativeAudit;
}> = ({ audit }) => (
	<div className="zettlebank-card" style={{ background: "var(--background-secondary)" }}>
		<h4 className="zettlebank-card-label">⬡ Bridge Network</h4>

		{audit.bridge_note_ids.length > 0 ? (
			<>
				<span style={{ ...LABEL_STYLE, display: "block", marginBottom: "6px" }}>
					Bridged Communities ({audit.bridge_note_ids.length} nodes)
				</span>
				<div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
					{audit.bridge_note_ids.map((id) => (
						<span key={id} style={CHIP_STYLE}>{id}</span>
					))}
				</div>
			</>
		) : (
			<span style={{ ...LABEL_STYLE, display: "block" }}>
				Bridge confirmed — no neighbour node IDs returned.
			</span>
		)}
	</div>
);

// ---------------------------------------------------------------------------
// Tag Toggle chip
// ---------------------------------------------------------------------------

const TagToggle: FC<{ value: string; accepted: boolean; onToggle: () => void }> = ({
	value, accepted, onToggle,
}) => (
	<button
		className={`zettlebank-tag-toggle ${accepted ? "is-accepted" : "is-rejected"}`}
		onClick={onToggle}
		type="button"
	>
		{value}
	</button>
);

// ---------------------------------------------------------------------------
// Tag Group Card  (topic/, aspect/, code/ — affect/ handled by ValenceBadge)
// ---------------------------------------------------------------------------

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
					<TagToggle key={t} value={t} accepted={accepted.has(t)} onToggle={() => onToggle(t)} />
				))}
			</div>
		) : (
			<span className="zettlebank-empty">None detected</span>
		)}
	</div>
);

// ---------------------------------------------------------------------------
// Relation Card — full EdgeMatrix data grid
// ---------------------------------------------------------------------------

/**
 * Full EdgeMatrix display in 4 rows:
 *  Row 1 (header, clickable): relation_type | confidence | accept status
 *  Row 2: provenance (icon + text label)  |  target narrative act (kanji + label)
 *  Row 3: Momentum Vector  [CurrentAct] arrow [TargetAct] (Direction)
 *  Row 4: editable target_id input
 */
const RelationCard: FC<{
	rel: EdgeMatrix;
	currentAct: string;
	accepted: boolean;
	editedTargetId: string;
	onToggle: () => void;
	onTargetChange: (next: string) => void;
}> = ({ rel, currentAct, accepted, editedTargetId, onToggle, onTargetChange }) => {
	const momentum    = getMomentumVector(currentAct, rel.narrative_act);
	const fromShort   = ACT_SHORT[currentAct]        ?? currentAct;
	const toShort     = ACT_SHORT[rel.narrative_act] ?? rel.narrative_act;
	const targetAct   = ACT_LABELS[rel.narrative_act];
	const provenLabel = PROVENANCE_LABEL[rel.provenance] ?? rel.provenance;

	return (
		<div className={`zettlebank-relation-card ${accepted ? "is-accepted" : "is-rejected"}`}>

			{/* Row 1: relation type | confidence | status — click to toggle */}
			<div className="zettlebank-relation-header" onClick={onToggle}>
				<span
					className="zettlebank-relation-type"
					style={{ color: RELATION_COLORS[rel.relation_type] }}
				>
					{rel.relation_type}
				</span>
				<span className="zettlebank-relation-confidence">
					{(rel.confidence * 100).toFixed(0)}%
				</span>
				<span className="zettlebank-relation-status">
					{accepted ? "accepted" : "rejected"}
				</span>
			</div>

			{/* Row 2: provenance  |  target narrative act */}
			<div style={{
				display: "grid", gridTemplateColumns: "1fr 1fr",
				gap: "4px", padding: "4px 0",
				fontSize: "var(--font-ui-smaller)",
				borderBottom: "1px solid var(--background-modifier-border)",
			}}>
				<div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
					<span style={{ ...LABEL_STYLE, fontSize: "10px" }}>Provenance</span>
					<span style={{ color: "var(--text-normal)", fontFamily: "var(--font-monospace)" }}>
						{provenLabel}
					</span>
				</div>
				<div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
					<span style={{ ...LABEL_STYLE, fontSize: "10px" }}>Target Act</span>
					<span style={{ color: "var(--text-normal)" }}>
						{targetAct
							? <><span className="zettlebank-act-kanji">{targetAct.kanji}</span>{" "}{targetAct.label}</>
							: rel.narrative_act
						}
					</span>
				</div>
			</div>

			{/* Row 3: Momentum Vector */}
			<div style={{
				display: "flex", alignItems: "center", gap: "4px",
				padding: "4px 0", fontSize: "var(--font-ui-smaller)",
			}}>
				<span style={{ ...LABEL_STYLE, fontSize: "10px" }}>Momentum</span>
				<span className="zettlebank-edge-act">[{fromShort}]</span>
				<span style={{ color: "var(--text-accent)", fontWeight: 600 }}>{momentum.arrow}</span>
				<span className="zettlebank-edge-act">[{toShort}]</span>
				<span style={{ color: "var(--text-faint)" }}>({momentum.label})</span>
			</div>

			{/* Row 4: editable target_id */}
			<input
				className="zettlebank-relation-link-input"
				type="text"
				value={editedTargetId}
				onChange={(e) => onTargetChange(e.target.value)}
				placeholder="Target note id"
				onClick={(e) => e.stopPropagation()}
			/>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Description Card
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
// NarrativeArcPanel — 4-act beat generator
// ---------------------------------------------------------------------------

const NarrativeArcPanel: FC<{ ollamaAlive: boolean }> = ({ ollamaAlive }) => {
	const [beats, setBeats] = useState<Record<ArcAct, string>>({
		ki: "", sho: "", ten: "", ketsu: "",
	});
	const [locked, setLocked] = useState<Record<ArcAct, boolean>>({
		ki: false, sho: false, ten: false, ketsu: false,
	});
	const [loading, setLoading] = useState(false);
	const [error, setError]     = useState<string | null>(null);

	const toggleLock = (act: ArcAct) => {
		if (loading) return;
		setLocked((prev) => ({ ...prev, [act]: !prev[act] }));
	};

	const generate = async () => {
		if (!ollamaAlive || loading) return;
		setLoading(true);
		setError(null);

		const locked_acts = ARC_ACTS.filter((a) => locked[a]);

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
			setError(e instanceof Error ? e.message : "Generation failed.");
		} finally {
			setLoading(false);
		}
	};

	return (
		<div className="zettlebank-staging">
			{ARC_ACTS.map((act) => {
				const cfg       = ARC_CFG[act];
				const isLocked  = locked[act];
				const beat      = beats[act];
				const isPending = loading && !isLocked;

				return (
					<div
						key={act}
						className="zettlebank-card"
						onClick={() => toggleLock(act)}
						style={{
							cursor:      loading ? "default" : "pointer",
							borderColor: isLocked ? cfg.color : undefined,
							boxShadow:   isLocked
								? `0 0 0 1px ${cfg.color}55, inset 0 0 14px ${cfg.color}0d`
								: undefined,
							background:  isLocked ? "var(--background-secondary)" : undefined,
							transition:  "border-color 0.18s, box-shadow 0.18s, background 0.18s",
						}}
					>
						{/* Block header */}
						<div style={{ display: "flex", alignItems: "center", gap: "7px", marginBottom: "6px" }}>
							<span style={{
								width: 8, height: 8, borderRadius: "50%",
								background: cfg.color, flexShrink: 0, display: "inline-block",
							}} />
							<span style={{
								flex: 1, fontSize: "11px", fontWeight: 700,
								color: "var(--text-normal)", letterSpacing: "0.02em",
							}}>
								{cfg.label}{" "}
								<span style={{ color: "var(--text-faint)", fontWeight: 400 }}>
									{cfg.kanji} · {cfg.sub}
								</span>
							</span>
							<span style={{ fontSize: "11px", opacity: 0.75 }}>
								{isLocked ? "🔒" : "🔓"}
							</span>
						</div>

						{/* Beat text */}
						<p style={{
							fontSize: "11px", lineHeight: 1.6, margin: 0, paddingLeft: "15px",
							color:     beat ? "var(--text-muted)" : "var(--text-faint)",
							fontStyle: beat ? "normal" : "italic",
							opacity:   isPending ? 0.4 : 1,
							transition: "opacity 0.2s",
						}}>
							{isPending ? "· · ·" : beat || "Click generate to draft..."}
						</p>
					</div>
				);
			})}

			{error && (
				<div className="zettlebank-error" style={{ marginBottom: "8px" }}>
					<p>{error}</p>
				</div>
			)}

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
// Results Panel  (shown after auto-approve write)
// ---------------------------------------------------------------------------

const ResultsPanel: React.FC<{
	data: AnalyzeResponse;
	onReanalyze: () => void;
}> = ({ data, onReanalyze }) => {
	const affectTag = data.metadata.tags.find((t) => t.startsWith("affect/"));

	return (
		<div className="zettlebank-staging">
			{affectTag && <ValenceBadge affectTag={affectTag} />}

			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">community</h4>
				<span className="zettlebank-chip">
					{data.community_id !== null ? `#${data.community_id}` : "—"}
				</span>
			</div>

			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">tags</h4>
				<div className="zettlebank-chip-row">
					{data.metadata.tags
						.filter((t) => !t.startsWith("affect/"))
						.map((t) => (
							<span key={t} className="zettlebank-chip">{t}</span>
						))}
				</div>
			</div>

			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">smart_relations</h4>
				{data.metadata.smart_relations.length > 0 ? (
					<div className="zettlebank-relation-list">
						{data.metadata.smart_relations.map((rel) => (
							<div key={`${rel.target_id}::${rel.relation_type}`} className="zettlebank-relation-row">
								<span className="zettlebank-rel-type">{rel.relation_type}</span>
								<span className="zettlebank-rel-arrow">→</span>
								<span className="zettlebank-rel-target">{rel.target_id}</span>
								<span className="zettlebank-rel-conf">
									{(rel.confidence * 100).toFixed(0)}%
								</span>
							</div>
						))}
					</div>
				) : (
					<span className="zettlebank-empty">No relations detected</span>
				)}
			</div>

			<div className="zettlebank-actions">
				<button
					className="zettlebank-btn-approve"
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
// Root sidebar
// ---------------------------------------------------------------------------

export function ZettleBankSidebar({ state, onAnalyze }: SidebarProps) {
	const ollamaAlive = useHealth();
	const [activeTab, setActiveTab] = useState<"analysis" | "arc">("analysis");

	const tabStyle = (tab: "analysis" | "arc"): React.CSSProperties => ({
		flex: 1,
		padding: "8px 0",
		background: "none",
		border: "none",
		borderBottom: activeTab === tab
			? "2px solid var(--interactive-accent)"
			: "2px solid transparent",
		color: activeTab === tab ? "var(--text-normal)" : "var(--text-faint)",
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
					state.phase !== "results" && (
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
			<div style={{
				display: "flex",
				borderBottom: "1px solid var(--background-modifier-border)",
				padding: "0 12px",
				flexShrink: 0,
			}}>
				<button type="button" onClick={() => setActiveTab("analysis")} style={tabStyle("analysis")}>
					Analysis
				</button>
				<button type="button" onClick={() => setActiveTab("arc")} style={tabStyle("arc")}>
					Arc Generator
				</button>
			</div>

			{/* Ollama offline warning */}
			{!ollamaAlive && (
				<div style={{
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
				}}>
					<span style={{ flexShrink: 0 }}>⚠️</span>
					<span>Ollama is offline. Arc generation is disabled.</span>
				</div>
			)}

			{/* Tab content */}
			{activeTab === "analysis" && (
				<>
					{state.phase === "idle" && (
						<div className="zettlebank-empty-state">
							<p>Select a note and click Analyze to extract narrative metadata.</p>
						</div>
					)}

					{state.phase === "loading" && (
						<div className="zettlebank-loading">Analyzing note…</div>
					)}

					{state.phase === "error" && (
						<div className="zettlebank-error">
							<p>{state.message}</p>
							<button className="zettlebank-btn-secondary" onClick={onAnalyze} type="button">
								Retry
							</button>
						</div>
					)}

					{state.phase === "results" && (
						<ResultsPanel data={state.data} onReanalyze={() => onAnalyze()} />
					)}
				</>
			)}

			{activeTab === "arc" && (
				<NarrativeArcPanel ollamaAlive={ollamaAlive} />
			)}
		</div>
	);
}
