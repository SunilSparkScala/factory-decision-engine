# Engineering Knowledge Base

This directory contains the synthetic Engineering Knowledge Base for **Apex Automotive Plant 1**.

It provides structured engineering documents, standard operating procedures, maintenance guides, quality precautions, incident reports, and technical constraints used by the Factory Decision Engine to supply retrievable evidence for decision verification.

> **Important Notice:** This phase uses deterministic keyword/topic retrieval. Semantic embeddings and Gemini-based reasoning are intentionally deferred to later phases. All knowledge documents are entirely synthetic and contain no proprietary OEM data.

## Document Categories

1. **`machine_manuals/`**: Technical specs, rated capacities, cycle times, and operational limits (e.g. M01, M05, M17, M23).
2. **`maintenance/`**: Restart protocols, degraded machinery operating guidelines, PM schedules, and changeover protocols.
3. **`quality/`**: First Article Inspection requirements post-transfer, defect containment procedures, and degraded equipment quality precautions.
4. **`production_sops/`**: Machine substitution rules, order resequencing hierarchies, overtime authorization rules, and high-priority order handling.
5. **`incident_reports/`**: Post-mortem reports and lessons learned from past downtime and transfer incidents.
6. **`engineering_constraints/`**: Cross-line station operation compatibility matrices and technical transfer boundaries.

## Frontmatter Schema

Each document uses Markdown with YAML frontmatter:

```yaml
---
document_id: KB-MAN-M17
title: M17 Heavy Duty Robotic Welding Station Manual
document_type: machine_manual
machine_ids:
  - M17
station_ids:
  - STA_B2
line_ids:
  - LINE_B
topics:
  - machine_operation
  - welding
  - capacity
source_type: synthetic
---
```

## Deterministic Evidence-Centric Search Engine (Phase 3B)

The engineering knowledge repository provides deterministic, evidence-centric retrieval. It enables downstream agents to cite verifiable evidence (e.g. *"According to KB-QTY-001..."*) rather than generating ungrounded assertions.

### 1. Transparent Ranking Formula

Relevance scoring is calculated strictly via deterministic heuristic weights without randomness, external APIs, or LLM evaluation:

| Component | Match Criteria | Score Boost | Rationale |
| :--- | :--- | :---: | :--- |
| **Machine ID (Query)** | Query explicitly references machine ID in `doc.machine_ids` | `+12.0` | Queries referencing a specific machine (e.g. `M17`) must strongly prioritize that machine's specs/manuals. |
| **Machine ID (Filter)** | Document passes explicit `machine_id` filter | `+10.0` | Filter adherence confirms exact asset context. |
| **Topic (Filter)** | Document passes explicit `topic` filter | `+8.0` | Filter adherence confirms operational topic constraint. |
| **Topic (Query)** | Query term matches one of `doc.topics` | `+6.0` (per topic) | Direct match against curated domain taxonomy. |
| **Station / Line (Query)** | Station ID or Line ID mentioned in query | `+5.0` | Locational context alignment. |
| **Station / Line (Filter)** | Document passes `station_id` or `line_id` filter | `+5.0` | Explicit topological constraint satisfaction. |
| **Document Type (Filter)** | Document passes explicit `document_type` filter | `+4.0` | Document classification filter satisfaction. |
| **Title Keywords** | Query token found in document title | `+4.0` (per token) | High intent match in document title. |
| **Exact Title Phrase** | Complete query substring found in document title | `+5.0` | Exact title match bonus. |
| **Document Type (Query)** | Document type name or category mentioned in query | `+3.0` | Query intent targeting document type (e.g. "manual", "sop"). |
| **Content Keywords** | Query token found in document content body | `+1.0` (per token) | Substantive text mention. |
| **Term Frequency Bonus** | Additional occurrences of query tokens in content | `+0.2` (max `+2.0`) | Density boost for core terms. |

- **Deterministic Tie-Breaking**: When two documents receive identical relevance scores, ties are broken strictly by `document_id` ascending alphabetically.
- **Stopword Filtering**: Standard English stopwords (e.g. *the, and, in, for, with, at*) are filtered prior to token matching to eliminate spurious false positive matches.
- **Empty & Weak Match Handling**:
  - Unmatched queries or queries below `min_score` return an empty list (`[]`).
  - Strict composable filters ensure incompatible combinations (e.g. `machine_id="M01", topic="laser"`) return empty results without fabricating evidence.

### 2. Contextual Excerpt Generation

Excerpts are generated deterministically:
- Content is parsed into substantive candidate blocks (paragraphs and bullet points, excluding markdown headers).
- Candidate blocks are evaluated for keyword density against query terms, matched topics, and machine IDs.
- The highest-density candidate block is selected, formatted with clean whitespace, bounded to 250 characters, and suffixed with `"..."` if truncated.

### 3. Composable Metadata Filtering

The retrieval engine supports composable multi-attribute filters:
```python
search_engineering_knowledge(
    query="first article inspection requirement",
    machine_id="M17",
    topic="quality",
    document_type="quality"
)
```
Filters act as strict constraints: documents must satisfy all specified filters to be scored.

---

## Future Agent Tool Contract (Phase 4 / Gemini Integration)

This proposed tool contract defines the exact interface for exposing engineering knowledge retrieval to the Gemini decision agent:

### Function Signature

```python
def search_engineering_knowledge(
    query: Optional[str] = None,
    machine_id: Optional[str] = None,
    station_id: Optional[str] = None,
    line_id: Optional[str] = None,
    topic: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: int = 5,
    min_score: float = 0.0,
) -> List[KnowledgeSearchResult]:
    """Search engineering SOPs, machine manuals, quality standards, and incident reports for operational evidence.

    Args:
        query: Free-text keyword query (e.g. 'quality inspection after machine transfer').
        machine_id: Filter by machine identifier (e.g. 'M17', 'M01').
        station_id: Filter by factory station (e.g. 'STA_B2').
        line_id: Filter by production line (e.g. 'LINE_B').
        topic: Filter by engineering domain topic (e.g. 'quality', 'overtime', 'welding').
        document_type: Filter by category ('machine_manual', 'maintenance', 'quality', 'production_sop', 'incident_report', 'engineering_constraint').
        limit: Maximum number of evidence results to return (default 5).
        min_score: Minimum relevance threshold to filter out weak evidence (default 0.0).

    Returns:
        List of KnowledgeSearchResult objects containing verifiable excerpt, source metadata, and relevance score.
    """
```

### Tool Response Schema (Evidence Object)

```json
{
  "document_id": "KB-QTY-001",
  "title": "Inspection Requirements Following Machine Transfer",
  "document_type": "quality",
  "relevance_score": 38.6,
  "matched_topics": ["machine_transfer", "quality"],
  "excerpt": "Whenever production operations are transferred to an alternative machine (e.g. from M17 to M05 or M16), a mandatory First Article Inspection (FAI) must be performed. Protocol: Sample Size: First 3 consecutive parts produced on target machine...",
  "source_type": "synthetic",
  "machine_ids": [],
  "station_ids": [],
  "line_ids": []
}
```

---

## Architectural Boundary

The knowledge retrieval layer is strictly responsible for:
$$\text{ENGINEERING KNOWLEDGE} \longrightarrow \text{EVIDENCE}$$

It explicitly does **NOT**:
1. Choose recovery plans.
2. Rank or select recovery plans.
3. Modify factory database state.
4. Perform numerical optimization.
5. Schedule production.
6. Make autonomous factory control decisions.
7. Override deterministic simulation results or multi-objective optimization scores.

The optimization engine ([app/evaluation/engine.py](file:///c:/genAI/FactoryDecisionEngine/app/evaluation/engine.py)) retains exclusive responsibility for numerical trade-off evaluation.

---

## Limitations

1. **Lexical/Topic Retrieval**: Retrieval is deterministic, rules-based, and keyword/attribute driven. Semantic vector embeddings (FAISS/Chroma) and LLM-based reasoning are intentionally deferred to future agent phases.
2. **Synthetic Data**: All documents and parameters are synthetic representations calibrated for Apex Automotive Plant 1.
3. **No Inference Over Gaps**: If no engineering document contains a rule for a given machine or scenario, the search returns an empty set rather than interpolating assumptions.
