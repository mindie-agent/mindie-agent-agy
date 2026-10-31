"""Tier 0 & Tier 1 tests for `organizer.py` (`mindie-summary-outcome/1` contract and `outcome_unknown` safety)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import pytest

from mindie_knowledge.materials import summarizer
import organizer


def _sample_request() -> tuple[dict, str]:
    block_id = "a" * 64
    task_id = "b" * 64
    blocks = [
        {
            "block_id": block_id,
            "text": "### user\n排查 A3 HCCL 超时\n\n### assistant\n定位到双 Die Phy-ID 绑定冲突。",
            "source_range": {"start": 0, "end": 120, "digest": "c" * 64},
        }
    ]
    pol = organizer.identity()
    req = summarizer.make_request(
        task_id=task_id,
        body_version="rev-1",
        blocks=blocks,
        prior_navigation=None,
        identity=pol,
    )
    return req, block_id


def test_organizer_identity_passes_core_summarizer_validation():
    ident = organizer.identity()
    validated = summarizer.validate_identity(ident)
    assert validated["fingerprint"] == ident["fingerprint"]
    assert "mindie-distill-request/1" in validated["capabilities"]


def test_organizer_emits_valid_summary_outcome_and_never_retries_unknown(
    monkeypatch: pytest.MonkeyPatch,
):
    req, block_id = _sample_request()
    mode = {"behavior": "ok"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if mode["behavior"] == "timeout":
                self.send_response(504)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "result": {
                        "blocks": [
                            {
                                "block_id": block_id,
                                "title": "A3 双 Die Phy-ID 绑定冲突排查",
                                "summary": "定位昇腾 A3 910C 双 Die 拓扑下 HCCL 通信超时根因为相邻 Die 混绑。",
                            }
                        ],
                        "navigation": {
                            "title": "A3 HCCL 通信超时与双 Die 映射排查",
                            "summary": "排查并解决昇腾 A3 双 Die Phy-ID 绑定冲突导致的 HCCL 超时。",
                        },
                    },
                    "usage": {"input_tokens": 180, "cached_input_tokens": 0, "output_tokens": 65},
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("MINDIE_SUMMARY_ENDPOINT", f"http://127.0.0.1:{port}/summarize")

        out = organizer.execute_request(req)
        summarizer.validate_outcome(out, req)
        assert out["status"] == "returned"
        assert out["model_calls"] == 1
        assert out["billing_status"] == "reported"
        assert out["usage"] == {"input_tokens": 180, "cached_input_tokens": 0, "output_tokens": 65}

        # When failure happens after request transmission, status MUST be outcome_unknown (never auto-replayed)
        mode["behavior"] = "timeout"
        out_unknown = organizer.execute_request(req)
        summarizer.validate_outcome(out_unknown, req)
        assert out_unknown["status"] == "outcome_unknown"
        assert out_unknown["model_calls"] == 1
        assert out_unknown["billing_status"] == "unknown"
    finally:
        server.shutdown()
        server.server_close()
