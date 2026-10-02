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

class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scenario_summary: str
    assumptions: List[str] = Field(default_factory=list)
    impact_summary: Dict[str, Any] = Field(default_factory=dict)
    affected_orders: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_plans: List[CandidatePlanSummary] = Field(default_factory=list)
    selected_plan: Optional[CandidatePlanSummary] = None
    objective_weights: Dict[str, float] = Field(default_factory=dict)
    engineering_evidence: List[EvidenceItem] = Field(default_factory=list)
    rationale: str
    risks: List[str] = Field(default_factory=list)
    human_approval_required: bool = True
    execution_mode: str = Field(default="deterministic_fallback")
    fallback_reason: Optional[str] = None

    def to_markdown_explanation(self) -> str:
        """Render a structured executive briefing for plant leadership."""
        lines = [
            f"# Operational Decision Briefing: {self.scenario_summary}",
            "",
            "## 1. Impact Summary",
            f"- **Execution Mode:** `{self.execution_mode}`",
        ]
        if self.fallback_reason:
            lines.append(f"- **Fallback Notice:** {self.fallback_reason}")
        lines.extend([
            f"- **Target Asset:** {self.impact_summary.get('machine_id', 'N/A')}",
            f"- **Unscheduled Downtime:** {self.impact_summary.get('downtime_hours', 0.0)} hrs (Capacity Loss: {self.impact_summary.get('capacity_loss_units', 0.0):.1f} units, {self.impact_summary.get('capacity_loss_percentage', 0.0):.1f}%)",
            f"- **Delivery Risk Level:** {self.impact_summary.get('delivery_risk', 'LOW')}",
            f"- **Affected Orders:** {self.impact_summary.get('affected_order_count', 0)} total ({self.impact_summary.get('high_priority_affected_count', 0)} high-priority)",
            "",
            "## 2. Recommended Recovery Action",
        ])
        if self.selected_plan:
            sp = self.selected_plan
            lines.extend([
                f"- **Selected Plan ID:** `{sp.plan_id}` ({sp.strategy_type.upper()})",
                f"- **Strategy:** {sp.description}",
                f"- **Action Sequence:** {sp.actions_summary}",
                f"- **Weighted Objective Score:** {sp.weighted_score:.4f}" if sp.weighted_score is not None else "- **Weighted Score:** N/A",
                f"- **Estimated Cost Impact:** ${sp.cost_impact_usd:.2f}" if sp.cost_impact_usd is not None else "",
                f"- **Delivery Delay Metric:** {sp.delivery_delay_score:.2f}" if sp.delivery_delay_score is not None else "",
            ])
        else:
            lines.append("- **No feasible recovery plan identified.** Immediate supervisory intervention required.")

        lines.extend([
            "",
            "## 3. Decision Rationale & Trade-offs",
            self.rationale,
            "",
            "## 4. Supporting Engineering Evidence",
        ])

        if self.engineering_evidence:
            for ev in self.engineering_evidence:
                lines.append(f"- **[{ev.document_id}]** *{ev.title}* ({ev.document_type}): \"{ev.excerpt}\"")
        else:
            lines.append("- *No specific engineering document retrieved for this operational pattern.*")

        lines.extend([
            "",
            "## 5. Identified Risks",
        ])
        for r in self.risks:
            lines.append(f"- {r}")

        lines.extend([
            "",
            f"> **Human Approval Required:** {'YES — Plant Manager or Shift Supervisor sign-off mandatory before dispatch.' if self.human_approval_required else 'No'}",
        ])
        return "\n".join(line for line in lines if line is not None)
