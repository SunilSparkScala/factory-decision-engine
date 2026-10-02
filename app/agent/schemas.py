from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field, ConfigDict

class EvidenceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    title: str
    document_type: str
    relevance_score: float
    excerpt: str
    matched_topics: List[str] = []
    machine_ids: List[str] = []
    source_type: str = "synthetic"
    evidence_category: Optional[str] = Field(
        default=None,
        description="Categorization: historical_precedent, machine_specification, quality_requirement, or operational_procedure",
    )

class CandidatePlanSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: str
    strategy_type: str
    description: str
    is_feasible: bool
    infeasibility_reasons: List[str] = []
    actions_summary: str
    weighted_score: Optional[float] = None
    cost_impact_usd: Optional[float] = None
    delivery_delay_score: Optional[float] = None
    quality_penalty_score: Optional[float] = None
    energy_kwh: Optional[float] = None
    risk_score: Optional[float] = None
    target_machine_id: Optional[str] = None
    target_line_id: Optional[str] = None

class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scenario_summary: str
    execution_mode: str = Field(default="deterministic_fallback")
    fallback_reason: Optional[str] = None
    dependency_chain: Dict[str, Any] = Field(default_factory=dict)
    impact_summary: Dict[str, Any] = Field(default_factory=dict)
    affected_orders: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_plans: List[CandidatePlanSummary] = Field(default_factory=list)
    feasible_alternatives: List[CandidatePlanSummary] = Field(default_factory=list)
    infeasible_alternatives: List[CandidatePlanSummary] = Field(default_factory=list)
    selected_plan: Optional[CandidatePlanSummary] = None
    objective_weights: Dict[str, float] = Field(default_factory=dict)
    trade_off_metrics: Dict[str, Any] = Field(default_factory=dict)
    engineering_evidence: List[EvidenceItem] = Field(default_factory=list)
    hard_constraints_checked: List[str] = Field(default_factory=list)
    objectives_scored: List[str] = Field(default_factory=lambda: ["delivery", "cost", "quality", "energy", "risk"])
    assumptions: List[str] = Field(default_factory=list)
    rationale: str
    risks: List[str] = Field(default_factory=list)
    human_approval_required: bool = True
    clarification_needed: Optional[str] = None

    def to_markdown_explanation(self) -> str:
        """Render a structured executive briefing matching Phase 4B Decision Intelligence Contract."""
        lines = [
            f"# Operational Decision Briefing: {self.scenario_summary}",
            "",
            "## 1. Scenario & Execution Provenance",
            f"- **Execution Mode:** `{self.execution_mode}`",
        ]
        if self.fallback_reason:
            lines.append(f"- **Fallback Notice:** {self.fallback_reason}")
        if self.clarification_needed:
            lines.append(f"- **Clarification Required:** {self.clarification_needed}")

        target_m = self.impact_summary.get('machine_id', 'N/A')
        downtime = self.impact_summary.get('downtime_hours', 0.0)
        lines.append(f"- **Target Asset:** {target_m} (Downtime: {downtime} hrs)")

        # Section 2: Asset Dependency Trace
        lines.extend([
            "",
            "## 2. Asset Dependency Trace",
        ])
        if self.dependency_chain:
            dc = self.dependency_chain
            trace_str = dc.get("trace_summary") or f"{dc.get('machine_id')} ({dc.get('machine_type')}) -> {dc.get('station_id')} ({dc.get('station_name')}) -> {dc.get('line_id')} ({dc.get('line_name')}) -> {dc.get('operation_type')} -> {dc.get('affected_order_count')} Orders -> Delivery"
            lines.extend([
                f"- **Chain:** `{trace_str}`",
                f"- **Station:** {dc.get('station_id', 'N/A')} ({dc.get('station_name', 'N/A')})",
                f"- **Line:** {dc.get('line_id', 'N/A')} ({dc.get('line_name', 'N/A')})",
                f"- **Operation Type:** {dc.get('operation_type', 'N/A')}",
            ])
        else:
            lines.append(f"- **Asset Chain:** `{target_m} -> Station N/A -> Line N/A`")

        # Section 3: Operational Impact Summary
        lines.extend([
            "",
            "## 3. Operational Impact Summary",
            f"- **Target Asset:** {target_m}",
            f"- **Unscheduled Downtime:** {downtime} hrs (Capacity Loss: {self.impact_summary.get('capacity_loss_units', 0.0):.1f} units, {self.impact_summary.get('capacity_loss_percentage', 0.0):.1f}%)",
            f"- **Delivery Risk Level:** {self.impact_summary.get('delivery_risk', 'LOW')}",
            f"- **Affected Orders:** {self.impact_summary.get('affected_order_count', 0)} total ({self.impact_summary.get('high_priority_affected_count', 0)} high-priority)",
        ])
        if self.impact_summary.get("affected_product_models"):
            models_str = ", ".join(self.impact_summary["affected_product_models"])
            lines.append(f"- **Affected Product Models:** {models_str}")

        # Section 4: Recovery Options & Feasibility
        lines.extend([
            "",
            "## 4. Recovery Options & Feasibility",
        ])
        feasible = self.feasible_alternatives or [p for p in self.candidate_plans if p.is_feasible]
        infeasible = self.infeasible_alternatives or [p for p in self.candidate_plans if not p.is_feasible]

        lines.append(f"- **Feasible Alternatives ({len(feasible)}):**")
        if feasible:
            for p in feasible:
                score_str = f"Weighted Score: {p.weighted_score:.4f}" if p.weighted_score is not None else "Score: Pending"
                lines.append(f"  - `{p.plan_id}` ({p.strategy_type.upper()}): {p.description} [{score_str}]")
        else:
            lines.append("  - *No feasible alternative plans found.*")

        lines.append(f"- **Infeasible Alternatives ({len(infeasible)}):**")
        if infeasible:
            for p in infeasible:
                reasons_str = "; ".join(p.infeasibility_reasons) if p.infeasibility_reasons else "Operational constraint violation"
                lines.append(f"  - `{p.plan_id}` ({p.strategy_type.upper()}): {p.description} — *Reason: {reasons_str}*")
        else:
            lines.append("  - *None.*")

        # Section 5: Multi-Objective Trade-Offs
        lines.extend([
            "",
            "## 5. Multi-Objective Trade-Offs & Scoring",
        ])
        if self.objective_weights:
            w_str = ", ".join(f"{k.capitalize()}: {v:.1%}" for k, v in self.objective_weights.items())
            lines.append(f"- **Applied Optimizer Weights:** {w_str}")

        if self.selected_plan:
            sp = self.selected_plan
            lines.extend([
                f"- **Delivery Impact Score:** {sp.delivery_delay_score:.2f} (normalized impact penalty, 0.0 best to 1.0 worst)",
                f"- **Estimated Cost Impact:** ${sp.cost_impact_usd:.2f} USD",
                f"- **Quality Penalty Metric:** {sp.quality_penalty_score:.2f} (synthetic quality penalty proxy)" if sp.quality_penalty_score is not None else "",
                f"- **Energy Consumption:** {sp.energy_kwh:.1f} kWh (synthetic energy proxy)" if sp.energy_kwh is not None else "",
                f"- **Operational Risk Metric:** {sp.risk_score:.2f} (synthetic risk proxy)" if sp.risk_score is not None else "",
                f"- **Optimal Weighted Score:** {sp.weighted_score:.4f}" if sp.weighted_score is not None else "",
            ])

        # Section 6: Recommended Action
        lines.extend([
            "",
            "## 6. Recommended Recovery Action",
        ])
        if self.selected_plan:
            sp = self.selected_plan
            lines.extend([
                f"- **Selected Plan ID:** `{sp.plan_id}` ({sp.strategy_type.upper()})",
                f"- **Strategy:** {sp.description}",
                f"- **Action Sequence:** {sp.actions_summary}",
                f"- **Optimal Weighted Score:** {sp.weighted_score:.4f}" if sp.weighted_score is not None else "- **Weighted Score:** N/A",
                f"- **Estimated Cost Impact:** ${sp.cost_impact_usd:.2f}" if sp.cost_impact_usd is not None else "",
                f"- **Delivery Impact Score:** {sp.delivery_delay_score:.2f} (normalized penalty score)" if sp.delivery_delay_score is not None else "",
            ])
        else:
            lines.append("- **No feasible recovery plan identified.** Immediate supervisory intervention required.")

        # Section 7: Decision Rationale & Engineering Evidence
        lines.extend([
            "",
            "## 7. Decision Rationale & Engineering Evidence",
            "### Rationale",
            self.rationale,
            "",
            "### Supporting Engineering Evidence",
        ])
        if self.engineering_evidence:
            for ev in self.engineering_evidence:
                cat_label = f" | Category: {ev.evidence_category}" if ev.evidence_category else ""
                lines.append(f"- **[{ev.document_id}]** *{ev.title}* ({ev.document_type}{cat_label}): \"{ev.excerpt}\"")
        else:
            lines.append("- *No specific engineering document retrieved for this operational pattern.*")

        # Section 8: Hard Constraints & Operational Assumptions
        lines.extend([
            "",
            "## 8. Hard Constraints & Operational Assumptions",
            "### Hard Constraints Evaluated",
        ])
        constraints = self.hard_constraints_checked or [
            "failed_machine_exclusion",
            "machine_status_operational",
            "operation_type_compatibility",
            "target_line_model_compatibility",
            "capacity_availability",
            "schedule_conflict_check",
            "overtime_limit_threshold",
        ]
        for c in constraints:
            lines.append(f"- [x] {c.replace('_', ' ').capitalize()}")

        lines.extend([
            "",
            "### Operational Assumptions",
        ])
        for a in self.assumptions:
            lines.append(f"- {a}")

        lines.extend([
            "",
            "### Operational Risks",
        ])
        for r in self.risks:
            lines.append(f"- {r}")

        lines.extend([
            "",
            f"> **Human Approval Required:** {'YES — Plant Manager or Shift Supervisor sign-off mandatory before dispatch.' if self.human_approval_required else 'No'}",
        ])
        return "\n".join(line for line in lines if line is not None)
