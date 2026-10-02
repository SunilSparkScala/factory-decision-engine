from enum import Enum
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class MaintenanceType(str, Enum):
    PREVENTIVE = "PREVENTIVE"
    CORRECTIVE = "CORRECTIVE"
    EMERGENCY = "EMERGENCY"

class MaintenanceStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"

class MaintenanceEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    maintenance_id: str
    machine_id: str
    start_time: datetime
    duration_hours: float
    maintenance_type: MaintenanceType = MaintenanceType.PREVENTIVE
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
