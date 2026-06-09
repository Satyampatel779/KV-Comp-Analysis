from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize Calgary current assessment GeoJSON into NDJSON."
    )
    parser.add_argument(
        "--input",
        default="data/raw/Current_Year_Property_Assessments_(Parcel)_20260603.geojson",
        help="Path to the raw Calgary current GeoJSON file.",
    )
    parser.add_argument(
        "--output",
        default="data/processed/properties.ndjson",
        help="Path to the normalized NDJSON output.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of normalized records to write.",
    )
    return parser.parse_args()


def iter_feature_lines(file_path: Path) -> Iterable[dict]:
    with file_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or not line.startswith('{"type":"Feature"'):
                continue

            if line.endswith(","):
                line = line[:-1]

            yield json.loads(line)


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: object) -> int | None:
    parsed = parse_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def bbox_center(geometry: dict | None) -> list[float] | None:
    if not geometry:
        return None

    coords = geometry.get("coordinates")
    if not coords:
        return None

    longitudes: list[float] = []
    latitudes: list[float] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            if len(node) == 2 and all(isinstance(item, (int, float)) for item in node):
                longitudes.append(float(node[0]))
                latitudes.append(float(node[1]))
                return
            for child in node:
                walk(child)

    walk(coords)

    if not longitudes or not latitudes:
        return None

    min_lon = min(longitudes)
    max_lon = max(longitudes)
    min_lat = min(latitudes)
    max_lat = max(latitudes)
    return [(min_lon + max_lon) / 2, (min_lat + max_lat) / 2]


def normalize_feature(feature: dict) -> dict | None:
    properties = feature.get("properties", {})

    if properties.get("assessment_class") != "RE":
        return None

    address = properties.get("address")
    unique_key = properties.get("unique_key")
    roll_number = properties.get("roll_number")

    if not address or not unique_key or not roll_number:
        return None

    center = bbox_center(feature.get("geometry"))
    assessed_value = parse_float(properties.get("assessed_value"))

    return {
        "property_id": f"calgary_{unique_key}",
        "city": "Calgary",
        "province": "AB",
        "source_dataset": "calgary_current",
        "source_record_id": properties.get(":id"),
        "roll_year": parse_int(properties.get("roll_year")),
        "roll_number": str(roll_number),
        "cpid": str(properties.get("cpid")) if properties.get("cpid") is not None else None,
        "address": {
            "full": str(address),
            "postal_code": None,
        },
        "location": {
            "type": "Point",
            "coordinates": center,
        }
        if center
        else None,
        "community": {
            "code": properties.get("comm_code"),
            "name": properties.get("comm_name"),
        },
        "assessment": {
            "class_code": properties.get("assessment_class"),
            "class_description": properties.get("assessment_class_description"),
            "assessed_value": assessed_value,
            "residential_assessed_value": parse_float(properties.get("re_assessed_value")),
        },
        "property": {
            "year_built": parse_int(properties.get("year_of_construction")),
            "land_use_designation": properties.get("land_use_designation"),
            "property_type": properties.get("property_type"),
            "sub_property_use": properties.get("sub_property_use"),
            "land_size_sqm": parse_float(properties.get("land_size_sm")),
            "land_size_sqft": parse_float(properties.get("land_size_sf")),
            "land_size_acres": parse_float(properties.get("land_size_ac")),
        },
        "raw_refs": {
            "unique_key": unique_key,
            "mod_date": properties.get("mod_date"),
        },
    }


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0

    with output_path.open("w", encoding="utf-8") as out_handle:
        for feature in iter_feature_lines(input_path):
            normalized = normalize_feature(feature)
            if normalized is None:
                skipped += 1
                continue

            out_handle.write(json.dumps(normalized, separators=(",", ":")) + "\n")
            written += 1

            if args.limit is not None and written >= args.limit:
                break

    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output_path),
                "written": written,
                "skipped": skipped,
                "limit": args.limit,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()