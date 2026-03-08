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
// EdgeMatrix enums — mirrors server.py NarrativeActEnum / ProvenanceEnum
// ---------------------------------------------------------------------------

export const NarrativeActEnumSchema = z.enum(["ki", "sho", "ten", "ketsu"]);
export type NarrativeActType = z.infer<typeof NarrativeActEnumSchema>;

export const ProvenanceEnumSchema = z.enum(["sc_embedding", "wikilink", "llm"]);
export type ProvenanceType = z.infer<typeof ProvenanceEnumSchema>;

// ---------------------------------------------------------------------------
// EdgeMatrix — sole relation schema; mirrors server.py EdgeMatrix
//
// The YAML frontmatter key remains `smart_relations` (ADR-003) to preserve
// Obsidian Dataview query compatibility.
// ---------------------------------------------------------------------------

export const EdgeMatrixSchema = z.object({
	target_id:     z.string().min(1),
	relation_type: RelationTypeEnum,
	narrative_act: NarrativeActEnumSchema.default("sho"),
	confidence:    z.number().min(0).max(1),
	provenance:    ProvenanceEnumSchema.default("sc_embedding"),
});
export type EdgeMatrix = z.infer<typeof EdgeMatrixSchema>;

// ---------------------------------------------------------------------------
// NarrativeMetadata — mirrors server.py NarrativeMetadata
// (1:1 with choracle-remote-00/templates/frontmatter-template.md)
// ---------------------------------------------------------------------------

export const NarrativeMetadataSchema = z.object({
	aliases: z.string().nullable().default(null),
	description: z.string().nullable().default(null),
	tags: z.array(z.string()).default([]),
	smart_relations: z.array(EdgeMatrixSchema).default([]),
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

// ---------------------------------------------------------------------------
// StructuralHole — mirrors server.py StructuralHole
// Burt's constraint data for the analyzed note.
// ---------------------------------------------------------------------------

export const StructuralHoleSchema = z.object({
	constraint_score: z.number().min(0).max(1),
	is_ten_candidate: z.boolean(),
});
export type StructuralHole = z.infer<typeof StructuralHoleSchema>;

export const AnalyzeResponseSchema = z.object({
	note_id: z.string().min(1),
	metadata: NarrativeMetadataSchema,
	community_id: z.number().int().nullable(),
	community_tiers: z.array(CommunityTierSchema).default([]),
	bridge_detected: z.boolean().default(false),
	narrative_audit: NarrativeAuditSchema.nullable().default(null),
	narrative_act: z.string().default("sho"),
	structural_hole: StructuralHoleSchema.default({
		constraint_score: 1.0,
		is_ten_candidate: false,
	}),
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
