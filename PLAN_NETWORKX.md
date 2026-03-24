# PLAN_NETWORKX.md — ZettleBank Graph Refactoring Plan

**Status**: For review — no code has been changed.
**Scope**: `server.py`, `schema.ts`, and the persisted `vault_graph.json` migration.
**Goal**: Richer edge matrix, corrected resolution constants, structured `_partition_subgraphs()`, and structural-hole detection via `nx.constraint` for Ten-pivot identification.

---

## 0. Pre-Refactoring Audit

### What the current code actually is

| Component | Current state | Issue |
|-----------|--------------|-------|
| `graph` (line 159) | `nx.DiGraph()` | Already directed — but edge attributes are thin (`relation_type`, `weight` only) |
| `_nx_to_igraph()` (line 481) | Creates `ig.Graph(directed=True)` | Passes directed graph to leidenalg, but `RBConfigurationVertexPartition` is a modularity-based method that implicitly symmetrises weights anyway — the direction is silently discarded |
| `RESOLUTION_MACRO` (line 59) | `0.5` | Produces fewer, larger communities; with a 55-note vault this often yields 2–3 clusters, not 4 Kishōtenketsu acts |
| `RESOLUTION_MICRO` (line 58) | `2.0` | Fine-grained, but no sweep mechanism |
| `_detect_multi_resolution()` (line 538) | Runs both tiers | Returns flat dicts with no subgraph identity; no structural-hole pass |
| `SmartRelation.link` (line 195) | Note stem string | Ambiguous name; no act or provenance tracking |
| `SmartRelation.type` (line 197) | `RelationType` enum | `type` shadows Python builtin, confusing JSON key |
| Stage D prompt (line 883) | `qi / law / mu` | Violates CLAUDE.md Rule 2 — must use Kishōtenketsu beat codes |
| `CODE_VALUES` (line 111) | `{"qi", "law", "mu"}` | Same violation |
| `nx.constraint` | Not called | Structural holes undetected; Ten nodes not identified |

### What the code correctly does already

- `graph = nx.DiGraph()` is already directed. The refactor does **not** need to replace the data structure — it needs to correctly exploit directionality in edge attributes and use the undirected projection only where appropriate (Leiden partitioning, constraint computation).
- `_nx_to_igraph()` correctly builds the conversion scaffolding; it needs one change: convert to undirected before partitioning.
- The multi-stage pipeline architecture (A→B‖C‖D) is sound and does not change.

---

## 1. Schema Changes — `schema.ts` and `server.py` Pydantic Models

These must be updated in a single commit (Rule 3, Schema Contract v1.0).

### 1.1 New Enums

#### server.py — add after `RelationType` (after line 184)

```python
class ProvenanceType(str, Enum):
    """How was this edge generated?"""
    sc_embedding = "sc_embedding"   # cosine similarity from Smart Connections vectors
    wikilink     = "wikilink"       # [[wiki-link]] extracted from note body
    llm          = "llm"            # Ollama LLM classification
    leiden       = "leiden"         # Leiden community co-membership inference

class NarrativeAct(str, Enum):
    """Kishōtenketsu macro-act assignment."""
    ki    = "ki"
    sho   = "sho"
    ten   = "ten"
    ketsu = "ketsu"
```

#### schema.ts — add after `RelationTypeEnum`

```typescript
export const ProvenanceEnum = z.enum([
    "sc_embedding",
    "wikilink",
    "llm",
    "leiden",
]);
export type Provenance = z.infer<typeof ProvenanceEnum>;

export const NarrativeActEnum = z.enum(["ki", "sho", "ten", "ketsu"]);
export type NarrativeAct = z.infer<typeof NarrativeActEnum>;
```

### 1.2 Replace `SmartRelation` with `EdgeMatrix`

The rename addresses three problems simultaneously:
1. `link` → `target_id`: clarifies that this is a normalized note stem, not an Obsidian `[[...]]` wiki-link string.
2. `type` → `relation_type`: eliminates the Python builtin shadow and the ambiguous JSON key.
3. Adds `narrative_act` and `provenance` as first-class fields, enabling graph queries by act and edge audit trails.

#### server.py — replace `SmartRelation` (lines 187–198)

**Before:**
```python
class SmartRelation(BaseModel):
    link: str
    type: RelationType = RelationType.related
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
```

**After:**
```python
class EdgeMatrix(BaseModel):
    """A directed edge in the narrative graph — the shared wire format.

    Field names match the YAML frontmatter template exactly (Rule 4).
    All five fields are required on the wire; defaults exist only for
    programmatic construction during fallback paths.

      target_id     : normalized note stem (e.g. "khmer-tiger-spirit")
      relation_type : controlled vocabulary label (ADR-002)
      narrative_act : Kishōtenketsu macro-act of the TARGET node
      confidence    : float [0, 1]; source depends on provenance
      provenance    : how was this edge generated
    """
    target_id:     str           = Field(..., min_length=1)
    relation_type: RelationType  = RelationType.related
    narrative_act: NarrativeAct  = NarrativeAct.ki
    confidence:    float         = Field(default=1.0, ge=0.0, le=1.0)
    provenance:    ProvenanceType = ProvenanceType.wikilink
```

> **Backward-compatibility note**: `SmartRelation` is removed entirely. Any code that constructs or reads `SmartRelation` must be updated to `EdgeMatrix`. The YAML frontmatter key `smart_relations` is preserved; only the sub-fields change. Existing notes will need a one-time migration (see §6).

#### schema.ts — replace `SmartRelationSchema`

**Before:**
```typescript
export const SmartRelationSchema = z.object({
    link: z.string().min(1),
    type: RelationTypeEnum,
    confidence: z.number().min(0).max(1),
});
export type SmartRelation = z.infer<typeof SmartRelationSchema>;
```

**After:**
```typescript
export const EdgeMatrixSchema = z.object({
    target_id:     z.string().min(1),
    relation_type: RelationTypeEnum,
    narrative_act: NarrativeActEnum,
    confidence:    z.number().min(0).max(1),
    provenance:    ProvenanceEnum,
});
export type EdgeMatrix = z.infer<typeof EdgeMatrixSchema>;

// Deprecated alias — remove after all vault notes are migrated (§6)
/** @deprecated Use EdgeMatrixSchema */
export const SmartRelationSchema = EdgeMatrixSchema;
```

### 1.3 Update `NarrativeMetadata`

#### server.py — update field type (lines 219–221)

**Before:**
```python
smart_relations: list[SmartRelation] = Field(default_factory=list, ...)
```

**After:**
```python
smart_relations: list[EdgeMatrix] = Field(
    default_factory=list,
    description="Directed edges to other notes. Each edge carries act and provenance.",
)
```

#### schema.ts — update `NarrativeMetadataSchema`

**Before:**
```typescript
smart_relations: z.array(SmartRelationSchema).default([]),
```

**After:**
```typescript
smart_relations: z.array(EdgeMatrixSchema).default([]),
```

### 1.4 New Model: `StructuralHoleInfo`

Carries Burt's constraint result back to the frontend for Ten-candidate display.

#### server.py — add after `AnalyzeResponse`

```python
class StructuralHoleInfo(BaseModel):
    """Burt's constraint score for the analyzed note.

    constraint ∈ [0, 1]:
      0.0 → pure structural hole (bridges otherwise-disconnected clusters)
      1.0 → fully constrained (all neighbours are mutually connected)

    is_ten_candidate is True when constraint < TEN_CONSTRAINT_THRESHOLD.
    This is how we identify Ten (pivot) transitions in the Kishōtenketsu matrix.
    """
    note_id:          str
    constraint:       float
    is_ten_candidate: bool
```

#### schema.ts — add after `CommunityTierSchema`

```typescript
export const StructuralHoleSchema = z.object({
    note_id:          z.string().min(1),
    constraint:       z.number().min(0).max(1),
    is_ten_candidate: z.boolean(),
});
export type StructuralHoleInfo = z.infer<typeof StructuralHoleSchema>;
```

### 1.5 Update `AnalyzeResponse`

Adds `structural_hole` and `narrative_act` so the frontend can show the note's act position and Ten-candidate flag without a separate API call.

#### server.py

**Before:**
```python
class AnalyzeResponse(BaseModel):
    note_id: str
    metadata: NarrativeMetadata
    community_id: Optional[int] = None
    community_tiers: list[CommunityTier] = Field(default_factory=list)
```

**After:**
```python
class AnalyzeResponse(BaseModel):
    note_id:         str
    metadata:        NarrativeMetadata
    community_id:    Optional[int]              = None
    community_tiers: list[CommunityTier]        = Field(default_factory=list)
    narrative_act:   Optional[NarrativeAct]     = None   # macro-act of this note
    structural_hole: Optional[StructuralHoleInfo] = None  # None if graph too small
```

#### schema.ts

**Before:**
```typescript
export const AnalyzeResponseSchema = z.object({
    note_id: z.string().min(1),
    metadata: NarrativeMetadataSchema,
    community_id: z.number().int().nullable(),
    community_tiers: z.array(CommunityTierSchema).default([]),
});
```

**After:**
```typescript
export const AnalyzeResponseSchema = z.object({
    note_id:         z.string().min(1),
    metadata:        NarrativeMetadataSchema,
    community_id:    z.number().int().nullable(),
    community_tiers: z.array(CommunityTierSchema).default([]),
    narrative_act:   NarrativeActEnum.nullable().default(null),
    structural_hole: StructuralHoleSchema.nullable().default(null),
});
```

---

## 2. Constants Update — `server.py` lines 58–59

The MACRO resolution of `0.5` was tuned for the Louvain era. At γ=0.5 with a 55-note vault, we typically get 2–3 mega-clusters, not 4 distinct acts. Raising to γ=1.0 brings the expected community count into the 4–6 range, giving a plausible mapping to the four Kishōtenketsu acts.

The MICRO range of `2.0–5.0` is intentionally wider: small vaults produce ≤16 micro-communities at γ=2.0 already; larger vaults may need γ up to 5.0 to reach the 16-beat target.

**Before:**
```python
RESOLUTION_MICRO = 2.0
RESOLUTION_MACRO = 0.5
```

**After:**
```python
RESOLUTION_MACRO     = 1.0   # γ≈1.0 → ~4 macro communities → Kishōtenketsu acts
RESOLUTION_MICRO     = 2.0   # γ≈2.0 default → fine scene beats
RESOLUTION_MICRO_MAX = 5.0   # upper sweep bound for _partition_subgraphs()

TEN_CONSTRAINT_THRESHOLD = 0.5  # Burt constraint below this → Ten candidate
```

---

## 3. Graph Edge Attribute Schema

All `graph.add_edge()` calls must be updated to carry the five EdgeMatrix fields. The `weight` attribute is kept as an **alias** for `confidence` because leidenalg reads `graph.es["weight"]` during partition.

### 3.1 Updated `_upsert_edges()` (lines 288–296)

**Before:**
```python
def _upsert_edges(source: str, relations: list[SmartRelation]) -> None:
    for rel in relations:
        graph.add_edge(
            source,
            rel.link,
            relation_type=rel.type.value,
            weight=rel.confidence,
        )
```

**After:**
```python
def _upsert_edges(source: str, edges: list[EdgeMatrix]) -> None:
    """Write EdgeMatrix objects into the persistent DiGraph.

    `weight` mirrors `confidence` so leidenalg can read it without
    special handling. Do not remove the `weight` attribute.
    """
    for edge in edges:
        graph.add_edge(
            source,
            edge.target_id,
            relation_type=edge.relation_type.value,
            narrative_act=edge.narrative_act.value,
            confidence=edge.confidence,
            provenance=edge.provenance.value,
            weight=edge.confidence,          # leidenalg alias
        )
```

### 3.2 Updated `_upsert_wikilink_edges()` (lines 299–308)

Wiki-links are provenance=`wikilink`, act defaults to `ki` (introductory context — the most conservative assumption; Stage A will refine act assignment after Leiden runs).

**After:**
```python
def _upsert_wikilink_edges(source: str, targets: list[str]) -> None:
    """Create 'related' wikilink edges with full EdgeMatrix attributes."""
    for target in targets:
        if target != source:
            graph.add_edge(
                source, target,
                relation_type="related",
                narrative_act="ki",         # default; overwritten by Leiden pass
                confidence=0.5,
                provenance="wikilink",
                weight=0.5,
            )
```

### 3.3 Updated `_generate_smart_relations()` → `_generate_edges()` (lines 450–464)

**After:**
```python
def _generate_edges(
    note_id: str,
    neighbors: list[tuple[str, float]],
    macro_membership: dict[str, int],
    act_map: dict[int, NarrativeAct],
    limit: int = 5,
) -> list[EdgeMatrix]:
    """Build EdgeMatrix objects from top-K embedding neighbors.

    `act_map` maps macro community_id → NarrativeAct so each edge
    carries the act of its target node at construction time.
    """
    edges: list[EdgeMatrix] = []
    for neighbor_id, sim_score in neighbors[:limit]:
        rel_type = _infer_relation_type(note_id, neighbor_id, sim_score)
        target_community = macro_membership.get(neighbor_id)
        act = act_map.get(target_community, NarrativeAct.ki) if target_community is not None else NarrativeAct.ki
        edges.append(EdgeMatrix(
            target_id=neighbor_id,
            relation_type=rel_type,
            narrative_act=act,
            confidence=round(min(sim_score, 1.0), 3),
            provenance=ProvenanceType.sc_embedding,
        ))
    return edges
```

> **Note**: `_generate_edges()` now takes `macro_membership` and `act_map` as parameters. This means it must be called **after** Stage A's Leiden pass, not before. The `/analyze` handler call order must be updated (see §5.3).

---

## 4. `_partition_subgraphs()` — Replacing `_detect_multi_resolution()`

### 4.1 Design Decisions

**Why undirected for partitioning?**
`RBConfigurationVertexPartition` (Leiden) is defined over undirected modularity. When given a directed igraph, leidenalg symmetrises the adjacency matrix internally — but inconsistently across versions. Making the conversion explicit in our code makes the behaviour version-stable and testable.

**Why γ=1.0 for macro?**
At γ=1.0 (the "natural" resolution), Leiden recovers communities of roughly equal density. For a vault of 55–200 notes, this typically yields 4–7 communities. We label the 4 largest by Kishōtenketsu act order based on their structural position (act_assignment, see §4.3).

**Why a sweep for micro?**
A fixed γ=2.0 may yield only 5–6 communities on a small vault, not 16 beats. The sweep finds the lowest γ in [2.0, 5.0] that produces ≥ `target_beats` distinct communities (default 8; we don't rigidly demand 16 because not all vaults have enough notes).

### 4.2 Return Type

```python
from dataclasses import dataclass

@dataclass
class SubgraphPartition:
    macro_membership:  dict[str, int]       # note_id → macro community_id
    micro_membership:  dict[str, int]       # note_id → micro community_id
    macro_labels:      dict[int, str]       # community_id → human label
    micro_labels:      dict[int, str]       # community_id → human label
    act_map:           dict[int, NarrativeAct]  # macro community_id → NarrativeAct
    gamma_micro_used:  float                # actual γ used for micro pass
```

### 4.3 Act Assignment Heuristic

After the macro Leiden pass, we have N communities (ideally 4). We assign acts by **structural ordering**:

1. Compute each community's **mean out-degree** of its member nodes.
2. Sort communities ascending by mean out-degree.
3. Assign acts in Kishōtenketsu sequence: lowest out-degree → `ki`, next → `sho`, highest-jump outlier → `ten`, remaining → `ketsu`.

Rationale: Ki notes introduce concepts with few outgoing links (low out-degree). Shō notes develop themes and gain more connections. Ten nodes bridge otherwise-disconnected clusters (structural holes — confirmed by `nx.constraint` in §5.2). Ketsu notes synthesise and tend to link back to Ki nodes.

If N ≠ 4: map proportionally (N=3 → ki/ten/ketsu, N=5 → ki/ki/sho/ten/ketsu, etc.). Document the mapping in the returned `act_map`.

### 4.4 Full Function Signature and Logic

```python
def _partition_subgraphs(
    gamma_macro:  float = RESOLUTION_MACRO,      # 1.0
    gamma_micro:  float = RESOLUTION_MICRO,      # 2.0 start
    gamma_micro_max: float = RESOLUTION_MICRO_MAX,  # 5.0
    target_micro_communities: int = 8,
) -> SubgraphPartition:
    """Dual-resolution Leiden partition with act assignment.

    Algorithm:
    1. Convert DiGraph → undirected igraph (preserving `weight` attribute).
    2. Macro pass at gamma_macro → N communities → assign NarrativeAct labels.
    3. Micro sweep from gamma_micro to gamma_micro_max:
       - Run Leiden at each γ (step 0.5).
       - Stop when number of communities >= target_micro_communities.
       - Use the last γ that meets the target (or gamma_micro_max if never met).
    4. Return SubgraphPartition.

    Falls back to trivial partition {all nodes → 0} if graph has < 2 nodes.
    """
```

### 4.5 `_nx_to_igraph()` Change

One line changes: create an **undirected** igraph for partitioning.

**Before (line 486):**
```python
ig_graph = ig.Graph(directed=True)
```

**After:**
```python
ig_graph = ig.Graph(directed=False)  # undirected projection for Leiden
# Weight: use `confidence` attribute; fall back to `weight`, then 1.0
weights = [
    graph[u][v].get("confidence", graph[u][v].get("weight", 1.0))
    for u, v in graph.edges()
]
```

> The DiGraph edge (u→v) and reverse (v→u) both become the same undirected edge. If both directions exist, `ig.Graph` will accept both — igraph silently deduplicates parallel edges by default. We should sum their weights: `ig_graph.es["weight"]` = summed confidence when both directions exist. Implement this via an explicit edge accumulation loop.

---

## 5. `nx.constraint` — Structural Hole Detection

### 5.1 Mathematical Grounding

Burt's constraint C_i measures how much a node's contacts are themselves interconnected:

```
C_i = Σ_{j≠i} ( p_ij + Σ_{q≠i,q≠j} p_iq · p_qj )²
```

Where `p_ij = a_ij / Σ_k a_ik` (normalized edge weight to j from i).

- C_i → 0: node i bridges many otherwise-unconnected clusters (structural hole = Ten candidate)
- C_i → 1: node i's entire neighbourhood is fully connected (no bridging = not a pivot)

NetworkX implementation: `nx.constraint(G, nodes=None, weight='weight')`.

### 5.2 `_identify_ten_candidates()` Implementation

```python
def _identify_ten_candidates(
    threshold: float = TEN_CONSTRAINT_THRESHOLD,  # 0.5
) -> dict[str, float]:
    """Return {note_id: constraint_score} for nodes below threshold.

    Uses the undirected projection of the DiGraph because Burt's
    original constraint formulation is direction-agnostic (it asks
    "do my contacts know each other?", not "who do they point at?").

    Requires ≥ 3 nodes; returns {} otherwise.
    Isolated nodes (degree 0) are excluded — constraint is undefined for them.
    """
    if graph.number_of_nodes() < 3:
        return {}

    undirected = graph.to_undirected(reciprocal=False)
    # Use `confidence` as weight; fall back to `weight`, then uniform 1.0
    # nx.constraint reads the named weight attribute directly.
    # Rename `confidence` to `weight` on the projected copy so we don't
    # mutate the original graph.
    for u, v, data in undirected.edges(data=True):
        data["weight"] = data.get("confidence", data.get("weight", 1.0))

    eligible = [n for n in undirected.nodes() if undirected.degree(n) > 0]
    if not eligible:
        return {}

    try:
        raw = nx.constraint(undirected, nodes=eligible, weight="weight")
    except (nx.NetworkXError, ZeroDivisionError) as exc:
        logger.warning(f"nx.constraint failed: {exc}")
        return {}

    return {
        node: round(score, 4)
        for node, score in raw.items()
        if score < threshold
    }
```

### 5.3 Integration into `/analyze`

The structural hole score for the analyzed note must be computed after Stage A (graph mutation + Leiden pass). Stage A already does this; we just add one call:

```python
# After _detect_multi_resolution() → _partition_subgraphs() call:
ten_candidates = _identify_ten_candidates()
note_constraint = nx.constraint(
    graph.to_undirected(), nodes=[req.note_id], weight="confidence"
).get(req.note_id)

structural_hole = None
if note_constraint is not None:
    structural_hole = StructuralHoleInfo(
        note_id=req.note_id,
        constraint=round(note_constraint, 4),
        is_ten_candidate=note_constraint < TEN_CONSTRAINT_THRESHOLD,
    )
```

The `narrative_act` for the response note is read from `partition.act_map[macro_id]`.

### 5.4 New Endpoint: `/graph/structural-holes`

```python
@app.get("/graph/structural-holes")
async def get_structural_holes(threshold: float = TEN_CONSTRAINT_THRESHOLD):
    """Return all nodes with Burt constraint below threshold.

    These are Ten-candidate notes — structural bridges between communities.
    Query parameter `threshold` defaults to TEN_CONSTRAINT_THRESHOLD (0.5).
    """
    candidates = _identify_ten_candidates(threshold=threshold)
    return {
        "threshold": threshold,
        "ten_candidates": [
            {"note_id": nid, "constraint": score}
            for nid, score in sorted(candidates.items(), key=lambda x: x[1])
        ],
    }
```

---

## 6. Stage D Prompt Fix — `CODE_VALUES` and `_run_stage_d_llm_classify()`

This is a Rule 2 violation in the current code (CLAUDE.md). Must be fixed in the same PR as the schema changes.

### 6.1 Update constants (lines 110–111)

**Before:**
```python
AFFECT_VALUES = {"positive", "negative", "mu"}
CODE_VALUES = {"qi", "law", "mu"}
```

**After:**
```python
AFFECT_VALUES = {"positive", "negative", "mu"}

# 16-beat Kishōtenketsu codes (CLAUDE.md Rule 2)
CODE_VALUES = {
    "ki-1", "ki-2", "ki-3", "ki-4",
    "sho-5", "sho-6", "sho-7", "sho-8",
    "ten-9", "ten-10", "ten-11", "ten-12",
    "ketsu-13", "ketsu-14", "ketsu-15", "ketsu-16",
    "unplaced",
}
```

### 6.2 Update Stage D prompt (lines 883–917)

The Ollama prompt must be updated from a 3-choice classification to the 16-beat discriminative list. The fallback changes from `"mu"` to `"ki-1"` (CLAUDE.md Rule 2: "The fallback is `code/ki-1` (unplaced introduction), not omission.").

**New prompt structure (abbreviated — write the full text):**
```
Classify the following text. Respond with a JSON object with two keys:
- "affect": one of "positive", "negative", or "mu"
- "code": one of the following Kishōtenketsu beat codes:
    ki-1 (world establishment), ki-2 (subject emergence),
    ki-3 (status quo), ki-4 (threshold),
    sho-5 (engagement), sho-6 (complication),
    sho-7 (interweaving), sho-8 (suspension),
    ten-9 (pivot/reframe), ten-10 (revelation),
    ten-11 (cascade), ten-12 (inversion),
    ketsu-13 (synthesis), ketsu-14 (integration),
    ketsu-15 (resolution), ketsu-16 (echo),
    unplaced (none of the above)

Choose the single beat that best describes this text's narrative function.
Output ONLY the JSON object, no explanation.

Text:
{content[:3000]}

JSON:
```

**Fallback change (line 873):**
```python
# Before
return {"affect": "mu", "code": "mu"}
# After
return {"affect": "mu", "code": "ki-1"}
```

**Validation change (lines 912–916):**
```python
# Before
if code not in CODE_VALUES:
    code = "mu"
# After
if code not in CODE_VALUES:
    code = "ki-1"
```

---

## 7. `vault_graph.json` — Persistence Migration

The persisted graph uses the old edge schema (`relation_type`, `weight`). On server startup, `_load_graph()` must detect the schema version and handle old data.

### 7.1 Schema Version Strategy

On `_save_graph()`, write a top-level `schema_version` key:

```python
def _save_graph() -> None:
    data = nx.node_link_data(graph)
    data["schema_version"] = 2          # increment on schema change
    GRAPH_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

On `_load_graph()`, check version and migrate edge attributes:

```python
def _load_graph() -> None:
    global graph
    if not GRAPH_PATH.exists():
        return
    raw = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    schema_version = raw.pop("schema_version", 1)
    edges_key = "links" if "links" in raw else "edges"
    graph = nx.node_link_graph(raw, directed=True, edges=edges_key)

    if schema_version < 2:
        _migrate_graph_v1_to_v2()
        _save_graph()   # rewrite with new schema
        logger.info("Graph migrated from schema v1 → v2")


def _migrate_graph_v1_to_v2() -> None:
    """Back-fill missing EdgeMatrix attributes on all edges."""
    for u, v, data in graph.edges(data=True):
        if "narrative_act" not in data:
            data["narrative_act"] = "ki"
        if "provenance" not in data:
            # Infer from existing edge attribute or default
            data["provenance"] = (
                "wikilink" if data.get("relation_type") == "related" else "sc_embedding"
            )
        if "confidence" not in data:
            data["confidence"] = data.get("weight", 0.5)
        data["weight"] = data["confidence"]   # ensure alias is set
        # v1 used `type` in some paths — normalise to `relation_type`
        if "type" in data and "relation_type" not in data:
            data["relation_type"] = data.pop("type")
```

---

## 8. `/analyze` Handler Rewrite — Updated Call Order

The handler must be updated to thread `macro_membership` and `act_map` into `_generate_edges()` (formerly `_generate_smart_relations()`). The Leiden pass must happen before edge generation, not after.

### Current call order (lines 1013–1020):
```
1. _classify_relations()     ← calls _find_top_k_neighbors + _generate_smart_relations
2. _extract_wikilinks()
3. _upsert_node()
4. _upsert_edges()           ← writes edges with OLD SmartRelation
5. _upsert_wikilink_edges()
6. _save_graph()
7. _detect_multi_resolution() ← Leiden runs here
```

### New call order:
```
1. _extract_wikilinks()
2. _upsert_node()
3. _upsert_wikilink_edges()  ← wikilinks first (provenance=wikilink)
4. _save_graph()             ← temporary save so Leiden has graph context
5. partition = _partition_subgraphs()  ← Leiden with new γ values
6. neighbors = _find_top_k_neighbors()
7. edges = _generate_edges(neighbors, partition.macro_membership, partition.act_map)
8. _upsert_edges(edges)      ← NOW we write SC edges with correct act labels
9. _save_graph()             ← final save
10. structural_hole = _identify_ten_candidates() for this note
```

Rationale: `_generate_edges()` needs `act_map` from the Leiden output to label each edge's `narrative_act`. If we run Leiden before embedding-neighbor edge insertion, the act labels are slightly less accurate (the SC edges aren't in the graph yet for Leiden), but this is a necessary ordering trade-off. An alternative — running Leiden twice (once before SC edges, once after) — is too expensive for a request-time operation.

---

## 9. CLAUDE.md and `decisions.md` Updates Required

After this refactoring, two documentation updates are needed:

### 9.1 CLAUDE.md
- Rule 2 §Mapping to the Pipeline: Update "Stage D" to reference the 16-beat codes (already correct in the plan; the code was wrong).
- Rule 6 §Graph Logic: Update `RESOLUTION_MACRO` reference from γ=0.5 to γ=1.0.
- Rule 7 §Multi-Stage Pipeline: Note that Leiden now runs before SC edge generation.
- Add entry for `TEN_CONSTRAINT_THRESHOLD` under Rule 6.

### 9.2 `decisions.md`
Add **ADR-004: Undirected Projection for Leiden / Directed Graph for Constraint**:
> **Context**: Leiden (RBConfigurationVertexPartition) is defined over undirected modularity and implicitly symmetrises directed adjacency anyway. Burt's constraint is defined over ego networks and is also direction-agnostic in its standard form.
> **Decision**: Convert DiGraph → undirected projection (`.to_undirected()`) before calling both leidenalg and `nx.constraint`. The directed graph is preserved for traversal queries (`/graph/node/{id}`, outgoing vs incoming edge counts).
> **Consequence**: Community detection does not distinguish "note A cites note B" from "note B cites note A". This is acceptable because both directions indicate narrative proximity.

---

## 10. Implementation Order (for a safe incremental rollout)

```
Step 1 — Constants and enums only (no logic change)
  server.py: add ProvenanceType, NarrativeAct, update RESOLUTION_MACRO/MICRO
  schema.ts: add ProvenanceEnum, NarrativeActEnum
  Risk: zero — new symbols, no call sites yet

Step 2 — Replace SmartRelation with EdgeMatrix (schema + wire)
  server.py: rename class, update NarrativeMetadata
  schema.ts: rename schema, add deprecated alias
  server.py: update _upsert_edges, _upsert_wikilink_edges, _generate_edges
  Risk: breaking if any code still imports SmartRelation; audit all call sites first

Step 3 — Graph persistence migration
  server.py: update _save_graph, _load_graph, add _migrate_graph_v1_to_v2
  Test: delete vault_graph.json, re-ingest, verify schema_version=2 in output
  Risk: data loss if migration logic is wrong — backup vault_graph.json first

Step 4 — Replace _detect_multi_resolution with _partition_subgraphs
  server.py: add SubgraphPartition dataclass, implement _partition_subgraphs
  Update _nx_to_igraph to produce undirected igraph
  Risk: Leiden output changes due to γ shift (0.5→1.0) — expect different community assignments

Step 5 — Implement _identify_ten_candidates + structural hole endpoint
  server.py: add _identify_ten_candidates, add /graph/structural-holes route
  schema.ts: add StructuralHoleSchema
  Risk: nx.constraint is O(n³) on dense graphs — add a node-count guard (skip if > 500 nodes)

Step 6 — Update /analyze handler call order and response model
  server.py: rewrite Stage A sequence (see §8), add structural_hole and narrative_act to response
  schema.ts: update AnalyzeResponseSchema
  Risk: behavioral change in analyze — run test_workflow.py before and after

Step 7 — Fix Stage D prompt (CODE_VALUES + LLM prompt)
  server.py: update CODE_VALUES, _run_stage_d_llm_classify prompt text, fallback
  Risk: LLM outputs; test with a few representative notes via /analyze
  Note: this step can be done independently of Steps 1-6

Step 8 — Update documentation
  CLAUDE.md: update γ references, add TEN_CONSTRAINT_THRESHOLD
  decisions.md: add ADR-004
```

---

## 11. Risk Register

| Risk | Severity | Mitigation |
|------|----------|-----------|
| `nx.constraint` is O(n³) on dense graphs | Medium | Add guard: skip if `graph.number_of_nodes() > 500`; cache result per graph-mutation event |
| γ=1.0 produces different community assignments than γ=0.5, invalidating existing `community_id` values stored in vault frontmatter | Low-Medium | `community_id` is advisory (display only, not a primary key). Notes will be re-assigned on next `/analyze` call. No data corruption. |
| leidenalg version variance in undirected handling | Low | Pinned to `leidenalg>=0.10.2` in requirements.txt; the explicit undirected conversion makes this version-stable |
| `EdgeMatrix.target_id` vs `SmartRelation.link` in existing frontmatter YAML | Medium | The YAML key is `smart_relations` (unchanged). Sub-fields change. Existing notes with old frontmatter will have `link:` instead of `target_id:`. The deprecated alias in schema.ts handles the read path; the write path (processFrontMatter) will overwrite with new field names on next Approve. |
| Stage D LLM refuses to pick a 16-beat code (longer prompt = more hallucination risk with tinyllama) | Medium | Discriminative fallback to `"ki-1"` is already in the plan. Consider switching OLLAMA_MODEL from `llama3.2` to a model with better instruction-following for structured output if refusals are frequent. |
| `SubgraphPartition.act_map` heuristic (§4.3) misassigns acts on atypical vaults | Low | Act assignment is a display label, not a hard constraint. The `narrative_act` on each edge is derived from Leiden community membership, not hardcoded. Worst case: communities are mislabelled but graph topology is correct. |
