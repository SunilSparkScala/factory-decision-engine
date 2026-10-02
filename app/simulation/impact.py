from enum import Enum
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.models import Machine, Station, ProductionLine, OrderPriority, OrderStatus, MachineStatus
from app.simulation.scenarios import Scenario

class DeliveryRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class CapacityImpact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    downtime_hours: float
    capacity_loss_units: float
    capacity_loss_percentage: float
    affected_schedule_count: int

class AffectedOrderDetails(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    product_model: str
    quantity: int
    priority: OrderPriority
    due_time: datetime
    status: OrderStatus
    affected_schedule_count: int = 1

class AlternativeCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    machine_id: str
    line_id: str
    station_id: str
    machine_type: str
    capacity_per_hour: float
    status: MachineStatus
    health_score: float

class MachineFailureImpact(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scenario: Scenario
    machine: Machine
    station: Station
    line: ProductionLine
    capacity_impact: CapacityImpact
    affected_orders: List[AffectedOrderDetails]
    high_priority_orders: List[AffectedOrderDetails]
    delivery_risk: DeliveryRisk
    alternative_candidates: List[AlternativeCandidate]
