"""Bounded incremental reader for Antigravity (AGY) brain JSONL transcripts.
[Role]
Per-host transcript adapter (`FileIdentity`, `identify`, `capture_source`,
`read_material`, `read_increment`) for `brain/<conversationId>/
.system_generated/logs/transcript.jsonl` and `transcript_full.jsonl`.
[Invariants]
- Prefers `transcript_full.jsonl` so public messages are never truncated.
- Extracts only `USER_INPUT` (`USER_EXPLICIT`) and `PLANNER_RESPONSE` (`MODEL`)
  text; excludes `thinking`, `tool_calls`, `GENERIC`, `<ADDITIONAL_METADATA>`,
  `<USER_SETTINGS_CHANGE>`, and `<SYSTEM_MESSAGE>`.
- Enforces exact `[start, end)` SHA-256 byte accounting with a 512-byte anchor.
[Timeouts & Locks]
- `read_material` enforces `max_scan_bytes=16 MiB` and `max_seconds=2.0s`.
[Side-Effect Policy]
- Strictly read-only over the caller-specified transcript; mutates no files.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, json, math, os, re, time
from pathlib import Path
MAX_TEXT = 48 * 1024
MAX_RECORDS = 200
ANCHOR_BYTES = 512
SOURCE_HEAD_BYTES = 1024 * 1024
SOURCE_HEAD_LINES = 64
PUBLIC_TYPES = {"USER_INPUT", "PLANNER_RESPONSE"}
SKIP_TYPES = {"GENERIC", "SYSTEM", "TOOL_RESULT", "ERROR"}
SESSION_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
STRIP_TAGS_RE = re.compile(r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>.*?</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>", re.DOTALL)
def _canonical_path(path: str | Path) -> str:
    try: resolved = str(Path(path).resolve())
    except TimeoutError: raise
    except OSError: resolved = str(Path(path))
    return resolved.removeprefix("\\\\?\\") if os.name == "nt" else resolved
@dataclass(frozen=True)
class FileIdentity:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    anchor_len: int = 0
    anchor_digest: str = ""
    @property
    def key(self) -> str:
        return f"{self.path}|{self.dev}:{self.ino}"
    def serialize(self) -> str:
        return json.dumps(dict(dev=self.dev, ino=self.ino, anchor_len=self.anchor_len, anchor_digest=self.anchor_digest), sort_keys=True, separators=(",", ":"))
    @staticmethod
    def unserialize(text: str, path: str) -> FileIdentity | None:
        try:
            data = json.loads(text); anchor_len, anchor_digest = data["anchor_len"], data["anchor_digest"]
            if type(anchor_len) is not int or not 0 < anchor_len <= ANCHOR_BYTES or not isinstance(anchor_digest, str) or len(anchor_digest) != 64: return None
            return FileIdentity(_canonical_path(path), int(data["dev"]), int(data["ino"]), 0, 0, anchor_len, anchor_digest)
        except (ValueError, KeyError, TypeError): return None
def identify(path: str | Path) -> FileIdentity | None:
    try: fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except TimeoutError: raise
    except (OSError, ValueError): return None
    try:
        stat = os.fstat(fd); anchor = os.read(fd, ANCHOR_BYTES)
    except TimeoutError: raise
    except OSError: return None
    finally: os.close(fd)
    return FileIdentity(_canonical_path(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())
def to_seconds(raw) -> float | None:
    if type(raw) in (int, float) and raw > 0:
        value = float(raw) / (1000.0 if raw >= 1e12 else 1.0)
        return value if math.isfinite(value) else None
    if not isinstance(raw, str) or not raw.strip(): return None
    text = raw.strip(); text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try: stamp = datetime.fromisoformat(text)
    except ValueError: return None
    return (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).timestamp()
def _session_in_path(path: str | Path) -> str | None:
    parts = Path(path).parts
    brain = next((idx for idx, part in enumerate(parts) if part.lower() == "brain"), -1)
    if 0 <= brain < len(parts) - 1 and SESSION_UUID_RE.fullmatch(parts[brain + 1]): return parts[brain + 1].lower()
    for part in reversed(parts):
        if SESSION_UUID_RE.fullmatch(part): return part.lower()
    return Path(path).stem.lower() if SESSION_UUID_RE.fullmatch(Path(path).stem) else None
def _select_full_transcript(path: str | Path) -> Path:
    selected = Path(path).resolve(strict=True)
    if selected.name == "transcript.jsonl" and selected.with_name("transcript_full.jsonl").is_file():
        return selected.with_name("transcript_full.jsonl").resolve(strict=True)
    return selected
def capture_source(path: str | Path, session_id: str, *, binding: dict | None = None) -> dict:
    """Validate an Antigravity transcript path and return its capture source descriptor."""
    if not isinstance(session_id, str) or not SESSION_UUID_RE.fullmatch(session_id.strip()): raise ValueError("native Antigravity session_id is missing or invalid")
    norm_session = session_id.strip().lower()
    if not isinstance(path, (str, Path)) or not Path(path).is_absolute(): raise ValueError("capture transcript path must be absolute")
    try: selected = _select_full_transcript(path)
    except TimeoutError: raise
    except (OSError, RuntimeError) as exc: raise ValueError("capture transcript is unavailable") from exc
    if not selected.is_file() or _session_in_path(selected) != norm_session: raise ValueError("capture transcript is unavailable or belongs to another conversationId")
    with selected.open("rb") as stream: lines = stream.read(SOURCE_HEAD_BYTES).split(b"\n")[:SOURCE_HEAD_LINES]
    created = None
    for raw in lines:
        if not raw.strip(): continue
        try: record = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError): continue
        if not isinstance(record, dict): continue
        stamp = to_seconds(record.get("created_at") or record.get("timestamp"))
        if stamp is not None and math.isfinite(stamp): created = stamp; break
    if created is None: raise ValueError("native Antigravity task creation timestamp is unavailable")
    if binding is not None:
        scope, boundary = binding.get("project_root"), binding.get("activated_at")
        if (str(binding.get("session", "")).lower() != norm_session or type(binding.get("enabled")) not in (bool, int)
                or int(binding["enabled"]) != 1 or not isinstance(scope, str) or not os.path.isabs(scope)
                or type(boundary) not in (int, float) or not math.isfinite(boundary) or boundary < 0):
            raise ValueError("native task binding is invalid")
        return dict(session_id=norm_session, project_root=str(Path(scope).resolve()), created_at=created, transcript_path=str(selected))
    raise ValueError("native task binding is required to establish project scope")
def _clean_user_text(raw: str) -> str:
    stripped = STRIP_TAGS_RE.sub("", raw)
    match = USER_REQUEST_RE.search(stripped)
    return (match.group(1) if match is not None else stripped).strip()
def _extract(record: dict) -> tuple[str, str] | None:
    rtype, source, content = record.get("type"), record.get("source"), record.get("content")
    if not isinstance(content, str): return None
    if rtype == "USER_INPUT" and source == "USER_EXPLICIT":
        cleaned = _clean_user_text(content); return ("user", cleaned) if cleaned else None
    if rtype == "PLANNER_RESPONSE" and source == "MODEL":
        cleaned = STRIP_TAGS_RE.sub("", content).strip(); return ("assistant", cleaned) if cleaned else None
    return None
def read_material(path: str | Path, start: int, *, session_id: str | None = None, not_before: float | str | None = None,
                  expected: FileIdentity | None = None, max_scan_bytes: int = 16777216, max_seconds: float = 2.0,
                  max_text_bytes: int = MAX_TEXT, scan_until: int | None = None) -> dict:
    if type(start) is not int or start < 0 or max_scan_bytes <= 0 or max_seconds <= 0 or max_text_bytes <= 0: raise ValueError("start must be a nonnegative byte offset and budgets positive")
    if scan_until is not None and (type(scan_until) is not int or scan_until < start): raise ValueError("scan_until must be an integer byte boundary at or after start")
    boundary = to_seconds(not_before) if not_before is not None else None
    norm_session = session_id.strip().lower() if isinstance(session_id, str) and session_id.strip() else None
    result = dict(status="ok", start=start, end=start, digest=hashlib.sha256(b"").hexdigest(), text="", records=0, skipped_records=0, oversize_records=0,
                  partial=False, more=False, timestamps_reliable=True, session_match=None, coverage_note=None, coverage=[])
    consumed, recognized, included, text_size, begun = hashlib.sha256(), 0, [], 0, time.monotonic()
    try:
        with open(path, "rb") as stream:
            stat, anchor = os.fstat(stream.fileno()), stream.read(ANCHOR_BYTES)
            current = FileIdentity(_canonical_path(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())
            result["identity"], result["snapshot_size"] = current.serialize(), stat.st_size
            if expected is not None:
                stream.seek(0)
                if (os.path.normcase(expected.path) != os.path.normcase(current.path) or expected.dev != stat.st_dev or expected.ino != stat.st_ino
                        or not expected.anchor_len or hashlib.sha256(stream.read(expected.anchor_len)).hexdigest() != expected.anchor_digest):
                    result.update(status="replaced", coverage_note="transcript changed before read"); return result
            if stat.st_size < start: result.update(status="replaced", coverage_note="transcript truncated"); return result
            owner = _session_in_path(path)
            if owner:
                result["session_match"] = not norm_session or owner == norm_session
                if norm_session and owner != norm_session: result.update(status="wrong-task", coverage_note="transcript belongs to another task"); return result
            elif norm_session: result.update(status="unknown-format", coverage_note="task path identity unavailable; no public read"); return result
            stream.seek(max(0, start - 1))
            if start > 0 and stream.read(1) != b"\n": result.update(status="invalid-boundary", coverage_note="cursor is not a whole-record boundary"); return result
            stream.seek(start)
            end_limit = min(stat.st_size, start + max_scan_bytes)
            if scan_until is not None: end_limit = min(end_limit, scan_until)
            while stream.tell() < end_limit and time.monotonic() - begun < max_seconds:
                offset, room = stream.tell(), min(stat.st_size - stream.tell(), 1048576)
                if scan_until is not None: room = min(room, scan_until - offset)
                raw = stream.readline(room)
                if not raw: break
                if not raw.endswith(b"\n"): result["partial"] = offset + len(raw) == stat.st_size; break
                def drop(reason: str) -> None:
                    result.setdefault("discarded_records", []).append(dict(start=offset, end=stream.tell(), reason=reason))
                    consumed.update(raw); result["end"] = stream.tell(); result["skipped_records"] += 1
                try: record = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError): record = None
                if not isinstance(record, dict): drop("invalid record"); continue
                if record.get("type") in PUBLIC_TYPES or record.get("type") in SKIP_TYPES or "step_index" in record: recognized += 1
                stamp, extracted = to_seconds(record.get("created_at") or record.get("timestamp")), _extract(record)
                if extracted and extracted[1]:
                    if isinstance(record.get("truncated_fields"), list) and "content" in record["truncated_fields"]: drop("truncated content field"); continue
                    if boundary is not None and stamp is None: drop("missing public timestamp"); continue
                    if boundary is not None and stamp < boundary: extracted = None
                    else:
                        kind, text = extracted
                        rendered = f"### {kind}\n" + text.replace("\r\n", "\n").replace("\r", "\n")
                        encoded = rendered.encode("utf-8")
                        if not included and len(encoded) > max_text_bytes:
                            rendered = encoded[:max_text_bytes].decode("utf-8", errors="ignore"); encoded = rendered.encode("utf-8"); result["oversize_records"] += 1
                        size = len(encoded) + 2
                        if included and (text_size + size > max_text_bytes or len(included) >= MAX_RECORDS): break
                        included.append(rendered); text_size += size
                if not extracted or not extracted[1]: result["skipped_records"] += 1
                consumed.update(raw); result["end"] = stream.tell()
            result["more"] = result["end"] < stat.st_size
    except TimeoutError: raise
    except OSError: result.update(status="missing", coverage_note="transcript unreadable"); return result
    result.update(digest=consumed.hexdigest(), text="\n\n".join(included), records=len(included))
    established = expected is not None or result.get("session_match") is True
    if result["status"] != "ok": return result
    if result["end"] == start: result["status"] = "unchanged"
    elif not recognized and not (established and result["more"] and result["end"] > result["start"]):
        result.update(status="unknown-format", text="", coverage_note="no recognized native Antigravity transcript signature")
    return result
read_increment = read_material
