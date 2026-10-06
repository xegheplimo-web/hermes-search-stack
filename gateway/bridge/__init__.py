"""Hermes sidecar bridge worker package (r8-interfaces.md section 4).

``worker.py`` is the ONLY gateway file allowed to import Hermes internals; it
runs under the Hermes runtime venv, spawned by
``gateway.backends.hermes_bridge.HermesBridge``.
"""

from __future__ import annotations
