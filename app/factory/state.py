import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict

from app.config import config
from app.exceptions import (
    UnknownMachineError,
    UnknownStationError,
    UnknownLineError,
    UnknownOrderError,
)
from app.models import (
    Factory,
    ProductionLine,
    Station,
    Machine,
    MachineStatus,
    ProductionOrder,
    OrderPriority,
    OrderStatus,
    ProductionSchedule,
    Material,
    Supplier,
    MaintenanceEvent,
    MaintenanceStatus,
)
from app.data.database import (
    get_connection,
    load_factories,
    load_lines,
    load_stations,
    load_machines,
    load_orders,
    load_schedules,
    load_materials,
    load_suppliers,
    load_maintenance_events,
)

logger = logging.getLogger(__name__)

class FactoryState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    factory: Optional[Factory] = None
    production_lines: List[ProductionLine] = []
    stations: List[Station] = []
    machines: List[Machine] = []
    production_orders: List[ProductionOrder] = []
    production_schedules: List[ProductionSchedule] = []
    materials: List[Material] = []
    suppliers: List[Supplier] = []
    maintenance_events: List[MaintenanceEvent] = []

def get_factory_state(db_path: Optional[Path] = None) -> FactoryState:
    logger.info("Loading factory state from database")
    conn = get_connection(db_path)
    try:
        factories = load_factories(conn)
        lines = load_lines(conn)
        stations = load_stations(conn)
        machines = load_machines(conn)
        orders = load_orders(conn)
        schedules = load_schedules(conn)
        materials = load_materials(conn)
        suppliers = load_suppliers(conn)
        maintenance = load_maintenance_events(conn)

        return FactoryState(
            factory=factories[0] if factories else None,
            production_lines=lines,
            stations=stations,
            machines=machines,
            production_orders=orders,
            production_schedules=schedules,
            materials=materials,
            suppliers=suppliers,
            maintenance_events=maintenance,
        )
    finally:
        conn.close()

def get_machine(machine_id: str, db_path: Optional[Path] = None) -> Machine:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM machines WHERE machine_id = ?", (machine_id,))
        row = cursor.fetchone()
        if not row:
            raise UnknownMachineError(f"Machine '{machine_id}' not found.")
        return Machine(
            machine_id=row["machine_id"],
            line_id=row["line_id"],
            station_id=row["station_id"],
            machine_type=row["machine_type"],
            cycle_time_seconds=row["cycle_time_seconds"],
            capacity_per_hour=row["capacity_per_hour"],
            status=row["status"],
            health_score=row["health_score"],
        )
    finally:
        conn.close()

def get_station(station_id: str, db_path: Optional[Path] = None) -> Station:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stations WHERE station_id = ?", (station_id,))
        row = cursor.fetchone()
        if not row:
            raise UnknownStationError(f"Station '{station_id}' not found.")
        return Station(
            station_id=row["station_id"],
            line_id=row["line_id"],
            name=row["name"],
            operation_type=row["operation_type"],
            capacity_per_hour=row["capacity_per_hour"],
        )
    finally:
        conn.close()

def get_line(line_id: str, db_path: Optional[Path] = None) -> ProductionLine:
    conn = get_connection(db_path)
    try:
        lines = load_lines(conn)
        for line in lines:
            if line.line_id == line_id:
                return line
        raise UnknownLineError(f"Production line '{line_id}' not found.")
    finally:
        conn.close()

def get_production_order(order_id: str, db_path: Optional[Path] = None) -> ProductionOrder:
    conn = get_connection(db_path)
    try:
        orders = load_orders(conn)
        for order in orders:
            if order.order_id == order_id:
                return order
        raise UnknownOrderError(f"Production order '{order_id}' not found.")
    finally:
        conn.close()

def get_active_orders(db_path: Optional[Path] = None) -> List[ProductionOrder]:
    conn = get_connection(db_path)
    try:
        orders = load_orders(conn)
        return [o for o in orders if o.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)]
    finally:
        conn.close()

def get_machine_status(machine_id: str, db_path: Optional[Path] = None) -> MachineStatus:
    machine = get_machine(machine_id, db_path=db_path)
    return machine.status

def get_machine_dependencies(machine_id: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    state = get_factory_state(db_path)
    
    # Locate machine
    machine = next((m for m in state.machines if m.machine_id == machine_id), None)
    if not machine:
        raise UnknownMachineError(f"Machine '{machine_id}' not found.")
        
    # Locate station
    station = next((s for s in state.stations if s.station_id == machine.station_id), None)
    
    # Locate line
    line = next((l for l in state.production_lines if l.line_id == machine.line_id), None)
    
    # Locate schedules assigned to this machine
    schedules = [s for s in state.production_schedules if s.machine_id == machine_id]
    
    # Locate affected order IDs
    affected_order_ids = {s.order_id for s in schedules}
    affected_orders = [o for o in state.production_orders if o.order_id in affected_order_ids]
    
    high_priority_orders = [o for o in affected_orders if o.priority == OrderPriority.HIGH]

    return {
        "machine_id": machine.machine_id,
        "machine_type": machine.machine_type,
        "machine_status": machine.status.value,
        "station": {
            "station_id": station.station_id if station else None,
            "name": station.name if station else None,
            "operation_type": station.operation_type if station else None,
        },
        "line": {
            "line_id": line.line_id if line else None,
            "name": line.name if line else None,
        },
        "schedules": [
            {
                "schedule_id": s.schedule_id,
                "order_id": s.order_id,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "planned_quantity": s.planned_quantity,
            }
            for s in schedules
        ],
        "affected_orders": [
            {
                "order_id": o.order_id,
                "product_model": o.product_model,
                "quantity": o.quantity,
                "priority": o.priority.value,
                "due_time": o.due_time.isoformat(),
                "status": o.status.value,
            }
            for o in affected_orders
        ],
        "high_priority_orders": [
            {
                "order_id": o.order_id,
                "product_model": o.product_model,
                "due_time": o.due_time.isoformat(),
            }
            for o in high_priority_orders
        ],
    }

def get_factory_summary(db_path: Optional[Path] = None) -> Dict[str, Any]:
    state = get_factory_state(db_path)
    
    status_counts = {
        MachineStatus.HEALTHY.value: 0,
        MachineStatus.DEGRADED.value: 0,
        MachineStatus.MAINTENANCE.value: 0,
        MachineStatus.DOWN.value: 0,
    }
    for m in state.machines:
        status_counts[m.status.value] = status_counts.get(m.status.value, 0) + 1
        
    active_orders = [o for o in state.production_orders if o.status in (OrderStatus.PLANNED, OrderStatus.IN_PROGRESS)]
    high_priority_orders = [o for o in state.production_orders if o.priority == OrderPriority.HIGH]
    upcoming_maintenance = [me for me in state.maintenance_events if me.status in (MaintenanceStatus.SCHEDULED, MaintenanceStatus.IN_PROGRESS)]
    
    return {
        "factory_name": state.factory.name if state.factory else "Unknown",
        "total_machines": len(state.machines),
        "machines_by_status": status_counts,
        "total_production_lines": len(state.production_lines),
        "total_stations": len(state.stations),
        "total_orders": len(state.production_orders),
        "active_production_orders": len(active_orders),
        "high_priority_orders": len(high_priority_orders),
        "upcoming_maintenance_events": len(upcoming_maintenance),
    }
