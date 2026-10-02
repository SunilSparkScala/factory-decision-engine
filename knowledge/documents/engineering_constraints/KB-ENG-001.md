---
document_id: KB-ENG-001
title: Cross-Line Station Operation Compatibility Matrix
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
- **WELDING:** Operations at Station A2 (Line A), Station B2 (Line B), and Station C1 (Line C) share standardized end-effector interfaces. Inter-line welding transfers between these stations are technically feasible.
- **STAMPING:** Station A1 (Line A) and Station B1 (Line B) support interchangeable heavy press dies.
- **ASSEMBLY:** Station A4 (Line A), Station B4 (Line B), and Station C3 (Line C) support modular vehicle assembly.

## 2. Constraints
- Workload transfers between stations supporting different operation types (e.g. WELDING to PAINTING) are strictly prohibited.

*Notice: Synthetic engineering constraint document for Apex Automotive Plant 1.*
