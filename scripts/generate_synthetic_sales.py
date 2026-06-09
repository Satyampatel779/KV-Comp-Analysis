from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Calgary sale events from properties NDJSON."
    )
    parser.add_argument(
        "--input",
        default="data/processed/properties.ndjson",
        help="Path to the normalized property master NDJSON file.",
    )
    parser.add_argument(
        "--output",
        default="data/processed/sales.ndjson",
        help="Path to the synthetic sales NDJSON output.",
    )
    parser.add_argument(
        "--sale-rate",
        type=float,
        default=0.12,
        help="Fraction of properties to mark as sold in the last year.",
    )
    parser.add_argument(
        "--reference-date",
        default="2026-06-03",
        help="Anchor date used to backfill synthetic sale dates.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max number of synthetic sales to write.",
    )
    return parser.parse_args()


def stable_unit_interval(key: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big")
    return integer / float(2**64 - 1)


def iter_documents(file_path: Path):
    with file_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                yield json.loads(line)


def classify_property(sub_property_use: str | None) -> str:
    mapping = {
        "R110": "detached",
        "R112": "detached",
        "R120": "semi_detached",
        "R121": "duplex",
        "R201": "condo_apartment",
        "R202": "condo_apartment",
        "R210": "townhouse",
        "R211": "townhouse",
        "R402": "townhouse",
    }
    if not sub_property_use:
        return "other_residential"
    return mapping.get(sub_property_use, "other_residential")


def bounded_int(low: int, high: int, ratio: float) -> int:
    span = high - low + 1
    return low + min(int(ratio * span), span - 1)


def infer_bedrooms(property_type: str, assessed_value: float, land_size_sqm: float | None, ratio: float) -> int:
    if property_type == "condo_apartment":
        return bounded_int(1, 3, ratio)
    if property_type in {"townhouse", "semi_detached", "duplex"}:
        return bounded_int(2, 4, ratio)

    land_bonus = 0
    if land_size_sqm and land_size_sqm >= 700:
        land_bonus = 1
    if assessed_value >= 1_200_000:
        return bounded_int(4, 6 + land_bonus, ratio)
    if assessed_value >= 700_000:
        return bounded_int(3, 5 + land_bonus, ratio)
    if assessed_value >= 400_000:
        return bounded_int(3, 4 + land_bonus, ratio)
    return bounded_int(2, 4, ratio)


def infer_bathrooms(property_type: str, bedrooms: int, ratio: float) -> int:
    if property_type == "condo_apartment":
        return min(3, max(1, bedrooms - 1 + bounded_int(0, 1, ratio)))
    if property_type in {"townhouse", "semi_detached", "duplex"}:
        return min(4, max(1, bedrooms - 1 + bounded_int(0, 1, ratio)))
    return min(4, max(2, bedrooms - 1 + bounded_int(0, 1, ratio)))


def infer_garage_count(property_type: str, ratio: float) -> int:
    if property_type == "condo_apartment":
        return bounded_int(0, 1, ratio)
    if property_type in {"townhouse", "semi_detached", "duplex"}:
        return bounded_int(0, 2, ratio)
    return bounded_int(1, 3, ratio)


def sale_multiplier(assessed_value: float, property_type: str, ratio: float) -> float:
    base_low = 0.93
    base_high = 1.11

    if property_type == "condo_apartment":
        base_low = 0.91
        base_high = 1.07
    elif property_type in {"townhouse", "semi_detached", "duplex"}:
        base_low = 0.92
        base_high = 1.09
    elif assessed_value >= 1_500_000:
        base_low = 0.95
        base_high = 1.14

    return base_low + (base_high - base_low) * ratio


def build_sale_document(property_doc: dict, reference_date: date) -> dict | None:
    property_id = property_doc.get("property_id")
    assessment = property_doc.get("assessment") or {}
    property_info = property_doc.get("property") or {}
    community = property_doc.get("community") or {}

    assessed_value = assessment.get("assessed_value")
    location = property_doc.get("location")

    if not property_id or not assessed_value or not location:
        return None

    assessed_value = float(assessed_value)
    property_type = classify_property(property_info.get("sub_property_use"))

    sale_date_ratio = stable_unit_interval(property_id, "sale_date")
    sale_date_offset = bounded_int(30, 365, sale_date_ratio)
    sale_date = reference_date - timedelta(days=sale_date_offset)

    price_ratio = stable_unit_interval(property_id, "sale_price")
    price = round(assessed_value * sale_multiplier(assessed_value, property_type, price_ratio), -3)

    bedroom_ratio = stable_unit_interval(property_id, "bedrooms")
    bedrooms = infer_bedrooms(
        property_type,
        assessed_value,
        property_info.get("land_size_sqm"),
        bedroom_ratio,
    )
    bathrooms = infer_bathrooms(property_type, bedrooms, stable_unit_interval(property_id, "bathrooms"))
    garage_count = infer_garage_count(property_type, stable_unit_interval(property_id, "garage"))
    condition_score = bounded_int(2, 5, stable_unit_interval(property_id, "condition"))
    renovation_score = bounded_int(1, 5, stable_unit_interval(property_id, "renovation"))

    return {
        "sale_id": f"sale_{property_id}",
        "property_id": property_id,
        "city": property_doc.get("city"),
        "province": property_doc.get("province"),
        "sale_date": f"{sale_date.isoformat()}T00:00:00Z",
        "sale_price": int(price),
        "sale_type": "arms_length",
        "is_true_sale": True,
        "listing_status_at_sale": "sold",
        "source_type": "synthetic",
        "community": {
            "code": community.get("code"),
            "name": community.get("name"),
        },
        "location": location,
        "property_snapshot": {
            "address": (property_doc.get("address") or {}).get("full"),
            "property_type_normalized": property_type,
            "sub_property_use": property_info.get("sub_property_use"),
            "year_built": property_info.get("year_built"),
            "land_use_designation": property_info.get("land_use_designation"),
            "land_size_sqm": property_info.get("land_size_sqm"),
            "assessed_value": assessed_value,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "garage_count": garage_count,
        },
        "special_features": {
            "condition_score": condition_score,
            "renovation_score": renovation_score,
            "has_legal_suite": stable_unit_interval(property_id, "suite") < 0.08,
            "has_walkout_basement": stable_unit_interval(property_id, "walkout") < 0.1,
            "backs_onto_flag": stable_unit_interval(property_id, "backs_onto") < 0.12,
        },
        "synthetic_metadata": {
            "generation_version": 1,
            "reference_date": reference_date.isoformat(),
        },
    }


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    reference_date = date.fromisoformat(args.reference_date)

    written = 0
    skipped = 0
    considered = 0

    with output_path.open("w", encoding="utf-8") as out_handle:
        for property_doc in iter_documents(input_path):
            property_id = property_doc.get("property_id")
            if not property_id:
                skipped += 1
                continue

            considered += 1
            if stable_unit_interval(str(property_id), "sale_selector") >= args.sale_rate:
                skipped += 1
                continue

            sale_doc = build_sale_document(property_doc, reference_date)
            if sale_doc is None:
                skipped += 1
                continue

            out_handle.write(json.dumps(sale_doc, separators=(",", ":")) + "\n")
            written += 1

            if args.limit is not None and written >= args.limit:
                break

    print(
        json.dumps(
            {
                "input": str(input_path),
                "output": str(output_path),
                "considered": considered,
                "written": written,
                "skipped": skipped,
                "sale_rate": args.sale_rate,
                "reference_date": args.reference_date,
                "limit": args.limit,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()