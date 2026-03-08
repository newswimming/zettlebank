/**
 * Zod schemas — strict TypeScript mirror of the Pydantic models in server.py.
 *
 * Every backend response is validated through these schemas before it
 * reaches the React sidebar.  This prevents Metadata Drift (architecture.md)
 * by catching contract violations at the boundary rather than deep in the UI.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Controlled vocabulary (architecture.md §Relation Types)
// ---------------------------------------------------------------------------

export const RelationTypeEnum = z.enum([
	"contradicts",
	"supports",
	"potential_to",
	"kinetic_to",
	"motivates",
	"hinders",
	"related",
]);
export type RelationType = z.infer<typeof RelationTypeEnum>;

// ---------------------------------------------------------------------------
// SmartRelation — mirrors server.py SmartRelation
// ---------------------------------------------------------------------------

export const SmartRelationSchema = z.object({
	link: z.string().min(1),
	type: RelationTypeEnum,
	confidence: z.number().min(0).max(1),
});
export type SmartRelation = z.infer<typeof SmartRelationSchema>;

// ---------------------------------------------------------------------------
// NarrativeMetadata — mirrors server.py NarrativeMetadata
// (1:1 with choracle-remote-00/templates/frontmatter-template.md)
// ---------------------------------------------------------------------------

export const NarrativeMetadataSchema = z.object({
	aliases: z.string().nullable().default(null),
	description: z.string().nullable().default(null),
	tags: z.array(z.string()).default([]),
	smart_relations: z.array(SmartRelationSchema).default([]),
	source: z.string().nullable().default(null),
	citationID: z.string().nullable().default(null),
});
export type NarrativeMetadata = z.infer<typeof NarrativeMetadataSchema>;

// ---------------------------------------------------------------------------
// AnalyzeResponse — full /analyze endpoint response
// ---------------------------------------------------------------------------

export const CommunityTierSchema = z.object({
	resolution: z.number(),
	label: z.string(),
	community_id: z.number().int(),
});
export type CommunityTier = z.infer<typeof CommunityTierSchema>;

// ---------------------------------------------------------------------------
// NarrativeAudit — mirrors server.py NarrativeAudit
// Only present when bridge_detected=true (Burt constraint < threshold).
// ---------------------------------------------------------------------------

export const NarrativeAuditSchema = z.object({
	beat_position: z.string().default("unplaced"),
	bridge_note_ids: z.array(z.string()).default([]),
	narrative_summary: z.string().default(""),
});
export type NarrativeAudit = z.infer<typeof NarrativeAuditSchema>;

export const AnalyzeResponseSchema = z.object({
	note_id: z.string().min(1),
	metadata: NarrativeMetadataSchema,
	community_id: z.number().int().nullable(),
	community_tiers: z.array(CommunityTierSchema).default([]),
	bridge_detected: z.boolean().default(false),
	narrative_audit: NarrativeAuditSchema.nullable().default(null),
});
export type AnalyzeResponse = z.infer<typeof AnalyzeResponseSchema>;

// ---------------------------------------------------------------------------
// Validation helper — call at the Client-Server Bridge boundary
// ---------------------------------------------------------------------------

/**
 * Parses a raw `/analyze` response through the full Zod schema.
 * Throws a `ZodError` if the response shape doesn't match the contract,
 * catching Metadata Drift at the Client-Server boundary before it reaches React.
 */
export function validateAnalyzeResponse(raw: unknown): AnalyzeResponse {
	return AnalyzeResponseSchema.parse(raw);
}
