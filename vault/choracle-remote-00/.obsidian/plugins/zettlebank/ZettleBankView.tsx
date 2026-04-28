import React, { useState, useEffect, type FC } from "react";
import type {
	AnalyzeResponse,
	ApprovedPayload,
	EdgeMatrix,
	NarrativeAudit,
	RelationType,
	StructuralHole,
	CommunityTier,
	WeightSignals,
} from "./schema";

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
	onApprove: (payload: ApprovedPayload) => Promise<void>;
	onPush: () => Promise<void>;
	backendUrl: string;
}

interface ActDistribution {
	total: number;
	distribution: Record<string, number>;
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
	ten:   { label: "Act 3: Ten · Pivot",         kanji: "轉" },
	ketsu: { label: "Act 4: Ketsu · Resolution", kanji: "合" },
};

const ACT_SHORT: Record<string, string> = {
	ki: "起", sho: "承", ten: "轉", ketsu: "合",
};

const ACT_ORDER: Record<string, number> = { ki: 1, sho: 2, ten: 3, ketsu: 4 };

const PROVENANCE_LABEL: Record<string, string> = {
	sc_embedding: "~ Embedding",
	wikilink:     "↗ Wiki-link",
	llm:          "✦ LLM",
};

const TEN_TRIGGER_LABEL: Record<string, string> = {
	low_constraint: "structural hole (Burt constraint)",
	is_pivot:       "narrative pivot flag",
	symbiote_role:  "symbiote character role",
};

// ---------------------------------------------------------------------------
// Arc Generator constants
// ---------------------------------------------------------------------------

const ARC_CFG = {
	ki:    { label: "Ki",    kanji: "起", sub: "Introduction", color: "#34d399" },
	sho:   { label: "Sho",   kanji: "承", sub: "Development",  color: "#60a5fa" },
	ten:   { label: "Ten",   kanji: "轉", sub: "Twist",        color: "#f59e0b" },
	ketsu: { label: "Ketsu", kanji: "合", sub: "Resolution",   color: "#c084fc" },
} as const;

type ArcAct = keyof typeof ARC_CFG;
const ARC_ACTS: ArcAct[] = ["ki", "sho", "ten", "ketsu"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
	positive:   { color: "var(--color-green, #3dba6f)", bg: "rgba(61,186,111,0.12)",   label: "Positive"   },
	negative:   { color: "var(--color-red,  #e05252)",  bg: "rgba(224,82,82,0.12)",    label: "Negative"   },
	neutral:    { color: "var(--text-muted)",            bg: "var(--background-modifier-hover)", label: "Neutral"    },
	ambivalent: { color: "#a78bfa",                      bg: "rgba(167,139,250,0.12)",  label: "Ambivalent" },
	melancholic:{ color: "#60a5fa",                      bg: "rgba(96,165,250,0.12)",   label: "Melancholic"},
	tense:      { color: "#f59e0b",                      bg: "rgba(245,158,11,0.12)",   label: "Tense"      },
	hopeful:    { color: "#34d399",                      bg: "rgba(52,211,153,0.12)",   label: "Hopeful"    },
};

const ValenceBadge: FC<{
	affectTag: string | undefined;
}> = ({ affectTag }) => {
	if (!affectTag) return null;
	const value = affectTag.slice("affect/".length);
	const style = VALENCE_STYLES[value] ?? VALENCE_STYLES["neutral"];
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
	const macro = communityTiers.find((t) => t.resolution === 1.0);
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
					<span className="zettlebank-structural-key">Macro (γ=1.0)</span>
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
				轉 Pivot Analysis
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
						{audit.beat_position}
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
// Tag Group Card  (topic/, character/, place/, time/, object/ — affect/ handled by ValenceBadge selector)
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
	onToggle: () => void;
}> = ({ rel, currentAct, accepted, onToggle }) => {
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
// ActMapPanel — act position, structural freedom, relation flow, vault distribution
// ---------------------------------------------------------------------------

const ActMapPanel: FC<{ data: AnalyzeResponse | null; dist: ActDistribution | null }> = ({ data, dist }) => {

	if (!data) {
		return (
			<div className="zettlebank-empty-state">
				<p>Analyze a note to see its act position in the vault narrative.</p>
			</div>
		);
	}

	const act = (data.narrative_act ?? "ki") as ArcAct;
	const cfg = ARC_CFG[act] ?? ARC_CFG.ki;
	const freedom = Math.max(0, 1 - data.structural_hole.constraint_score);
	const freedomPct = (freedom * 100).toFixed(1);

	// act_weights from backend (soft distribution across 4 acts)
	const weights = data.act_weights ?? { ki: 0.25, sho: 0.25, ten: 0.25, ketsu: 0.25 };
	const dominantWeight = weights[act] ?? 0.25;
	const confidence = dominantWeight > 0.5 ? "Strong" : dominantWeight > 0.35 ? "Moderate" : "Contested";
	const confidenceColor = dominantWeight > 0.5 ? "var(--color-green)" : dominantWeight > 0.35 ? "var(--text-muted)" : "var(--color-orange)";

	// Count smart_relations by target narrative_act
	const relsByAct: Record<string, number> = {};
	for (const rel of data.metadata.smart_relations) {
		const targetAct = rel.narrative_act ?? "sho";
		relsByAct[targetAct] = (relsByAct[targetAct] ?? 0) + 1;
	}
	const hasRelations = data.metadata.smart_relations.length > 0;

	return (
		<div className="zettlebank-staging">

			{/* 1. Weighted act position card */}
			<div className="zettlebank-card" style={{ borderColor: cfg.color }}>
				<div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
					<h4 className="zettlebank-card-label" style={{ margin: 0 }}>
						Act Position
						<span style={{ marginLeft: "6px", fontSize: "10px", color: confidenceColor, fontWeight: 400 }}>
							{confidence}
						</span>
					</h4>
					<span style={{ fontSize: "11px", color: "var(--text-faint)" }}>
						Community {data.community_id !== null ? `#${data.community_id}` : "—"}
					</span>
				</div>
				{/* Weighted 4-band bar */}
				<div style={{
					display: "flex", height: "28px", borderRadius: "var(--radius-s)",
					overflow: "hidden", marginBottom: "8px",
				}}>
					{ARC_ACTS.map((a) => {
						const w = weights[a] ?? 0;
						const c = ARC_CFG[a];
						const isDom = a === act;
						return (
							<div
								key={a}
								title={`${c.label} (${c.sub}): ${(w * 100).toFixed(1)}%`}
								style={{
									flex: w > 0 ? w : 0.01,
									background: isDom ? c.color : `${c.color}60`,
									display: "flex", alignItems: "center", justifyContent: "center",
									minWidth: w > 0.05 ? 24 : 0,
									transition: "flex 0.4s ease",
								}}
							>
								{w > 0.12 && (
									<span style={{
										fontSize: isDom ? "15px" : "12px",
										lineHeight: 1,
										filter: isDom ? "none" : "opacity(0.7)",
										pointerEvents: "none",
									}}>
										{c.kanji}
									</span>
								)}
							</div>
						);
					})}
				</div>
				{/* Legend */}
				<div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
					{ARC_ACTS.map((a) => {
						const w = weights[a] ?? 0;
						const c = ARC_CFG[a];
						const isDom = a === act;
						return (
							<span key={a} style={{
								display: "flex", alignItems: "center", gap: "4px",
								fontSize: "11px", fontWeight: isDom ? 700 : 400,
							}}>
								<span style={{
									width: "7px", height: "7px", borderRadius: "50%",
									background: c.color, display: "inline-block",
									outline: isDom ? `2px solid ${c.color}` : "none",
									outlineOffset: "1px",
								}} />
								<span style={{ color: isDom ? "var(--text-normal)" : "var(--text-muted)" }}>
									{c.kanji} {(w * 100).toFixed(0)}%
								</span>
							</span>
						);
					})}
				</div>
			</div>

			{/* 2. Structural freedom bar */}
			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">Structural Freedom</h4>
				<div style={{ display: "flex", justifyContent: "space-between", marginBottom: "3px" }}>
					<span style={LABEL_STYLE}>Burt Constraint</span>
					<span style={{
						fontSize: "var(--font-ui-smaller)",
						fontWeight: 600,
						color: data.structural_hole.is_ten_candidate
							? "var(--text-accent)"
							: "var(--text-muted)",
					}}>
						{freedomPct}% free
						{data.structural_hole.is_ten_candidate && " · 轉 candidate"}
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
						background: data.structural_hole.is_ten_candidate
							? "var(--text-accent)"
							: cfg.color,
						transition: "width 0.3s ease",
					}} />
				</div>
				<div style={{ display: "flex", justifyContent: "space-between", marginTop: "2px" }}>
					<span style={{ fontSize: "10px", color: "var(--text-faint)" }}>Constrained</span>
					<span style={{ fontSize: "10px", color: "var(--text-faint)" }}>Free Bridge</span>
				</div>
				{data.structural_hole.ten_trigger && (
					<div style={{ marginTop: "5px", fontSize: "10px", color: "var(--text-faint)" }}>
						Trigger: {TEN_TRIGGER_LABEL[data.structural_hole.ten_trigger] ?? data.structural_hole.ten_trigger}
					</div>
				)}
			</div>

			{/* 3. Candidature signals breakdown */}
			{data.weight_signals && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">Candidature Signals</h4>
					<div style={{ fontSize: "11px", display: "flex", flexDirection: "column", gap: "3px" }}>
						<div style={{ display: "flex", justifyContent: "space-between" }}>
							<span style={{ color: "var(--text-muted)" }}>Community anchor</span>
							<span style={{ fontFamily: "var(--font-monospace)" }}>{data.weight_signals.community_anchor}</span>
						</div>
						<div style={{ display: "flex", justifyContent: "space-between" }}>
							<span style={{ color: "var(--text-muted)" }}>Constraint 轉 pull</span>
							<span style={{ fontFamily: "var(--font-monospace)" }}>{data.weight_signals.constraint_ten_pull.toFixed(3)}</span>
						</div>
						<div style={{ display: "flex", justifyContent: "space-between" }}>
							<span style={{ color: "var(--text-muted)" }}>Place/time 起 boost</span>
							<span style={{ fontFamily: "var(--font-monospace)" }}>{data.weight_signals.place_time_ki_boost.toFixed(3)}</span>
						</div>
						{data.weight_signals.character_role && (
							<div style={{ display: "flex", justifyContent: "space-between" }}>
								<span style={{ color: "var(--text-muted)" }}>Character role</span>
								<span style={{ fontFamily: "var(--font-monospace)" }}>
									{data.weight_signals.character_role}
									{data.weight_signals.character_role_act && ` → ${data.weight_signals.character_role_act}`}
								</span>
							</div>
						)}
						{Object.keys(data.weight_signals.neighbor_act_counts).length > 0 && (
							<div style={{ marginTop: "2px" }}>
								<span style={{ color: "var(--text-muted)" }}>Neighbor acts: </span>
								{Object.entries(data.weight_signals.neighbor_act_counts).map(([a, n]) => (
									<span key={a} style={{ fontFamily: "var(--font-monospace)", marginRight: "6px" }}>
										{ARC_CFG[a as keyof typeof ARC_CFG]?.kanji ?? a} {n}
									</span>
								))}
							</div>
						)}
					</div>
				</div>
			)}

			{/* 4. Relation flow across acts */}
			{hasRelations && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">Relation Flow</h4>
					{ARC_ACTS.map((targetAct) => {
						const count = relsByAct[targetAct] ?? 0;
						if (count === 0) return null;
						const targetCfg = ARC_CFG[targetAct];
						const momentum  = getMomentumVector(act, targetAct);
						return (
							<div
								key={targetAct}
								style={{
									display: "flex", alignItems: "center",
									gap: "6px", marginBottom: "5px", fontSize: "12px",
								}}
							>
								<span style={{
									fontFamily: "var(--font-monospace)",
									color: cfg.color, fontWeight: 700,
								}}>
									{cfg.kanji}
								</span>
								<span style={{ color: "var(--text-accent)", fontWeight: 600 }}>
									{momentum.arrow}
								</span>
								<span style={{
									fontFamily: "var(--font-monospace)",
									color: targetCfg.color, fontWeight: 700,
								}}>
									{targetCfg.kanji}
								</span>
								<span style={{ color: "var(--text-faint)", fontSize: "11px" }}>
									{count} {count === 1 ? "relation" : "relations"} · {momentum.label}
								</span>
							</div>
						);
					})}
				</div>
			)}

			{/* 4. Vault act distribution */}
			{dist && dist.total > 0 && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">
						Vault Distribution ({dist.total} notes)
					</h4>
					{/* Stacked proportional bar */}
					<div style={{
						display: "flex", height: "10px", borderRadius: "3px",
						overflow: "hidden", marginBottom: "6px",
					}}>
						{ARC_ACTS.map((a) => {
							const count = dist.distribution[a] ?? 0;
							return (
								<div
									key={a}
									title={`${ARC_CFG[a].label}: ${count} of ${dist.total}`}
									style={{
										flex: count,
										background: ARC_CFG[a].color,
										minWidth: count > 0 ? 3 : 0,
									}}
								/>
							);
						})}
					</div>
					{/* Legend */}
					<div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
						{ARC_ACTS.map((a) => {
							const count = dist.distribution[a] ?? 0;
							const pct   = ((count / dist.total) * 100).toFixed(0);
							const c     = ARC_CFG[a];
							const isCurrent = a === act;
							return (
								<span
									key={a}
									style={{
										display: "flex", alignItems: "center",
										gap: "3px", fontSize: "11px",
										fontWeight: isCurrent ? 700 : 400,
									}}
								>
									<span style={{
										width: "7px", height: "7px", borderRadius: "50%",
										background: c.color, display: "inline-block",
										outline: isCurrent ? `2px solid ${c.color}` : "none",
										outlineOffset: "1px",
									}} />
									<span style={{
										color: isCurrent
											? "var(--text-normal)"
											: "var(--text-muted)",
									}}>
										{c.kanji} {count} ({pct}%)
									</span>
								</span>
							);
						})}
					</div>
				</div>
			)}
		</div>
	);
};

// ---------------------------------------------------------------------------
// Results Panel — editable staging area; writes to frontmatter on approve
// ---------------------------------------------------------------------------

const ResultsPanel: React.FC<{
	data: AnalyzeResponse;
	onApprove: (payload: ApprovedPayload) => Promise<void>;
}> = ({ data, onApprove }) => {

	// ── Description (user-editable) ──────────────────────────────────────
	const [description, setDescription] = useState<string>(data.metadata.description ?? "");

	// ── Affect (multi-select: 1–3 values) ────────────────────────────────
	const initialAffects = new Set(
		data.metadata.tags
			.filter((t) => t.startsWith("affect/"))
			.map((t) => t.slice("affect/".length))
			.filter((v) => v in VALENCE_STYLES)
	);
	if (initialAffects.size === 0) initialAffects.add("neutral");
	const [affects, setAffects] = useState<Set<string>>(initialAffects);

	// ── Tags (non-affect) — all accepted by default ───────────────────────
	const nonAffectTags = data.metadata.tags.filter((t) => !t.startsWith("affect/"));
	const [acceptedTags, setAcceptedTags] = useState<Set<string>>(
		() => new Set(nonAffectTags)
	);

	// ── Relations keyed by target_id::relation_type ───────────────────────
	const [relAccepted, setRelAccepted] = useState<Set<string>>(
		() => new Set(data.metadata.smart_relations.map((r) => `${r.target_id}::${r.relation_type}`))
	);

	// ── Community members — now served directly from AnalyzeResponse ─────
	const communityMembers = data.community_members ?? [];

	// ── Action state ──────────────────────────────────────────────────────
	const [approving, setApproving] = useState(false);
	const [saved, setSaved]         = useState(false);

	// ── Handlers ──────────────────────────────────────────────────────────
	const toggleTag = (tag: string) => {
		setAcceptedTags((prev) => {
			const next = new Set(prev);
			if (next.has(tag)) next.delete(tag); else next.add(tag);
			return next;
		});
		setSaved(false);
	};

	const toggleRel = (key: string) => {
		setRelAccepted((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key); else next.add(key);
			return next;
		});
		setSaved(false);
	};

	const handleApprove = async () => {
		setApproving(true);
		try {
			const approvedTags = nonAffectTags
				.filter((t) => acceptedTags.has(t))
				.concat(Array.from(affects).map((v) => `affect/${v}`));

			const approvedRels = data.metadata.smart_relations
				.filter((r) => relAccepted.has(`${r.target_id}::${r.relation_type}`))
;

			await onApprove({
				metadata: {
					...data.metadata,
					tags:            approvedTags,
					smart_relations: approvedRels,
					description:     description.trim() || null,
				},
				community_id: data.community_id,
			});

			setSaved(true);
			setTimeout(() => setSaved(false), 2500);
		} finally {
			setApproving(false);
		}
	};

	return (
		<div className="zettlebank-staging">

			{/* ── Emotional Valence selector ─────────────────────────── */}
			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">Emotional Valence</h4>
				<div style={{ fontSize: "10px", color: "var(--text-faint)", marginBottom: "4px" }}>
					Select 1–3 tones
				</div>
				<div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
					{Object.entries(VALENCE_STYLES).map(([key, vs]) => {
						const active = affects.has(key);
						return (
							<button
								key={key}
								type="button"
								onClick={() => {
									setAffects((prev) => {
										const next = new Set(prev);
										if (next.has(key)) {
											if (next.size > 1) next.delete(key);
										} else {
											if (next.size < 3) next.add(key);
										}
										return next;
									});
									setSaved(false);
								}}
								style={{
									padding: "2px 10px",
									borderRadius: "var(--radius-s)",
									border: `1px solid ${active ? vs.color : "var(--background-modifier-border)"}`,
									background: active ? vs.bg : "transparent",
									color: active ? vs.color : "var(--text-faint)",
									fontSize: "var(--font-ui-smaller)",
									fontWeight: active ? 600 : 400,
									cursor: "pointer",
									transition: "all 0.15s",
								}}
							>
								{vs.label}
							</button>
						);
					})}
				</div>
			</div>


			{/* ── Description ───────────────────────────────────────── */}
		<DescriptionCard value={description} onChange={(v) => { setDescription(v); setSaved(false); }} />

		{/* ── Community members ─────────────────────────────────── */}
			<div className="zettlebank-card">
				<h4 className="zettlebank-card-label">
					Community {data.community_id !== null ? `#${data.community_id}` : "—"}
				</h4>
				{communityMembers.length === 0 && (
					<span className="zettlebank-empty">No other notes in this community</span>
				)}
				{communityMembers.length > 0 && (
					<div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "4px" }}>
						{communityMembers.map((id) => (
							<span
								key={id}
								style={{
									fontFamily: "var(--font-monospace)",
									fontSize: "var(--font-ui-smaller)",
									color: "var(--text-muted)",
									whiteSpace: "nowrap",
									overflow: "hidden",
									textOverflow: "ellipsis",
								}}
								title={id}
							>
								{id}
							</span>
						))}
					</div>
				)}
			</div>

			{/* ── Tags by prefix (click to toggle) ──────────────────── */}
			{(["topic", "character", "place", "time", "object"] as const).map((prefix) => {
				const group = nonAffectTags.filter((t) => t.startsWith(prefix + "/"));
				if (group.length === 0) return null;
				return (
					<TagGroupCard
						key={prefix}
						prefix={prefix}
						tags={group}
						accepted={acceptedTags}
						onToggle={toggleTag}
					/>
				);
			})}

			{/* ── Relations (click header to accept/reject) ─────────── */}
			{data.metadata.smart_relations.length > 0 && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">smart_relations</h4>
					<div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "4px" }}>
						{data.metadata.smart_relations.map((rel) => {
							const key = `${rel.target_id}::${rel.relation_type}`;
							return (
								<RelationCard
									key={key}
									rel={rel}
									currentAct={data.narrative_act}
									accepted={relAccepted.has(key)}
									onToggle={() => toggleRel(key)}
								/>
							);
						})}
					</div>
				</div>
			)}

			{/* ── Actions ───────────────────────────────────────────── */}
			<div style={{ padding: "0 0 8px" }}>
				<button
					className="zettlebank-btn-approve"
					onClick={handleApprove}
					disabled={approving}
					type="button"
					style={{ width: "100%" }}
				>
					{approving ? "Pushing…" : saved ? "Pushed!" : "Push to Graph"}
				</button>
			</div>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Root sidebar
// ---------------------------------------------------------------------------

export function ZettleBankSidebar({ state, onAnalyze, onApprove, onPush, backendUrl }: SidebarProps) {
	const [activeTab, setActiveTab] = useState<"analysis" | "map">("analysis");
	const [dist, setDist] = useState<ActDistribution | null>(null);

	// Fetch act-distribution once on mount
	useEffect(() => {
		fetch(`${backendUrl}/graph/act-distribution`, { signal: AbortSignal.timeout(8000) })
			.then((r) => r.json()).then(setDist).catch(() => {});
	}, [backendUrl]);

	// Re-fetch whenever an analysis completes so the vault bar stays current
	useEffect(() => {
		if (state.phase !== "results") return;
		fetch(`${backendUrl}/graph/act-distribution`, { signal: AbortSignal.timeout(8000) })
			.then((r) => r.json()).then(setDist).catch(() => {});
	}, [backendUrl, state.phase]);

	const tabStyle = (tab: "analysis" | "map"): React.CSSProperties => ({
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
				<button type="button" onClick={() => setActiveTab("map")} style={tabStyle("map")}>
					Act Map
				</button>
			</div>

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
						<ResultsPanel data={state.data} onApprove={onApprove} />
					)}
				</>
			)}

			{activeTab === "map" && (
				<ActMapPanel data={state.phase === "results" ? state.data : null} dist={dist} />
			)}
		</div>
	);
}
