# Ward pilot data source (R15-E)

- Repo: https://github.com/thanglequoc/vietnamese-provinces-database
- Pinned commit SHA: `8b78ba5118715e1fa81769286724db79346abf52` (master @ 2026-09-22)
- GIS add-on: ward-level per-unit GeoJSON under `json/geojson/`
- Raw URL pattern:
  `https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/`
  `<SHA>/json/geojson/<province_dir>/wards/<ward_file>.geojson`
- Listing URL pattern (GitHub contents API):
  `https://api.github.com/repos/thanglequoc/vietnamese-provinces-database/`
  `contents/json/geojson/<province_dir>/wards?ref=<SHA>`

## Pilot scope (2 provinces only)

| Upstream dir     | Province  | Ward files | Bytes      |
|------------------|-----------|-----------:|-----------:|
| `01_ha_noi`      | Hà Nội    |        126 | ~11.6 MB   |
| `31_hai_phong`   | Hải Phòng |        114 | ~7.8 MB    |

Total: 240 files, ~19.4 MB (limit: ~30 MB).

## File shape

Each `wards/*.geojson` is a FeatureCollection with exactly one Feature:
feature `id` = ward code; properties `code`/`name`/`fullName`/`codeName`/
`areaKm2`/`gisServerId`; geometry Polygon or MultiPolygon in `[lon, lat]`
order. Layout here mirrors upstream: `<province_dir>/wards/*.geojson`.

## Refresh

Manual only, via `vn_geo.wards.fetch_wards(["hai-phong", "ha-noi"])`
(live network; pytest never touches the network).
