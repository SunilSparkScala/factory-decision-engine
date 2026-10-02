from enum import Enum
from pydantic import BaseModel, ConfigDict

class SupplierStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DELAYED = "DELAYED"
    INACTIVE = "INACTIVE"

class Material(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    material_id: str
    name: str
    available_quantity: float
    safety_stock: float

class Supplier(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    supplier_id: str
    material_id: str
    lead_time_hours: float
    status: SupplierStatus = SupplierStatus.ACTIVE
