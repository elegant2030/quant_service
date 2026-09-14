"""Point-in-time company news and announcement ingestion.

The adapters deliberately retain the publisher timestamp, retrieval timestamp and
original URL.  Title/summary keyword scoring is deterministic; it is a first-pass
research signal and must not be represented as an LLM interpretation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from quant_workbench.store import DatasetStore, StateStore

DATASET = "events"
SCHEMA_VERSION = 2
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")

POSITIVE_TERMS = (
    "增持",
    "回购",
    "中标",
    "签订",
    "大额订单",
    "预增",
    "扭亏",
    "上调",
    "分红",
    "派息",
    "获批",
    "投产",
    "突破",
    "beat",
    "beats",
    "raise guidance",
    "raised guidance",
    "upgrade",
    "buyback",
    "wins contract",
    "approval",
    "record revenue",
)
NEGATIVE_TERMS = (
    "减持",
    "立案",
    "处罚",
    "诉讼",
    "亏损",
    "预减",
    "下调",
    "终止",
    "退市",
    "停产",
    "事故",
    "召回",
    "违约",
    "风险提示",
    "调查",
    "misses",
    "missed estimates",
    "lower guidance",
    "cuts guidance",
    "downgrade",
    "lawsuit",
    "investigation",
    "recall",
    "default",
    "bankruptcy",
    "layoffs",
)

US_MACRO_PROXIES = ("SPY", "QQQ", "TLT")
EXPECTATION_TERMS = (
    "expect",
    "forecast",
    "likely",
    "could",
    "may ",
    "might",
    "odds",
    "bets",
    "priced in",
    "ahead",
    "预期",
    "预计",
    "或将",
    "可能",
)
CENTRAL_BANK_TERMS = (
    "federal reserve",
    "fomc",
    "fed meeting",
    "fed rate",
    "rate hike",
    "rate cut",
    "interest rate decision",
    "加息",
    "降息",
    "美联储",
    "央行",
)
MACRO_DATA_TERMS = (
    "cpi",
    "ppi",
    "inflation",
    "nonfarm",
    "payrolls",
    "unemployment",
    "gdp",
    "pmi",
    "通胀",
    "非农",
    "失业率",
    "居民消费价格",
)
CONFIRMED_MONETARY_TERMS = (
    "fed raises rates",
    "fed hikes rates",
    "fomc raises rates",
    "fed cuts rates",
    "fomc cuts rates",
    "decision to raise rates",
    "decision to cut rates",
    "announces rate increase",
    "announces rate cut",
    "宣布加息",
    "宣布降息",
    "决定加息",
    "决定降息",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value).strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return _utc(parsed)


def _material_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_id(source: str, source_id: str, url: str, title: str) -> str:
    if source_id:
        return f"{source}:{source_id}"
    return f"{source}:{hashlib.sha256(f'{url}|{title}'.encode()).hexdigest()[:24]}"


def classify_event_text(title: str, summary: str = "") -> dict[str, Any]:
    """Classify title/summary, preserving forecast versus confirmed macro facts."""
    text = f"{title} {summary}".lower()
    expectation = any(term in text for term in EXPECTATION_TERMS)
    central_bank = any(term in text for term in CENTRAL_BANK_TERMS)
    macro_data = any(term in text for term in MACRO_DATA_TERMS)
    if central_bank or macro_data:
        subtype = "central_bank" if central_bank else "data_release"
        direction = 0
        if central_bank:
            if any(term in text for term in ("rate hike", "hikes", "raises rates", "加息")):
                direction = -1
            elif any(term in text for term in ("rate cut", "cuts rates", "降息")):
                direction = 1
        elif any(term in text for term in ("hotter", "accelerat", "above forecast", "超预期")):
            direction = -1
        elif any(term in text for term in ("cooler", "eas", "below forecast", "低于预期")):
            direction = 1
        confirmed = any(term in text for term in CONFIRMED_MONETARY_TERMS)
        certainty = "likely" if central_bank and not confirmed else "confirmed"
        if expectation:
            certainty = "likely"
        return {
            "event_type": "macro",
            "subtype": subtype,
            "direction": direction,
            "magnitude": "none",
            "novelty": 1.0,
            "scope": "market",
            "horizon": "quarter" if central_bank else "days",
            "certainty": certainty,
            "affected_driver": "multiple" if central_bank else "margin",
            "maps_to_estimate": False,
            "confidence": 0.75 if central_bank else 0.65,
            "matched_terms": [
                term
                for term in (*CENTRAL_BANK_TERMS, *MACRO_DATA_TERMS)
                if term in text
            ][:6],
            "scoring_method": "deterministic_macro_taxonomy_v2",
            "taxonomy_version": 2,
        }
    positive = [term for term in POSITIVE_TERMS if term in text]
    negative = [term for term in NEGATIVE_TERMS if term in text]
    raw_direction = len(positive) - len(negative)
    direction = max(-2, min(2, raw_direction))
    subtype = "other_corporate"
    mappings = (
        (("业绩", "earnings", "revenue", "profit"), "earnings_release"),
        (("指引", "guidance"), "guidance_change"),
        (("回购", "buyback"), "buyback"),
        (("增持", "减持", "insider", "stake"), "insider_holding"),
        (("分红", "派息", "dividend"), "dividend"),
        (("中标", "订单", "contract"), "product_order"),
        (("并购", "收购", "重组", "merger", "acquisition"), "ma_announce"),
        (("诉讼", "处罚", "立案", "调查", "lawsuit", "investigation"), "litigation_regulatory"),
        (("董事", "总经理", "ceo", "cfo", "management"), "management_change"),
        (("退市", "停牌", "复牌", "delist", "halt"), "trading_halt"),
        (("增发", "可转债", "配股", "offering", "convertible"), "capital_raise"),
    )
    for terms, candidate in mappings:
        if any(term in text for term in terms):
            subtype = candidate
            break
    matched = positive + negative
    return {
        "event_type": "corporate",
        "subtype": subtype,
        "direction": direction,
        "magnitude": "none",
        "novelty": 1.0,
        "scope": "single",
        "horizon": "quarter" if subtype in {"earnings_release", "guidance_change"} else "days",
        "certainty": "confirmed" if direction or subtype != "other_corporate" else "likely",
        "affected_driver": "none",
        "maps_to_estimate": False,
        "confidence": 0.70 if matched else 0.35,
        "matched_terms": matched[:6],
        "scoring_method": "deterministic_title_summary_keywords_v1",
        "taxonomy_version": 1,
    }


def _classify(title: str, summary: str = "") -> dict[str, Any]:
    """Backward-compatible internal alias."""
    return classify_event_text(title, summary)


def _tradable_at(market: str, published_at: datetime) -> datetime:
    """Conservative next-session/open approximation used by the event layer."""
    local_tz = NEW_YORK if market == "us" else SHANGHAI
    opening = time(9, 30)
    closing = time(16, 0) if market == "us" else time(15, 0)
    local = published_at.astimezone(local_tz)
    day = local.date()
    if local.weekday() >= 5 or local.time() >= closing:
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        local = datetime.combine(day, opening, tzinfo=local_tz)
    elif local.time() < opening:
        local = datetime.combine(day, opening, tzinfo=local_tz)
    return local.astimezone(timezone.utc)


def validate_event_row(row: dict[str, Any]) -> list[str]:
    required = {
        "event_id",
        "source",
        "source_symbol",
        "symbol",
        "market",
        "title",
        "url",
        "published_at",
        "retrieved_at",
        "effective_at",
        "tradable_at",
        "material_sha256",
        "schema_version",
    }
    errors = [f"missing:{name}" for name in sorted(required) if row.get(name) in (None, "")]
    try:
        effective = _parse_datetime(row["effective_at"])
        retrieved = _parse_datetime(row["retrieved_at"])
        published = _parse_datetime(row["published_at"])
        tradable = _parse_datetime(row["tradable_at"])
        if effective > retrieved:
            errors.append("effective_after_retrieved")
        if published > retrieved + timedelta(minutes=5):
            errors.append("published_in_future")
        if tradable < effective:
            errors.append("tradable_before_effective")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_timestamp")
    if not str(row.get("url", "")).startswith(("http://", "https://")):
        errors.append("invalid_url")
    direction = row.get("direction")
    if not isinstance(direction, int) or direction < -2 or direction > 2:
        errors.append("invalid_direction")
    if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("material_sha256", ""))):
        errors.append("invalid_material_sha256")
    return errors


class YFinanceNewsProvider:
    def __init__(self, fetcher: Callable[[str], list[dict[str, Any]]] | None = None) -> None:
        self.fetcher = fetcher or self._fetch

    @staticmethod
    def _fetch(symbol: str) -> list[dict[str, Any]]:
        import yfinance as yf

        return list(yf.Ticker(symbol).get_news(count=20, tab="all") or [])

    def fetch(
        self, symbol: str, since: datetime, retrieved_at: datetime
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw = self.fetcher(symbol)
        rows: list[dict[str, Any]] = []
        for item in raw:
            content = item.get("content") or item
            published_value = content.get("pubDate") or content.get("providerPublishTime")
            try:
                if isinstance(published_value, (int, float)):
                    published = datetime.fromtimestamp(published_value, tz=timezone.utc)
                else:
                    published = _parse_datetime(published_value)
            except (TypeError, ValueError):
                continue
            if published < since or published > retrieved_at + timedelta(minutes=5):
                continue
            finance = content.get("finance") or {}
            tickers = {
                str(value.get("symbol", "")).upper()
                for value in finance.get("stockTickers") or []
            }
            if tickers and symbol.upper() not in tickers:
                continue
            title = str(content.get("title") or "").strip()
            summary = str(content.get("summary") or content.get("description") or "").strip()
            canonical = content.get("canonicalUrl") or {}
            click = content.get("clickThroughUrl") or {}
            url = str(canonical.get("url") or click.get("url") or content.get("link") or "")
            if not title or not url:
                continue
            provider = content.get("provider") or {}
            source_id = str(content.get("id") or item.get("id") or "")
            score = _classify(title, summary)
            payload = {
                "title": title,
                "summary": summary,
                "url": url,
                "published_at": published.isoformat(),
            }
            rows.append(
                {
                    "event_id": _event_id("yfinance_news", source_id, url, title),
                    "source": "yfinance_news",
                    "source_symbol": symbol,
                    "symbol": symbol,
                    "market": "us",
                    "title": title,
                    "summary": summary,
                    "publisher": str(
                        provider.get("displayName") or provider.get("sourceId") or "unknown"
                    ),
                    "url": url,
                    "published_at": published.isoformat(),
                    "retrieved_at": retrieved_at.isoformat(),
                    "effective_at": published.isoformat(),
                    "tradable_at": _tradable_at("us", published).isoformat(),
                    "material_sha256": _material_hash(payload),
                    "evidence": title[:40],
                    "schema_version": SCHEMA_VERSION,
                    **score,
                }
            )
        return rows, raw


class CninfoAnnouncementProvider:
    def __init__(self, fetcher: Callable[..., Any] | None = None) -> None:
        self.fetcher = fetcher or self._fetch

    @staticmethod
    def _fetch(**kwargs: Any) -> Any:
        import akshare as ak

        return ak.stock_zh_a_disclosure_report_cninfo(**kwargs)

    def fetch(
        self, symbol: str, since: datetime, retrieved_at: datetime
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        start = since.astimezone(SHANGHAI).strftime("%Y%m%d")
        end = retrieved_at.astimezone(SHANGHAI).strftime("%Y%m%d")
        frame = self.fetcher(
            symbol=symbol,
            market="沪深京",
            keyword="",
            category="",
            start_date=start,
            end_date=end,
        )
        raw = frame.to_dict(orient="records")
        rows: list[dict[str, Any]] = []
        for item in raw:
            title = str(item.get("公告标题") or "").strip()
            url = str(item.get("公告链接") or "").strip()
            value = str(item.get("公告时间") or "").strip().split(" ", 1)[0]
            try:
                published_day = date.fromisoformat(value)
            except ValueError:
                continue
            published = datetime.combine(published_day, time.min, tzinfo=SHANGHAI).astimezone(
                timezone.utc
            )
            # CNInfo only exposes a date in this endpoint. Treat it as known no earlier
            # than the following local midnight, capped at retrieval for same-day pulls.
            effective = datetime.combine(
                published_day + timedelta(days=1), time.min, tzinfo=SHANGHAI
            ).astimezone(timezone.utc)
            effective = min(effective, retrieved_at)
            query = parse_qs(urlparse(url).query)
            announcement_id = str((query.get("announcementId") or [""])[0])
            score = _classify(title)
            payload = {"title": title, "url": url, "published_at": published.isoformat()}
            rows.append(
                {
                    "event_id": _event_id("cninfo", announcement_id, url, title),
                    "source": "cninfo",
                    "source_symbol": symbol,
                    "symbol": symbol,
                    "market": "cn",
                    "title": title,
                    "summary": "",
                    "publisher": "巨潮资讯",
                    "url": url,
                    "published_at": published.isoformat(),
                    "retrieved_at": retrieved_at.isoformat(),
                    "effective_at": effective.isoformat(),
                    "tradable_at": _tradable_at("cn", effective).isoformat(),
                    "material_sha256": _material_hash(payload),
                    "evidence": title[:40],
                    "schema_version": SCHEMA_VERSION,
                    **score,
                }
            )
        return rows, raw


def _universe(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def ingest_events(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    run_date: date,
    *,
    symbols: list[str] | None = None,
    limit: int | None = None,
    lookback_days: int = 14,
    provider: YFinanceNewsProvider | CninfoAnnouncementProvider | None = None,
    batch_key: str | None = None,
) -> dict[str, Any]:
    if market not in {"us", "cn"}:
        raise ValueError(f"unsupported market: {market}")
    members = _universe(universe_path)
    if symbols:
        wanted = {symbol.upper() for symbol in symbols}
        members = [row for row in members if row["symbol"].upper() in wanted]
    elif market == "us":
        existing = {row["symbol"].upper() for row in members}
        members.extend(
            {
                "symbol": symbol,
                "exchange": "ARCX",
                "name": f"Macro proxy {symbol}",
                "sector": "Market Proxy",
            }
            for symbol in US_MACRO_PROXIES
            if symbol not in existing
        )
    if limit:
        members = members[:limit]
    identity = (
        f"{market}:{batch_key or run_date.isoformat()}:{lookback_days}:"
        f"{','.join(row['symbol'] for row in members)}"
    )
    lease = state.acquire_job("ingest_events", identity, {"market": market})
    if not lease.acquired:
        return {"acquired": False, "reason": lease.reason, "rows": 0, "errors": []}
    assert lease.run_id is not None
    source = "yfinance_news" if market == "us" else "cninfo"
    adapter = provider or (
        YFinanceNewsProvider() if market == "us" else CninfoAnnouncementProvider()
    )
    retrieved = datetime.now(timezone.utc)
    since = retrieved - timedelta(days=lookback_days)
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    errors: list[str] = []
    succeeded = 0
    try:
        for member in members:
            symbol = member["symbol"]
            try:
                current, payload = adapter.fetch(symbol, since, retrieved)
                rows.extend(current)
                raw[symbol] = payload
                succeeded += 1
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        success_rate = succeeded / max(len(members), 1)
        raw_path = store.write_raw(
            source,
            DATASET,
            market,
            run_date,
            raw,
            f"{source}-{run_date.isoformat()}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
            {"symbols": len(members), "success_rate": success_rate, "errors": errors[:50]},
        )
        if success_rate < 0.80:
            raise RuntimeError(f"event source success rate {success_rate:.1%} below 80%")
        # A story may be returned for several queried symbols. Keep one row per natural
        # event/symbol key within this immutable part.
        unique = {(row["event_id"], row["symbol"]): row for row in rows}
        rows = list(unique.values())
        result = (
            store.write_rows(
                DATASET,
                market,
                source,
                run_date,
                rows,
                f"{source}-{run_date.isoformat()}-{hashlib.sha256(identity.encode()).hexdigest()[:12]}",
                schema_version=SCHEMA_VERSION,
                validator=validate_event_row,
            )
            if rows
            else None
        )
        for symbol in {row["symbol"] for row in rows}:
            latest = max(row["effective_at"] for row in rows if row["symbol"] == symbol)
            state.set_watermark(source, DATASET, market, latest, symbol)
        state.record_source_success(source)
        state.complete_job(
            lease.run_id,
            len(rows),
            {"errors": errors[:50], "success_rate": success_rate, "raw_path": str(raw_path)},
        )
        return {
            "acquired": True,
            "reason": lease.reason,
            "rows": len(rows),
            "requested": len(members),
            "successful_symbols": succeeded,
            "errors": errors,
            "path": str(result.path) if result else None,
            "raw_path": str(raw_path),
        }
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise


def run_due_events(
    store: DatasetStore,
    state: StateStore,
    universe_directory: Path,
    now: datetime | None = None,
    *,
    lookback_days: int = 14,
) -> dict[str, Any]:
    """Refresh before each report window, once per market/stage/day."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    schedules: dict[str, tuple[ZoneInfo, tuple[tuple[str, time], ...]]] = {
        "cn": (
            SHANGHAI,
            (("premarket", time(8, 0)), ("midday", time(10, 30)), ("postmarket", time(14, 30))),
        ),
        "us": (
            NEW_YORK,
            (("premarket", time(7, 30)), ("midday", time(11, 30)), ("postmarket", time(15, 30))),
        ),
    }
    output: dict[str, Any] = {"status": "ok", "results": {}, "errors": []}
    for market, (market_timezone, stages) in schedules.items():
        local = current.astimezone(market_timezone)
        due = [stage for stage, cutoff in stages if local.time() >= cutoff]
        if local.weekday() >= 5 or not due:
            output["results"][market] = {"due": False, "local_time": local.isoformat()}
            continue
        stage = due[-1]
        try:
            result = ingest_events(
                store,
                state,
                market,
                universe_directory / f"universe_{market}.csv",
                local.date(),
                lookback_days=lookback_days,
                batch_key=f"{local.date().isoformat()}:{stage}",
            )
            output["results"][market] = {"due": True, "stage": stage, **result}
        except Exception as exc:
            output["status"] = "error"
            message = f"{market}: {type(exc).__name__}: {exc}"
            output["errors"].append(message)
            output["results"][market] = {"due": True, "error": message}
    store.write_json("health/last-events-run.json", output)
    return output
