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
// Controlled vocabulary display maps
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
	ki:    "起",
	sho:   "承",
	ten:   "転",
	ketsu: "結",
};

const ACT_ORDER: Record<string, number> = { ki: 1, sho: 2, ten: 3, ketsu: 4 };

const PROVENANCE_ICON: Record<string, string> = {
	sc_embedding: "~",
	wikilink:     "↗",
	llm:          "✦",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Groups tags by prefix. `affect/` is intentionally excluded — it is
 * rendered separately as the ValenceBadge at the top of StagingArea.
 */
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

/**
 * Computes the directional relationship between two narrative acts.
 * Forward = moving later in the Ki→Shō→Ten→Ketsu sequence.
 * Regression = moving earlier. Lateral = same act.
 */
function getMomentumVector(
	fromAct: string,
	toAct: string
): { arrow: string; label: string } {
	const from = ACT_ORDER[fromAct] ?? 0;
	const to   = ACT_ORDER[toAct]   ?? 0;
	if (from < to) return { arrow: "➔", label: "Forward" };
	if (from > to) return { arrow: "⟲", label: "Regression" };
	return { arrow: "↔", label: "Lateral" };
}

// ---------------------------------------------------------------------------
// Emotional Valence Badge — prominent, toggleable, pinned to StagingArea top
// ---------------------------------------------------------------------------

const VALENCE_STYLES: Record<string, { color: string; bg: string; label: string }> = {
	positive: {
		color: "var(--color-green, #3dba6f)",
		bg:    "rgba(61,186,111,0.12)",
		label: "Positive",
	},
	negative: {
		color: "var(--color-red, #e05252)",
		bg:    "rgba(224,82,82,0.12)",
		label: "Negative",
	},
	mu: {
		color: "var(--text-muted)",
		bg:    "var(--background-modifier-hover)",
		label: "Mu (Neutral)",
	},
};

/** Full-width toggleable affect badge. Click to accept/reject the affect tag. */
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
				display:        "flex",
				alignItems:     "center",
				gap:            "8px",
				width:          "100%",
				padding:        "8px 12px",
				borderRadius:   "var(--radius-m)",
				border:         `1px solid ${style.color}`,
				background:     style.bg,
				cursor:         "pointer",
				opacity:        accepted ? 1 : 0.45,
				textDecoration: accepted ? "none" : "line-through",
				boxSizing:      "border-box",
			}}
		>
			<span style={{
				fontSize:      "var(--font-ui-small)",
				color:         "var(--text-muted)",
				textTransform: "uppercase",
				letterSpacing: "0.05em",
				flexShrink:    0,
			}}>
				Emotional Valence
			</span>
			<span style={{
				fontWeight: 700,
				fontSize:   "var(--font-ui-medium)",
				color:      style.color,
			}}>
				{style.label}
			</span>
			<span style={{
				marginLeft:  "auto",
				fontSize:    "var(--font-ui-smaller)",
				color:       "var(--text-faint)",
				fontFamily:  "var(--font-monospace)",
			}}>
				{affectTag}
			</span>
		</button>
	);
};

// ---------------------------------------------------------------------------
// Tag Toggle chip
// ---------------------------------------------------------------------------

const TagToggle: FC<{ value: string; accepted: boolean; onToggle: () => void }> = ({
	value,
	accepted,
	onToggle,
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
// Tag Group Card  (topic/, aspect/, code/ — affect/ is rendered as ValenceBadge)
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
// World-Building Weight meter  (Entity Density — counts aspect/ tags)
// ---------------------------------------------------------------------------

const EntityDensityMeter: FC<{ aspectTags: string[] }> = ({ aspectTags }) => {
	const count  = aspectTags.length;
	const MAX    = 6;
	const filled = Math.min(count, MAX);
	const bars   = Array.from({ length: MAX }, (_, i) => i < filled);
	return (
		<div className="zettlebank-card" style={{ padding: "8px 12px" }}>
			<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
				<span style={{
					fontSize:      "var(--font-ui-small)",
					color:         "var(--text-muted)",
					textTransform: "uppercase",
					letterSpacing: "0.05em",
					flexShrink:    0,
				}}>
					World-Building Weight
				</span>
				<div style={{ display: "flex", gap: "3px" }}>
					{bars.map((on, i) => (
						<div
							key={i}
							style={{
								width:        "10px",
								height:       "10px",
								borderRadius: "2px",
								background:   on
									? "var(--interactive-accent)"
									: "var(--background-modifier-border)",
							}}
						/>
					))}
				</div>
				<span style={{ fontSize: "var(--font-ui-smaller)", color: "var(--text-faint)" }}>
					{count} {count === 1 ? "entity" : "entities"}
				</span>
			</div>
		</div>
	);
};

// ---------------------------------------------------------------------------
// Structural Card — act, communities, Burt constraint, Pivot Analysis
// ---------------------------------------------------------------------------

/**
 * Read-only structural analysis card.
 * When `structuralHole.is_ten_candidate` is true AND `audit` is present,
 * a "Pivot Analysis" section renders showing bridged note IDs and the
 * LLM's narrative summary of the note's bridge function.
 */
const StructuralCard: FC<{
	narrativeAct: string;
	communityTiers: CommunityTier[];
	structuralHole: StructuralHole;
	audit: NarrativeAudit | null;
}> = ({ narrativeAct, communityTiers, structuralHole, audit }) => {
	const act       = ACT_LABELS[narrativeAct] ?? { label: narrativeAct, kanji: "?" };
	const macro     = communityTiers.find((t) => t.resolution === 0.5);
	const micro     = communityTiers.find((t) => t.resolution === 2.0);
	const showPivot = structuralHole.is_ten_candidate && audit !== null;

	return (
		<div className="zettlebank-card zettlebank-structural-card">
			<h4 className="zettlebank-card-label">Structural Analysis</h4>

			<div className="zettlebank-structural-row">
				<span className="zettlebank-structural-key">Narrative Act</span>
				<span className="zettlebank-structural-val">
					<span className="zettlebank-act-kanji">{act.kanji}</span>{" "}
					{act.label}
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
						<span className="zettlebank-pivot-badge">
							{" "}⚠ High Pivot Potential
						</span>
					)}
				</span>
			</div>

			{showPivot && audit && (
				<div style={{
					marginTop:  "10px",
					borderTop:  "1px solid var(--background-modifier-border)",
					paddingTop: "10px",
				}}>
					<h4 className="zettlebank-card-label" style={{ marginBottom: "6px" }}>
						Pivot Analysis
					</h4>

					{audit.bridge_note_ids.length > 0 && (
						<div className="zettlebank-structural-row" style={{ alignItems: "flex-start" }}>
							<span className="zettlebank-structural-key">Bridges</span>
							<div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
								{audit.bridge_note_ids.map((id) => (
									<span
										key={id}
										className="zettlebank-provenance-badge"
										style={{ padding: "1px 6px" }}
									>
										{id}
									</span>
								))}
							</div>
						</div>
					)}

					{audit.narrative_summary && (
						<p className="zettlebank-bridge-summary" style={{ marginTop: "6px" }}>
							{audit.narrative_summary}
						</p>
					)}
				</div>
			)}
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
// Relation Card — dense grid with Momentum Vector
// ---------------------------------------------------------------------------

/**
 * Dense EdgeMatrix row.
 * Row 1: relation type (colored) | confidence | provenance | accept status
 * Row 2: Momentum Vector — [CurrentAct] arrow [TargetAct] (Direction)
 * Row 3: Editable target_id input
 *
 * Click the header row to toggle accept/reject.
 */
const RelationCard: FC<{
	rel: EdgeMatrix;
	currentAct: string;
	accepted: boolean;
	editedTargetId: string;
	onToggle: () => void;
	onTargetChange: (next: string) => void;
}> = ({ rel, currentAct, accepted, editedTargetId, onToggle, onTargetChange }) => {
	const momentum  = getMomentumVector(currentAct, rel.narrative_act);
	const fromLabel = ACT_SHORT[currentAct]        ?? currentAct;
	const toLabel   = ACT_SHORT[rel.narrative_act] ?? rel.narrative_act;

	return (
		<div className={`zettlebank-relation-card ${accepted ? "is-accepted" : "is-rejected"}`}>

			{/* Row 1: type | confidence | provenance | status */}
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
				<span className="zettlebank-provenance-badge" title={rel.provenance}>
					{PROVENANCE_ICON[rel.provenance] ?? rel.provenance}
				</span>
				<span className="zettlebank-relation-status">
					{accepted ? "accepted" : "rejected"}
				</span>
			</div>

			{/* Row 2: Momentum Vector */}
			<div style={{
				display:    "flex",
				alignItems: "center",
				gap:        "4px",
				padding:    "3px 0",
				fontSize:   "var(--font-ui-smaller)",
			}}>
				<span style={{
					color:         "var(--text-faint)",
					fontSize:      "10px",
					textTransform: "uppercase",
					letterSpacing: "0.05em",
					flexShrink:    0,
				}}>
					Momentum
				</span>
				<span className="zettlebank-edge-act">[{fromLabel}]</span>
				<span style={{ color: "var(--text-accent)", fontWeight: 600 }}>
					{momentum.arrow}
				</span>
				<span className="zettlebank-edge-act">[{toLabel}]</span>
				<span style={{ color: "var(--text-faint)" }}>({momentum.label})</span>
			</div>

			{/* Row 3: editable target_id */}
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
// Staging Area — full approval interface
// ---------------------------------------------------------------------------

const STANDARD_TAG_PREFIXES = ["topic", "aspect", "code"] as const;

/**
 * Full approval interface. The user reviews AI suggestions as interactive
 * cards, toggles tags and relations, edits the description, then confirms
 * with "Approve & Write". Nothing touches frontmatter until confirmed.
 */
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

	// -- Description --
	const [description, setDescription] = useState(data.metadata.description ?? "");

	// -- Relation toggles --
	const [acceptedRels, setAcceptedRels] = useState<Set<string>>(
		() => new Set(
			data.metadata.smart_relations.map((r) => `${r.target_id}::${r.relation_type}`)
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

	// -- Target-id overrides --
	const [targetEdits, setTargetEdits] = useState<Map<string, string>>(
		() => new Map(
			data.metadata.smart_relations.map((r) => [
				`${r.target_id}::${r.relation_type}`,
				r.target_id,
			])
		)
	);

	const updateTargetId = useCallback((key: string, next: string) => {
		setTargetEdits((prev) => new Map(prev).set(key, next));
	}, []);

	// -- Derived display data --
	// Affect tag is extracted for the ValenceBadge; not passed to TagGroupCard.
	const affectTag  = data.metadata.tags.find((t) => t.startsWith("affect/"));
	const tagGroups  = groupTagsByPrefix(data.metadata.tags);
	const aspectTags = tagGroups["aspect"] ?? [];

	// -- Build ApprovedPayload --
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

			{/* Emotional Valence badge — prominent, at top, toggleable */}
			<ValenceBadge
				affectTag={affectTag}
				accepted={affectTag ? acceptedTags.has(affectTag) : false}
				onToggle={() => affectTag && toggleTag(affectTag)}
			/>

			{/* Structural card — act, communities, Burt constraint, Pivot Analysis */}
			<StructuralCard
				narrativeAct={data.narrative_act}
				communityTiers={data.community_tiers}
				structuralHole={data.structural_hole}
				audit={data.narrative_audit ?? null}
			/>

			{/* Description */}
			<DescriptionCard value={description} onChange={setDescription} />

			{/* World-Building Weight meter */}
			<EntityDensityMeter aspectTags={aspectTags} />

			{/* Standard tag group cards (topic/, aspect/, code/) */}
			{STANDARD_TAG_PREFIXES.map((prefix) => (
				<TagGroupCard
					key={prefix}
					prefix={prefix}
					tags={tagGroups[prefix] ?? []}
					accepted={acceptedTags}
					onToggle={toggleTag}
				/>
			))}

			{/* EdgeMatrix relations with Momentum Vector */}
			{data.metadata.smart_relations.length > 0 && (
				<div className="zettlebank-card">
					<h4 className="zettlebank-card-label">Relations</h4>
					{data.metadata.smart_relations.map((rel) => {
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
					})}
				</div>
			)}

			{/* Community badge */}
			{data.community_id !== null && (
				<div className="zettlebank-community">
					<span className="zettlebank-community-label">Community</span>
					<span className="zettlebank-community-id">{data.community_id}</span>
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
// Root sidebar component
// ---------------------------------------------------------------------------

/**
 * Root sidebar. Switches between idle, loading, error, and staging views
 * based on state passed from the Obsidian ItemView. Purely presentational.
 */
export const ZettleBankSidebar: FC<SidebarProps> = ({ state, onAnalyze, onApprove }) => (
	<div className="zettlebank-container">
		<div className="zettlebank-header">
			<h3>ZettleBank</h3>
			{state.phase !== "loading" && state.phase !== "staging" && (
				<button
					className="zettlebank-analyze-btn"
					onClick={onAnalyze}
					type="button"
				>
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
	</div>
);
