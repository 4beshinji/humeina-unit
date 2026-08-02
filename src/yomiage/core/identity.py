"""Stable work/plan identities and per-execution identifiers.

The identity helpers deliberately live in the provider-independent core package.  They do not
touch the filesystem or a job database; callers can use the values from CLI, API, and batch
entrypoints alike.  ``work_id`` and ``plan_id`` are deterministic while ``run_id`` and
``job_id`` are generated for each request.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import PurePath
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

_DEFAULT_DIGEST_LENGTH = 20

# These values describe execution/output concerns rather than analysis.  They are removed from
# the plan fingerprint so changing a provider or output format can reuse an existing plan.
_RUNTIME_CONFIG_KEYS = frozenset(
    {
        "provider",
        "provider_name",
        "provider_config",
        "mode",
        "output",
        "output_dir",
        "output_format",
        "format",
        "cleanup",
        "video",
        "style",
        "execution",
        "retry",
        "retries",
        "timeout",
        "concurrency",
        "cache",
        "output_root",
        "artifact",
        "artifacts",
        "storage",
        "log_level",
        "run",
        "run_id",
        "job",
        "job_id",
        "tts",
        "voisona",
        "voicevox",
        "voicepeak",
        "voice_profile",
        "voice_id",
        "speaker",
        "narrator",
        "engine",
        # Connection/authentication and executable details do not affect NLP output.  They may
        # appear nested below a provider section, so strip them recursively as well.
        "username",
        "password",
        "token",
        "api_key",
        "api_token",
        "secret",
        "url",
        "base_url",
        "endpoint",
        "host",
        "port",
        "path",
        "binary",
    }
)


def _jsonable(value: object) -> object:
    """Convert common configuration values into canonical JSON values.

    ``Path`` values occur in settings loaded from YAML.  Converting them explicitly keeps the
    fingerprint deterministic without accepting arbitrary objects whose string representation may
    contain memory addresses.
    """

    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            # YAML/settings mappings normally have string keys.  Primitive keys are accepted for
            # compatibility, but collisions such as ``1`` and ``"1"`` must not be hidden.
            if isinstance(key, str):
                normalized_key = key
            elif isinstance(key, (int, float, bool)):
                normalized_key = str(key)
            else:
                raise TypeError(
                    f"mapping key of type {type(key).__name__} is not JSON serializable"
                )
            if normalized_key in result:
                raise ValueError(f"duplicate canonical mapping key: {normalized_key!r}")
            result[normalized_key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_jsonable(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        )
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float):
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError("non-finite float is not valid canonical JSON")
            # Canonical JSON should not distinguish values that compare equal as zero.
            if value == 0.0:
                return 0.0
        return value
    # Dataclasses and pydantic settings can provide a mapping through ``model_dump``/``asdict``;
    # avoid importing either framework in the core package.
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    as_dict = getattr(value, "to_dict", None)
    if callable(as_dict):
        return _jsonable(as_dict())
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def canonical_json(value: object) -> str:
    """Return a deterministic, compact JSON representation."""

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: object, *, length: int | None = _DEFAULT_DIGEST_LENGTH) -> str:
    """Return a SHA-256 fingerprint of canonical JSON data.

    A bounded length is enough for filesystem IDs while retaining substantially more entropy than
    the historic 12-character MD5 directory names.  Callers may request the full digest with
    ``length=None`` for persisted source/config fingerprints.
    """

    invalid_length = (
        length is not None
        and (isinstance(length, bool) or not isinstance(length, int) or length <= 0)
    )
    if invalid_length:
        raise ValueError("length must be a positive integer or None")
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return digest if length is None else digest[:length]


def normalize_url(url: str) -> str:
    """Normalize a URL for source identity purposes.

    Fragments are omitted because they identify a browser location, not source content.  Host and
    scheme are case-insensitive, default ports are removed, and query parameter ordering is made
    stable.  Credentials are intentionally dropped so secrets never become directory identities.
    Non-URL source keys are accepted and normalized as opaque strings.
    """

    if not isinstance(url, str):
        raise TypeError("url must be a string")
    value = url.strip()
    if not value:
        raise ValueError("url must not be empty")
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return re.sub(r"\s+", " ", value)

    scheme = parts.scheme.lower()
    try:
        hostname = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise ValueError("url contains an invalid port") from exc
    if not hostname:
        raise ValueError("url must contain a host")
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if default_port or port is None else f"{host}:{port}"

    # Keep the URL path semantics while normalizing percent escapes and a trailing slash.  The
    # root slash is retained because ``https://example.test`` and ``/`` are equivalent there.
    path = unquote(parts.path or "/")
    if path != "/":
        path = path.rstrip("/") or "/"
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")

    query_items = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def source_identity(source: str, *, source_kind: str | None = None) -> str:
    """Build a canonical source identity used as the work/plan input."""

    kind = (source_kind or "url").strip().lower() or "url"
    return f"{kind}:{normalize_url(source)}"


def source_fingerprint(source: str, *, source_kind: str | None = None) -> str:
    """Return a stable full source fingerprint."""

    return fingerprint(source_identity(source, source_kind=source_kind), length=None)


def legacy_url_work_id(url: str) -> str:
    """Historic work directory ID (MD5 of the unmodified URL).

    This function is intentionally retained solely for read compatibility with manifests written
    before RUN-001.
    """

    if not isinstance(url, str):
        raise TypeError("url must be a string")
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - compatibility format


def work_id_for_source(source: str, *, source_kind: str | None = None) -> str:
    """Generate the stable ID for a normalized source."""

    return f"work_{fingerprint(source_identity(source, source_kind=source_kind))}"


def analysis_config(value: object) -> object:
    """Return the analysis-relevant subset of a settings mapping.

    Runtime settings are removed recursively.  This keeps provider/output changes from creating a
    new plan while preserving analysis knobs such as window size, splitter limits, and LLM model.
    An explicit ``analysis``/``analysis_config`` section is preferred when present.
    """

    normalized = _jsonable(value)
    if not isinstance(normalized, Mapping):
        return normalized
    explicit = normalized.get("analysis_config", normalized.get("analysis"))
    if isinstance(explicit, Mapping):
        return _strip_runtime(explicit)
    return _strip_runtime(normalized)


def _strip_runtime(value: object, *, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, item in value.items():
            name = str(raw_key)
            if name.lower() in _RUNTIME_CONFIG_KEYS:
                continue
            result[name] = _strip_runtime(item, key=name)
        return result
    if isinstance(value, (list, tuple)):
        return [_strip_runtime(item) for item in value]
    return _jsonable(value)


def config_fingerprint(value: object) -> str:
    """Return a stable fingerprint for analysis settings."""

    return fingerprint(analysis_config(value), length=None)


def plan_id_for_source(
    source: str,
    settings: object | None = None,
    *,
    source_kind: str | None = None,
) -> str:
    """Generate a stable plan ID from source and analysis settings."""

    identity = source_identity(source, source_kind=source_kind)
    payload = {
        "source_fingerprint": fingerprint(identity, length=None),
        "config_fingerprint": config_fingerprint(settings or {}),
    }
    return f"plan_{fingerprint(payload)}"


def new_run_id() -> str:
    """Generate a unique identifier for one provider/output execution."""

    return uuid.uuid4().hex


def new_job_id() -> str:
    """Generate a unique identifier for one API/CLI job request."""

    return uuid.uuid4().hex


# Descriptive aliases make the utility convenient for callers that prefer ``make_*`` naming.
normalize_source = source_identity
make_work_id = work_id_for_source
make_plan_id = plan_id_for_source
generate_work_id = work_id_for_source
generate_plan_id = plan_id_for_source
generate_run_id = new_run_id
generate_job_id = new_job_id
work_id_from_url = work_id_for_source
plan_id_from_config = plan_id_for_source
run_id = new_run_id
job_id = new_job_id


@dataclass(frozen=True, slots=True)
class IdentitySet:
    """The four IDs and plan fingerprints for a batch request."""

    work_id: str
    plan_id: str
    run_id: str
    job_id: str
    source_fingerprint: str
    config_fingerprint: str


def build_identity(
    source: str,
    settings: object | None = None,
    *,
    source_kind: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
) -> IdentitySet:
    """Build deterministic work/plan IDs and fresh (or supplied) run/job IDs."""

    identity = source_identity(source, source_kind=source_kind)
    config_fp = config_fingerprint(settings or {})
    source_fp = fingerprint(identity, length=None)
    return IdentitySet(
        work_id=work_id_for_source(source, source_kind=source_kind),
        plan_id=plan_id_for_source(source, settings, source_kind=source_kind),
        run_id=run_id or new_run_id(),
        job_id=job_id or new_job_id(),
        source_fingerprint=source_fp,
        config_fingerprint=config_fp,
    )


__all__ = [
    "IdentitySet",
    "analysis_config",
    "build_identity",
    "canonical_json",
    "config_fingerprint",
    "fingerprint",
    "generate_job_id",
    "generate_plan_id",
    "generate_run_id",
    "generate_work_id",
    "job_id",
    "legacy_url_work_id",
    "make_plan_id",
    "make_work_id",
    "new_job_id",
    "new_run_id",
    "normalize_source",
    "normalize_url",
    "plan_id_for_source",
    "plan_id_from_config",
    "run_id",
    "source_fingerprint",
    "source_identity",
    "work_id_for_source",
    "work_id_from_url",
]
