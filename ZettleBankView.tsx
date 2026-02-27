import { useState, useCallback, type FC } from "react";
import type { ApprovedPayload } from "./main";
import type {
	AnalyzeResponse,
	SmartRelation,
	NarrativeMetadata,
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
// Root sidebar component
// ---------------------------------------------------------------------------

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
