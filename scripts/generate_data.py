import sys
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import config
from app.data import SyntheticFactoryGenerator, save_factory_data

def main():
    print("==================================================")
    print("GENERATING SYNTHETIC FACTORY DATASET")
    print(f"Random Seed: {config.RANDOM_SEED}")
    print("==================================================")

    generator = SyntheticFactoryGenerator(seed=config.RANDOM_SEED)
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
    )

    print("\nDataset generation completed successfully!")
    print(f"Saved database to: {config.DB_PATH}")
    print("\nSummary:")
    print(f"  Factories:          {len(data['factory'])}")
    print(f"  Production Lines:   {len(data['lines'])}")
    print(f"  Stations:           {len(data['stations'])}")
    print(f"  Machines:           {len(data['machines'])}")
    print(f"  Production Orders:  {len(data['orders'])}")
    print(f"  Schedules:          {len(data['schedules'])}")
    print(f"  Materials:          {len(data['materials'])}")
    print(f"  Suppliers:          {len(data['suppliers'])}")
    print(f"  Maintenance Events: {len(data['maintenance_events'])}")

if __name__ == "__main__":
    main()
