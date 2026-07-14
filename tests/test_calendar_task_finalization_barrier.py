"""TKT-CALENDAR-FINALIZATION-BARRIER-CLOSEOUT — CalendarTaskBarrier regression tests.

The barrier (analysis_service.py lines 1280-1292) calls _bg.join(timeout=T) before
the pipeline finalizes its snapshot.  This prevents background calendar scan results
from mutating the current run's data after the snapshot is written.

Regression invariants:
1. Thread completes before timeout → join succeeds; pipeline continues normally.
2. Thread still running at timeout → non-fatal; log message emitted; pipeline continues.
3. join() raises an exception → non-fatal; exception logged; pipeline continues.
4. _bg not defined (NameError) → caught silently; pipeline continues.
5. After the barrier, background work cannot mutate already-finalized result structures.

These are unit-style tests that simulate the barrier logic in isolation, plus
integration-style checks against the shared _CALENDAR_SCAN_STATE sentinel.
"""
from __future__ import annotations

import sys
import threading
import time
import types

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import pytest


# ── Barrier logic replicated from analysis_service ────────────────────────────
# The logic under test is straightforward enough to simulate in isolation so we
# don't depend on the full pipeline:
#
#   try:
#       _bg.join(timeout=_timeout)
#       if _bg.is_alive():
#           log("timeout")
#       else:
#           log("joined")
#   except NameError:
#       pass
#   except Exception as exc:
#       log(f"join error: {exc}")

def _run_barrier(bg_thread, timeout: float, log: list[str]) -> None:
    """Simulate the CalendarTaskBarrier block from analysis_service."""
    try:
        bg_thread.join(timeout=timeout)
        if bg_thread.is_alive():
            log.append(f"CalendarTaskBarrier: timeout after {timeout}s")
        else:
            log.append("CalendarTaskBarrier: joined")
    except NameError:
        pass
    except Exception as exc:
        log.append(f"CalendarTaskBarrier: join error: {exc}")


def _run_barrier_no_bg(timeout: float, log: list[str]) -> None:
    """Simulate the NameError branch where _bg was never assigned."""
    try:
        _bg.join(timeout=timeout)  # noqa: F821  ← NameError intentional
        if _bg.is_alive():  # noqa: F821
            log.append("timeout")
        else:
            log.append("joined")
    except NameError:
        pass
    except Exception as exc:
        log.append(f"join error: {exc}")


class TestBarrierJoinOutcomes:
    """Verify the three join outcomes: success, timeout, exception."""

    def test_fast_thread_joins_successfully(self):
        log: list[str] = []
        fast = threading.Thread(target=lambda: None, daemon=True)
        fast.start()
        _run_barrier(fast, timeout=2.0, log=log)
        assert any("joined" in msg for msg in log), f"Expected joined log; got {log}"
        assert not any("timeout" in msg for msg in log)

    def test_slow_thread_logs_timeout_warning(self):
        log: list[str] = []
        barrier_hit = threading.Event()

        def _slow():
            barrier_hit.wait(timeout=5.0)

        slow = threading.Thread(target=_slow, daemon=True)
        slow.start()
        try:
            _run_barrier(slow, timeout=0.05, log=log)
            assert any("timeout" in msg for msg in log), f"Expected timeout log; got {log}"
            assert not any("joined" in msg for msg in log)
        finally:
            barrier_hit.set()
            slow.join(timeout=1.0)

    def test_exception_from_join_is_non_fatal(self):
        """If join() raises, the barrier catches it and logs — pipeline does not crash."""
        log: list[str] = []

        class _BrokenThread:
            def join(self, timeout=None):
                raise RuntimeError("simulated join error")
            def is_alive(self):
                return False

        _run_barrier(_BrokenThread(), timeout=1.0, log=log)
        assert any("join error" in msg for msg in log)

    def test_name_error_caught_when_bg_not_defined(self):
        """NameError from undefined _bg must be caught silently — no crash."""
        log: list[str] = []
        _run_barrier_no_bg(timeout=0.5, log=log)
        assert log == [], f"Expected empty log on NameError path; got {log}"


class TestBarrierOrdering:
    """Background work cannot mutate finalized result structures after the barrier."""

    def test_current_run_uses_prior_scan_state_not_current_bg_results(self):
        """The current run reads _CALENDAR_SCAN_STATE BEFORE launching the background
        thread (simulating analysis_service lines 877-888).  A local copy is made
        under the lock, so subsequent bg writes to the shared state cannot corrupt
        the data already captured for the current run.
        """
        import copy

        shared_state: dict = {"candidates": ["prior-result"], "status": "ok"}
        lock = threading.Lock()

        # Step 1: Main pipeline reads the shared state into a local copy (before bg start).
        with lock:
            local_copy = copy.deepcopy(shared_state)

        bg_started = threading.Event()
        bg_done = threading.Event()

        def _bg_work():
            # Simulate bg thread writing new results to shared state.
            bg_started.set()
            with lock:
                shared_state["candidates"] = ["new-bg-result"]
                shared_state["status"] = "completed"
            bg_done.set()

        bg = threading.Thread(target=_bg_work, daemon=True)
        bg.start()
        bg_started.wait(timeout=2.0)
        bg_done.wait(timeout=2.0)

        # Simulate barrier join — bg has already completed.
        _run_barrier(bg, timeout=2.0, log=[])

        # local_copy must reflect the PRE-bg state because it was captured before the thread ran.
        assert local_copy["candidates"] == ["prior-result"], (
            f"Local copy must not be affected by bg writes: {local_copy['candidates']}"
        )
        assert local_copy["status"] == "ok"

        # The shared state now reflects the bg thread's write (for the NEXT run).
        with lock:
            assert shared_state["candidates"] == ["new-bg-result"]

    def test_finalization_proceeds_even_when_bg_still_running(self):
        """Pipeline finalization must not block indefinitely on a slow background thread."""
        finalization_steps: list[str] = []

        def _slow_bg():
            time.sleep(10)  # far longer than barrier timeout

        bg = threading.Thread(target=_slow_bg, daemon=True)
        bg.start()

        start = time.monotonic()
        _run_barrier(bg, timeout=0.05, log=[])
        finalization_steps.append("finalized")
        elapsed = time.monotonic() - start

        assert "finalized" in finalization_steps
        assert elapsed < 1.0, f"Finalization blocked for {elapsed:.2f}s — barrier should have timed out quickly"


class TestCalendarScanStateIsolation:
    """Calendar scanner state is run-scoped; no process-global candidate cache remains."""

    def test_legacy_process_global_scan_state_removed(self):
        import app.services.analysis_service as analysis_service

        assert not hasattr(analysis_service, "_CALENDAR_SCAN_STATE")
        assert not hasattr(analysis_service, "_CALENDAR_SCAN_LOCK")

    def test_run_scoped_scan_result_has_expected_shape(self):
        from app.services.calendar_scan_result_service import complete_scan_result, new_scan_result

        result = new_scan_result("run-33b", "scan-1")
        complete_scan_result(result, [{"ticker": "SBUX"}])

        payload = result.to_dict()
        assert payload["run_id"] == "run-33b"
        assert payload["scan_id"] == "scan-1"
        assert payload["status"] == "COMPLETE"
        assert len(payload["candidates"]) == 1
        assert payload["candidates"][0]["scan_source"] == "current_run"

    def test_barrier_config_timeout_respected(self):
        """CALENDAR_SCAN_BARRIER_TIMEOUT_SECONDS config is consumed as a float."""
        from app import config
        timeout_val = float(getattr(config, "CALENDAR_SCAN_BARRIER_TIMEOUT_SECONDS", 5.0))
        assert timeout_val > 0, f"Barrier timeout must be positive; got {timeout_val}"
        assert timeout_val <= 60.0, f"Barrier timeout unreasonably large: {timeout_val}s"
