import { useState, useCallback, type FC } from "react";
import type { ApprovedPayload } from "./main";
import type {
	AnalyzeResponse,
	SmartRelation,
	NarrativeMetadata,
	NarrativeAudit,
	RelationType,
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
// Tag Toggle Card
//
// Each tag gets an interactive chip the user can toggle on/off before
// approving. Tags use prefix/value format (topic/, aspect/, affect/, code/).
// ---------------------------------------------------------------------------

/** Interactive chip for a single tag. Renders with an accepted/rejected style that the user can toggle before approving. */
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

/** Buckets a flat tag list into groups keyed by prefix (topic, aspect, affect, code). Tags without a recognised prefix are dropped. */
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

/** Displays all tags for a single prefix as a row of `TagToggle` chips. Shows "None detected" when the group is empty. */
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
// Beat Matrix — 4×4 grid of the 16 Kishōtenketsu narrative beats
// ---------------------------------------------------------------------------

const BEAT_ROWS: Array<{ act: string; kanji: string; beats: Array<{ slug: string; n: string }> }> = [
	{
		act: "Ki", kanji: "起",
		beats: [
			{ slug: "ki-1", n: "1" }, { slug: "ki-2", n: "2" },
			{ slug: "ki-3", n: "3" }, { slug: "ki-4", n: "4" },
		],
	},
	{
		act: "Shō", kanji: "承",
		beats: [
			{ slug: "sho-5", n: "5" }, { slug: "sho-6", n: "6" },
			{ slug: "sho-7", n: "7" }, { slug: "sho-8", n: "8" },
		],
	},
	{
		act: "Ten", kanji: "転",
		beats: [
			{ slug: "ten-9", n: "9" }, { slug: "ten-10", n: "10" },
			{ slug: "ten-11", n: "11" }, { slug: "ten-12", n: "12" },
		],
	},
	{
		act: "Ketsu", kanji: "結",
		beats: [
			{ slug: "ketsu-13", n: "13" }, { slug: "ketsu-14", n: "14" },
			{ slug: "ketsu-15", n: "15" }, { slug: "ketsu-16", n: "16" },
		],
	},
];

/**
 * 4×4 grid rendering the 16 Kishōtenketsu narrative beats.
 * Highlights the AI-proposed beat; clicking a cell overrides it.
 * When bridge_detected, the audit summary is shown above the grid.
 */
const BeatMatrix: FC<{
	selected: string;
	onSelect: (beat: string) => void;
	audit: NarrativeAudit | null;
	bridgeDetected: boolean;
}> = ({ selected, onSelect, audit, bridgeDetected }) => (
	<div className="zettlebank-card zettlebank-beatmatrix">
		<h4 className="zettlebank-card-label">
			Beat Position
			{bridgeDetected && (
				<span className="zettlebank-bridge-badge"> ⬡ Bridge</span>
			)}
		</h4>
		{audit?.narrative_summary && (
			<p className="zettlebank-bridge-summary">{audit.narrative_summary}</p>
		)}
		<div className="zettlebank-beatmatrix-grid">
			{BEAT_ROWS.map((row) => (
				<div key={row.act} className="zettlebank-beatmatrix-row">
					<span className="zettlebank-beatmatrix-act" title={row.act}>
						{row.kanji}
					</span>
					{row.beats.map((b) => (
						<button
							key={b.slug}
							type="button"
							className={[
								"zettlebank-beat-cell",
								selected === b.slug ? "is-selected" : "",
							]
								.filter(Boolean)
								.join(" ")}
							onClick={() => onSelect(b.slug)}
							title={`code/${b.slug}`}
						>
							{b.n}
						</button>
					))}
				</div>
			))}
		</div>
		{selected && (
			<p className="zettlebank-beat-selected">
				Selected: <code>code/{selected}</code>
			</p>
		)}
	</div>
);

// ---------------------------------------------------------------------------
// Description Card — editable text field
// ---------------------------------------------------------------------------

/** Editable textarea for reviewing and adjusting the backend-proposed description before approval. */
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

/**
 * A single smart-relation row.
 * - Click the header row to toggle accept/reject.
 * - The link input is editable so users can correct the target note before approving.
 */
const RelationCard: FC<{
	rel: SmartRelation;
	accepted: boolean;
	editedLink: string;
	onToggle: () => void;
	onLinkChange: (next: string) => void;
}> = ({ rel, accepted, editedLink, onToggle, onLinkChange }) => (
	<div
		className={`zettlebank-relation-card ${accepted ? "is-accepted" : "is-rejected"}`}
	>
		<div className="zettlebank-relation-header" onClick={onToggle}>
			<span
				className="zettlebank-relation-type"
				style={{ color: RELATION_COLORS[rel.type] }}
			>
				{rel.type}
			</span>
			<span className="zettlebank-relation-confidence">
				{(rel.confidence * 100).toFixed(0)}%
			</span>
			<span className="zettlebank-relation-status">
				{accepted ? "accepted" : "rejected"}
			</span>
		</div>
		<input
			className="zettlebank-relation-link-input"
			type="text"
			value={editedLink}
			onChange={(e) => onLinkChange(e.target.value)}
			placeholder="Target note"
			onClick={(e) => e.stopPropagation()}
		/>
	</div>
);

// ---------------------------------------------------------------------------
// Staging Area — the full approval interface
//
// The user reviews AI suggestions as interactive cards, toggles individual
// tags / relations, edits the description, then hits "Approve & Write".
// Nothing touches frontmatter until the user confirms.
// ---------------------------------------------------------------------------

/**
 * The full approval interface. Lets the user toggle individual tags and relations,
 * edit the description, then confirm with "Approve & Write". Nothing touches
 * frontmatter until the user clicks Approve.
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

	const tagGroups = groupTagsByPrefix(data.metadata.tags);

	// -- Description editing --
	const [description, setDescription] = useState(
		data.metadata.description ?? ""
	);

	// -- Relation toggles (all accepted by default) --
	const [acceptedRels, setAcceptedRels] = useState<Set<string>>(
		() =>
			new Set(
				data.metadata.smart_relations.map((r) => `${r.link}::${r.type}`)
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

	// -- Relation link editing (target note overrides) --
	const [linkEdits, setLinkEdits] = useState<Map<string, string>>(
		() =>
			new Map(
				data.metadata.smart_relations.map((r) => [
					`${r.link}::${r.type}`,
					r.link,
				])
			)
	);

	const updateLink = useCallback((key: string, next: string) => {
		setLinkEdits((prev) => new Map(prev).set(key, next));
	}, []);

	// -- Beat selection via BeatMatrix --
	// Initialised from the server-proposed code/ tag or narrative_audit beat.
	const [selectedBeat, setSelectedBeat] = useState<string>(() => {
		const codeTag = data.metadata.tags.find((t) => t.startsWith("code/"));
		return codeTag
			? codeTag.slice(5)
			: (data.narrative_audit?.beat_position ?? "");
	});

	const selectBeat = useCallback((beat: string) => {
		setSelectedBeat(beat);
		// Replace all code/ tags in acceptedTags with the new selection
		setAcceptedTags((prev) => {
			const next = new Set(prev);
			for (const t of next) {
				if (t.startsWith("code/")) next.delete(t);
			}
			next.add(`code/${beat}`);
			return next;
		});
	}, []);

	// -- Build ApprovedPayload and hand to plugin --
	const handleApprove = useCallback(() => {
		const approvedRelations = data.metadata.smart_relations
			.filter((r) => acceptedRels.has(`${r.link}::${r.type}`))
			.map((r) => ({
				...r,
				link: linkEdits.get(`${r.link}::${r.type}`) ?? r.link,
			}));

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
	}, [data, acceptedTags, description, acceptedRels, linkEdits, onApprove]);

	return (
		<div className="zettlebank-staging">
			{/* Description card */}
			<DescriptionCard value={description} onChange={setDescription} />

			{/* Beat Matrix — always visible; shows bridge summary when bridge_detected */}
			<BeatMatrix
				selected={selectedBeat}
				onSelect={selectBeat}
				audit={data.narrative_audit ?? null}
				bridgeDetected={data.bridge_detected ?? false}
			/>

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

			{/* Smart relations with editable target links */}
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
								editedLink={linkEdits.get(key) ?? rel.link}
								onToggle={() => toggleRelation(key)}
								onLinkChange={(v) => updateLink(key, v)}
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
// Root sidebar component
// ---------------------------------------------------------------------------

/**
 * Root sidebar component. Switches between idle, loading, error, and staging
 * views based on the state passed down from `ZettleBankView` (the Obsidian ItemView).
 * All state lives in the ItemView — this component is purely presentational.
 */
export const ZettleBankSidebar: FC<SidebarProps> = ({
	state,
	onAnalyze,
	onApprove,
}) => {
	return (
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
					<p>
						Select a note and click Analyze to extract narrative
						metadata.
					</p>
				</div>
			)}

			{state.phase === "loading" && (
				<div className="zettlebank-loading">Analyzing note...</div>
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
};
