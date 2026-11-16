"""Structured updater failure classification shared by AGY update children.

[Role]
Classifies typed updater failures (DNS/resolver, busy/deferred locks, local
prerequisites, credentials and transport) and carries that structure across the
`component_update.py` -> `setup.py` -> `uv` subprocess boundary as one bounded
JSON marker line on stderr.

[Invariants]
- Classification uses typed facts first (`.category`, `.code`, `errno`, exception
  type/MRO, bounded output phrases) and returns a fixed vocabulary; raw child
  output is never recorded or echoed into diagnostics.
- `internal` is the only reportable class: transport, resolver, credentials,
  permission, cancellation and prerequisite failures are never product bugs.
- Marker parsing is bounded and validates every field before use.

[Timeouts & Locks]
- Pure in-memory helpers; no I/O, lock, network or subprocess call. The emitter
  writes at most one bounded stderr line.

[Side-Effect Policy]
- `emit_failure_marker` writes one stderr line (best effort); parsers and
  classifiers are strictly read-only.
"""

from __future__ import annotations

import errno
import http.client
import json
import re
import socket
import ssl
import subprocess
import sys
import urllib.error

SCHEMA = "mindie-agy-update-failure/1"
MARKER_PREFIX = "mindie-update-failure:"
MAX_MARKER_CHARS = 2048
MAX_OUTPUT_CHARS = 65536
MAX_CHAIN = 32

CONNECTIVITY = "connectivity"
CREDENTIALS = "credentials"
PERMISSION = "permission"
PREREQUISITE = "prerequisite"
CANCELLED = "cancelled"
INTERNAL = "internal"

CONNECTIVITY_CODES = frozenset({
    "release_transport_unavailable", "release_http_error",
})
CONNECTIVITY_CATEGORIES = frozenset({
    "temporary_network", "rate_limited", "deadline", "resolver",
})
CREDENTIAL_CATEGORIES = frozenset({"authentication", "certificate"})
PERMISSION_CATEGORIES = frozenset({"permission", "hook_trust"})
PREREQUISITE_CATEGORIES = frozenset({"busy", "deferred", "prerequisite"})
CANCELLED_CATEGORIES = frozenset({"cancelled"})
KNOWN_CATEGORIES = frozenset({
    CONNECTIVITY, CREDENTIALS, PERMISSION, PREREQUISITE, CANCELLED, INTERNAL,
    *CONNECTIVITY_CATEGORIES, *CREDENTIAL_CATEGORIES, *PERMISSION_CATEGORIES,
    *PREREQUISITE_CATEGORIES, *CANCELLED_CATEGORIES,
})
STAGES = frozenset({
    "prepare", "discover", "resolve", "build", "activate", "inventory",
    "bootstrap", "uv-sync", "reexec", "update",
})
REASON_CODES = {
    CONNECTIVITY: "update_transport_unavailable",
    CREDENTIALS: "update_credentials_required",
    PERMISSION: "update_permission_required",
    PREREQUISITE: "update_prerequisite_unavailable",
    CANCELLED: "update_cancelled",
    INTERNAL: "update_internal_failure",
}
_LOCK_TIMEOUT_NAMES = frozenset({
    "LockTimeout", "LockTimeoutError", "FileLockTimeoutError",
    "LockBusy", "LockBusyError", "BusyLockError", "AdmissionUnavailable",
    "UpdateLockBusy",
})
_PREREQUISITE_ERRNOS = frozenset({
    errno.ENOSPC, errno.EROFS, errno.EMFILE, errno.ENFILE, errno.EAGAIN,
})
_DNS_ERRORS = tuple(
    dict.fromkeys(
        cls for cls in (getattr(socket, "gaierror", OSError),
                        getattr(socket, "herror", OSError))
        if isinstance(cls, type) and issubclass(cls, OSError) and cls is not OSError
    )
)
_HTTP_ERRORS = (
    http.client.IncompleteRead, http.client.RemoteDisconnected,
    http.client.BadStatusLine,
)
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_HTTP_STATUS_RE = re.compile(r"(?:http(?:/\d(?:\.\d)?)?\s+|status\s*[:=]\s*|error\s*[:=]\s*)(\d{3})\b", re.I)
_CERT_PHRASES = (
    "certificate verify failed", "sslcertverificationerror",
    "certificate has expired", "self-signed certificate",
    "self signed certificate", "unable to get local issuer certificate",
    "ssl: certificate", "ssl certificate problem", "unknown ca", "curl: (60)",
)
_RESOLVER_PHRASES = (
    "no solution found", "version solving failed", "resolutionimpossible",
    "conflicting dependencies", "package versions have conflicting",
    "the conflict is caused by", "resolver conflict",
    "could not resolve host", "temporary failure in name resolution",
    "name or service not known", "nodename nor servname",
    "temporary failure resolving", "getaddrinfo failed", "dns error",
)
_AUTH_PHRASES = (
    "authentication failed", "could not read username",
    "terminal prompts disabled", "permission denied (publickey)",
    "invalid credentials", "http basic: access denied",
    "support for password authentication was removed",
    "authentication required", "invalid username or password",
)
_PERMISSION_PHRASES = (
    "permission denied", "access is denied", "operation not permitted",
    "read-only file system", "no space left on device", "disk full",
    "not enough space",
)
_NETWORK_PHRASES = (
    "network is unreachable", "connection timed out", "connection reset by peer",
    "connection refused", "connection aborted", "failed to connect",
    "operation timed out", "read operation timed out", "timed out",
    "max retries exceeded", "proxy connect aborted", "recv failure",
    "send failure", "unexpected eof", "connection broken", "curl: (6)",
    "curl: (7)", "curl: (28)", "curl: (56)",
)


class UpdateFailure(RuntimeError):
    """A staged updater failure with a non-secret transport/prerequisite class."""

    def __init__(self, message, *, category, stage="update", reason_code=None):
        super().__init__(message)
        mapped = _mapped_category(category) if isinstance(category, str) else None
        self.category = mapped if mapped is not None else INTERNAL
        chosen = stage if stage in STAGES else "update"
        self.stage = chosen
        self.substage = chosen
        self.reason_code = reason_code or REASON_CODES[self.category]


def attach_stage(exception, stage):
    """Record the producing stage on an existing exception without changing its type."""
    if stage in STAGES and not getattr(exception, "substage", None):
        try:
            exception.substage = stage
        except (AttributeError, TypeError):
            pass
    return exception


def stage_of(exception, default="update"):
    """The structured stage an exception carries, or ``default``."""
    for name in ("substage", "stage"):
        value = getattr(exception, name, None)
        if isinstance(value, str) and value in STAGES:
            return value
    return default


def _mapped_category(value):
    if value in {CONNECTIVITY, CREDENTIALS, PERMISSION, PREREQUISITE, CANCELLED}:
        return value
    if value in CONNECTIVITY_CATEGORIES:
        return CONNECTIVITY
    if value in CREDENTIAL_CATEGORIES:
        return CREDENTIALS
    if value in PERMISSION_CATEGORIES:
        return PERMISSION
    if value in PREREQUISITE_CATEGORIES:
        return PREREQUISITE
    if value in CANCELLED_CATEGORIES:
        return CANCELLED
    return None


def _mapped_mindie_category(value):
    """Typed `mindie_knowledge.loop.process` categories are structured facts too."""
    if value in {"deadline", "temporary_network", "rate_limited", "resolver"}:
        return CONNECTIVITY
    if value in {"configuration", "busy", "deferred", "prerequisite"}:
        return PREREQUISITE
    return None


def classify_exception(exception) -> tuple[str, bool, str | None]:
    """Return ``(category, reportable, reason_code)`` from typed facts only."""
    seen: set[int] = set()
    pending = [exception]
    saw_import_error = False
    while pending and len(seen) < MAX_CHAIN:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "code", None) in CONNECTIVITY_CODES:
            return CONNECTIVITY, False, REASON_CODES[CONNECTIVITY]
        category = getattr(current, "category", None)
        if isinstance(category, str):
            mapped = _mapped_category(category)
            if mapped is not None:
                return mapped, False, REASON_CODES[mapped]
        mindie_category = getattr(current, "mindie_category", None)
        if isinstance(mindie_category, str):
            mapped = _mapped_mindie_category(mindie_category)
            if mapped is not None:
                return mapped, False, REASON_CODES[mapped]
        names = {cls.__name__ for cls in type(current).__mro__}
        if names & _LOCK_TIMEOUT_NAMES:
            return PREREQUISITE, False, REASON_CODES[PREREQUISITE]
        if isinstance(current, ssl.SSLCertVerificationError):
            return CREDENTIALS, False, REASON_CODES[CREDENTIALS]
        if isinstance(current, ssl.SSLError):
            return CONNECTIVITY, False, REASON_CODES[CONNECTIVITY]
        if isinstance(current, (BlockingIOError, TimeoutError,
                                subprocess.TimeoutExpired, urllib.error.URLError,
                                ConnectionError, *_DNS_ERRORS, *_HTTP_ERRORS)):
            return CONNECTIVITY, False, REASON_CODES[CONNECTIVITY]
        if isinstance(current, (PermissionError, FileNotFoundError)):
            return PREREQUISITE, False, REASON_CODES[PREREQUISITE]
        if isinstance(current, OSError) and current.errno in _PREREQUISITE_ERRNOS:
            return PREREQUISITE, False, REASON_CODES[PREREQUISITE]
        if isinstance(current, ImportError):
            saw_import_error = True
        cause = getattr(current, "__cause__", None)
        if cause is not None:
            pending.append(cause)
        if not getattr(current, "__suppress_context__", False):
            context = getattr(current, "__context__", None)
            if context is not None:
                pending.append(context)
    if pending:
        return PREREQUISITE, False, REASON_CODES[PREREQUISITE]
    if saw_import_error:
        return INTERNAL, True, "runtime_dependency_missing"
    return INTERNAL, True, REASON_CODES[INTERNAL]


def classify_text(text) -> str | None:
    """Classify bounded child output phrases without retaining the text."""
    if not isinstance(text, str) or not text.strip():
        return None
    sample = text[:MAX_OUTPUT_CHARS]
    lower = sample.lower()
    if any(phrase in lower for phrase in _CERT_PHRASES):
        return CREDENTIALS
    if any(phrase in lower for phrase in _RESOLVER_PHRASES):
        return CONNECTIVITY
    if any(phrase in lower for phrase in _AUTH_PHRASES):
        return CREDENTIALS
    if any(phrase in lower for phrase in _PERMISSION_PHRASES):
        return PREREQUISITE
    statuses = {int(item) for item in _HTTP_STATUS_RE.findall(sample)}
    if statuses & {401}:
        return CREDENTIALS
    if statuses & {403}:
        return PERMISSION
    if statuses & {500, 502, 503, 504, 429}:
        return CONNECTIVITY
    if any(phrase in lower for phrase in _NETWORK_PHRASES):
        return CONNECTIVITY
    return None


def classify_output(stdout, stderr) -> str | None:
    """Prefer stderr, then stdout; return a vocabulary category or ``None``."""
    return classify_text(stderr) or classify_text(stdout)


def marker_line(exception, *, stage=None) -> str:
    """One bounded JSON marker line describing a classified child failure."""
    category, _reportable, reason_code = classify_exception(exception)
    chosen = stage if stage in STAGES else stage_of(exception)
    if chosen not in STAGES:
        chosen = "update"
    payload = {"schema": SCHEMA, "category": category, "stage": chosen,
               "reason_code": reason_code or REASON_CODES[category]}
    return MARKER_PREFIX + " " + json.dumps(payload, sort_keys=True, separators=(",", ":"))


def emit_failure_marker(exception, *, stage=None, stream=None) -> None:
    """Best-effort one-line stderr contract for the parent updater process."""
    target = stream if stream is not None else sys.stderr
    try:
        target.write(marker_line(exception, stage=stage) + "\n")
        target.flush()
    except TimeoutError:
        raise
    except (AttributeError, OSError, ValueError):
        pass


def parse_markers(text) -> dict | None:
    """Last valid marker in bounded child output, ignoring unrelated lines."""
    if not isinstance(text, str) or not text:
        return None
    found = None
    for line in text[-MAX_OUTPUT_CHARS:].splitlines():
        index = line.find(MARKER_PREFIX)
        if index < 0:
            continue
        raw = line[index + len(MARKER_PREFIX):].strip()
        if len(raw) > MAX_MARKER_CHARS:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            continue
        category = value.get("category")
        stage = value.get("stage")
        reason_code = value.get("reason_code")
        if category not in KNOWN_CATEGORIES or stage not in STAGES:
            continue
        if not isinstance(reason_code, str) or _REASON_RE.fullmatch(reason_code) is None:
            continue
        found = {"category": category, "stage": stage, "reason_code": reason_code}
    return found


def child_failure(stdout, stderr, *, stage, message):
    """Rebuild a typed child failure, preserving marker stage and transport class."""
    marker = parse_markers(stderr) or parse_markers(stdout)
    if marker is not None:
        return UpdateFailure(message, category=marker["category"],
                             stage=marker["stage"], reason_code=marker["reason_code"])
    category = classify_output(stdout, stderr)
    if category is not None and category != INTERNAL:
        return UpdateFailure(message, category=category, stage=stage)
    return attach_stage(RuntimeError(message), stage)


__all__ = [
    "CANCELLED", "CONNECTIVITY", "CREDENTIALS", "INTERNAL", "MARKER_PREFIX",
    "PERMISSION", "PREREQUISITE", "SCHEMA", "STAGES", "UpdateFailure",
    "attach_stage", "child_failure", "classify_exception", "classify_output",
    "classify_text", "emit_failure_marker", "marker_line", "parse_markers",
    "stage_of",
]
