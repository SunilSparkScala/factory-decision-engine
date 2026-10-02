import pytest
import sqlite3
from app.exceptions import UnknownMachineError, UnknownStationError, UnknownLineError, UnknownOrderError
from app.factory import (
    FactoryState,
    get_factory_state,
    get_machine,
    get_station,
    get_line,
    get_production_order,
    get_active_orders,
    get_machine_status,
    get_machine_dependencies,
    get_factory_summary,
)
from app.data import get_connection

def test_get_factory_state_loads():
    state = get_factory_state()
    assert isinstance(state, FactoryState)
    assert state.factory is not None
    assert state.factory.factory_id == "F01"
    assert len(state.production_lines) == 3
    assert len(state.stations) == 12
    assert len(state.machines) >= 30
    assert len(state.production_orders) >= 50
    assert len(state.materials) >= 20
    assert len(state.suppliers) >= 10
    assert len(state.maintenance_events) >= 1

def test_get_machine_valid():
    machine = get_machine("M17")
    assert machine.machine_id == "M17"
    assert machine.line_id in ["LINE_A", "LINE_B", "LINE_C"]
    assert machine.station_id is not None

def test_get_machine_unknown_raises():
    with pytest.raises(UnknownMachineError):
        get_machine("M9999_INVALID")

def test_get_station_valid_and_invalid():
    station = get_station("STA_A1")
    assert station.station_id == "STA_A1"
    
    with pytest.raises(UnknownStationError):
        get_station("STA_INVALID")

def test_get_line_valid_and_invalid():
    line = get_line("LINE_A")
    assert line.line_id == "LINE_A"
    
    with pytest.raises(UnknownLineError):
        get_line("LINE_INVALID")

def test_get_production_order_valid_and_invalid():
    state = get_factory_state()
    valid_order_id = state.production_orders[0].order_id
    order = get_production_order(valid_order_id)
    assert order.order_id == valid_order_id
    
    with pytest.raises(UnknownOrderError):
        get_production_order("O_INVALID")

def test_get_active_orders():
    active_orders = get_active_orders()
    assert isinstance(active_orders, list)
    assert len(active_orders) > 0

def test_get_machine_status():
    status = get_machine_status("M17")
    assert status.value in ["HEALTHY", "DEGRADED", "MAINTENANCE", "DOWN"]

def test_get_factory_summary_dynamic():
    summary = get_factory_summary()
    assert summary["total_machines"] == 36
    assert summary["total_production_lines"] == 3
    assert summary["total_stations"] == 12
    assert summary["total_orders"] >= 50
    assert "machines_by_status" in summary
    assert sum(summary["machines_by_status"].values()) == 36

def test_machine_dependency_traversal():
    deps = get_machine_dependencies("M17")
    assert deps["machine_id"] == "M17"
    assert "station" in deps
    assert "line" in deps
    assert "schedules" in deps
    assert "affected_orders" in deps
    assert isinstance(deps["schedules"], list)
    assert isinstance(deps["affected_orders"], list)

def test_machine_dependency_traversal_dynamic_variation():
    deps_m17 = get_machine_dependencies("M17")
    deps_m01 = get_machine_dependencies("M01")

    assert deps_m17["machine_id"] == "M17"
    assert deps_m01["machine_id"] == "M01"
    assert deps_m17["station"]["station_id"] != deps_m01["station"]["station_id"]

def test_machine_dependency_unknown_raises():
    with pytest.raises(UnknownMachineError):
        get_machine_dependencies("UNKNOWN_MACHINE_XYZ")

def test_sqlite_foreign_keys_enforced(tmp_path):
    test_db = tmp_path / "fk_test.db"
    conn = get_connection(test_db)
    cursor = conn.cursor()
    
    # Check PRAGMA foreign_keys is 1
    cursor.execute("PRAGMA foreign_keys;")
    fk_setting = cursor.fetchone()[0]
    assert fk_setting == 1, "SQLite foreign_keys PRAGMA must be 1 (ON)"

    # Create dummy parent table and child table with FK constraint
    cursor.execute("CREATE TABLE parent (id TEXT PRIMARY KEY);")
    cursor.execute("CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT, FOREIGN KEY (parent_id) REFERENCES parent(id));")
    conn.commit()

    # Inserting invalid child row should raise IntegrityError due to FK enforcement
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("INSERT INTO child VALUES ('c1', 'non_existent_parent');")
        conn.commit()
        
    conn.close()
