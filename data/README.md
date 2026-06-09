# Data

The datasets are **gitignored** — the raw GeoJSON is multi-GB and the processed NDJSON is
hundreds of MB — so a fresh clone has no `data/` contents. This file documents the layout and how
to regenerate it.

## Layout

```
data/
  raw/        # source open-data GeoJSON (Calgary assessment parcels) — not committed
  processed/  # normalized NDJSON the import step loads — not committed
    properties.ndjson            # full normalized properties (578,380)
    sales.ndjson                 # full synthetic sales (69,583)
    properties_atlas_mvp.ndjson  # deduped Atlas MVP subset (120,313)
    sales_atlas_mvp.ndjson       # deduped Atlas MVP subset (69,582)
```

## Document shapes

**properties** — `property_id`, `city`, `address.full`, `location` (GeoJSON Point),
`community.{code,name}`, `assessment.assessed_value`, `property.{property_type_normalized,
year_built, land_size_sqm, land_use_designation}`.

**sales** — `sale_id`, `property_id`, `sale_date`, `sale_price`, `community`, `location`, and a
`property_snapshot` (type, year_built, land_size_sqm, assessed_value, bedrooms, bathrooms,
garage_count) captured at sale time.

> Note: the open assessment data has **land** size, not finished floor area (GLA). See the README
> "Tradeoffs" section — size matching uses a land-size proxy + beds/baths/assessed-value.

## Regenerate (the `pipeline/` ETL, in order)

```bash
# 1. raw GeoJSON -> normalized properties.ndjson
.venv/Scripts/python.exe pipeline/normalize_calgary_current.py
# 2. synthesize plausible sales -> sales.ndjson
.venv/Scripts/python.exe pipeline/generate_synthetic_sales.py
# 3. build the deduped Atlas-free-tier MVP subset
.venv/Scripts/python.exe pipeline/build_atlas_mvp_exports.py
# 4. load into MongoDB Atlas (reads MONGODB_URI; creates indexes)
.venv/Scripts/python.exe pipeline/import_atlas_data.py \
  --properties-file data/processed/properties_atlas_mvp.ndjson \
  --sales-file data/processed/sales_atlas_mvp.ndjson --insert-only
```

Atlas database `kv_comp_analysis`: `properties` (120,313), `sales` (69,582), with a `sales`
`2dsphere` index (geo retrieval) and a compound hot-query index — both also auto-ensured by the
API on startup.
