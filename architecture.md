# Architecture Overview

## System Topology
The ZettleBank plugin operates on a decoupled Client-Server architecture to bypass Obsidian's Electron constraints.

### Frontend (Obsidian Environment)
- **Framework**: TypeScript / React mounted to a custom `ItemView` (Sidebar).
- **Responsibility**: State management, File I/O, User Interface, and "Shadow Database" rendering.
- **Communication**: Sends JSON payloads to `localhost:8000/analyze` via HTTP POST.

### Backend (Intelligence Layer)
- **Runtime**: Local Python server (FastAPI + Uvicorn).
- **Responsibility**: Graph state persistence (NetworkX), Community detection (Leiden), and LLM Inference (Ollama/Local LLMs).

## The Data Contract
Strict adherence to the schema is required to prevent "Metadata Drift".

### Target Fields (Ontology)
The backend must extract specific narrative buckets, not generic tags:
- `Topics` (High-level subjects)
- `Emotions` (Affective tone)
- `Character Arc` (Developmental beats)
- `Story Beat` (Structural components)

### Relation Types (Controlled Vocabulary)
Edges must use this specific vocabulary to ensure the graph remains queryable:
- `contradicts` / `supports`
- `potential_to` / `kinetic_to`
- `motivates` / `hinders`