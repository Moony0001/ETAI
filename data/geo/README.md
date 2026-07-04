# data/geo/

Drop the real Delhi ward boundary file here as:

```
delhi_wards.geojson
```

The loader (`backend/adapters/geodata.py`) auto-detects common property keys for the
ward id/name (`Ward_No`, `Ward_Name`, `wardno`, `wardcode`, `name`, …).

## Where to get it (MCD's ~250 wards)

- **Datameet / community mirrors** — search "Delhi MCD wards geojson" (GitHub, Datameet
  maps repos often carry municipal ward boundaries).
- **OpenCity.in** and Delhi open-data portals publish ward/administrative boundaries.
- **Election Commission / SDMC/MCD shapefiles** — convert with GDAL:
  ```bash
  ogr2ogr -f GeoJSON -t_srs EPSG:4326 delhi_wards.geojson wards.shp
  ```

## No file? You're still fine.

If this file is absent, the pipeline auto-generates a ~1 km grid over `DELHI_BBOX`
(cells get ids like `grid_r04_c05`) so everything runs. Add the real polygons later —
no code changes needed.
