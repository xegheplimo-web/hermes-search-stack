"""SearchStore v1 — single-file storage layer for the search stack."""

from .store import SearchStore, SearchStoreError, content_sha256, url_key

__version__ = "0.1.0"

__all__ = ["SearchStore", "SearchStoreError", "__version__", "content_sha256", "url_key"]
