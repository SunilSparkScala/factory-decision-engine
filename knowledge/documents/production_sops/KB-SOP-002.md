---
document_id: KB-SOP-002
title: Production Order Resequencing & Priority Rules
document_type: production_sop
machine_ids: []
station_ids: []
line_ids: []
topics:
  - resequencing
  - order_priority
  - dispatching
  - sop
source_type: synthetic
---

# Production SOP: Order Resequencing & Dispatch Rules

## 1. Overview
When machine unavailability limits production capacity, production orders must be re-sequenced deterministically to protect delivery commitments.

## 2. Priority Dispatch Hierarchy
1. **HIGH Priority First:** All orders with priority `HIGH` must be scheduled ahead of `MEDIUM` or `LOW` priority orders.
2. **Earliest Due Date (EDD):** Among orders with equal priority, schedule the order with the earliest `due_time` first.
3. **Order ID Tie-Break:** If priority and due_time are identical, sort deterministically by `order_id` in ascending alphanumeric order.

*Notice: Synthetic SOP document for Apex Automotive Plant 1.*
