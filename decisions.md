# Architectural Decision Records (ADR)

## ADR-001: Leiden Algorithm over Louvain
- **Context**: Narrative communities require high internal connectivity to represent scenes and acts accurately.
- **Decision**: Use the **Leiden Algorithm**.
- **Rationale**: Unlike Louvain, Leiden guarantees connected communities and allows for a variable "Resolution Parameter" ($\gamma$) to tune for Micro-Clusters (Scenes) vs. Global Themes (Acts).

## ADR-002: Discriminative LLM Classification
- **Context**: Generative LLMs hallucinate arbitrary relationship types (e.g., "enemy_of" vs. "antagonist_to").
- **Decision**: Use a **Discriminative Prompt** strategy.
- **Rationale**: The LLM is restricted to a "classification" task using a fixed list of allowed edges. If the relationship is unclear, it must default to "related" rather than inventing a new type.

## ADR-003: The "Shadow Database" Pattern
- **Context**: Obsidian's native Properties UI cannot render complex nested objects like `smart_relations` (link + type + weight).
- **Decision**: Store data as valid YAML lists (Shadow) but render them exclusively via a custom React Sidebar (Light).
- **Rationale**: This preserves the data in a machine-readable format for plugins like Dataview while providing a rich UI for user interaction.
