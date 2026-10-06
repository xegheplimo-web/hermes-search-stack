"""Thin adapter over ``searchstore.answer_cache.AnswerCache`` (r8 §5, D5).

``get``/``put`` wrappers that stamp ``source="gateway"`` on hit recording and
hide the repo import behind a lazy boundary so the gateway package never needs
the repo modules at import time.
"""

from __future__ import annotations

import os


class GatewayCache:
    """Verified-answer cache facade; owns an ``AnswerCache`` on ``db_path``."""

    def __init__(self, db_path: str | os.PathLike):
        from searchstore.answer_cache import AnswerCache

        self._inner = AnswerCache(db_path)

    def get(self, query: str, *, scope: str = "") -> dict | None:
        """``{"pack", "fresh", "age_days"}`` on hit, ``None`` on miss."""
        return self._inner.get(query, scope=scope, source="gateway")

    def put(self, pack: dict) -> dict:
        """Store a verified ``research_pack.v1`` pack; raises on gate failure."""
        return self._inner.put(pack)

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> GatewayCache:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
