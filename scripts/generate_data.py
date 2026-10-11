import sys
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import config
from app.data import SyntheticFactoryGenerator, save_factory_data
from app.domains import db_path_for
from app.domains.packs import PACKS


def generate(domain: str) -> None:
    """Build one industry pack's database (same seed and structure for every pack)."""
    generator = SyntheticFactoryGenerator(seed=config.RANDOM_SEED, domain=domain)
    data = generator.generate_all()
    db_path = db_path_for(domain)
    if db_path.exists():
        db_path.unlink()  # rebuild from scratch so re-runs never duplicate rows

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
        db_path=db_path,
    )

    print(f"\n[{domain}] {data['factory'][0].name} — saved to {db_path}")
    print(f"  Lines: {len(data['lines'])} · Stations: {len(data['stations'])} · Machines: {len(data['machines'])}"
          f" · Orders: {len(data['orders'])} · Schedules: {len(data['schedules'])}"
          f" · Materials: {len(data['materials'])} · Suppliers: {len(data['suppliers'])}"
          f" · Maintenance events: {len(data['maintenance_events'])}")


def main():
    print("==================================================")
    print("GENERATING SYNTHETIC FACTORY DATASETS")
    print(f"Random Seed: {config.RANDOM_SEED}")
    print("==================================================")
    domains = sys.argv[1:] or list(PACKS)
    for domain in domains:
        generate(domain)
    print("\nDataset generation completed successfully!")


if __name__ == "__main__":
    main()
