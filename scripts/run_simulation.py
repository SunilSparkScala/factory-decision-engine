import sys
import json
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.simulation import simulate_machine_failure

def run_demo():
    machine_id = "M17"
    downtime_hours = 8.0

    print("==================================================")
    print("FACTORY DECISION ENGINE")
    print("Phase 1 — Scenario Simulator")
    print("==================================================")
    print(f"\nScenario:\nMachine {machine_id} unavailable for {downtime_hours} hours.\n")

    impact = simulate_machine_failure(machine_id=machine_id, downtime_hours=downtime_hours)

    print("--------------------------------------------------")
    print("FACTORY IMPACT")
    print("--------------------------------------------------")
    print(f"Machine: {impact.machine.machine_id}")
    print(f"Station: {impact.station.station_id} ({impact.station.name})")
    print(f"Line: {impact.line.line_id} ({impact.line.name})")
    print(f"\nEstimated capacity loss: {impact.capacity_impact.capacity_loss_percentage}% ({impact.capacity_impact.capacity_loss_units} units)")

    print("\nAffected orders:")
    if impact.affected_orders:
        for order in impact.affected_orders:
            print(f"- {order.order_id} [{order.priority.value}] (Model: {order.product_model}, Qty: {order.quantity})")
    else:
        print("- None")

    print(f"\nHigh-priority orders affected: {len(impact.high_priority_orders)}")
    print(f"Delivery risk: {impact.delivery_risk.value}")

    print("\n--------------------------------------------------")
    print("ALTERNATIVE CAPACITY CANDIDATES")
    print("--------------------------------------------------")
    if impact.alternative_candidates:
        for idx, candidate in enumerate(impact.alternative_candidates, start=1):
            print(f"Candidate {idx}:")
            print(f"  Machine: {candidate.machine_id}")
            print(f"  Line: {candidate.line_id}, Station: {candidate.station_id}")
            print(f"  Type: {candidate.machine_type}")
            print(f"  Capacity: {candidate.capacity_per_hour} units/hr (Status: {candidate.status.value})\n")
    else:
        print("No compatible alternative machines available.\n")

    print("--------------------------------------------------")
    print("Simulation completed successfully.")
    print("==================================================")

if __name__ == "__main__":
    run_demo()
