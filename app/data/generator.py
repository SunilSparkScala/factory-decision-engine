import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
from app.config import config
from app.domains import get_pack
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
    SupplierStatus,
    MaintenanceEvent,
    MaintenanceType,
    MaintenanceStatus,
)

# Station layout: (station_id, line_id, operation slot, capacity/hour). Operation slots index the
# pack's operations [press, join, finish, assemble, inspect]; names come from the industry pack.
STATION_LAYOUT = [
    ("STA_A1", "LINE_A", 0, 65.0), ("STA_A2", "LINE_A", 1, 60.0), ("STA_A3", "LINE_A", 2, 55.0), ("STA_A4", "LINE_A", 3, 50.0),
    ("STA_B1", "LINE_B", 0, 55.0), ("STA_B2", "LINE_B", 1, 50.0), ("STA_B3", "LINE_B", 2, 45.0), ("STA_B4", "LINE_B", 3, 45.0),
    ("STA_C1", "LINE_C", 1, 75.0), ("STA_C2", "LINE_C", 2, 70.0), ("STA_C3", "LINE_C", 3, 65.0), ("STA_C4", "LINE_C", 4, 80.0),
]


class SyntheticFactoryGenerator:
    def __init__(self, seed: int = config.RANDOM_SEED, domain: str = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.pack = get_pack(domain)
        
    def generate_all(self) -> Dict[str, list]:
        self.rng.seed(self.seed)
        base_time = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
        
        pack = self.pack
        m_s, m_e, m_c, m_x = pack["models"]

        # 1. Factory
        factory = Factory(
            factory_id="F01",
            name=pack["plant"]["name"],
            location=pack["plant"]["location"],
            operating_hours=config.OPERATING_HOURS_PER_DAY
        )

        # 2. Production Lines
        lines = [
            ProductionLine(line_id="LINE_A", name=pack["lines"]["LINE_A"], capacity_per_hour=60.0,
                           supported_models=[m_s, m_e], status="OPERATIONAL"),
            ProductionLine(line_id="LINE_B", name=pack["lines"]["LINE_B"], capacity_per_hour=50.0,
                           supported_models=[m_e, m_c], status="OPERATIONAL"),
            ProductionLine(line_id="LINE_C", name=pack["lines"]["LINE_C"], capacity_per_hour=70.0,
                           supported_models=[m_c, m_x], status="OPERATIONAL"),
        ]

        # 3. Stations (12 total across 3 lines)
        stations = []
        for station_id, line_id, op_slot, capacity in STATION_LAYOUT:
            op = pack["operations"][op_slot]
            stations.append(Station(
                station_id=station_id,
                line_id=line_id,
                name=f"Station {station_id[-2:]} - {op['display']}",
                operation_type=op["code"],
                capacity_per_hour=capacity,
            ))

        # 4. Machines (3 machines per station = 36 machines)
        machines = []
        machine_idx = 1
        for station in stations:
            for m_sub in range(1, 4):
                machine_id = f"M{machine_idx:02d}"
                # Explicitly make M17 healthy by default so tests can test its failure scenario deterministically
                if machine_id == "M17":
                    status = MachineStatus.HEALTHY
                    health = 95.0
                else:
                    rand_val = self.rng.random()
                    if rand_val < 0.85:
                        status = MachineStatus.HEALTHY
                        health = self.rng.uniform(90.0, 100.0)
                    elif rand_val < 0.93:
                        status = MachineStatus.DEGRADED
                        health = self.rng.uniform(60.0, 85.0)
                    elif rand_val < 0.97:
                        status = MachineStatus.MAINTENANCE
                        health = self.rng.uniform(40.0, 59.0)
                    else:
                        status = MachineStatus.DOWN
                        health = self.rng.uniform(0.0, 39.0)
                
                cycle_time = self.rng.uniform(60.0, 180.0)
                cap_per_hr = round(3600.0 / cycle_time, 1)
                
                machines.append(
                    Machine(
                        machine_id=machine_id,
                        line_id=station.line_id,
                        station_id=station.station_id,
                        machine_type=f"{station.operation_type}_Unit_{m_sub}",
                        cycle_time_seconds=round(cycle_time, 1),
                        capacity_per_hour=cap_per_hr,
                        status=status,
                        health_score=round(health, 1)
                    )
                )
                machine_idx += 1
                
        # 5. Orders (55 orders)
        orders = []
        model_line_map = {
            m_s: "LINE_A",
            m_e: "LINE_A",
            m_c: "LINE_B",
            m_x: "LINE_C"
        }
        
        priorities = [OrderPriority.HIGH, OrderPriority.MEDIUM, OrderPriority.LOW]
        priority_weights = [0.25, 0.50, 0.25]
        
        for o_idx in range(1, 56):
            order_id = f"O{1000 + o_idx}"
            model = self.rng.choice(list(model_line_map.keys()))
            line_id = model_line_map[model]
            quantity = self.rng.randint(50, 250)
            priority = self.rng.choices(priorities, weights=priority_weights)[0]
            
            # Due time 12 to 72 hours after base_time
            due_hours = self.rng.randint(12, 72)
            due_time = base_time + timedelta(hours=due_hours)
            
            orders.append(
                ProductionOrder(
                    order_id=order_id,
                    product_model=model,
                    quantity=quantity,
                    priority=priority,
                    due_time=due_time,
                    assigned_line=line_id,
                    status=OrderStatus.PLANNED
                )
            )
            
        # 6. Production Schedules (linking orders to machines)
        schedules = []
        sched_idx = 1
        station_by_line = {}
        for st in stations:
            station_by_line.setdefault(st.line_id, []).append(st)
            
        machines_by_station = {}
        for m in machines:
            machines_by_station.setdefault(m.station_id, []).append(m)
            
        for order in orders:
            line_stations = station_by_line[order.assigned_line]
            current_start = base_time + timedelta(hours=self.rng.randint(1, 8))
            
            for st in line_stations:
                st_machines = machines_by_station[st.station_id]
                # Pick a machine at this station
                m = self.rng.choice(st_machines)
                duration_hrs = max(0.5, order.quantity / max(1.0, m.capacity_per_hour))
                end_time = current_start + timedelta(hours=duration_hrs)
                
                schedules.append(
                    ProductionSchedule(
                        schedule_id=f"SCH_{sched_idx:04d}",
                        order_id=order.order_id,
                        machine_id=m.machine_id,
                        station_id=st.station_id,
                        start_time=current_start,
                        end_time=end_time,
                        planned_quantity=order.quantity
                    )
                )
                sched_idx += 1
                current_start = end_time + timedelta(minutes=15)

        # 7. Materials (25 materials)
        # (id, available quantity, safety stock); names come from the industry pack
        materials_stock = [
            (5000.0, 1000.0), (3500.0, 800.0), (1200.0, 300.0), (800.0, 200.0), (950.0, 250.0),
            (1500.0, 400.0), (600.0, 150.0), (400.0, 100.0), (350.0, 90.0), (2400.0, 600.0),
            (500.0, 120.0), (750.0, 200.0), (450.0, 100.0), (400.0, 100.0), (650.0, 150.0),
            (550.0, 130.0), (700.0, 180.0), (900.0, 220.0), (850.0, 200.0), (1100.0, 300.0),
            (480.0, 120.0), (1600.0, 400.0), (720.0, 180.0), (380.0, 90.0), (3000.0, 500.0),
        ]
        materials_data = [
            (f"MAT_{i:02d}", name, stock[0], stock[1])
            for i, (name, stock) in enumerate(zip(pack["materials"], materials_stock), start=1)
        ]

        materials = [
            Material(
                material_id=m[0],
                name=m[1],
                available_quantity=m[2],
                safety_stock=m[3]
            )
            for m in materials_data
        ]
        
        # 8. Suppliers (15 suppliers)
        suppliers = []
        for sup_idx in range(1, 16):
            mat = materials[sup_idx % len(materials)]
            suppliers.append(
                Supplier(
                    supplier_id=f"SUP_{sup_idx:02d}",
                    material_id=mat.material_id,
                    lead_time_hours=round(self.rng.uniform(6.0, 48.0), 1),
                    status=SupplierStatus.ACTIVE
                )
            )
            
        # 9. Maintenance Events (at least 5 events)
        maintenance_events = []
        candidate_machines = [m for m in machines if m.status in (MachineStatus.DEGRADED, MachineStatus.MAINTENANCE, MachineStatus.DOWN)]
        if len(candidate_machines) < 5:
            candidate_machines = (candidate_machines + machines)[:5]
            
        for me_idx, m in enumerate(candidate_machines, start=1):
            m_start = base_time + timedelta(hours=self.rng.randint(0, 12))
            maintenance_events.append(
                MaintenanceEvent(
                    maintenance_id=f"MAINT_{me_idx:03d}",
                    machine_id=m.machine_id,
                    start_time=m_start,
                    duration_hours=round(self.rng.uniform(2.0, 12.0), 1),
                    maintenance_type=MaintenanceType.PREVENTIVE if me_idx % 2 == 0 else MaintenanceType.CORRECTIVE,
                    status=MaintenanceStatus.SCHEDULED if me_idx % 2 == 0 else MaintenanceStatus.IN_PROGRESS
                )
            )
            
        return {
            "factory": [factory],
            "lines": lines,
            "stations": stations,
            "machines": machines,
            "orders": orders,
            "schedules": schedules,
            "materials": materials,
            "suppliers": suppliers,
            "maintenance_events": maintenance_events
        }
