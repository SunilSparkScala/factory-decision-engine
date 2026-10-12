---
document_id: KB-MAN-M17
title: "M17 High-Speed Pick-and-Place Station Manual"
document_type: machine_manual
machine_ids:
  - M17
station_ids:
  - STA_B2
line_ids:
  - LINE_B
topics:
  - machine_operation
  - pick_and_place
  - capacity
  - downtime
  - maintenance
source_type: synthetic
---

# M17 High-Speed Pick-and-Place Cell — Operating Characteristics & Constraints

## 1. Description & Location
M17 is a dual-head high-speed pick-and-place cell at Station B2 (Line B, Smart Speaker Line). It places components on NW-Hub and NW-Speaker main boards.

## 2. Key Performance Metrics
- **Nominal Capacity:** 38.6–42.5 panels/hour
- **Cycle Time:** 85.0–93.0 seconds
- **Health Rating:** 95.0% (Nominal Baseline)
- **Primary Consumables:** Component reels and placement nozzles

## 3. Machine Downtime & Recovery Guidelines
- **Unplanned Failure Mode:** Placement head vacuum faults are the most common cause of downtime on M17.
- **Cool-down Requirement:** 2.0 hours minimum before replacing a head drive.
- **Transfer Alternatives:** Work assigned to M17 can be transferred to M16, M18 on Line B, or M04, M05, M06 on Line A (Station A2), or M25, M26 on Line C (Station C1).

## 4. Quality Precautions
- Following any stoppage exceeding 4 hours, the first 5 boards must pass automated optical inspection before mass production resumes.

*Notice: Synthetic engineering documentation for Northwind Electronics Plant 1.*
