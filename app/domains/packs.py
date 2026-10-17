"""
Industry packs: everything that names or describes the plant.

Both packs share one structure (3 lines, 12 stations, 36 machines, same seed and capacities), so
the deterministic engine produces identical numbers in either industry. Only the vocabulary,
documents and examples differ. Lists are positional:
- `models` map to the generator's product slots [S, E, C, X]
- `operations` map to the station slots [press, join, finish, assemble, inspect]
"""

AUTOMOTIVE = {
    "key": "automotive",
    "label": "Automotive",
    "plant": {"name": "Apex Automotive Plant 1", "location": "Chennai, India"},
    "unit": "vehicles",
    "lines": {"LINE_A": "Line A (Sedan)", "LINE_B": "Line B (SUV)", "LINE_C": "Line C (Compact)"},
    "models": ["Aurora", "Breeze", "Cruiser", "Dash"],
    "operations": [
        {"code": "STAMPING", "display": "Stamping", "noun": "stamping press",
         "signals": {"vibration": 2.4, "temperature": 52.0, "current": 38.0}, "repair_hours": (4.0, 8.0)},
        {"code": "WELDING", "display": "Welding", "noun": "welding robot",
         "signals": {"vibration": 1.9, "temperature": 58.0, "current": 44.0}, "repair_hours": (6.0, 10.0)},
        {"code": "PAINTING", "display": "Painting", "noun": "paint booth",
         "signals": {"vibration": 1.2, "temperature": 46.0, "current": 21.0}, "repair_hours": (5.0, 9.0)},
        {"code": "ASSEMBLY", "display": "Assembly", "noun": "assembly cell",
         "signals": {"vibration": 1.6, "temperature": 41.0, "current": 17.0}, "repair_hours": (3.0, 6.0)},
        {"code": "INSPECTION", "display": "Inspection", "noun": "inspection station",
         "signals": {"vibration": 0.8, "temperature": 35.0, "current": 9.0}, "repair_hours": (2.0, 4.0)},
    ],
    "materials": [
        "Steel Sheets", "Aluminum Panels", "Welding Wire", "Automotive Primer", "Gloss Paint Coat",
        "Wiring Harness", "Brake Assemblies", "Engine Blocks", "Transmission Units", "Tires & Alloy Wheels",
        "Windshields", "LED Headlight Units", "Leather Seats", "Dashboard Assemblies", "Exhaust Pipes",
        "Fuel Tanks", "Radiator Grilles", "Side Mirrors", "Airbag Modules", "Suspension Springs",
        "Steering Columns", "Door Panels", "Bumper Guards", "Battery Packs", "Quality Test Markers",
    ],
    "examples": {
        "placeholder": "e.g. The Unit 2 welder at station B2 is down, maybe 6 to 10 hours. Keep it cheap, no overtime.",
        "clear": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.",
        "plain": "The Unit 2 welder at station B2 is down, maybe 6 to 10 hours. Keep it cheap and no overtime please.",
        "vague": "The welding robot on line B jammed, probably out till lunch. Don't let customer orders slip.",
    },
    "faults": [
        {"machine_id": "M17", "code": "E-42", "title": "Welding arm jam",
         "detail": "Robot arm stopped mid-cycle on Station B2; servo overcurrent detected.",
         "repair_hours": [6, 10], "severity": "HIGH"},
        {"machine_id": "M05", "code": "W-17", "title": "Wire feed fault",
         "detail": "Welding wire feed motor stalled on Station A2.",
         "repair_hours": [2, 4], "severity": "MEDIUM"},
        {"machine_id": "M35", "code": "V-31", "title": "Vision camera calibration fault",
         "detail": "Inspection camera lost calibration on Station C4; false rejects rising.",
         "repair_hours": [4, 8], "severity": "HIGH"},
        {"machine_id": "M20", "code": "P-23", "title": "Paint booth humidity out of range",
         "detail": "Booth humidity above process window on Station B3; line held for quality.",
         "repair_hours": [3, 5], "severity": "MEDIUM"},
    ],
}

ELECTRONICS = {
    "key": "electronics",
    "label": "Electronics",
    "plant": {"name": "Northwind Electronics Plant 1", "location": "Pune, India"},
    "unit": "boards",
    "lines": {"LINE_A": "Line A (Routers)", "LINE_B": "Line B (Smart speakers)", "LINE_C": "Line C (Thermostats)"},
    "models": ["NW-Router", "NW-Hub", "NW-Speaker", "NW-Thermo"],
    "operations": [
        {"code": "SOLDER_PASTE", "display": "Solder paste printing", "noun": "solder paste printer",
         "signals": {"vibration": 2.4, "temperature": 52.0, "current": 38.0}, "repair_hours": (4.0, 8.0)},
        {"code": "PICK_AND_PLACE", "display": "Pick-and-place", "noun": "pick-and-place machine",
         "signals": {"vibration": 1.9, "temperature": 58.0, "current": 44.0}, "repair_hours": (6.0, 10.0)},
        {"code": "REFLOW", "display": "Reflow soldering", "noun": "reflow oven",
         "signals": {"vibration": 1.2, "temperature": 46.0, "current": 21.0}, "repair_hours": (5.0, 9.0)},
        {"code": "FINAL_ASSEMBLY", "display": "Final assembly", "noun": "assembly cell",
         "signals": {"vibration": 1.6, "temperature": 41.0, "current": 17.0}, "repair_hours": (3.0, 6.0)},
        {"code": "AOI", "display": "Optical inspection", "noun": "AOI inspection station",
         "signals": {"vibration": 0.8, "temperature": 35.0, "current": 9.0}, "repair_hours": (2.0, 4.0)},
    ],
    "materials": [
        "SAC305 Solder Paste", "FR-4 PCB Panels", "Placement Nozzles", "Conformal Coating", "Stencil Foils",
        "Wiring Looms", "Wi-Fi Modules", "Speaker Drivers", "Plastic Enclosures", "Power Adapters",
        "LCD Displays", "Status LEDs", "Microcontrollers", "Memory Chips", "Capacitor Reels",
        "Resistor Reels", "Heat Sinks", "Antennas", "Temperature Sensors", "USB Connectors",
        "Screws & Fasteners", "Labels & Barcodes", "Packaging Cartons", "Li-ion Cells", "Test Fixtures",
    ],
    "examples": {
        "placeholder": "e.g. The Unit 2 pick-and-place machine at station B2 is down, maybe 6 to 10 hours. Keep it cheap, no overtime.",
        "clear": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.",
        "plain": "The Unit 2 pick-and-place machine at station B2 is down, maybe 6 to 10 hours. Keep it cheap and no overtime please.",
        "vague": "The pick-and-place robot on line B jammed, probably out till lunch. Don't let customer orders slip.",
    },
    "faults": [
        {"machine_id": "M17", "code": "E-42", "title": "Placement head vacuum fault",
         "detail": "Placement head lost vacuum mid-cycle on Station B2; nozzle pick errors spiking.",
         "repair_hours": [6, 10], "severity": "HIGH"},
        {"machine_id": "M05", "code": "F-17", "title": "Feeder jam",
         "detail": "Tape feeder bank jammed on Station A2; reel advance stopped.",
         "repair_hours": [2, 4], "severity": "MEDIUM"},
        {"machine_id": "M35", "code": "V-31", "title": "AOI camera calibration fault",
         "detail": "Optical inspection camera lost calibration on Station C4; false rejects spiking.",
         "repair_hours": [4, 8], "severity": "HIGH"},
        {"machine_id": "M20", "code": "R-23", "title": "Reflow zone temperature out of profile",
         "detail": "Reflow oven zone 4 drifted above the solder profile on Station B3; line held for quality.",
         "repair_hours": [3, 5], "severity": "MEDIUM"},
    ],
}

PACKS = {p["key"]: p for p in (ELECTRONICS, AUTOMOTIVE)}
