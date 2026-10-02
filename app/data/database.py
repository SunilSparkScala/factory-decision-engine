import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import config
from app.models import (
    Factory,
    ProductionLine,
    Station,
    Machine,
    ProductionOrder,
    ProductionSchedule,
    Material,
    Supplier,
    MaintenanceEvent,
)

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    target_path = db_path or config.DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS factories (
        factory_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        location TEXT NOT NULL,
        operating_hours REAL NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS production_lines (
        line_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        capacity_per_hour REAL NOT NULL,
        supported_models TEXT NOT NULL,
        status TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stations (
        station_id TEXT PRIMARY KEY,
        line_id TEXT NOT NULL,
        name TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        capacity_per_hour REAL NOT NULL,
        FOREIGN KEY (line_id) REFERENCES production_lines (line_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS machines (
        machine_id TEXT PRIMARY KEY,
        line_id TEXT NOT NULL,
        station_id TEXT NOT NULL,
        machine_type TEXT NOT NULL,
        cycle_time_seconds REAL NOT NULL,
        capacity_per_hour REAL NOT NULL,
        status TEXT NOT NULL,
        health_score REAL NOT NULL,
        FOREIGN KEY (line_id) REFERENCES production_lines (line_id),
        FOREIGN KEY (station_id) REFERENCES stations (station_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS production_orders (
        order_id TEXT PRIMARY KEY,
        product_model TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        priority TEXT NOT NULL,
        due_time TEXT NOT NULL,
        assigned_line TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (assigned_line) REFERENCES production_lines (line_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS production_schedules (
        schedule_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        machine_id TEXT NOT NULL,
        station_id TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        planned_quantity INTEGER NOT NULL,
        FOREIGN KEY (order_id) REFERENCES production_orders (order_id),
        FOREIGN KEY (machine_id) REFERENCES machines (machine_id),
        FOREIGN KEY (station_id) REFERENCES stations (station_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS materials (
        material_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        available_quantity REAL NOT NULL,
        safety_stock REAL NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS suppliers (
        supplier_id TEXT PRIMARY KEY,
        material_id TEXT NOT NULL,
        lead_time_hours REAL NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (material_id) REFERENCES materials (material_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS maintenance_events (
        maintenance_id TEXT PRIMARY KEY,
        machine_id TEXT NOT NULL,
        start_time TEXT NOT NULL,
        duration_hours REAL NOT NULL,
        maintenance_type TEXT NOT NULL,
        status TEXT NOT NULL,
        FOREIGN KEY (machine_id) REFERENCES machines (machine_id)
    );
    """)

    conn.commit()
    conn.close()

def save_factory_data(
    factories: List[Factory],
    lines: List[ProductionLine],
    stations: List[Station],
    machines: List[Machine],
    orders: List[ProductionOrder],
    schedules: List[ProductionSchedule],
    materials: List[Material],
    suppliers: List[Supplier],
    maintenance_events: List[MaintenanceEvent],
    db_path: Optional[Path] = None,
) -> None:
    init_db(db_path)
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Clear existing tables for fresh generator write
    tables = [
        "factories", "production_lines", "stations", "machines",
        "production_orders", "production_schedules", "materials",
        "suppliers", "maintenance_events"
    ]
    for table in tables:
        cursor.execute(f"DELETE FROM {table}")

    for f in factories:
        cursor.execute(
            "INSERT INTO factories VALUES (?, ?, ?, ?)",
            (f.factory_id, f.name, f.location, f.operating_hours),
        )

    for l in lines:
        cursor.execute(
            "INSERT INTO production_lines VALUES (?, ?, ?, ?, ?)",
            (l.line_id, l.name, l.capacity_per_hour, json.dumps(l.supported_models), l.status),
        )

    for s in stations:
        cursor.execute(
            "INSERT INTO stations VALUES (?, ?, ?, ?, ?)",
            (s.station_id, s.line_id, s.name, s.operation_type, s.capacity_per_hour),
        )

    for m in machines:
        cursor.execute(
            "INSERT INTO machines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                m.machine_id,
                m.line_id,
                m.station_id,
                m.machine_type,
                m.cycle_time_seconds,
                m.capacity_per_hour,
                m.status.value,
                m.health_score,
            ),
        )

    for o in orders:
        cursor.execute(
            "INSERT INTO production_orders VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                o.order_id,
                o.product_model,
                o.quantity,
                o.priority.value,
                o.due_time.isoformat(),
                o.assigned_line,
                o.status.value,
            ),
        )

    for ps in schedules:
        cursor.execute(
            "INSERT INTO production_schedules VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ps.schedule_id,
                ps.order_id,
                ps.machine_id,
                ps.station_id,
                ps.start_time.isoformat(),
                ps.end_time.isoformat(),
                ps.planned_quantity,
            ),
        )

    for mat in materials:
        cursor.execute(
            "INSERT INTO materials VALUES (?, ?, ?, ?)",
            (mat.material_id, mat.name, mat.available_quantity, mat.safety_stock),
        )

    for sup in suppliers:
        cursor.execute(
            "INSERT INTO suppliers VALUES (?, ?, ?, ?)",
            (sup.supplier_id, sup.material_id, sup.lead_time_hours, sup.status.value),
        )

    for me in maintenance_events:
        cursor.execute(
            "INSERT INTO maintenance_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                me.maintenance_id,
                me.machine_id,
                me.start_time.isoformat(),
                me.duration_hours,
                me.maintenance_type.value,
                me.status.value,
            ),
        )

    conn.commit()
    conn.close()

def load_factories(conn: sqlite3.Connection) -> List[Factory]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM factories")
    rows = cursor.fetchall()
    return [
        Factory(
            factory_id=r["factory_id"],
            name=r["name"],
            location=r["location"],
            operating_hours=r["operating_hours"],
        )
        for r in rows
    ]

def load_lines(conn: sqlite3.Connection) -> List[ProductionLine]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM production_lines")
    rows = cursor.fetchall()
    return [
        ProductionLine(
            line_id=r["line_id"],
            name=r["name"],
            capacity_per_hour=r["capacity_per_hour"],
            supported_models=json.loads(r["supported_models"]),
            status=r["status"],
        )
        for r in rows
    ]

def load_stations(conn: sqlite3.Connection) -> List[Station]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stations")
    rows = cursor.fetchall()
    return [
        Station(
            station_id=r["station_id"],
            line_id=r["line_id"],
            name=r["name"],
            operation_type=r["operation_type"],
            capacity_per_hour=r["capacity_per_hour"],
        )
        for r in rows
    ]

def load_machines(conn: sqlite3.Connection) -> List[Machine]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machines")
    rows = cursor.fetchall()
    return [
        Machine(
            machine_id=r["machine_id"],
            line_id=r["line_id"],
            station_id=r["station_id"],
            machine_type=r["machine_type"],
            cycle_time_seconds=r["cycle_time_seconds"],
            capacity_per_hour=r["capacity_per_hour"],
            status=r["status"],
            health_score=r["health_score"],
        )
        for r in rows
    ]

def load_orders(conn: sqlite3.Connection) -> List[ProductionOrder]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM production_orders")
    rows = cursor.fetchall()
    return [
        ProductionOrder(
            order_id=r["order_id"],
            product_model=r["product_model"],
            quantity=r["quantity"],
            priority=r["priority"],
            due_time=datetime.fromisoformat(r["due_time"]),
            assigned_line=r["assigned_line"],
            status=r["status"],
        )
        for r in rows
    ]

def load_schedules(conn: sqlite3.Connection) -> List[ProductionSchedule]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM production_schedules")
    rows = cursor.fetchall()
    return [
        ProductionSchedule(
            schedule_id=r["schedule_id"],
            order_id=r["order_id"],
            machine_id=r["machine_id"],
            station_id=r["station_id"],
            start_time=datetime.fromisoformat(r["start_time"]),
            end_time=datetime.fromisoformat(r["end_time"]),
            planned_quantity=r["planned_quantity"],
        )
        for r in rows
    ]

def load_materials(conn: sqlite3.Connection) -> List[Material]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials")
    rows = cursor.fetchall()
    return [
        Material(
            material_id=r["material_id"],
            name=r["name"],
            available_quantity=r["available_quantity"],
            safety_stock=r["safety_stock"],
        )
        for r in rows
    ]

def load_suppliers(conn: sqlite3.Connection) -> List[Supplier]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM suppliers")
    rows = cursor.fetchall()
    return [
        Supplier(
            supplier_id=r["supplier_id"],
            material_id=r["material_id"],
            lead_time_hours=r["lead_time_hours"],
            status=r["status"],
        )
        for r in rows
    ]

def load_maintenance_events(conn: sqlite3.Connection) -> List[MaintenanceEvent]:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM maintenance_events")
    rows = cursor.fetchall()
    return [
        MaintenanceEvent(
            maintenance_id=r["maintenance_id"],
            machine_id=r["machine_id"],
            start_time=datetime.fromisoformat(r["start_time"]),
            duration_hours=r["duration_hours"],
            maintenance_type=r["maintenance_type"],
            status=r["status"],
        )
        for r in rows
    ]

