import pytest
import sqlite3
from app.config import config
from app.data import SyntheticFactoryGenerator, save_factory_data, get_connection

def test_data_generation_counts():
    generator = SyntheticFactoryGenerator(seed=42)
    data = generator.generate_all()

    assert len(data["factory"]) == 1
    assert len(data["lines"]) == 3
    assert len(data["stations"]) == 12
    assert len(data["machines"]) >= 30
    assert len(data["orders"]) >= 50
    assert len(data["materials"]) >= 20
    assert len(data["suppliers"]) >= 10
    assert len(data["maintenance_events"]) >= 5

def test_data_relationships():
    generator = SyntheticFactoryGenerator(seed=42)
    data = generator.generate_all()

    line_ids = {l.line_id for l in data["lines"]}
    station_ids = {s.station_id for s in data["stations"]}
    machine_ids = {m.machine_id for m in data["machines"]}
    order_ids = {o.order_id for o in data["orders"]}
    material_ids = {mat.material_id for mat in data["materials"]}

    # Verify stations belong to valid lines
    for s in data["stations"]:
        assert s.line_id in line_ids, f"Station {s.station_id} has invalid line_id {s.line_id}"

    # Verify machines belong to valid stations and lines
    for m in data["machines"]:
        assert m.station_id in station_ids, f"Machine {m.machine_id} has invalid station_id {m.station_id}"
        assert m.line_id in line_ids, f"Machine {m.machine_id} has invalid line_id {m.line_id}"

    # Verify orders assigned to valid lines
    for o in data["orders"]:
        assert o.assigned_line in line_ids, f"Order {o.order_id} assigned to invalid line {o.assigned_line}"

    # Verify schedules refer to valid orders, machines, stations
    for ps in data["schedules"]:
        assert ps.order_id in order_ids
        assert ps.machine_id in machine_ids
        assert ps.station_id in station_ids
        assert ps.start_time < ps.end_time

    # Verify suppliers refer to valid materials
    for sup in data["suppliers"]:
        assert sup.material_id in material_ids

def test_reproducibility():
    gen1 = SyntheticFactoryGenerator(seed=42).generate_all()
    gen2 = SyntheticFactoryGenerator(seed=42).generate_all()

    assert len(gen1["machines"]) == len(gen2["machines"])
    for m1, m2 in zip(gen1["machines"], gen2["machines"]):
        assert m1.machine_id == m2.machine_id
        assert m1.cycle_time_seconds == m2.cycle_time_seconds
        assert m1.status == m2.status

    for o1, o2 in zip(gen1["orders"], gen2["orders"]):
        assert o1.order_id == o2.order_id
        assert o1.quantity == o2.quantity
        assert o1.priority == o2.priority

def test_database_persistence(tmp_path):
    test_db = tmp_path / "test_factory.db"
    generator = SyntheticFactoryGenerator(seed=42)
    data = generator.generate_all()

    save_factory_data(
        factories=data["factory"],
        lines=data["lines"],
        stations=data["stations"],
        machines=data["machines"],
        orders=data["orders"],
        schedules=data["schedules"],
        materials=data["materials"],
        suppliers=data["suppliers"],
        maintenance_events=data["maintenance_events"],
        db_path=test_db,
    )

    conn = sqlite3.connect(str(test_db))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM machines")
    machine_count = cursor.fetchone()[0]
    assert machine_count == len(data["machines"])

    cursor.execute("SELECT COUNT(*) FROM production_orders")
    order_count = cursor.fetchone()[0]
    assert order_count == len(data["orders"])

    conn.close()
