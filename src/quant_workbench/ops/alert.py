"""Telegram alerting for the background runtime.

Design rules:
- Alerting must never fail a job: every public entry point swallows transport errors.
- Credentials never live in the repository. They are read from ``config/alerts.env``
  next to the data root (``<runtime>/config/alerts.env``) or from environment variables.
- Alerts are de-duplicated through ``health/alert-state.json`` so a 15-minute watchdog
  cadence does not spam the chat with the same failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from quant_workbench.store import DatasetStore, StateStore

LOGGER = logging.getLogger(__name__)

ALERT_STATE_PATH = Path("health") / "alert-state.json"
CONFIG_FILENAME = "alerts.env"
TELEGRAM_API = "https://api.telegram.org"
DEFAULT_REPEAT_HOURS = 6
DEFAULT_HEARTBEAT_HOUR = 9
MAX_MESSAGE_CHARS = 3500
CONSECUTIVE_FAILURE_THRESHOLD = 2


# --------------------------------------------------------------------------- config


@dataclass(frozen=True, slots=True)
class AlertConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = True
    repeat_hours: float = DEFAULT_REPEAT_HOURS
    heartbeat_hour: int = DEFAULT_HEARTBEAT_HOUR
    heartbeat_timezone: str = "America/Los_Angeles"
    source_path: str | None = None

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def default_config_path(root: Path) -> Path:
    """``<runtime>/config/alerts.env`` for a data root of ``<runtime>/data``."""
    return root.parent / "config" / CONFIG_FILENAME


def load_alert_config(
    root: Path | None = None,
    path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> AlertConfig:
    env = dict(environ if environ is not None else os.environ)
    candidate = path
    if candidate is None and env.get("QW_ALERTS_ENV"):
        candidate = Path(env["QW_ALERTS_ENV"])
    if candidate is None and root is not None:
        candidate = default_config_path(Path(root).resolve())
    file_values: dict[str, str] = {}
    source_path: str | None = None
    if candidate is not None and candidate.is_file():
        try:
            file_values = _parse_env_file(candidate)
            source_path = str(candidate)
        except OSError as exc:
            LOGGER.warning("cannot read alert config %s: %s", candidate, exc)
    merged = {
        **file_values,
        **{k: v for k, v in env.items() if k.startswith("TELEGRAM_") or k.startswith("QW_ALERT")},
    }

    def number(key: str, default: float) -> float:
        try:
            return float(merged.get(key, default))
        except (TypeError, ValueError):
            return default

    enabled = str(merged.get("QW_ALERTS_ENABLED", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    return AlertConfig(
        bot_token=str(merged.get("TELEGRAM_BOT_TOKEN", "")).strip(),
        chat_id=str(merged.get("TELEGRAM_CHAT_ID", "")).strip(),
        enabled=enabled,
        repeat_hours=number("QW_ALERT_REPEAT_HOURS", DEFAULT_REPEAT_HOURS),
        heartbeat_hour=int(number("QW_ALERT_HEARTBEAT_HOUR", DEFAULT_HEARTBEAT_HOUR)),
        heartbeat_timezone=str(merged.get("QW_ALERT_HEARTBEAT_TZ", "America/Los_Angeles")).strip(),
        source_path=source_path,
    )


# ------------------------------------------------------------------------ transport


class Transport(Protocol):
    def __call__(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]: ...


def _urllib_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed host
        return json.loads(response.read().decode("utf-8") or "{}")


@dataclass(slots=True)
class SendResult:
    ok: bool
    error: str | None = None
    message_id: int | None = None


class TelegramNotifier:
    """Minimal Bot API client. ``send`` never raises."""

    def __init__(
        self,
        config: AlertConfig,
        transport: Transport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.config = config
        self.transport = transport or _urllib_transport
        self.timeout = timeout

    def send(self, text: str) -> SendResult:
        if not self.config.configured:
            return SendResult(False, "alerts not configured")
        url = f"{TELEGRAM_API}/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": text[:MAX_MESSAGE_CHARS],
            "disable_web_page_preview": "true",
        }
        try:
            response = self.transport(url, payload, self.timeout)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # pragma: no cover - best effort
                pass
            return SendResult(False, f"HTTP {exc.code}: {detail or exc.reason}")
        except Exception as exc:
            return SendResult(False, f"{type(exc).__name__}: {exc}")
        if not response.get("ok"):
            return SendResult(False, f"telegram: {response.get('description', 'unknown error')}")
        result = response.get("result") or {}
        return SendResult(True, None, result.get("message_id"))


# ---------------------------------------------------------------------- alert state


@dataclass(slots=True)
class AlertState:
    error_fingerprint: str | None = None
    error_last_sent_at: str | None = None
    quarantine_files: int | None = None
    heartbeat_date: str | None = None
    job_failure_fingerprints: dict[str, str] = field(default_factory=dict)
    pipeline_fingerprint: str | None = None
    last_send_error: str | None = None

    @classmethod
    def load(cls, store: DatasetStore) -> AlertState:
        path = store.root / ALERT_STATE_PATH
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOGGER.warning("corrupt alert state %s: %s", path, exc)
            return cls()
        return cls(
            error_fingerprint=payload.get("error_fingerprint"),
            error_last_sent_at=payload.get("error_last_sent_at"),
            quarantine_files=payload.get("quarantine_files"),
            heartbeat_date=payload.get("heartbeat_date"),
            job_failure_fingerprints=dict(payload.get("job_failure_fingerprints") or {}),
            pipeline_fingerprint=payload.get("pipeline_fingerprint"),
            last_send_error=payload.get("last_send_error"),
        )

    def save(self, store: DatasetStore) -> Path:
        return store.write_json(
            ALERT_STATE_PATH,
            {
                "error_fingerprint": self.error_fingerprint,
                "error_last_sent_at": self.error_last_sent_at,
                "quarantine_files": self.quarantine_files,
                "heartbeat_date": self.heartbeat_date,
                "job_failure_fingerprints": self.job_failure_fingerprints,
                "pipeline_fingerprint": self.pipeline_fingerprint,
                "last_send_error": self.last_send_error,
            },
        )


@dataclass(frozen=True, slots=True)
class AlertMessage:
    kind: str  # error | recovered | quarantine | job_failures | pipeline | heartbeat | test
    text: str


def _fingerprint(items: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode("utf-8")).hexdigest()[:16]


def _format_lines(title: str, lines: list[str], limit: int = 8) -> str:
    shown = [f"- {line}" for line in lines[:limit]]
    if len(lines) > limit:
        shown.append(f"- ... {len(lines) - limit} more")
    return "\n".join([title, *shown])


def _local_now(current: datetime, timezone_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return current.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return current.astimezone()


def consecutive_job_failures(
    state: StateStore, threshold: int = CONSECUTIVE_FAILURE_THRESHOLD
) -> dict[str, dict[str, Any]]:
    """Jobs whose most recent ``threshold`` finished runs all failed."""
    rows = state.connection.execute(
        """
        SELECT job_name, status, error, finished_at, id FROM job_runs
        WHERE status IN ('succeeded', 'failed')
        ORDER BY id DESC
        """
    ).fetchall()
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        bucket = grouped.setdefault(row["job_name"], [])
        if len(bucket) < threshold:
            bucket.append(row)
    result: dict[str, dict[str, Any]] = {}
    for job_name, latest in grouped.items():
        if len(latest) >= threshold and all(row["status"] == "failed" for row in latest):
            result[job_name] = {
                "count": threshold,
                "last_error": latest[0]["error"] or "",
                "last_run_id": latest[0]["id"],
            }
    return result


def evaluate_health_alerts(
    report: dict[str, Any],
    alert_state: AlertState,
    config: AlertConfig,
    now: datetime,
    job_failures: dict[str, dict[str, Any]] | None = None,
) -> list[AlertMessage]:
    """Decide which messages a watchdog run should send. Mutates ``alert_state``."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    messages: list[AlertMessage] = []
    errors = [str(item) for item in report.get("errors", [])]
    status = report.get("status", "unknown")

    # 1. Health errors: send on change, repeat every ``repeat_hours``, and announce recovery.
    if status == "error" and errors:
        fingerprint = _fingerprint(errors)
        due_for_repeat = False
        if alert_state.error_last_sent_at:
            try:
                last = datetime.fromisoformat(alert_state.error_last_sent_at)
                due_for_repeat = now - last >= timedelta(hours=config.repeat_hours)
            except ValueError:
                due_for_repeat = True
        if fingerprint != alert_state.error_fingerprint or due_for_repeat:
            prefix = (
                "🔴 watchdog ERROR"
                if fingerprint != alert_state.error_fingerprint
                else "🔴 watchdog still failing"
            )
            messages.append(AlertMessage("error", _format_lines(prefix, errors)))
            alert_state.error_fingerprint = fingerprint
            alert_state.error_last_sent_at = now.isoformat()
    elif alert_state.error_fingerprint is not None:
        messages.append(AlertMessage("recovered", f"🟢 watchdog recovered: status={status}"))
        alert_state.error_fingerprint = None
        alert_state.error_last_sent_at = None

    # 2. Quarantine growth.
    quarantine_files = report.get("inventory", {}).get("quarantine_files")
    if isinstance(quarantine_files, int):
        previous = alert_state.quarantine_files
        if previous is not None and quarantine_files > previous:
            messages.append(
                AlertMessage(
                    "quarantine",
                    f"🟠 quarantine grew: {previous} → {quarantine_files} files "
                    "(validation rejected rows; see data/quarantine)",
                )
            )
        alert_state.quarantine_files = quarantine_files

    # 3. Consecutive job failures (same job failed ``threshold`` times in a row).
    for job_name, detail in sorted((job_failures or {}).items()):
        fingerprint = _fingerprint([job_name, str(detail.get("last_run_id"))])
        if alert_state.job_failure_fingerprints.get(job_name) == fingerprint:
            continue
        alert_state.job_failure_fingerprints[job_name] = fingerprint
        messages.append(
            AlertMessage(
                "job_failures",
                f"🟠 job {job_name} failed {detail.get('count')}x in a row: "
                f"{str(detail.get('last_error', ''))[:300]}",
            )
        )
    for job_name in list(alert_state.job_failure_fingerprints):
        if job_name not in (job_failures or {}):
            del alert_state.job_failure_fingerprints[job_name]

    # 4. Daily heartbeat after ``heartbeat_hour`` local time.
    local = _local_now(now, config.heartbeat_timezone)
    today = local.date().isoformat()
    if local.hour >= config.heartbeat_hour and alert_state.heartbeat_date != today:
        markets = report.get("markets", {})
        market_lines = [
            f"{market}: watermark {info.get('watermark')} "
            f"coverage {float(info.get('coverage', 0)):.0%}"
            for market, info in sorted(markets.items())
        ]
        options = report.get("options", {})
        option_watermark = str(options.get("watermark") or "")[:10] or "none"
        icon = {"ok": "🟢", "degraded": "🟡"}.get(status, "🔴")
        text = "\n".join(
            [
                f"{icon} heartbeat {today}: status={status}",
                *market_lines,
                f"options: {option_watermark}",
                f"quarantine files: {quarantine_files if quarantine_files is not None else 'n/a'}",
                f"build: {report.get('build_sha') or 'unknown'}",
            ]
        )
        messages.append(AlertMessage("heartbeat", text))
        alert_state.heartbeat_date = today
    return messages


def evaluate_pipeline_alert(report: dict[str, Any], alert_state: AlertState) -> list[AlertMessage]:
    """run-due failures: send when the error set changes, clear on success."""
    errors = [str(item) for item in report.get("errors", [])]
    if report.get("status") == "ok" or not errors:
        alert_state.pipeline_fingerprint = None
        return []
    fingerprint = _fingerprint(errors)
    if fingerprint == alert_state.pipeline_fingerprint:
        return []
    alert_state.pipeline_fingerprint = fingerprint
    return [AlertMessage("pipeline", _format_lines("🔴 pipeline run-due failed", errors))]


# ------------------------------------------------------------------------ dispatch


def dispatch(
    store: DatasetStore,
    config: AlertConfig,
    messages: list[AlertMessage],
    alert_state: AlertState,
    notifier: TelegramNotifier | None = None,
) -> list[dict[str, Any]]:
    """Send messages, persist state, never raise. Returns a per-message outcome log."""
    outcomes: list[dict[str, Any]] = []
    try:
        if messages and config.configured:
            client = notifier or TelegramNotifier(config)
            for message in messages:
                result = client.send(message.text)
                outcomes.append({"kind": message.kind, "ok": result.ok, "error": result.error})
                if not result.ok:
                    LOGGER.warning("telegram send failed (%s): %s", message.kind, result.error)
                    alert_state.last_send_error = f"{message.kind}: {result.error}"
                else:
                    alert_state.last_send_error = None
        elif messages:
            outcomes.extend(
                {"kind": message.kind, "ok": False, "error": "alerts not configured"}
                for message in messages
            )
        alert_state.save(store)
    except Exception as exc:  # alerting must never break the calling job
        LOGGER.warning("alert dispatch failed: %s: %s", type(exc).__name__, exc)
        outcomes.append({"kind": "dispatch", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return outcomes


def notify_health_report(
    store: DatasetStore,
    state: StateStore,
    report: dict[str, Any],
    now: datetime | None = None,
    config: AlertConfig | None = None,
    notifier: TelegramNotifier | None = None,
) -> dict[str, Any]:
    """Entry point for the watchdog. Returns a summary suitable for the health JSON."""
    current = now or datetime.now(timezone.utc)
    resolved = config or load_alert_config(store.root)
    if not resolved.configured:
        # Do not advance de-duplication state while nothing can be delivered.
        return {
            "configured": False,
            "config_path": resolved.source_path,
            "sent": [],
            "skipped": "alerts not configured",
        }
    try:
        alert_state = AlertState.load(store)
        failures = consecutive_job_failures(state)
        messages = evaluate_health_alerts(report, alert_state, resolved, current, failures)
        outcomes = dispatch(store, resolved, messages, alert_state, notifier)
    except Exception as exc:
        LOGGER.warning("health alert evaluation failed: %s: %s", type(exc).__name__, exc)
        return {
            "configured": resolved.configured,
            "sent": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "configured": resolved.configured,
        "config_path": resolved.source_path,
        "sent": outcomes,
    }


def notify_pipeline_report(
    store: DatasetStore,
    report: dict[str, Any],
    config: AlertConfig | None = None,
    notifier: TelegramNotifier | None = None,
) -> dict[str, Any]:
    """Entry point for run-due."""
    resolved = config or load_alert_config(store.root)
    if not resolved.configured:
        return {
            "configured": False,
            "config_path": resolved.source_path,
            "sent": [],
            "skipped": "alerts not configured",
        }
    try:
        alert_state = AlertState.load(store)
        messages = evaluate_pipeline_alert(report, alert_state)
        outcomes = dispatch(store, resolved, messages, alert_state, notifier)
    except Exception as exc:
        LOGGER.warning("pipeline alert evaluation failed: %s: %s", type(exc).__name__, exc)
        return {
            "configured": resolved.configured,
            "sent": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "configured": resolved.configured,
        "config_path": resolved.source_path,
        "sent": outcomes,
    }


def send_test_message(
    store: DatasetStore,
    text: str | None = None,
    config: AlertConfig | None = None,
    notifier: TelegramNotifier | None = None,
) -> dict[str, Any]:
    resolved = config or load_alert_config(store.root)
    if not resolved.configured:
        return {
            "ok": False,
            "configured": False,
            "expected_config_path": str(default_config_path(store.root)),
            "error": "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in alerts.env or the environment",
        }
    client = notifier or TelegramNotifier(resolved)
    stamp = datetime.now(timezone.utc).isoformat()
    body = text or f"🔔 quant-workbench alert test from {store.root} at {stamp}"
    result = client.send(body)
    return {
        "ok": result.ok,
        "configured": True,
        "config_path": resolved.source_path,
        "message_id": result.message_id,
        "error": result.error,
    }
