"""
ASA Patch 30D — Payload Guard Tests

Verifies the payload_profile_service (already exists) accurately implements the guard:
  - Thresholds: healthy ≤750KB, watch >750KB–1MB, warning >1MB–2MB, critical >2MB
  - summary_json_bytes, summary_payload_status, strategy_row_profile all present
  - payload_warnings emitted correctly at each tier
  - _strategy_row_profile includes skew_rows_bytes and skew_row_count
  - json_bytes() utility is accurate
  - compact_payload_log returns a string
  - Run does not fail on warning-level payload (only logs)
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_payload_profile_service_compiles(self):
        py_compile.compile("app/services/payload_profile_service.py", doraise=True)


# ─── Thresholds ───────────────────────────────────────────────────────────────

class TestPayloadThresholds:
    def _status(self, size_bytes: int) -> str:
        from app.services.payload_profile_service import _payload_status
        return _payload_status(size_bytes)

    def test_zero_bytes_is_healthy(self):
        assert self._status(0) == "healthy"

    def test_below_750kb_is_healthy(self):
        assert self._status(500_000) == "healthy"

    def test_exactly_750kb_is_healthy(self):
        assert self._status(750_000) == "healthy"

    def test_just_over_750kb_is_watch(self):
        assert self._status(750_001) == "watch"

    def test_just_below_1mb_is_watch(self):
        assert self._status(999_999) == "watch"

    def test_exactly_1mb_is_watch(self):
        assert self._status(1_000_000) == "watch"

    def test_just_over_1mb_is_warning(self):
        assert self._status(1_000_001) == "warning"

    def test_just_below_2mb_is_warning(self):
        assert self._status(1_999_999) == "warning"

    def test_exactly_2mb_is_warning(self):
        assert self._status(2_000_000) == "warning"

    def test_just_over_2mb_is_critical(self):
        assert self._status(2_000_001) == "critical"

    def test_large_payload_is_critical(self):
        assert self._status(5_000_000) == "critical"


# ─── json_bytes utility ───────────────────────────────────────────────────────

class TestJsonBytes:
    def _bytes(self, value) -> int:
        from app.services.payload_profile_service import json_bytes
        return json_bytes(value)

    def test_empty_dict_has_positive_bytes(self):
        assert self._bytes({}) > 0

    def test_none_returns_zero_or_positive(self):
        # Serializes to "null"
        result = self._bytes(None)
        assert result >= 0

    def test_small_dict_within_expected_range(self):
        d = {"a": 1, "b": "hello"}
        result = self._bytes(d)
        expected_str = json.dumps(d, separators=(",", ":"))
        assert result == len(expected_str.encode("utf-8"))

    def test_large_payload_accurately_measured(self):
        data = {"rows": ["x" * 100] * 100}
        result = self._bytes(data)
        assert result > 10_000


# ─── build_payload_size_profile ───────────────────────────────────────────────

class TestPayloadSizeProfile:
    def _build(self, snapshot: dict | None = None) -> dict:
        from app.services.payload_profile_service import build_payload_size_profile
        return build_payload_size_profile(
            payload="",
            positions=[],
            news=[],
            recommendations=[],
            snapshot=snapshot or {},
            log=[],
            report_summary={"test": True},
        )

    def test_returns_dict(self):
        result = self._build()
        assert isinstance(result, dict)

    def test_summary_json_bytes_present(self):
        result = self._build()
        assert "summary_json_bytes" in result
        assert isinstance(result["summary_json_bytes"], int)

    def test_summary_payload_status_present(self):
        result = self._build()
        assert result.get("summary_payload_status") in ("healthy", "watch", "warning", "critical")

    def test_strategy_row_profile_present(self):
        result = self._build()
        assert "strategy_row_profile" in result
        assert isinstance(result["strategy_row_profile"], dict)

    def test_strategy_row_profile_includes_skew(self):
        result = self._build()
        profile = result["strategy_row_profile"]
        assert "skew_rows_bytes" in profile
        assert "skew_row_count" in profile

    def test_largest_strategy_rows_present(self):
        result = self._build()
        assert "largest_strategy_rows" in result
        assert isinstance(result["largest_strategy_rows"], list)

    def test_largest_top_level_keys_present(self):
        result = self._build()
        assert "largest_top_level_keys" in result

    def test_sections_bytes_present(self):
        result = self._build()
        assert "sections_bytes" in result
        assert isinstance(result["sections_bytes"], dict)

    def test_skew_rows_profiled_from_strategy_results(self):
        from app.services.payload_profile_service import build_payload_size_profile
        fake_skew = {
            "items": [
                {"ticker": "AAPL", "verdict": "PASS / POSSIBLE ENTRY SETUP", "score": 72.0},
                {"ticker": "NVDA", "verdict": "WATCH / SKEW NOT RICH ENOUGH", "score": 40.0},
            ]
        }
        snapshot = {
            "_strategy_results": {"skew_momentum_vertical": fake_skew}
        }
        result = build_payload_size_profile(
            payload="", positions=[], news=[], recommendations=[],
            snapshot=snapshot, log=[], report_summary={},
        )
        profile = result["strategy_row_profile"]
        assert profile["skew_row_count"] == 2
        assert profile["skew_rows_bytes"] > 0


# ─── build_payload_warnings ───────────────────────────────────────────────────

class TestPayloadWarnings:
    def _warnings(self, size_bytes: int) -> list:
        from app.services.payload_profile_service import build_payload_warnings
        profile = {
            "summary_json_bytes": size_bytes,
            "summary_payload_status": _status(size_bytes),
        }
        return build_payload_warnings(profile)

    def test_healthy_payload_no_warnings(self):
        warnings = self._warnings(500_000)
        size_warnings = [w for w in warnings if w["name"] == "payload_size_warning"]
        assert size_warnings == []

    def test_watch_payload_emits_watch_warning(self):
        warnings = self._warnings(800_000)
        levels = [w["level"] for w in warnings if w["name"] == "payload_size_warning"]
        assert "watch" in levels

    def test_warning_payload_emits_warning(self):
        warnings = self._warnings(1_200_000)
        levels = [w["level"] for w in warnings if w["name"] == "payload_size_warning"]
        assert "warning" in levels

    def test_critical_payload_emits_critical(self):
        warnings = self._warnings(3_000_000)
        levels = [w["level"] for w in warnings if w["name"] == "payload_size_warning"]
        assert "critical" in levels

    def test_warning_includes_actual_bytes(self):
        from app.services.payload_profile_service import build_payload_warnings
        profile = {
            "summary_json_bytes": 1_200_000,
            "summary_payload_status": "warning",
        }
        warnings = build_payload_warnings(profile)
        for w in warnings:
            if w["name"] == "payload_size_warning":
                assert w["actual_bytes"] == 1_200_000

    def test_warning_does_not_fail_run(self):
        # Warnings should just be a list; not raise an exception
        try:
            self._warnings(1_200_000)
        except Exception as exc:
            assert False, f"payload warnings should not raise: {exc}"


# ─── compact_payload_log ──────────────────────────────────────────────────────

class TestCompactPayloadLog:
    def test_returns_string(self):
        from app.services.payload_profile_service import compact_payload_log, build_payload_size_profile
        profile = build_payload_size_profile(
            payload="", positions=[], news=[], recommendations=[],
            snapshot={}, log=[], report_summary={},
        )
        result = compact_payload_log(profile)
        assert isinstance(result, str)

    def test_log_contains_status(self):
        from app.services.payload_profile_service import compact_payload_log, build_payload_size_profile
        profile = build_payload_size_profile(
            payload="", positions=[], news=[], recommendations=[],
            snapshot={}, log=[], report_summary={},
        )
        result = compact_payload_log(profile)
        assert "PayloadProfile[" in result


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _status(size_bytes: int) -> str:
    from app.services.payload_profile_service import _payload_status
    return _payload_status(size_bytes)
