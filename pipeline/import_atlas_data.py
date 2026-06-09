from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import normalized property and sales NDJSON into MongoDB Atlas."
    )
    parser.add_argument(
        "--uri",
        default=os.environ.get("MONGODB_URI"),
        help="MongoDB Atlas cluster URI. Defaults to MONGODB_URI.",
    )
    parser.add_argument(
        "--db",
        default="kv_comp_analysis",
        help="Target MongoDB database name.",
    )
    parser.add_argument(
        "--properties-file",
        default="data/processed/properties.ndjson",
        help="Path to the properties NDJSON file.",
    )
    parser.add_argument(
        "--sales-file",
        default="data/processed/sales.ndjson",
        help="Path to the sales NDJSON file.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Bulk upsert batch size.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and report counts without connecting to MongoDB.",
    )
    parser.add_argument(
        "--drop-first",
        action="store_true",
        help="Drop the target properties and sales collections before importing.",
    )
    parser.add_argument(
        "--skip-sales-geo-index",
        action="store_true",
        help="Skip the sales location 2dsphere index to reduce Atlas free-tier storage use.",
    )
    parser.add_argument(
        "--insert-only",
        action="store_true",
        help="Use insert-many batches instead of upserts for fresh empty collections.",
    )
    return parser.parse_args()


def iter_documents(file_path: Path):
    with file_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                yield json.loads(line)


def count_documents(file_path: Path) -> int:
    count = 0
    for _ in iter_documents(file_path):
        count += 1
    return count


def bulk_upsert(collection, file_path: Path, key_field: str, batch_size: int) -> dict:
    from pymongo import ReplaceOne

    matched = 0
    modified = 0
    upserted = 0
    batch = []

    def flush() -> None:
        nonlocal matched, modified, upserted, batch
        if not batch:
            return

        result = collection.bulk_write(batch, ordered=False)
        matched += result.matched_count
        modified += result.modified_count
        upserted += len(result.upserted_ids)
        batch = []

    for document in iter_documents(file_path):
        key_value = document.get(key_field)
        if not key_value:
            continue
        batch.append(ReplaceOne({key_field: key_value}, document, upsert=True))
        if len(batch) >= batch_size:
            flush()

    flush()
    return {
        "matched": matched,
        "modified": modified,
        "upserted": upserted,
    }


def bulk_insert(collection, file_path: Path, key_field: str, batch_size: int) -> dict:
    from pymongo.errors import BulkWriteError

    inserted = 0
    batch = []

    def flush() -> None:
        nonlocal inserted, batch
        if not batch:
            return

        try:
            result = collection.insert_many(batch, ordered=False)
            inserted += len(result.inserted_ids)
        except BulkWriteError as exc:
            details = exc.details or {}
            write_errors = details.get("writeErrors", [])
            if any(error.get("code") != 11000 for error in write_errors):
                raise
            inserted += details.get("nInserted", 0)
        batch = []

    for document in iter_documents(file_path):
        key_value = document.get(key_field)
        if not key_value:
            continue

        doc_to_insert = dict(document)
        doc_to_insert["_id"] = key_value
        batch.append(doc_to_insert)
        if len(batch) >= batch_size:
            flush()

    flush()
    return {
        "matched": 0,
        "modified": 0,
        "upserted": inserted,
    }


def create_indexes(database, skip_sales_geo_index: bool) -> None:
    database.properties.create_index("property_id", unique=True)
    database.properties.create_index([("location", "2dsphere")])
    database.sales.create_index("sale_id", unique=True)
    database.sales.create_index("property_id")
    database.sales.create_index([("sale_date", -1)])
    if not skip_sales_geo_index:
        database.sales.create_index([("location", "2dsphere")])


def main() -> None:
    args = parse_args()
    properties_path = Path(args.properties_file)
    sales_path = Path(args.sales_file)

    if not properties_path.exists():
        raise SystemExit(f"Properties file not found: {properties_path}")
    if not sales_path.exists():
        raise SystemExit(f"Sales file not found: {sales_path}")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "db": args.db,
                    "properties_file": str(properties_path),
                    "properties_count": count_documents(properties_path),
                    "sales_file": str(sales_path),
                    "sales_count": count_documents(sales_path),
                    "batch_size": args.batch_size,
                    "dry_run": True,
                },
                indent=2,
            )
        )
        return

    if not args.uri:
        raise SystemExit(
            "MongoDB Atlas cluster URI is required. Set MONGODB_URI or pass --uri."
        )

    if "query.mongodb.net" in args.uri or "atlas-sql" in args.uri:
        raise SystemExit(
            "The provided URI is an Atlas SQL/Federated endpoint. Use a normal MongoDB cluster URI instead."
        )

    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise SystemExit(
            "pymongo is required. Install it with: pip install pymongo[srv]"
        ) from exc

    client = MongoClient(args.uri, appname="kv-comp-analysis-import")
    database = client[args.db]

    if args.drop_first:
        database.properties.drop()
        database.sales.drop()

    if args.insert_only:
        properties_result = bulk_insert(
            database.properties,
            properties_path,
            key_field="property_id",
            batch_size=args.batch_size,
        )
        sales_result = bulk_insert(
            database.sales,
            sales_path,
            key_field="sale_id",
            batch_size=args.batch_size,
        )
    else:
        properties_result = bulk_upsert(
            database.properties,
            properties_path,
            key_field="property_id",
            batch_size=args.batch_size,
        )
        sales_result = bulk_upsert(
            database.sales,
            sales_path,
            key_field="sale_id",
            batch_size=args.batch_size,
        )
    create_indexes(database, skip_sales_geo_index=args.skip_sales_geo_index)

    print(
        json.dumps(
            {
                "db": args.db,
                "properties": properties_result,
                "sales": sales_result,
                "indexes_created": True,
                "drop_first": args.drop_first,
                "insert_only": args.insert_only,
                "skip_sales_geo_index": args.skip_sales_geo_index,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()