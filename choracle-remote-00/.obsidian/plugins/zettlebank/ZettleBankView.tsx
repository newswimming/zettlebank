import { useState, useCallback, type FC } from "react";
import type { ApprovedPayload } from "./main";
import type {
	AnalyzeResponse,
	EdgeMatrix,
	NarrativeMetadata,
	NarrativeAudit,
	RelationType,
	StructuralHole,
	CommunityTier,
} from "./schema";

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
	accepted: boolean;
	onToggle: () => void;
}> = ({ affectTag, accepted, onToggle }) => {
	if (!affectTag) return null;
	const value = affectTag.slice("affect/".length);
	const style = VALENCE_STYLES[value] ?? VALENCE_STYLES["mu"];
	return (
		<button
			type="button"
			onClick={onToggle}
			title={accepted ? "Click to reject affect tag" : "Click to accept affect tag"}
			style={{
				display: "flex", alignItems: "center", gap: "8px",
				width: "100%", padding: "8px 12px", boxSizing: "border-box",
				borderRadius: "var(--radius-m)", border: `1px solid ${style.color}`,
				background: style.bg, cursor: "pointer",
				opacity: accepted ? 1 : 0.45,
				textDecoration: accepted ? "none" : "line-through",
			}}
		>
			<span style={LABEL_STYLE}>Emotional Valence</span>
			<span style={{ fontWeight: 700, fontSize: "var(--font-ui-medium)", color: style.color }}>
				{style.label}
			</span>
			<span style={{ marginLeft: "auto", fontFamily: "var(--font-monospace)", fontSize: "var(--font-ui-smaller)", color: "var(--text-faint)" }}>
				{affectTag}
			</span>
		</button>
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
// Staging Area
// ---------------------------------------------------------------------------

const STANDARD_TAG_PREFIXES = ["topic", "aspect", "code"] as const;

const StagingArea: FC<{
	data: AnalyzeResponse;
	onApprove: (payload: ApprovedPayload) => void;
	onReanalyze: () => void;
}> = ({ data, onApprove, onReanalyze }) => {

	const [acceptedTags, setAcceptedTags] = useState<Set<string>>(
		() => new Set(data.metadata.tags)
	);
	const toggleTag = useCallback((tag: string) => {
		setAcceptedTags((prev) => {
			const next = new Set(prev);
			if (next.has(tag)) next.delete(tag); else next.add(tag);
			return next;
		});
	}, []);

	const [description, setDescription] = useState(data.metadata.description ?? "");

	const [acceptedRels, setAcceptedRels] = useState<Set<string>>(
		() => new Set(data.metadata.smart_relations.map((r) => `${r.target_id}::${r.relation_type}`))
	);
	const toggleRelation = useCallback((key: string) => {
		setAcceptedRels((prev) => {
			const next = new Set(prev);
			if (next.has(key)) next.delete(key); else next.add(key);
			return next;
		});
	}, []);

	const [targetEdits, setTargetEdits] = useState<Map<string, string>>(
		() => new Map(data.metadata.smart_relations.map((r) => [
			`${r.target_id}::${r.relation_type}`, r.target_id,
		]))
	);
	const updateTargetId = useCallback((key: string, next: string) => {
		setTargetEdits((prev) => new Map(prev).set(key, next));
	}, []);

	const affectTag = data.metadata.tags.find((t) => t.startsWith("affect/"));
	const tagGroups = groupTagsByPrefix(data.metadata.tags);

	const totalRels    = data.metadata.smart_relations.length;
	const acceptedCount = data.metadata.smart_relations.filter(
		(r) => acceptedRels.has(`${r.target_id}::${r.relation_type}`)
	).length;

	const handleApprove = useCallback(() => {
		const approvedRelations: EdgeMatrix[] = data.metadata.smart_relations
			.filter((r) => acceptedRels.has(`${r.target_id}::${r.relation_type}`))
			.map((r) => ({
				...r,
				target_id: targetEdits.get(`${r.target_id}::${r.relation_type}`) ?? r.target_id,
			}));

		const metadata: NarrativeMetadata = {
			aliases:         data.metadata.aliases,
			description:     description.trim() || null,
			tags:            Array.from(acceptedTags),
			smart_relations: approvedRelations,
			source:          data.metadata.source,
			citationID:      data.metadata.citationID,
		};
		onApprove({ metadata, community_id: data.community_id });
	}, [data, acceptedTags, description, acceptedRels, targetEdits, onApprove]);

	return (
		<div className="zettlebank-staging">

			{/* 1. Emotional Valence */}
			<ValenceBadge
				affectTag={affectTag}
				accepted={affectTag ? acceptedTags.has(affectTag) : false}
				onToggle={() => affectTag && toggleTag(affectTag)}
			/>

			{/* 2. Structural Analysis (act, communities, Burt score) */}
			<StructuralCard
				narrativeAct={data.narrative_act}
				communityTiers={data.community_tiers}
				structuralHole={data.structural_hole}
			/>

			{/* 3. Pivot Analysis — whenever is_ten_candidate */}
			{data.structural_hole.is_ten_candidate && (
				<PivotAnalysisCard
					structuralHole={data.structural_hole}
					audit={data.narrative_audit ?? null}
				/>
			)}

			{/* 4. Bridge Network — whenever bridge_detected */}
			{data.bridge_detected && data.narrative_audit && (
				<BridgeCard audit={data.narrative_audit} />
			)}

			{/* 5. Description */}
			<DescriptionCard value={description} onChange={setDescription} />

			{/* 6. Tag group cards (topic/, aspect/, code/) */}
			{STANDARD_TAG_PREFIXES.map((prefix) => (
				<TagGroupCard
					key={prefix}
					prefix={prefix}
					tags={tagGroups[prefix] ?? []}
					accepted={acceptedTags}
					onToggle={toggleTag}
				/>
			))}

			{/* 7. EdgeMatrix / smart_relations */}
			<div className="zettlebank-card">
				<div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: totalRels > 0 ? "6px" : 0 }}>
					<h4 className="zettlebank-card-label" style={{ margin: 0 }}>
						smart_relations
					</h4>
					{totalRels > 0 && (
						<span style={{ fontSize: "var(--font-ui-smaller)", color: "var(--text-faint)" }}>
							{acceptedCount}/{totalRels} accepted
						</span>
					)}
				</div>

				{totalRels > 0 ? (
					data.metadata.smart_relations.map((rel) => {
						const key = `${rel.target_id}::${rel.relation_type}`;
						return (
							<RelationCard
								key={key}
								rel={rel}
								currentAct={data.narrative_act}
								accepted={acceptedRels.has(key)}
								editedTargetId={targetEdits.get(key) ?? rel.target_id}
								onToggle={() => toggleRelation(key)}
								onTargetChange={(v) => updateTargetId(key, v)}
							/>
						);
					})
				) : (
					<span className="zettlebank-empty">No relations detected</span>
				)}
			</div>

			{/* 8. Community badge */}
			{data.community_id !== null && (
				<div className="zettlebank-community">
					<span className="zettlebank-community-label">Community</span>
					<span className="zettlebank-community-id">{data.community_id}</span>
				</div>
			)}

			{/* 9. Action bar */}
			<div className="zettlebank-actions">
				<button className="zettlebank-btn-approve" onClick={handleApprove} type="button">
					Approve &amp; Write
				</button>
				<button className="zettlebank-btn-secondary" onClick={onReanalyze} type="button">
					Re-analyze
				</button>
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
// Root sidebar
// ---------------------------------------------------------------------------

export const ZettleBankSidebar: FC<SidebarProps> = ({ state, onAnalyze, onApprove }) => (
	<div className="zettlebank-container">
		<div className="zettlebank-header">
			<h3>ZettleBank</h3>
			{state.phase !== "loading" && state.phase !== "staging" && (
				<button className="zettlebank-analyze-btn" onClick={onAnalyze} type="button">
					Analyze
				</button>
			)}
		</div>

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

		{state.phase === "staging" && (
			<StagingArea data={state.data} onApprove={onApprove} onReanalyze={onAnalyze} />
		)}
	</div>
);
