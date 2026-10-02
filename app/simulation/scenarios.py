from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator
from app.exceptions import InvalidDowntimeError, InvalidScenarioError

class ScenarioType(str, Enum):
    MACHINE_UNAVAILABILITY = "MACHINE_UNAVAILABILITY"
    SUPPLIER_DELAY = "SUPPLIER_DELAY"
    QUALITY_DEGRADATION = "QUALITY_DEGRADATION"
    ENERGY_CONSTRAINT = "ENERGY_CONSTRAINT"
    DEMAND_CHANGE = "DEMAND_CHANGE"

class Scenario(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scenario_type: ScenarioType = ScenarioType.MACHINE_UNAVAILABILITY
    machine_id: str
    downtime_hours: float
    start_time: Optional[datetime] = None

    @field_validator("downtime_hours")
    @classmethod
    def validate_downtime(cls, v: float) -> float:
        if v <= 0:
            raise InvalidDowntimeError(f"downtime_hours must be greater than 0, got {v}")
        return v

    @field_validator("machine_id")
    @classmethod
    def validate_machine_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise InvalidScenarioError("machine_id cannot be empty")
        return v.strip()
