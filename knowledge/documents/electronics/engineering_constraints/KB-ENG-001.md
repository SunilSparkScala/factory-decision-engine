---
document_id: KB-ENG-001
title: "Cross-Line Station Operation Compatibility Matrix"
document_type: engineering_constraint
machine_ids: []
station_ids:
  - STA_A1
  - STA_A2
  - STA_B2
  - STA_C1
line_ids:
  - LINE_A
  - LINE_B
  - LINE_C
topics:
  - engineering_constraint
  - operation_compatibility
  - station_matrix
source_type: synthetic
---

# Engineering Constraint: Cross-Line Operation Compatibility Matrix

## 1. Station Operation Compatibility
- **PICK_AND_PLACE:** Placement stations A2 (Line A), B2 (Line B) and C1 (Line C) use the same feeder bank interface and nozzle library. Inter-line placement transfers between these stations are technically feasible.
- **SOLDER_PASTE:** Stencil printers at Station A1 (Line A) and Station B1 (Line B) accept the same stencil frame size.
- **FINAL_ASSEMBLY:** Station A4 (Line A), Station B4 (Line B) and Station C3 (Line C) support modular device enclosure assembly.

## 2. Constraints
- Workload transfers between stations with different operation types (e.g. PICK_AND_PLACE to REFLOW) are strictly prohibited.

*Notice: Synthetic engineering documentation for Northwind Electronics Plant 1.*
