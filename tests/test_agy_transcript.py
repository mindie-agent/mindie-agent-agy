"""Tier 0 tests for `agy_transcript.py` on real Antigravity JSONL transcript records."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

import agy_transcript


SESSION_ID = "454c6ee9-afea-47be-8188-abb9c7fb1f26"


def _write_transcript_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for rec in records:
            stream.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))


def test_transcript_extracts_public_messages_and_excludes_thinking_and_metadata(tmp_path: Path):
    logs_dir = tmp_path / "brain" / SESSION_ID / ".system_generated" / "logs"
    compact_file = logs_dir / "transcript.jsonl"
    full_file = logs_dir / "transcript_full.jsonl"

    records = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-31T08:20:00Z",
            "content": "<USER_REQUEST>\n早期未激活对话\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "created_at": "2026-10-31T08:25:00Z",
            "content": (
                "<USER_REQUEST>\n/mindie-agent 分析 vLLM-Ascend A3 双 Die Phy-ID 映射规则\n</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\nThe current local time is 2026-10-31.\n</ADDITIONAL_METADATA>\n"
                "<USER_SETTINGS_CHANGE>\nModel changed.\n</USER_SETTINGS_CHANGE>"
            ),
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-10-31T08:25:05Z",
            "thinking": "SECRET_INTERNAL_CHAIN_OF_THOUGHT_DO_NOT_EXPORT",
            "tool_calls": [{"name": "read_file", "args": {"Path": "secret_tool_arg.py"}}],
            "content": "在昇腾 A3 910C 上，单张物理卡包含 2 个 Die，8 张卡对应 Phy-ID 0..15。",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "GENERIC",
            "status": "DONE",
            "created_at": "2026-10-31T08:25:08Z",
            "content": "RAW_TOOL_OUTPUT_MUST_BE_EXCLUDED",
        },
    ]
    _write_transcript_lines(compact_file, records)
    _write_transcript_lines(full_file, records)

    binding = {
        "session": SESSION_ID,
        "enabled": 1,
        "entry_enabled": 1,
        "project_root": str(tmp_path),
        "activated_at": agy_transcript.to_seconds("2026-10-31T08:24:00Z"),
    }
    source = agy_transcript.capture_source(compact_file, SESSION_ID, binding=binding)
    # Must automatically promote to transcript_full.jsonl when present alongside transcript.jsonl
    assert Path(source["transcript_path"]) == full_file.resolve()

    material = agy_transcript.read_material(
        source["transcript_path"],
        0,
        session_id=SESSION_ID,
        not_before=binding["activated_at"],
    )
    assert material["status"] == "ok"
    assert material["records"] == 2
    assert "早期未激活对话" not in material["text"]
    assert "分析 vLLM-Ascend A3 双 Die Phy-ID 映射规则" in material["text"]
    assert "在昇腾 A3 910C 上，单张物理卡包含 2 个 Die" in material["text"]
    assert "SECRET_INTERNAL_CHAIN_OF_THOUGHT_DO_NOT_EXPORT" not in material["text"]
    assert "secret_tool_arg.py" not in material["text"]
    assert "RAW_TOOL_OUTPUT_MUST_BE_EXCLUDED" not in material["text"]
    assert "ADDITIONAL_METADATA" not in material["text"]
    assert "USER_SETTINGS_CHANGE" not in material["text"]

    # Incremental read from `material["end"]` with persisted FileIdentity reports unchanged
    persisted = agy_transcript.FileIdentity.unserialize(material["identity"], str(full_file.resolve()))
    assert persisted is not None
    inc2 = agy_transcript.read_material(
        source["transcript_path"],
        material["end"],
        session_id=SESSION_ID,
        expected=persisted,
    )
    assert inc2["status"] == "unchanged"
