---
document_id: KB-SOP-003
title: Extended Shift Overtime Authorization Rules
document_type: production_sop
machine_ids: []
station_ids: []
line_ids: []
topics:
  - overtime
  - capacity_recovery
  - sop
  - constraints
source_type: synthetic
---

# Production SOP: Shift Overtime Capacity Recovery Rules

## 1. Overtime Authorization Rules
- **Maximum Overtime Limit:** Overtime per machine is capped at a maximum of 12.0 hours per recovery plan (`MAX_OVERTIME_HOURS_PER_MACHINE = 12.0`).
- **Cost Allocation:** Overtime operations are billed at a synthetic rate of $50.00/hour.
- **Machine Health Requirement:** Machines currently in `DOWN` or `MAINTENANCE` status cannot be authorized for overtime operations.

*Notice: Synthetic SOP document for Apex Automotive Plant 1.*
