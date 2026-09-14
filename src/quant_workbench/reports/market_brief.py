"""Three-times-daily cross-market opportunity and heat reports.

The score is a deterministic research ranking, not a trading instruction.  Completed
daily bars remain the source of truth.  A best-effort Yahoo intraday snapshot may
overlay the US cross-section; the snapshot is stored as a research artifact and never
silently promoted into canonical data.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from quant_workbench.ai.market_report import (
    MarketReportGPTClient,
    generate_gpt_analysis,
    render_gpt_markdown,
)
from quant_workbench.ops.alert import TelegramNotifier, load_alert_config
from quant_workbench.store import DatasetStore

NEW_YORK = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_STAGES: dict[str, dict[str, tuple[str, time]]] = {
    "us": {
        "premarket": ("盘前", time(8, 30)),
        "midday": ("盘中", time(12, 30)),
        "postmarket": ("盘后", time(16, 30)),
    },
    "cn": {
        "premarket": ("盘前", time(9, 0)),
        "midday": ("盘中", time(11, 30)),
        "postmarket": ("盘后", time(15, 30)),
    },
}
MARKET_NAMES = {"us": "美股", "cn": "A股"}
MARKET_TIMEZONES = {"us": NEW_YORK, "cn": SHANGHAI}
MARKET_CALENDARS = {"us": "XNYS", "cn": "XSHG"}


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    market: str
    stage: str
    json_path: Path
    markdown_path: Path
    sent: bool
    send_error: str | None
    gpt_json_path: Path | None = None
    gpt_markdown_path: Path | None = None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def _is_market_session(market: str, day: date) -> bool:
    try:
        import exchange_calendars as calendars
        import pandas as pd

        calendar = calendars.get_calendar(MARKET_CALENDARS[market])
        return bool(calendar.is_session(pd.Timestamp(day)))
    except ImportError:
        return day.weekday() < 5


def due_report_stages(market: str, now: datetime) -> list[str]:
    """Return report stages whose local cut-off has passed on a trading day."""
    if market not in REPORT_STAGES:
        raise ValueError(f"unsupported market: {market}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(MARKET_TIMEZONES[market])
    if not _is_market_session(market, local.date()):
        return []
    return [
        key for key, (_, cutoff) in REPORT_STAGES[market].items() if local.time() >= cutoff
    ]


def _load_daily_bars(store: DatasetStore) -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("Install ops dependencies: pip install -e '.[ops]'") from exc
    files = list((store.root / "canonical" / "dataset=daily_bars").rglob("*.parquet"))
    if not files:
        raise RuntimeError("daily_bars canonical dataset is empty")
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT market, symbol, name, sector, session_date, close, volume
            FROM read_parquet(?, union_by_name=true)
            ORDER BY market, symbol, session_date
            """,
            [str(store.root / "canonical" / "dataset=daily_bars" / "**" / "*.parquet")],
        ).df()
    finally:
        connection.close()


def fetch_us_intraday_snapshot(symbols: list[str]) -> dict[str, Any]:
    """Fetch a best-effort latest-price snapshot; callers must tolerate total failure."""
    import pandas as pd
    import yfinance as yf

    retrieved = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for offset in range(0, len(symbols), 60):
        chunk = symbols[offset : offset + 60]
        try:
            # yfinance prints one line per failed ticker; retain structured errors below
            # without flooding a long-running launchd log.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                frame = yf.download(
                    chunk,
                    period="5d",
                    interval="5m",
                    auto_adjust=True,
                    actions=False,
                    prepost=True,
                    repair=False,
                    progress=False,
                    group_by="ticker",
                    threads=True,
                    multi_level_index=True,
                )
            for symbol in chunk:
                try:
                    if getattr(frame.columns, "nlevels", 1) == 1:
                        part = frame
                    elif symbol in frame.columns.get_level_values(0):
                        part = frame[symbol]
                    else:
                        part = frame.xs(symbol, axis=1, level=1)
                    close = part["Close"].dropna()
                    if close.empty:
                        raise ValueError("no intraday close")
                    last_index = close.index[-1]
                    latest_day = pd.Timestamp(last_index).date()
                    volume = part.loc[pd.to_datetime(part.index).date == latest_day, "Volume"]
                    rows.append(
                        {
                            "symbol": symbol,
                            "price": _number(close.iloc[-1]),
                            "volume": _number(volume.fillna(0).sum()),
                            "quoted_at": pd.Timestamp(last_index).isoformat(),
                        }
                    )
                except Exception as exc:
                    errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
        except Exception as exc:
            errors.extend(f"{symbol}: {type(exc).__name__}: {exc}" for symbol in chunk)
    return {
        "source": "yfinance",
        "retrieved_at": retrieved.isoformat(),
        "rows": rows,
        "errors": errors,
    }


def fetch_cn_intraday_snapshot(symbols: list[str]) -> dict[str, Any]:
    """Fetch one whole-market Eastmoney snapshot through AKShare."""
    import akshare as ak

    retrieved = datetime.now(timezone.utc)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        frame = ak.stock_zh_a_spot_em()
    wanted = set(symbols)
    rows: list[dict[str, Any]] = []
    for _, item in frame.iterrows():
        symbol = str(item.get("代码", "")).zfill(6)
        price = _number(item.get("最新价"))
        if symbol not in wanted or price <= 0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "price": price,
                # Eastmoney reports A-share volume in lots; canonical volume is shares.
                "volume": _number(item.get("成交量")) * 100,
                "quoted_at": retrieved.astimezone(SHANGHAI).isoformat(),
                "provider_return_pct": _number(item.get("涨跌幅")),
            }
        )
    return {
        "source": "akshare_eastmoney",
        "retrieved_at": retrieved.isoformat(),
        "rows": rows,
        "errors": [] if rows else ["AKShare spot snapshot returned no universe members"],
    }


def _cross_section(bars: Any, live: dict[str, Any] | None) -> Any:
    import pandas as pd

    live_rows = (live or {}).get("rows") or []
    live_map = {str(row["symbol"]): row for row in live_rows}
    records: list[dict[str, Any]] = []
    for (market, symbol), history in bars.groupby(["market", "symbol"], sort=False):
        history = history.sort_values("session_date")
        close = history["close"].astype(float).reset_index(drop=True)
        volume = history["volume"].astype(float).reset_index(drop=True)
        if len(close) < 61:
            continue
        latest = history.iloc[-1]
        observed_price = _number(close.iloc[-1])
        observed_volume = _number(volume.iloc[-1])
        live_row = live_map.get(str(symbol))
        live_used = bool(live_row and _number(live_row.get("price")) > 0)
        if live_used:
            observed_price = _number(live_row["price"])
            observed_volume = _number(live_row.get("volume"))
        returns = close.pct_change().dropna()
        average_volume = max(_number(volume.iloc[-20:].mean()), 1.0)
        records.append(
            {
                "market": str(market),
                "symbol": str(symbol),
                "name": str(latest["name"] or symbol),
                "sector": str(latest["sector"] or "未分类"),
                "as_of": str(latest["session_date"]),
                "close": observed_price,
                "return_1": observed_price
                / _number(close.iloc[-1] if live_used else close.iloc[-2], 1.0)
                - 1,
                "return_20": observed_price
                / _number(close.iloc[-20] if live_used else close.iloc[-21], 1.0)
                - 1,
                "return_60": observed_price
                / _number(close.iloc[-60] if live_used else close.iloc[-61], 1.0)
                - 1,
                "volume_ratio": observed_volume / average_volume,
                "breakout_20": observed_price / max(_number(close.iloc[-20:].max()), 1e-9),
                "volatility_20": _number(returns.iloc[-20:].std(ddof=0) * sqrt(252)),
                "above_ma20": bool(observed_price > _number(close.iloc[-20:].mean())),
                "above_ma60": bool(observed_price > _number(close.iloc[-60:].mean())),
                "live_used": live_used,
            }
        )
    cross = pd.DataFrame(records)
    if cross.empty:
        raise RuntimeError("no symbols have 61 completed bars")
    components = {
        "return_1": 0.20,
        "return_20": 0.25,
        "return_60": 0.20,
        "volume_ratio": 0.15,
        "breakout_20": 0.10,
    }
    cross["score"] = 0.0
    for _market, indexes in cross.groupby("market").groups.items():
        for column, weight in components.items():
            cross.loc[indexes, "score"] += cross.loc[indexes, column].rank(pct=True) * weight
        cross.loc[indexes, "score"] += (
            1 - cross.loc[indexes, "volatility_20"].rank(pct=True)
        ) * 0.10
    cross["score"] *= 100
    cross["technical_score"] = cross["score"]
    return cross


def _load_latest_fundamentals(
    store: DatasetStore, market: str, as_of: datetime
) -> Any | None:
    import json

    import pandas as pd

    try:
        import duckdb
    except ImportError:
        return None
    root = store.root / "canonical" / "dataset=fundamental_metrics" / f"market={market}"
    canonical = None
    if list(root.rglob("*.parquet")):
        connection = duckdb.connect()
        try:
            canonical = connection.execute(
                """
                SELECT * EXCLUDE(row_number) FROM (
                  SELECT *, row_number() OVER (
                    PARTITION BY symbol ORDER BY effective_at DESC, retrieved_at DESC
                  ) AS row_number
                  FROM read_parquet(?, union_by_name=true)
                  WHERE effective_at <= ?
                ) WHERE row_number = 1
                """,
                [str(root / "**" / "*.parquet"), as_of.astimezone(timezone.utc).isoformat()],
            ).df()
        finally:
            connection.close()
    research_rows: list[dict[str, Any]] = []
    research_root = store.root / "research" / "fundamentals" / f"market={market}"
    for path in research_root.glob("symbol=*/latest.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            effective = datetime.fromisoformat(str(row["effective_at"]).replace("Z", "+00:00"))
            if effective <= as_of.astimezone(timezone.utc):
                research_rows.append(row)
        except (OSError, ValueError, KeyError):
            continue
    research = pd.DataFrame(research_rows)
    if canonical is None or canonical.empty:
        return research if not research.empty else None
    if research.empty:
        return canonical
    missing = research[~research["symbol"].isin(set(canonical["symbol"]))]
    return pd.concat([canonical, missing], ignore_index=True, sort=False)


def _apply_fundamental_scores(cross: Any, fundamentals: Any | None) -> Any:
    import pandas as pd

    metric_names = [
        "roe",
        "gross_margin",
        "net_margin",
        "cash_conversion",
        "revenue_growth",
        "earnings_growth",
        "debt_to_assets",
    ]
    if fundamentals is None or fundamentals.empty:
        for name in metric_names:
            cross[name] = pd.NA
        for name in ("period_end", "effective_at", "document_id", "source", "evidence_quality"):
            cross[name] = pd.NA
        cross["fundamental_score"] = pd.NA
        cross["fundamental_coverage"] = 0
        return cross
    available = [
        "symbol",
        "period_end",
        "effective_at",
        "document_id",
        "source",
        "evidence_quality",
        *metric_names,
    ]
    available = [column for column in available if column in fundamentals.columns]
    merged = cross.merge(fundamentals[available], on="symbol", how="left")
    for name in ("period_end", "effective_at", "document_id", "source", "evidence_quality"):
        if name not in merged.columns:
            merged[name] = pd.NA
    merged["fundamental_coverage"] = merged[metric_names].notna().sum(axis=1)
    components = []
    for name in metric_names:
        values = pd.to_numeric(merged[name], errors="coerce")
        rank = values.rank(pct=True)
        if name == "debt_to_assets":
            rank = 1 - rank
        components.append(rank)
    merged["fundamental_score"] = pd.concat(components, axis=1).mean(axis=1) * 100
    eligible = merged["fundamental_coverage"] >= 2
    merged.loc[eligible, "score"] = (
        merged.loc[eligible, "technical_score"] * 0.75
        + merged.loc[eligible, "fundamental_score"] * 0.25
    )
    return merged


def _load_recent_events(store: DatasetStore, market: str, as_of: datetime) -> Any | None:
    try:
        import duckdb
    except ImportError:
        return None
    root = store.root / "canonical" / "dataset=events" / f"market={market}"
    if not list(root.rglob("*.parquet")):
        return None
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT * EXCLUDE(row_number) FROM (
              SELECT *, row_number() OVER (
                PARTITION BY event_id, symbol ORDER BY retrieved_at DESC
              ) AS row_number
              FROM read_parquet(?, union_by_name=true)
              WHERE effective_at <= ? AND effective_at >= ?
            ) WHERE row_number = 1
            """,
            [
                str(root / "**" / "*.parquet"),
                as_of.astimezone(timezone.utc).isoformat(),
                (as_of.astimezone(timezone.utc) - timedelta(days=14)).isoformat(),
            ],
        ).df()
    finally:
        connection.close()


def _apply_event_scores(cross: Any, events: Any | None, as_of: datetime) -> Any:
    import pandas as pd

    defaults = {
        "news_event_count": 0,
        "news_directional_count": 0,
        "news_direction": 0.0,
        "news_score": pd.NA,
        "news_top_title": pd.NA,
        "news_top_url": pd.NA,
        "news_top_publisher": pd.NA,
        "news_top_published_at": pd.NA,
        "news_top_subtype": pd.NA,
        "news_scoring_method": pd.NA,
    }
    if events is None or events.empty:
        for name, value in defaults.items():
            cross[name] = value
        cross["pre_news_score"] = cross["score"]
        return cross
    current = as_of.astimezone(timezone.utc)
    frame = events.copy()
    frame["direction"] = pd.to_numeric(frame["direction"], errors="coerce").fillna(0.0)
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    effective = pd.to_datetime(frame["effective_at"], utc=True, errors="coerce")
    age_days = (pd.Timestamp(current) - effective).dt.total_seconds().clip(lower=0) / 86400
    frame["decay"] = 0.5 ** (age_days / 5.0)
    frame["impact"] = frame["direction"] * frame["confidence"] * frame["decay"]
    frame["impact_abs"] = frame["impact"].abs()
    records: list[dict[str, Any]] = []
    for symbol, group in frame.groupby("symbol"):
        group = group.sort_values(
            ["impact_abs", "published_at"], ascending=[False, False]
        )
        top = group.iloc[0]
        denominator = max(float((group["confidence"] * group["decay"]).sum()), 1e-9)
        direction = max(-2.0, min(2.0, float(group["impact"].sum()) / denominator))
        records.append(
            {
                "symbol": str(symbol),
                "news_event_count": int(len(group)),
                "news_directional_count": int((group["direction"] != 0).sum()),
                "news_direction": direction,
                "news_score": 50 + direction * 25,
                "news_top_title": str(top["title"]),
                "news_top_url": str(top["url"]),
                "news_top_publisher": str(top.get("publisher") or top["source"]),
                "news_top_published_at": str(top["published_at"]),
                "news_top_subtype": str(top["subtype"]),
                "news_scoring_method": str(top["scoring_method"]),
            }
        )
    merged = cross.merge(pd.DataFrame(records), on="symbol", how="left")
    for name, value in defaults.items():
        if name in {"news_event_count", "news_directional_count", "news_direction"}:
            merged[name] = pd.to_numeric(merged[name], errors="coerce").fillna(value)
        elif name not in merged:
            merged[name] = value
    merged["pre_news_score"] = merged["score"]
    eligible = merged["news_directional_count"] > 0
    merged.loc[eligible, "score"] = (
        merged.loc[eligible, "pre_news_score"] * 0.85
        + merged.loc[eligible, "news_score"] * 0.15
    )
    return merged


def _stock_reason(row: dict[str, Any]) -> dict[str, Any]:
    reason_types: list[str] = []
    evidence: list[str] = []
    if _number(row["return_20"]) > 0 or _number(row["return_60"]) > 0:
        reason_types.append("技术面")
        evidence.append(
            f"20日{_pct(row['return_20'])}、60日{_pct(row['return_60'])}"
        )
    if _number(row["volume_ratio"]) >= 1.2 or abs(_number(row["return_1"])) >= 0.02:
        reason_types.append("量价面")
        evidence.append(
            f"当日{_pct(row['return_1'])}、量比{_number(row['volume_ratio']):.2f}"
        )
    if _number(row["breakout_20"]) >= 0.98:
        if "技术面" not in reason_types:
            reason_types.append("技术面")
        evidence.append("接近或突破20日高位")
    if _number(row["volatility_20"]) < 0.30:
        reason_types.append("风险调整")
        evidence.append(f"20日年化波动{_number(row['volatility_20']) * 100:.0f}%")
    fundamental_coverage = int(_number(row.get("fundamental_coverage")))
    if fundamental_coverage >= 2:
        reason_types.append("基本面")
        fundamental_bits = []
        if _number(row.get("roe"), float("nan")) == _number(
            row.get("roe"), float("nan")
        ):
            fundamental_bits.append(f"ROE {_number(row['roe']) * 100:.1f}%")
        if _number(row.get("earnings_growth"), float("nan")) == _number(
            row.get("earnings_growth"), float("nan")
        ):
            fundamental_bits.append(f"净利润同比 {_pct(row['earnings_growth'])}")
        if _number(row.get("net_margin"), float("nan")) == _number(
            row.get("net_margin"), float("nan")
        ):
            fundamental_bits.append(f"净利率 {_number(row['net_margin']) * 100:.1f}%")
        evidence.append(
            "基本面" + ("、".join(fundamental_bits[:2]) if fundamental_bits else "横截面得分有效")
        )
    news_event_count = int(_number(row.get("news_event_count")))
    news_direction = _number(row.get("news_direction"))
    if news_event_count:
        reason_types.append("消息面")
        direction_text = (
            "偏多" if news_direction > 0.15 else "偏空" if news_direction < -0.15 else "中性"
        )
        evidence.append(f"消息面{direction_text}，近14日{news_event_count}条")
    if not reason_types:
        reason_types.append("相对强度")
        evidence.append("市场内综合排名靠前")
    return {
        "basis": reason_types,
        "summary": "；".join(evidence[:4]),
        "fundamental": (
            f"已纳入{'（研究快照）' if row.get('source') == 'yfinance_research' else ''}："
            f"{fundamental_coverage}项指标，得分"
            f"{_number(row.get('fundamental_score')):.1f}"
            if fundamental_coverage >= 2
            else "未纳入：该股票尚无足够的PIT财务数据"
        ),
        "news": (
            f"已纳入（规则评分）：{direction_text}；"
            f"{str(row.get('news_top_publisher') or '未知来源')}；"
            f"{str(row.get('news_top_title') or '').replace('|', ' ')[:80]}；"
            f"[原文]({row.get('news_top_url')})"
            if news_event_count
            else "未纳入：近14日无可审计事件"
        ),
    }


def _sector_reason(row: dict[str, Any]) -> dict[str, Any]:
    basis = ["技术面"]
    if abs(_number(row["return_1"])) >= 0.01:
        basis.append("量价面")
    fundamental_members = int(_number(row.get("fundamental_members")))
    if fundamental_members:
        basis.append("基本面")
    news_members = int(_number(row.get("news_members")))
    if news_members:
        basis.append("消息面")
    return {
        "basis": basis,
        "summary": (
            f"板块当日{_pct(row['return_1'])}、20日{_pct(row['return_20'])}，"
            f"MA20上方占比{_number(row['breadth_20']) * 100:.0f}%"
            + (f"，{fundamental_members}只含基本面" if fundamental_members else "")
            + (f"，{news_members}只有近期事件" if news_members else "")
        ),
        "fundamental": "已纳入" if fundamental_members else "未纳入",
        "news": "已纳入" if news_members else "未纳入",
    }


def _select_rankings(cross: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sector = (
        cross.groupby(["market", "sector"])
        .agg(
            members=("symbol", "count"),
            score=("score", "mean"),
            breadth_20=("above_ma20", "mean"),
            breadth_60=("above_ma60", "mean"),
            return_1=("return_1", "mean"),
            return_20=("return_20", "mean"),
            fundamental_members=(
                "fundamental_coverage",
                lambda values: int((values >= 2).sum()),
            ),
            fundamental_score=("fundamental_score", "mean"),
            news_members=("news_event_count", lambda values: int((values > 0).sum())),
            news_score=("news_score", "mean"),
        )
        .reset_index()
    )
    eligible = sector[sector["members"] >= 3].copy()
    if len(eligible) < 5:
        eligible = sector.copy()
    eligible["heat_score"] = (
        eligible["score"] * 0.55
        + eligible["breadth_20"] * 20
        + eligible["breadth_60"] * 15
        + eligible.groupby("market")["return_1"].rank(pct=True) * 10
    )
    top_sectors = eligible.nlargest(5, "heat_score")
    keys = set(zip(top_sectors["market"], top_sectors["sector"], strict=False))
    pool = cross[
        cross.apply(lambda row: (row["market"], row["sector"]) in keys, axis=1)
    ].sort_values("score", ascending=False)
    selected_indexes: list[int] = []
    counts: dict[tuple[str, str], int] = {}
    for index, row in pool.iterrows():
        key = (str(row["market"]), str(row["sector"]))
        if counts.get(key, 0) >= 5:
            continue
        selected_indexes.append(index)
        counts[key] = counts.get(key, 0) + 1
        if len(selected_indexes) == 15:
            break
    if len(selected_indexes) < 15:
        for index in cross.sort_values("score", ascending=False).index:
            if index not in selected_indexes:
                selected_indexes.append(index)
            if len(selected_indexes) == 15:
                break
    columns = [
        "market",
        "symbol",
        "name",
        "sector",
        "as_of",
        "close",
        "return_1",
        "return_20",
        "return_60",
        "volume_ratio",
        "breakout_20",
        "volatility_20",
        "above_ma20",
        "above_ma60",
        "score",
        "live_used",
        "technical_score",
        "fundamental_score",
        "fundamental_coverage",
        "period_end",
        "effective_at",
        "document_id",
        "source",
        "evidence_quality",
        "roe",
        "gross_margin",
        "net_margin",
        "cash_conversion",
        "revenue_growth",
        "earnings_growth",
        "debt_to_assets",
        "pre_news_score",
        "news_event_count",
        "news_directional_count",
        "news_direction",
        "news_score",
        "news_top_title",
        "news_top_url",
        "news_top_publisher",
        "news_top_published_at",
        "news_top_subtype",
        "news_scoring_method",
    ]
    sector_rows = top_sectors.to_dict(orient="records")
    stock_rows = cross.loc[selected_indexes, columns].to_dict(orient="records")
    for row in sector_rows:
        row["reason"] = _sector_reason(row)
    for row in stock_rows:
        row["reason"] = _stock_reason(row)
    return sector_rows, stock_rows


def _option_pulse(store: DatasetStore) -> list[dict[str, Any]]:
    try:
        import duckdb
    except ImportError:
        return []
    files = list((store.root / "canonical" / "dataset=option_chain").rglob("*.parquet"))
    if not files:
        return []
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            WITH snapshots AS (
              SELECT *, max(effective_at) OVER (PARTITION BY symbol) AS latest_effective_at
              FROM read_parquet(?, union_by_name=true)
            )
            SELECT symbol, max(effective_at) AS effective_at,
              sum(CASE WHEN option_type='put' THEN open_interest ELSE 0 END) /
                nullif(sum(CASE WHEN option_type='call' THEN open_interest ELSE 0 END), 0)
                AS put_call_oi,
              sum(CASE WHEN option_type='put' THEN volume ELSE 0 END) /
                nullif(sum(CASE WHEN option_type='call' THEN volume ELSE 0 END), 0)
                AS put_call_volume,
              median(implied_volatility) AS median_iv
            FROM snapshots
            WHERE effective_at = latest_effective_at
            GROUP BY symbol ORDER BY symbol
            """,
            [str(store.root / "canonical" / "dataset=option_chain" / "**" / "*.parquet")],
        ).fetchall()
        return [
            {
                "symbol": row[0],
                "effective_at": row[1],
                "put_call_oi": _number(row[2]),
                "put_call_volume": _number(row[3]),
                "median_iv": _number(row[4]),
            }
            for row in rows
        ]
    finally:
        connection.close()


def _pct(value: Any) -> str:
    return f"{_number(value) * 100:+.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    fundamental_symbols = report["evidence_coverage"]["fundamental"]["symbols"]
    news_symbols = report["evidence_coverage"]["news"]["symbols"]
    lines = [
        f"# Quant Workbench {report['market_name']}{report['stage_name']}报告",
        "",
        f"生成时间：{report['generated_at_local']}（{report['timezone']}）",
        f"数据模式：{report['data_mode']}；完成日线截至 {report['data_as_of']}",
        "",
        "> 这是量化研究候选清单，不是交易指令。热度分数衡量相对趋势、量能、突破与风险，"
        "不代表未来收益。",
        (
            f"> 基本面已对 {fundamental_symbols} 只股票参与评分；其余股票会逐只标明数据不足。"
            if fundamental_symbols
            else "> 当前尚无可用的 PIT 财务数据，本版不会冒充基本面结论。"
        ),
        (
            f"> 消息面已纳入近14日可追溯事件，覆盖 {news_symbols} 只股票；"
            "评分仍由确定性规则完成，GPT 只负责解释。"
            if news_symbols
            else "> 近14日没有可用事件，本版不会冒充消息面结论。"
        ),
        "",
        "## 热度板块 TOP 5",
        "",
        "| 排名 | 板块 | 热度 | 成分数 | 推荐依据 | 具体理由 |",
        "|---:|---|---:|---:|---|---|",
    ]
    for rank, row in enumerate(report["sectors"], 1):
        lines.append(
            f"| {rank} | {row['sector']} | {_number(row['heat_score']):.1f} | "
            f"{int(row['members'])} | {'+'.join(row['reason']['basis'])} | "
            f"{row['reason']['summary']} |"
        )
    lines.extend(
        [
            "",
            "## 候选股票 TOP 15",
            "",
            "| 排名 | 股票 | 名称 | 板块 | 分数 | 推荐依据 | 具体理由 | 基本面 | 消息面 |",
            "|---:|---|---|---|---:|---|---|---|---|",
        ]
    )
    for rank, row in enumerate(report["stocks"], 1):
        lines.append(
            f"| {rank} | {row['symbol']} | {row['name']} | {row['sector']} | "
            f"{_number(row['score']):.1f} | {'+'.join(row['reason']['basis'])} | "
            f"{row['reason']['summary']} | {row['reason']['fundamental']} | "
            f"{row['reason']['news']} |"
        )
    if report["options"]:
        lines.extend(["", "## 期权温度（市场级）", ""])
        for row in report["options"]:
            lines.append(
                f"- {row['symbol']}：Put/Call OI {_number(row['put_call_oi']):.2f}，"
                f"Put/Call 成交量 {_number(row['put_call_volume']):.2f}，"
                f"中位 IV {_number(row['median_iv']) * 100:.1f}%（{row['effective_at']}）"
            )
    lines.extend(
        [
            "",
            "## 使用限制",
            "",
            "免费数据源没有生产 SLA；盘中快照失败会自动退回最近已完成日线。"
            "候选池存在幸存者偏差；标题规则无法替代公告原文或人工复核，执行前仍需检查"
            "公告、财报、流动性、停牌/涨跌停和期权价差。",
            "",
        ]
    )
    if "gpt_analysis" in report:
        lines.extend(["", "---", "", render_gpt_markdown(report["gpt_analysis"], report)])
    return "\n".join(lines)


def render_telegram(report: dict[str, Any]) -> str:
    fundamental_symbols = report["evidence_coverage"]["fundamental"]["symbols"]
    news_symbols = report["evidence_coverage"]["news"]["symbols"]
    lines = [
        f"Quant Workbench {report['market_name']}{report['stage_name']} | "
        f"{report['report_date']} {report['timezone']}",
        f"模式: {report['data_mode']} | 日线 {report['data_as_of']}",
        f"依据: 技术/量价/风险调整 + 基本面({fundamental_symbols}只) "
        f"+ 消息面({news_symbols}只，规则评分)",
        "",
        "热度板块 TOP5",
    ]
    for rank, row in enumerate(report["sectors"], 1):
        lines.append(
            f"{rank}. {row['sector']} {_number(row['heat_score']):.1f} "
            f"[{'/'.join(row['reason']['basis'])}] {row['reason']['summary']}"
        )
    lines.extend(["", "候选股票 TOP15"])
    for rank, row in enumerate(report["stocks"], 1):
        lines.append(
            f"{rank}. {row['symbol']} {row['name']} {row['score']:.1f} "
            f"[{'/'.join(row['reason']['basis'])}] {row['reason']['summary']}"
        )
    if report["options"]:
        pulse = " / ".join(
            f"{row['symbol']} P/C-OI {_number(row['put_call_oi']):.2f} IV "
            f"{_number(row['median_iv']) * 100:.0f}%"
            for row in report["options"]
        )
        lines.extend(["", f"期权温度: {pulse}"])
    gpt = report.get("gpt_analysis") or {}
    analysis = gpt.get("analysis") or {}
    if gpt.get("status") == "completed" and analysis:
        lines.extend(
            [
                "",
                f"GPT解读({gpt.get('provider')}/{gpt.get('model')}): "
                f"{analysis['executive_summary']}",
            ]
        )
        priorities = [
            f"{item['symbol']}({item['research_priority']})"
            for item in analysis.get("stock_reports", [])
            if item.get("research_priority") == "高"
        ][:5]
        if priorities:
            lines.append("优先复核: " + " / ".join(priorities))
    elif gpt.get("status") in {"disabled", "failed"}:
        lines.extend(["", f"GPT解读: {gpt['status']}（确定性报告不受影响）"])
    lines.extend(["", "研究候选，不是交易指令；详版已保存本地。"])
    return "\n".join(lines)


def build_market_brief(
    store: DatasetStore,
    market: str,
    stage: str,
    now: datetime | None = None,
    fetch_live: bool = True,
    live_fetcher: Callable[[list[str]], dict[str, Any]] | None = None,
    send: bool = True,
    generate_gpt: bool = True,
    gpt_model: str | None = None,
    gpt_client: MarketReportGPTClient | None = None,
) -> tuple[dict[str, Any], ReportArtifacts]:
    if market not in REPORT_STAGES:
        raise ValueError(f"unsupported market: {market}")
    if stage not in REPORT_STAGES[market]:
        raise ValueError(f"unsupported report stage: {stage}")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    market_timezone = MARKET_TIMEZONES[market]
    local = current.astimezone(market_timezone)
    bars = _load_daily_bars(store)
    bars = bars[bars["market"] == market].copy()
    if bars.empty:
        raise RuntimeError(f"daily bars are empty for {market}")
    live: dict[str, Any] | None = None
    should_fetch_live = fetch_live and not (market == "cn" and stage == "premarket")
    if should_fetch_live:
        symbols = sorted(bars["symbol"].unique().tolist())
        try:
            default_fetcher = (
                fetch_us_intraday_snapshot if market == "us" else fetch_cn_intraday_snapshot
            )
            live = (live_fetcher or default_fetcher)(symbols)
            snapshot_name = current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            store.write_json(f"reports/snapshots/{market}-{snapshot_name}.json", live)
        except Exception as exc:
            live = {
                "source": "yfinance" if market == "us" else "akshare_eastmoney",
                "rows": [],
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
    usable_live = live
    if live and live.get("rows"):
        usable_rows: list[dict[str, Any]] = []
        stale_rows = 0
        for row in live["rows"]:
            try:
                quoted = datetime.fromisoformat(str(row["quoted_at"]))
                if quoted.tzinfo is None:
                    quoted = quoted.replace(tzinfo=market_timezone)
                if quoted.astimezone(market_timezone).date() == local.date():
                    usable_rows.append(row)
                else:
                    stale_rows += 1
            except (KeyError, ValueError):
                stale_rows += 1
        usable_live = {**live, "rows": usable_rows}
        if stale_rows:
            usable_live["errors"] = [
                *((live.get("errors") or [])),
                f"ignored {stale_rows} stale or invalid intraday rows",
            ]
    cross = _cross_section(bars, usable_live)
    fundamentals = _load_latest_fundamentals(store, market, current)
    cross = _apply_fundamental_scores(cross, fundamentals)
    events = _load_recent_events(store, market, current)
    cross = _apply_event_scores(cross, events, current)
    sectors, stocks = _select_rankings(cross)
    data_as_of = str(bars["session_date"].max())
    live_count = len((usable_live or {}).get("rows") or [])
    if live_count:
        data_mode = f"日线 + {MARKET_NAMES[market]}实时快照({live_count})"
    elif market == "cn" and stage == "premarket":
        data_mode = "最新完成日线（盘前）"
    else:
        data_mode = "已完成日线（实时快照不可用）"
    report = {
        "schema_version": 4,
        "market": market,
        "market_name": MARKET_NAMES[market],
        "timezone": str(market_timezone),
        "report_date": local.date().isoformat(),
        "stage": stage,
        "stage_name": REPORT_STAGES[market][stage][0],
        "generated_at": current.astimezone(timezone.utc).isoformat(),
        "generated_at_local": local.isoformat(),
        "data_mode": data_mode,
        "data_as_of": data_as_of,
        "symbols_analyzed": int(len(cross)),
        "scoring": {
            "return_1": 0.20,
            "return_20": 0.25,
            "return_60": 0.20,
            "volume_ratio": 0.15,
            "breakout_20": 0.10,
            "low_volatility": 0.10,
            "fundamental_overlay": 0.25,
            "news_overlay": 0.15,
        },
        "sectors": sectors,
        "stocks": stocks,
        "options": _option_pulse(store) if market == "us" else [],
        "evidence_coverage": {
            "technical_and_price_volume": "included",
            "fundamental": {
                "status": "included_when_available",
                "symbols": int((cross["fundamental_coverage"] >= 2).sum()),
                "weight": 0.25,
            },
            "news": {
                "status": "included_when_available",
                "symbols": int((cross["news_event_count"] > 0).sum()),
                "directional_symbols": int((cross["news_directional_count"] > 0).sum()),
                "lookback_days": 14,
                "weight": 0.15,
                "method": "deterministic_title_summary_keywords_v1",
            },
            "options": "market_level_only" if market == "us" else "not_applicable",
            "llm": {
                "role": "interpretation_only",
                "ranking_and_scores": "deterministic",
                "status": "pending" if generate_gpt else "disabled_by_command",
            },
        },
        "live_snapshot": {
            "retrieved_at": (usable_live or {}).get("retrieved_at"),
            "rows": live_count,
            "errors": ((usable_live or {}).get("errors") or [])[:10],
        },
        "disclaimer": "量化研究候选，不是交易指令。",
    }
    base = Path("reports") / "market" / market / report["report_date"] / stage
    if generate_gpt:
        report["gpt_analysis"] = generate_gpt_analysis(
            report, store.root, model=gpt_model, client=gpt_client
        )
    else:
        report["gpt_analysis"] = {
            "status": "disabled",
            "provider": "disabled_by_command",
            "reason": "disabled by command; GPT was not called",
            "model": gpt_model,
            "prompt_version": None,
            "material_sha256": None,
            "analysis": None,
        }
    report["evidence_coverage"]["llm"] = {
        "role": "interpretation_only",
        "ranking_and_scores": "deterministic",
        "status": report["gpt_analysis"]["status"],
        "provider": report["gpt_analysis"].get("provider"),
        "model": report["gpt_analysis"].get("model"),
        "prompt_version": report["gpt_analysis"].get("prompt_version"),
        "material_sha256": report["gpt_analysis"].get("material_sha256"),
    }
    prior_delivery: dict[str, Any] = {}
    existing_json_path = store.root / base.with_suffix(".json")
    if existing_json_path.is_file():
        try:
            prior_delivery = json.loads(existing_json_path.read_text(encoding="utf-8")).get(
                "delivery", {}
            )
        except (OSError, ValueError):
            prior_delivery = {}
    sent = bool(prior_delivery.get("sent"))
    send_error: str | None = prior_delivery.get("error")
    attempted_at = prior_delivery.get("attempted_at")
    if send and not sent:
        config = load_alert_config(store.root)
        result = TelegramNotifier(config).send(render_telegram(report))
        sent, send_error = result.ok, result.error
        attempted_at = current.astimezone(timezone.utc).isoformat()
    report["delivery"] = {
        "attempted": bool(send or prior_delivery.get("attempted")),
        "sent": sent,
        "error": send_error,
        "attempted_at": attempted_at,
    }
    json_path = store.write_json(base.with_suffix(".json"), report)
    markdown_path = store.write_text(base.with_suffix(".md"), render_markdown(report))
    gpt_base = base.with_name(f"{stage}-gpt")
    gpt_json_path = store.write_json(gpt_base.with_suffix(".json"), report["gpt_analysis"])
    gpt_markdown_path = store.write_text(
        gpt_base.with_suffix(".md"), render_gpt_markdown(report["gpt_analysis"], report)
    )
    artifacts = ReportArtifacts(
        market,
        stage,
        json_path,
        markdown_path,
        sent,
        send_error,
        gpt_json_path,
        gpt_markdown_path,
    )
    return report, artifacts


def run_due_market_briefs(
    store: DatasetStore,
    now: datetime | None = None,
    fetch_live: bool = True,
    send: bool = True,
    generate_gpt: bool = True,
    gpt_model: str | None = None,
) -> dict[str, Any]:
    """Generate every due, missing report so wake-from-sleep runs catch up safely."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    output: dict[str, Any] = {
        "status": "ok",
        "report_dates": {
            market: current.astimezone(MARKET_TIMEZONES[market]).date().isoformat()
            for market in REPORT_STAGES
        },
        "due": {market: due_report_stages(market, current) for market in REPORT_STAGES},
        "generated": [],
        "skipped": [],
        "delivery_retries": [],
        "errors": [],
    }
    cached_live: dict[str, dict[str, Any]] = {}
    for market in REPORT_STAGES:
        report_date = output["report_dates"][market]

        def fetch_once(symbols: list[str], current_market: str = market) -> dict[str, Any]:
            if current_market not in cached_live:
                fetcher = (
                    fetch_us_intraday_snapshot
                    if current_market == "us"
                    else fetch_cn_intraday_snapshot
                )
                cached_live[current_market] = fetcher(symbols)
            return cached_live[current_market]

        for stage in output["due"][market]:
            path = (
                store.root
                / "reports"
                / "market"
                / market
                / report_date
                / f"{stage}.json"
            )
            if path.is_file():
                if send:
                    try:
                        prior = json.loads(path.read_text(encoding="utf-8"))
                        if not (prior.get("delivery") or {}).get("sent"):
                            result = TelegramNotifier(load_alert_config(store.root)).send(
                                render_telegram(prior)
                            )
                            prior["delivery"] = {
                                "attempted": True,
                                "sent": result.ok,
                                "error": result.error,
                                "attempted_at": current.astimezone(timezone.utc).isoformat(),
                            }
                            store.write_json(path, prior)
                            output["delivery_retries"].append(
                                {
                                    "market": market,
                                    "stage": stage,
                                    "sent": result.ok,
                                    "error": result.error,
                                }
                            )
                            continue
                    except Exception as exc:
                        output["errors"].append(
                            f"{market}/{stage} delivery retry: {type(exc).__name__}: {exc}"
                        )
                output["skipped"].append(
                    {"market": market, "stage": stage, "reason": "already_generated"}
                )
                continue
            try:
                report, artifacts = build_market_brief(
                    store,
                    market,
                    stage,
                    current,
                    fetch_live=fetch_live,
                    live_fetcher=fetch_once if fetch_live else None,
                    send=send,
                    generate_gpt=generate_gpt,
                    gpt_model=gpt_model,
                )
                output["generated"].append(
                    {
                        "market": market,
                        "stage": stage,
                        "json_path": str(artifacts.json_path),
                        "markdown_path": str(artifacts.markdown_path),
                        "gpt_status": report["gpt_analysis"]["status"],
                        "gpt_json_path": str(artifacts.gpt_json_path),
                        "gpt_markdown_path": str(artifacts.gpt_markdown_path),
                        "sent": artifacts.sent,
                        "send_error": artifacts.send_error,
                        "sectors": len(report["sectors"]),
                        "stocks": len(report["stocks"]),
                    }
                )
            except Exception as exc:
                output["status"] = "error"
                output["errors"].append(f"{market}/{stage}: {type(exc).__name__}: {exc}")
    store.write_json("health/last-market-reports-run.json", output)
    return output
