"""vn_geo — Vietnam geo/business data kit for the search stack (round 4).

Modules (frozen contract: analysis/r4-interfaces.md):

- ``admin_units``    — provinces.open-api.vn v1/v2 fetch + SearchStore ingest (địa danh).
- ``overpass_poi``   — OpenStreetMap Overpass POI fetch + SearchStore ingest.
- ``places``         — place records (Google Maps scans, OSM, Foody…) + refresh/diff.

Everything persists into a SearchStore database (see ``searchstore`` package);
no schema changes, stdlib-only (plus ``searchstore``).
"""

from __future__ import annotations


class VnGeoError(Exception):
    """All vn_geo errors. Message must be actionable."""


__version__ = "0.1.0"

__all__ = ["VnGeoError", "__version__"]
