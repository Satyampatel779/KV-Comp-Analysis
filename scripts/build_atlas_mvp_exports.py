from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Atlas-safe slim NDJSON exports for the free-tier MVP."
    )
    parser.add_argument(
        "--properties-input",
        default="data/processed/properties.ndjson",
        help="Path to the full normalized properties NDJSON file.",
    )
    parser.add_argument(
        "--sales-input",
        default="data/processed/sales.ndjson",
        help="Path to the full synthetic sales NDJSON file.",
    )
    parser.add_argument(
        "--properties-output",
        default="data/processed/properties_atlas_mvp.ndjson",
        help="Path to the slim Atlas properties NDJSON output.",
    )
    parser.add_argument(
        "--sales-output",
        default="data/processed/sales_atlas_mvp.ndjson",
        help="Path to the slim Atlas sales NDJSON output.",
    )
    parser.add_argument(
        "--unsold-sample-rate",
        type=float,
        default=0.1,
        help="Deterministic sample rate for non-sold properties.",
    )
    return parser.parse_args()


def iter_documents(file_path: Path):
    with file_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                yield json.loads(line)


def stable_unit_interval(key: str, salt: str) -> float:
    digest = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big")
    return integer / float(2**64 - 1)


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


def slim_property(document: dict) -> dict | None:
    property_id = document.get("property_id")
    location = document.get("location")
    assessment = document.get("assessment") or {}
    property_info = document.get("property") or {}
    community = document.get("community") or {}
    address = document.get("address") or {}

    if not property_id or not location:
        return None

    return {
        "property_id": property_id,
        "city": document.get("city"),
        "province": document.get("province"),
        "address": {"full": address.get("full")},
        "location": location,
        "community": {
            "code": community.get("code"),
            "name": community.get("name"),
        },
        "assessment": {
            "assessed_value": assessment.get("assessed_value"),
        },
        "property": {
            "property_type_normalized": classify_property(property_info.get("sub_property_use")),
            "year_built": property_info.get("year_built"),
            "land_size_sqm": property_info.get("land_size_sqm"),
            "land_use_designation": property_info.get("land_use_designation"),
        },
    }


def slim_sale(document: dict) -> dict | None:
    sale_id = document.get("sale_id")
    property_id = document.get("property_id")
    location = document.get("location")
    snapshot = document.get("property_snapshot") or {}
    community = document.get("community") or {}

    if not sale_id or not property_id or not location:
        return None

    return {
        "sale_id": sale_id,
        "property_id": property_id,
        "city": document.get("city"),
        "province": document.get("province"),
        "sale_date": document.get("sale_date"),
        "sale_price": document.get("sale_price"),
        "source_type": document.get("source_type"),
        "community": {
            "code": community.get("code"),
            "name": community.get("name"),
        },
        "location": location,
        "property_snapshot": {
            "property_type_normalized": snapshot.get("property_type_normalized"),
            "year_built": snapshot.get("year_built"),
            "land_size_sqm": snapshot.get("land_size_sqm"),
            "assessed_value": snapshot.get("assessed_value"),
            "bedrooms": snapshot.get("bedrooms"),
            "bathrooms": snapshot.get("bathrooms"),
            "garage_count": snapshot.get("garage_count"),
        },
    }


def main() -> None:
    args = parse_args()
    properties_input = Path(args.properties_input)
    sales_input = Path(args.sales_input)
    properties_output = Path(args.properties_output)
    sales_output = Path(args.sales_output)
    properties_output.parent.mkdir(parents=True, exist_ok=True)

    sold_property_ids: set[str] = set()
    seen_sale_ids: set[str] = set()
    sales_written = 0
    sales_skipped = 0
    sales_duplicates_skipped = 0

    with sales_output.open("w", encoding="utf-8") as out_handle:
        for document in iter_documents(sales_input):
            slimmed = slim_sale(document)
            if slimmed is None:
                sales_skipped += 1
                continue

            sale_id = slimmed["sale_id"]
            if sale_id in seen_sale_ids:
                sales_duplicates_skipped += 1
                continue

            seen_sale_ids.add(sale_id)
            sold_property_ids.add(slimmed["property_id"])
            out_handle.write(json.dumps(slimmed, separators=(",", ":")) + "\n")
            sales_written += 1

    seen_property_ids: set[str] = set()
    properties_written = 0
    properties_skipped = 0
    properties_duplicates_skipped = 0
    sold_properties_written = 0
    sampled_unsold_written = 0

    with properties_output.open("w", encoding="utf-8") as out_handle:
        for document in iter_documents(properties_input):
            property_id = document.get("property_id")
            if not property_id:
                properties_skipped += 1
                continue

            include_document = property_id in sold_property_ids
            if not include_document:
                include_document = (
                    stable_unit_interval(property_id, "atlas_mvp_unsold")
                    < args.unsold_sample_rate
                )

            if not include_document:
                properties_skipped += 1
                continue

            slimmed = slim_property(document)
            if slimmed is None:
                properties_skipped += 1
                continue

            if property_id in seen_property_ids:
                properties_duplicates_skipped += 1
                continue

            seen_property_ids.add(property_id)

            out_handle.write(json.dumps(slimmed, separators=(",", ":")) + "\n")
            properties_written += 1

            if property_id in sold_property_ids:
                sold_properties_written += 1
            else:
                sampled_unsold_written += 1

    print(
        json.dumps(
            {
                "properties_input": str(properties_input),
                "sales_input": str(sales_input),
                "properties_output": str(properties_output),
                "sales_output": str(sales_output),
                "sales_written": sales_written,
                "sales_skipped": sales_skipped,
                "sales_duplicates_skipped": sales_duplicates_skipped,
                "sold_property_ids": len(sold_property_ids),
                "properties_written": properties_written,
                "sold_properties_written": sold_properties_written,
                "sampled_unsold_written": sampled_unsold_written,
                "properties_skipped": properties_skipped,
                "properties_duplicates_skipped": properties_duplicates_skipped,
                "unsold_sample_rate": args.unsold_sample_rate,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()