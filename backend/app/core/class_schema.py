"""Versioned ATCC class schemas. See docs/01_PRD.md section 8.

Not a DB table: docs/01_PRD.md only requires class configuration to be
"versioned and project-configurable", and docs/05_DATABASE_DESIGN.md
already versions it via `projects.class_schema_version`. A per-project
custom schema editor is out of Phase 4's scope (not in its build
list) - every project currently uses "v1".
"""

ATCC_CLASSES_V1: list[dict] = [
    {"id": 1, "name": "Two Wheeler"},
    {"id": 2, "name": "Three-Wheeler Passenger"},
    {"id": 3, "name": "Three-Wheeler Freight"},
    {"id": 4, "name": "Car/Jeep/Van"},
    {"id": 5, "name": "LCV 2-Axle"},
    {"id": 6, "name": "LCV 3-Axle"},
    {"id": 7, "name": "Bus 2-Axle"},
    {"id": 8, "name": "Bus 3-Axle"},
    {"id": 9, "name": "Mini-Bus"},
    {"id": 10, "name": "Truck 2-Axle"},
    {"id": 11, "name": "Truck 3-Axle"},
    {"id": 12, "name": "Truck 4-Axle"},
    {"id": 13, "name": "Truck 5-Axle"},
    {"id": 14, "name": "Truck 6-Axle"},
    {"id": 15, "name": "Truck Multi-Axle (7+)"},
    {"id": 16, "name": "Earth Moving Machinery"},
    {"id": 17, "name": "Heavy Construction Machinery"},
    {"id": 18, "name": "Tractor"},
    {"id": 19, "name": "Tractor with Trailer"},
    {"id": 20, "name": "Tata Ace / similar mini LCV"},
]

CLASS_SCHEMAS: dict[str, list[dict]] = {"v1": ATCC_CLASSES_V1}


def get_class_schema(version: str) -> list[dict]:
    return CLASS_SCHEMAS.get(version, [])


def is_valid_class_id(version: str, class_id: int) -> bool:
    return any(c["id"] == class_id for c in get_class_schema(version))
