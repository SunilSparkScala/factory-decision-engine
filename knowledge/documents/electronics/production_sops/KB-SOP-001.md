---
document_id: KB-SOP-001
title: "Machine Substitution & Reassignment Rules"
document_type: production_sop
machine_ids: []
station_ids: []
line_ids: []
topics:
  - machine_substitution
  - machine_transfer
  - sop
  - production_rules
source_type: synthetic
---

# Production SOP: Machine Substitution Rules

## 1. Purpose
Defines mandatory rules for substituting one machine for another during downtime or capacity constraints.

## 2. Substitution Rules
1. **Station Capability Match:** The alternative machine must belong to a station with the exact same operation type (e.g. PICK_AND_PLACE to PICK_AND_PLACE).
2. **Product Compatibility:** The target line must build the product (e.g. Line A builds NW-Router and NW-Hub).
3. **Operational Status:** Target machine status must be HEALTHY or DEGRADED. DOWN or MAINTENANCE machines are strictly prohibited.
4. **Setup Penalty:** All cross-line machine transfers incur a standard synthetic setup penalty of $100.0.

*Notice: Synthetic engineering documentation for Northwind Electronics Plant 1.*
