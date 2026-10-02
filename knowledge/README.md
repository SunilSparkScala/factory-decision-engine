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

## Deterministic Search Engine

The repository provides a deterministic search engine scored by:
- Exact machine ID match boost ($+10.0$)
- Exact station/line ID match boost ($+5.0$)
- Exact topic match boost ($+4.0$)
- Title keyword match boost ($+3.0$)
- Content keyword match boost ($+1.0$)
- Deterministic tie-breaking by `document_id` ascending.

## Example Queries

- `"machine M17 operating procedure"`
- `"quality requirements after machine transfer"`
- `"overtime recovery constraints"`
- `"machine substitution rules"`
- `"previous incidents involving machine transfer"`

## Limitations

- Retrieval is keyword/topic based; natural language embedding similarity is deferred to Phase 5.
- Documents serve as retrievable evidence and do not modify factory state or overwrite deterministic optimization scores.
