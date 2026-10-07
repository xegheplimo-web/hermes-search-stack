"""Gateway configuration (r8-interfaces.md section 5, decisions D7/D8).

``GatewayConfig`` is a plain dataclass; ``from_env()`` reads every
``HERMES_GATEWAY_*`` variable. Synthesis API-key resolution (D7) is
``HERMES_GATEWAY_SYNTH_API_KEY`` -> ``OPENCODE_GO_API_KEY`` in the Hermes
``.env`` — the key is read from disk only at call time and is never printed,
logged, or committed. All paths are Windows-safe: relative ``cache_db`` /
``store_db`` values resolve against ``repo_root``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENV_PREFIX = "HERMES_GATEWAY_"

DEFAULT_SYNTH_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_SYNTH_MODEL = "deepseek-flash"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_RATE_LIMIT_RPS = 20.0
DEFAULT_SYNTH_TIMEOUT = 120.0
DEFAULT_ADMISSION_MAX_INFLIGHT = 4
DEFAULT_ADMISSION_QUEUE_CAP = 16
DEFAULT_REQUEST_DEADLINE_S = 180.0

# Repo root = the directory containing the ``gateway`` package.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_hermes_home() -> str:
    """Hermes home without importing hermes_constants (gateway stays Hermes-free)."""
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return env
    if sys.platform == "win32":
        return str(Path.home() / "AppData" / "Local" / "hermes")
    return str(Path.home() / ".hermes")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw is not None and raw.strip() else default


def _env_opt(name: str) -> str | None:
    raw = os.environ.get(name)
    return raw.strip() if raw is not None and raw.strip() else None


def _env_headers(name: str) -> dict[str, str]:
    """Parse a JSON object of extra HTTP headers from *name*; {} when unset/invalid."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _read_dotenv_value(dotenv_path: Path, key: str) -> str | None:
    """Read ``KEY=value`` from a dotenv file; ``None`` when absent/unreadable.

    Minimal parser: ignores comments/blank lines and ``export `` prefixes,
    strips matching single/double quotes. Never prints the value.
    """
    try:
        lines = dotenv_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value or None
    return None


@dataclass(slots=True)
class GatewayConfig:
    """Runtime configuration for the gateway (all envs ``HERMES_GATEWAY_*``)."""

    backend: str = "auto"  # auto | hermes | standalone | stub
    synth_base_url: str = DEFAULT_SYNTH_BASE_URL
    synth_model: str = DEFAULT_SYNTH_MODEL
    synth_api_key: str | None = None  # explicit override only; resolved lazily
    synth_headers: dict[str, str] = field(default_factory=dict)  # extra HTTP headers
    synth_timeout: float = DEFAULT_SYNTH_TIMEOUT
    cache_db: str = "data/answers.db"
    store_db: str = "data/searchstore.db"
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    api_key: str | None = None  # bearer auth; unset -> loopback-only (D8)
    rate_limit_rps: float = DEFAULT_RATE_LIMIT_RPS
    admission_max_inflight: int = DEFAULT_ADMISSION_MAX_INFLIGHT
    admission_queue_cap: int = DEFAULT_ADMISSION_QUEUE_CAP
    request_deadline_s: float = DEFAULT_REQUEST_DEADLINE_S
    metrics_enabled: bool = True
    fast_max_results: int = 10
    fast_extract: int = 4
    deep_search_queries: int = 3
    deep_extract: int = 8
    hermes_python: str | None = None
    hermes_home: str | None = None
    repo_root: str = ""

    @classmethod
    def from_env(cls) -> GatewayConfig:
        """Build a config from ``HERMES_GATEWAY_*`` environment variables."""
        return cls(
            backend=_env_str(f"{ENV_PREFIX}BACKEND", "auto"),
            synth_base_url=_env_str(f"{ENV_PREFIX}SYNTH_BASE_URL", DEFAULT_SYNTH_BASE_URL),
            synth_model=_env_str(f"{ENV_PREFIX}SYNTH_MODEL", DEFAULT_SYNTH_MODEL),
            synth_api_key=_env_opt(f"{ENV_PREFIX}SYNTH_API_KEY"),
            synth_headers=_env_headers(f"{ENV_PREFIX}SYNTH_HEADERS"),
            synth_timeout=_env_float(f"{ENV_PREFIX}SYNTH_TIMEOUT", DEFAULT_SYNTH_TIMEOUT),
            cache_db=_env_str(f"{ENV_PREFIX}CACHE_DB", "data/answers.db"),
            store_db=_env_str(f"{ENV_PREFIX}STORE_DB", "data/searchstore.db"),
            host=_env_str(f"{ENV_PREFIX}HOST", DEFAULT_HOST),
            port=_env_int(f"{ENV_PREFIX}PORT", DEFAULT_PORT),
            api_key=_env_opt(f"{ENV_PREFIX}API_KEY"),
            rate_limit_rps=_env_float(f"{ENV_PREFIX}RATE_LIMIT_RPS", DEFAULT_RATE_LIMIT_RPS),
            admission_max_inflight=_env_int(f"{ENV_PREFIX}ADMISSION_MAX_INFLIGHT", DEFAULT_ADMISSION_MAX_INFLIGHT),
            admission_queue_cap=_env_int(f"{ENV_PREFIX}ADMISSION_QUEUE_CAP", DEFAULT_ADMISSION_QUEUE_CAP),
            request_deadline_s=_env_float(f"{ENV_PREFIX}REQUEST_DEADLINE_S", DEFAULT_REQUEST_DEADLINE_S),
            metrics_enabled=_env_bool(f"{ENV_PREFIX}METRICS", True),
            fast_max_results=_env_int(f"{ENV_PREFIX}FAST_MAX_RESULTS", 10),
            fast_extract=_env_int(f"{ENV_PREFIX}FAST_EXTRACT", 4),
            deep_search_queries=_env_int(f"{ENV_PREFIX}DEEP_SEARCH_QUERIES", 3),
            deep_extract=_env_int(f"{ENV_PREFIX}DEEP_EXTRACT", 8),
            hermes_python=_env_opt(f"{ENV_PREFIX}HERMES_PYTHON"),
            hermes_home=_env_opt(f"{ENV_PREFIX}HERMES_HOME"),
            repo_root=_env_str(f"{ENV_PREFIX}REPO_ROOT", str(_REPO_ROOT)),
        )

    # ---------- derived helpers ----------

    @property
    def root(self) -> Path:
        """Repo root; defaults to the directory containing ``gateway/``."""
        return Path(self.repo_root) if self.repo_root else _REPO_ROOT

    @property
    def home(self) -> str:
        """Hermes home (``HERMES_GATEWAY_HERMES_HOME`` -> ``HERMES_HOME`` -> platform default)."""
        return self.hermes_home or _default_hermes_home()

    def abspath(self, value: str | os.PathLike) -> Path:
        """Resolve *value* against ``repo_root`` when it is a relative path."""
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.root / path

    @property
    def cache_db_path(self) -> Path:
        return self.abspath(self.cache_db)

    @property
    def store_db_path(self) -> Path:
        return self.abspath(self.store_db)

    @property
    def tmp_dir(self) -> Path:
        """Scratch dir for fact_check draft/ledger files (``data/tmp/``)."""
        return self.root / "data" / "tmp"

    def resolve_synth_api_key(self) -> str | None:
        """D7 key resolution, evaluated at call time — never hardcoded/printed.

        Order: explicit ``synth_api_key`` -> ``OPENCODE_GO_API_KEY`` in the
        process environment -> ``OPENCODE_GO_API_KEY`` in ``<hermes_home>/.env``.
        """
        if self.synth_api_key:
            return self.synth_api_key
        env_key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
        if env_key:
            return env_key
        return _read_dotenv_value(Path(self.home) / ".env", "OPENCODE_GO_API_KEY")
