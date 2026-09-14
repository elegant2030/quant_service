from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from quant_workbench.ops.alert import (
    AlertConfig,
    AlertState,
    TelegramNotifier,
    consecutive_job_failures,
    evaluate_health_alerts,
    evaluate_pipeline_alert,
    load_alert_config,
    notify_health_report,
    send_test_message,
)
from quant_workbench.store import DatasetStore, StateStore


class FakeTransport:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "timeout": timeout})
        if self.fail:
            raise ConnectionError("network down")
        return {"ok": True, "result": {"message_id": len(self.calls)}}


class FakeDocumentTransport:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def __call__(
        self, url: str, fields: dict[str, str], path: Path, timeout: float
    ) -> dict[str, Any]:
        self.calls.append(
            {"url": url, "fields": fields, "path": path, "timeout": timeout}
        )
        if self.fail:
            raise ConnectionError("upload down")
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def config(**overrides: Any) -> AlertConfig:
    base = {"bot_token": "123:abc", "chat_id": "42", "heartbeat_timezone": "UTC"}
    base.update(overrides)
    return AlertConfig(**base)


def report(
    status: str = "ok", errors: list[str] | None = None, quarantine: int = 0
) -> dict[str, Any]:
    return {
        "status": status,
        "errors": errors or [],
        "warnings": [],
        "markets": {"us": {"watermark": "2026-09-11", "coverage": 1.0}},
        "options": {"watermark": "2026-09-14T00:03:43+00:00"},
        "inventory": {"quarantine_files": quarantine},
        "build_sha": "abc1234",
    }


# 03:00 UTC keeps the heartbeat (09:00 local) out of the way unless a test wants it.
EARLY = datetime(2026, 9, 14, 3, tzinfo=timezone.utc)


class ConfigTests(unittest.TestCase):
    def test_env_file_is_read_from_runtime_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "config").mkdir()
            (runtime / "config" / "alerts.env").write_text(
                "# comment\nTELEGRAM_BOT_TOKEN='123:abc'\nexport TELEGRAM_CHAT_ID=42\n"
                "QW_ALERT_REPEAT_HOURS=2\n",
                encoding="utf-8",
            )
            loaded = load_alert_config(runtime / "data", environ={})
            self.assertTrue(loaded.configured)
            self.assertEqual(loaded.bot_token, "123:abc")
            self.assertEqual(loaded.chat_id, "42")
            self.assertEqual(loaded.repeat_hours, 2.0)

    def test_environment_overrides_file_and_missing_file_is_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loaded = load_alert_config(Path(directory) / "data", environ={})
            self.assertFalse(loaded.configured)
            loaded = load_alert_config(
                Path(directory) / "data",
                environ={
                    "TELEGRAM_BOT_TOKEN": "t",
                    "TELEGRAM_CHAT_ID": "c",
                    "QW_ALERTS_ENABLED": "0",
                },
            )
            self.assertFalse(loaded.configured)
            self.assertEqual(loaded.bot_token, "t")


class NotifierTests(unittest.TestCase):
    def test_send_posts_to_bot_api(self) -> None:
        transport = FakeTransport()
        result = TelegramNotifier(config(), transport).send("hello")
        self.assertTrue(result.ok)
        self.assertEqual(result.message_id, 1)
        self.assertIn("/bot123:abc/sendMessage", transport.calls[0]["url"])
        self.assertEqual(transport.calls[0]["payload"]["chat_id"], "42")

    def test_send_never_raises(self) -> None:
        result = TelegramNotifier(config(), FakeTransport(fail=True)).send("hello")
        self.assertFalse(result.ok)
        self.assertIn("ConnectionError", result.error or "")
        unconfigured = TelegramNotifier(AlertConfig(), FakeTransport()).send("hello")
        self.assertFalse(unconfigured.ok)

    def test_send_document_uploads_complete_file_and_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            path.write_text("complete report", encoding="utf-8")
            transport = FakeDocumentTransport()
            result = TelegramNotifier(
                config(), document_transport=transport
            ).send_document(path, "完整报告")
            self.assertTrue(result.ok)
            self.assertIn("/bot123:abc/sendDocument", transport.calls[0]["url"])
            self.assertEqual(transport.calls[0]["fields"]["chat_id"], "42")
            self.assertEqual(transport.calls[0]["path"], path)
            failed = TelegramNotifier(
                config(), document_transport=FakeDocumentTransport(fail=True)
            ).send_document(path)
            self.assertFalse(failed.ok)
            self.assertIn("ConnectionError", failed.error or "")


class HealthAlertRuleTests(unittest.TestCase):
    def test_error_sent_once_then_repeated_after_interval_then_recovery(self) -> None:
        state = AlertState(quarantine_files=0, heartbeat_date="2026-09-14")
        failing = report("error", ["us daily bars stale: 2026-09-10 < 2026-09-11"])
        first = evaluate_health_alerts(failing, state, config(), EARLY)
        self.assertEqual([m.kind for m in first], ["error"])
        self.assertIn("watchdog ERROR", first[0].text)

        again = evaluate_health_alerts(failing, state, config(), EARLY + timedelta(minutes=15))
        self.assertEqual(again, [])

        later = evaluate_health_alerts(failing, state, config(), EARLY + timedelta(hours=6))
        self.assertEqual([m.kind for m in later], ["error"])
        self.assertIn("still failing", later[0].text)

        changed = report("error", ["cn daily bars stale: 2026-09-10 < 2026-09-11"])
        differing = evaluate_health_alerts(
            changed, state, config(), EARLY + timedelta(hours=6, minutes=15)
        )
        self.assertEqual([m.kind for m in differing], ["error"])

        recovered = evaluate_health_alerts(
            report("ok"), state, config(), EARLY + timedelta(hours=7)
        )
        self.assertEqual([m.kind for m in recovered], ["recovered"])
        self.assertIsNone(state.error_fingerprint)
        self.assertEqual(
            evaluate_health_alerts(report("ok"), state, config(), EARLY + timedelta(hours=8)), []
        )

    def test_quarantine_growth_alerts_only_on_increase(self) -> None:
        state = AlertState(heartbeat_date="2026-09-14")
        self.assertEqual(evaluate_health_alerts(report(quarantine=0), state, config(), EARLY), [])
        grew = evaluate_health_alerts(report(quarantine=2), state, config(), EARLY)
        self.assertEqual([m.kind for m in grew], ["quarantine"])
        self.assertIn("0 → 2", grew[0].text)
        self.assertEqual(evaluate_health_alerts(report(quarantine=2), state, config(), EARLY), [])

    def test_consecutive_job_failures_alert_once_per_streak(self) -> None:
        state = AlertState(quarantine_files=0, heartbeat_date="2026-09-14")
        failures = {"ingest_incremental_bars": {"count": 2, "last_error": "boom", "last_run_id": 7}}
        first = evaluate_health_alerts(report(), state, config(), EARLY, failures)
        self.assertEqual([m.kind for m in first], ["job_failures"])
        self.assertIn("2x in a row", first[0].text)
        self.assertEqual(evaluate_health_alerts(report(), state, config(), EARLY, failures), [])
        newer = {"ingest_incremental_bars": {"count": 2, "last_error": "boom", "last_run_id": 9}}
        self.assertEqual(
            [m.kind for m in evaluate_health_alerts(report(), state, config(), EARLY, newer)],
            ["job_failures"],
        )
        self.assertEqual(evaluate_health_alerts(report(), state, config(), EARLY, {}), [])
        self.assertEqual(state.job_failure_fingerprints, {})

    def test_heartbeat_once_per_local_day_after_configured_hour(self) -> None:
        state = AlertState(quarantine_files=0)
        before = datetime(2026, 9, 14, 8, 59, tzinfo=timezone.utc)
        self.assertEqual(evaluate_health_alerts(report(), state, config(), before), [])
        at_nine = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
        beat = evaluate_health_alerts(report(), state, config(), at_nine)
        self.assertEqual([m.kind for m in beat], ["heartbeat"])
        self.assertIn("heartbeat 2026-09-14", beat[0].text)
        self.assertIn("abc1234", beat[0].text)
        self.assertEqual(
            evaluate_health_alerts(report(), state, config(), at_nine + timedelta(hours=5)), []
        )
        next_day = datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(
            [m.kind for m in evaluate_health_alerts(report(), state, config(), next_day)],
            ["heartbeat"],
        )

    def test_pipeline_alert_on_change_and_clear_on_success(self) -> None:
        state = AlertState()
        failed = {"status": "error", "errors": ["us: RuntimeError: coverage 90% below 98%"]}
        self.assertEqual([m.kind for m in evaluate_pipeline_alert(failed, state)], ["pipeline"])
        self.assertEqual(evaluate_pipeline_alert(failed, state), [])
        self.assertEqual(evaluate_pipeline_alert({"status": "ok", "errors": []}, state), [])
        self.assertEqual([m.kind for m in evaluate_pipeline_alert(failed, state)], ["pipeline"])


class IntegrationTests(unittest.TestCase):
    def test_consecutive_failures_read_from_state_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(Path(directory) / "control.db") as state:
                for key in ("a", "b"):
                    lease = state.acquire_job("daily", key)
                    state.fail_job(lease.run_id or 0, f"error {key}")
                lease = state.acquire_job("options", "x")
                state.fail_job(lease.run_id or 0, "only once")
                lease = state.acquire_job("backup", "y")
                state.complete_job(lease.run_id or 0)
                failures = consecutive_job_failures(state)
        self.assertEqual(set(failures), {"daily"})
        self.assertEqual(failures["daily"]["last_error"], "error b")

    def test_notify_health_report_persists_state_and_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(Path(directory) / "data")
            transport = FakeTransport()
            with StateStore(store.root / "state" / "control.db") as state:
                failing = report("error", ["us daily bars stale"])
                summary = notify_health_report(
                    store, state, failing, EARLY, config(), TelegramNotifier(config(), transport)
                )
                self.assertTrue(summary["configured"])
                self.assertEqual([item["kind"] for item in summary["sent"]], ["error"])
                self.assertTrue(all(item["ok"] for item in summary["sent"]))
                persisted = json.loads((store.root / "health" / "alert-state.json").read_text())
                self.assertIsNotNone(persisted["error_fingerprint"])

                repeat = notify_health_report(
                    store,
                    state,
                    failing,
                    EARLY + timedelta(minutes=15),
                    config(),
                    TelegramNotifier(config(), transport),
                )
                self.assertEqual(repeat["sent"], [])

                with self.assertLogs("quant_workbench.ops.alert", level="WARNING"):
                    broken = notify_health_report(
                        store,
                        state,
                        report("ok"),
                        EARLY + timedelta(hours=1),
                        config(),
                        TelegramNotifier(config(), FakeTransport(fail=True)),
                    )
                self.assertEqual([item["kind"] for item in broken["sent"]], ["recovered"])
                self.assertFalse(broken["sent"][0]["ok"])
                persisted = json.loads((store.root / "health" / "alert-state.json").read_text())
                self.assertIn("ConnectionError", persisted["last_send_error"])

    def test_unconfigured_runtime_reports_but_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DatasetStore(Path(directory) / "data")
            with StateStore(store.root / "state" / "control.db") as state:
                summary = notify_health_report(
                    store, state, report("error", ["x"]), EARLY, AlertConfig()
                )
            self.assertFalse(summary["configured"])
            self.assertEqual(summary["sent"], [])
            self.assertEqual(summary["skipped"], "alerts not configured")
            self.assertFalse((store.root / "health" / "alert-state.json").exists())
            test = send_test_message(store, config=AlertConfig())
            self.assertFalse(test["ok"])
            self.assertTrue(test["expected_config_path"].endswith("config/alerts.env"))


if __name__ == "__main__":
    unittest.main()
