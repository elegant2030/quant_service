"""Create a deterministic current cross-section and options research report."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from math import sqrt
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from quant_workbench.data.adjust import apply_adjustment
from quant_workbench.derivatives.options import OptionType, black_scholes

LAKE = Path("data/lake")
REPORTS = Path("reports")
CACHE = Path("data/cache/current_analysis")
BACKTEST_RESULTS = Path("data/cache/strategy_run_2026-09-13/results.json")


def number(value: Any) -> float:
    try:
        result = float(value)
        return result if result == result else 0.0
    except (TypeError, ValueError):
        return 0.0


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def load_bars() -> pd.DataFrame:
    pattern = str(LAKE / "canonical" / "dataset=daily_bars" / "**" / "*.parquet")
    connection = duckdb.connect()
    try:
        frame = connection.execute(
            """
            SELECT market, symbol, name, sector, session_date, close, volume, adj_factor,
                   schema_version
            FROM read_parquet(?, union_by_name=true)
            ORDER BY market, symbol, session_date
            """,
            [pattern],
        ).df()
    finally:
        connection.close()
    # Canonical bars are raw (schema 2); analysis uses forward-adjusted prices so the
    # series ends at the live quote. Legacy schema-1 rows are already adjusted.
    frame = apply_adjustment(frame, mode="forward")
    frame["close_raw"] = frame["close"]
    frame["close"] = frame["close_adj"]
    frame["volume"] = frame["volume_adj"]
    return frame


def max_drawdown(values: pd.Series) -> float:
    peak = values.cummax()
    return number((values / peak - 1).min())


def analyze_market(frame: pd.DataFrame) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for symbol, history in frame.groupby("symbol", sort=False):
        history = history.sort_values("session_date")
        close = history["close"].astype(float).reset_index(drop=True)
        volume = history["volume"].astype(float).reset_index(drop=True)
        if len(close) < 121:
            continue
        returns = close.pct_change().dropna()
        latest = history.iloc[-1]
        records.append(
            {
                "symbol": symbol,
                "name": latest["name"],
                "sector": latest["sector"] or "未分类",
                "close": number(close.iloc[-1]),
                "return_20": number(close.iloc[-1] / close.iloc[-21] - 1),
                "return_60": number(close.iloc[-1] / close.iloc[-61] - 1),
                "return_120": number(close.iloc[-1] / close.iloc[-121] - 1),
                "volatility_60": number(returns.iloc[-60:].std(ddof=0) * sqrt(252)),
                "drawdown_252": max_drawdown(close.iloc[-252:]),
                "above_ma20": bool(close.iloc[-1] > close.iloc[-20:].mean()),
                "above_ma60": bool(close.iloc[-1] > close.iloc[-60:].mean()),
                "above_ma120": bool(close.iloc[-1] > close.iloc[-120:].mean()),
                "average_turnover_20": number((close.iloc[-20:] * volume.iloc[-20:]).mean()),
                "history_rows": len(close),
            }
        )
    cross = pd.DataFrame(records)
    if cross.empty:
        raise RuntimeError("no symbols have enough history")

    for column, weight in (
        ("return_20", 0.20),
        ("return_60", 0.35),
        ("return_120", 0.45),
    ):
        cross[f"rank_{column}"] = cross[column].rank(pct=True) * weight
    cross["risk_penalty"] = cross["volatility_60"].rank(pct=True) * 0.15
    cross["composite_score"] = (
        cross["rank_return_20"]
        + cross["rank_return_60"]
        + cross["rank_return_120"]
        - cross["risk_penalty"]
    )

    market = str(frame["market"].iloc[0])
    liquidity_floor = 10_000_000 if market == "us" else 50_000_000
    eligible = cross[
        (cross["return_20"] > 0)
        & (cross["return_60"] > 0)
        & (cross["return_120"] > 0)
        & (cross["volatility_60"] < 0.60)
        & (cross["average_turnover_20"] >= liquidity_floor)
    ].copy()
    candidates = eligible.nlargest(15, "composite_score")
    low_volatility = cross[
        (cross["return_120"] > 0) & (cross["average_turnover_20"] >= liquidity_floor)
    ].nsmallest(10, "volatility_60")

    sector_table = (
        cross.groupby("sector")
        .agg(
            symbols=("symbol", "count"),
            return_20=("return_20", "mean"),
            return_60=("return_60", "mean"),
            breadth_60=("above_ma60", "mean"),
            volatility_60=("volatility_60", "mean"),
        )
        .reset_index()
    )
    sector_table = sector_table[sector_table["symbols"] >= 3].copy()
    sector_table["score"] = (
        sector_table["return_20"].rank(pct=True) * 0.4
        + sector_table["return_60"].rank(pct=True) * 0.4
        + sector_table["breadth_60"].rank(pct=True) * 0.2
    )
    sector_table = sector_table.sort_values("score", ascending=False)

    breadth = {
        "above_ma20": number(cross["above_ma20"].mean()),
        "above_ma60": number(cross["above_ma60"].mean()),
        "above_ma120": number(cross["above_ma120"].mean()),
        "positive_20": number((cross["return_20"] > 0).mean()),
        "positive_60": number((cross["return_60"] > 0).mean()),
        "positive_120": number((cross["return_120"] > 0).mean()),
    }
    breadth_60 = breadth["above_ma60"]
    regime = "偏多" if breadth_60 >= 0.65 else "中性" if breadth_60 >= 0.45 else "防守"

    columns = [
        "symbol",
        "name",
        "sector",
        "close",
        "return_20",
        "return_60",
        "return_120",
        "volatility_60",
        "drawdown_252",
        "average_turnover_20",
        "composite_score",
    ]
    return {
        "market": market,
        "as_of": str(frame["session_date"].max()),
        "symbols_analyzed": len(cross),
        "regime": regime,
        "breadth": breadth,
        "median_return": {
            str(horizon): number(cross[f"return_{horizon}"].median()) for horizon in (20, 60, 120)
        },
        "median_volatility_60": number(cross["volatility_60"].median()),
        "candidates": candidates[columns].to_dict(orient="records"),
        "low_volatility": low_volatility[columns].to_dict(orient="records"),
        "strong_sectors": sector_table.head(6).to_dict(orient="records"),
        "weak_sectors": sector_table.tail(4).iloc[::-1].to_dict(orient="records"),
    }


def load_options() -> pd.DataFrame:
    pattern = str(LAKE / "canonical" / "dataset=option_chain" / "**" / "*.parquet")
    connection = duckdb.connect()
    try:
        return connection.execute(
            "SELECT * FROM read_parquet(?, union_by_name=true)", [pattern]
        ).df()
    finally:
        connection.close()


def analyze_option_symbol(
    frame: pd.DataFrame, as_of: date, rate: float = 0.03913
) -> dict[str, Any]:
    expirations = sorted(frame["expiration"].dropna().unique())
    expiration = min(
        expirations,
        key=lambda value: abs((date.fromisoformat(str(value)) - as_of).days - 45),
    )
    chain = frame[frame["expiration"] == expiration].copy()
    chain["mid"] = (chain["bid"].fillna(0) + chain["ask"].fillna(0)) / 2
    chain["spread"] = (chain["ask"] - chain["bid"]) / chain["mid"].replace(0, pd.NA)
    liquid = chain[
        (chain["bid"] > 0) & (chain["ask"] >= chain["bid"]) & (chain["spread"] <= 0.35)
    ].copy()
    spot = number(chain["underlying_price"].iloc[0])
    dte = (date.fromisoformat(str(expiration)) - as_of).days
    years = max(dte / 365, 1 / 365)
    calls = liquid[liquid["option_type"] == "call"].copy()
    puts = liquid[liquid["option_type"] == "put"].copy()
    common_strikes = sorted(set(calls["strike"]) & set(puts["strike"]))
    atm_strike = min(common_strikes, key=lambda strike: abs(number(strike) - spot))
    atm_call = calls[calls["strike"] == atm_strike].iloc[0]
    atm_put = puts[puts["strike"] == atm_strike].iloc[0]

    def delta(row: pd.Series, kind: OptionType) -> float:
        volatility = max(number(row["implied_volatility"]), 0.01)
        return black_scholes(spot, number(row["strike"]), years, rate, volatility, kind).delta

    calls["delta"] = calls.apply(lambda row: delta(row, OptionType.CALL), axis=1)
    puts["delta"] = puts.apply(lambda row: delta(row, OptionType.PUT), axis=1)
    call_25 = calls.iloc[(calls["delta"] - 0.25).abs().argsort()[:1]].iloc[0]
    put_25 = puts.iloc[(puts["delta"] + 0.25).abs().argsort()[:1]].iloc[0]
    call_mid = number(atm_call["mid"])
    put_mid = number(atm_put["mid"])
    total_call_oi = number(calls["open_interest"].fillna(0).sum())
    total_put_oi = number(puts["open_interest"].fillna(0).sum())
    total_call_volume = number(calls["volume"].fillna(0).sum())
    total_put_volume = number(puts["volume"].fillna(0).sum())
    return {
        "symbol": str(frame["symbol"].iloc[0]),
        "spot": spot,
        "expiration": str(expiration),
        "dte": dte,
        "contracts": len(chain),
        "liquid_contracts": len(liquid),
        "liquid_ratio": len(liquid) / len(chain) if len(chain) else 0,
        "atm_strike": number(atm_strike),
        "atm_call_mid": call_mid,
        "atm_put_mid": put_mid,
        "straddle_cost": call_mid + put_mid,
        "implied_move": (call_mid + put_mid) / spot,
        "atm_call_iv": number(atm_call["implied_volatility"]),
        "atm_put_iv": number(atm_put["implied_volatility"]),
        "put_25_delta_iv": number(put_25["implied_volatility"]),
        "call_25_delta_iv": number(call_25["implied_volatility"]),
        "skew_25_delta": number(put_25["implied_volatility"])
        - number(call_25["implied_volatility"]),
        "put_call_open_interest": total_put_oi / total_call_oi if total_call_oi else None,
        "put_call_volume": total_put_volume / total_call_volume if total_call_volume else None,
        "median_spread": number(liquid["spread"].median()),
    }


def option_analysis(frame: pd.DataFrame, as_of: date) -> dict[str, Any]:
    return {
        symbol: analyze_option_symbol(group, as_of) for symbol, group in frame.groupby("symbol")
    }


def candidate_table(items: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 股票 | 名称 | 行业 | 20日 | 60日 | 120日 | 年化波动 | 252日回撤 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in items:
        lines.append(
            f"| {item['symbol']} | {item['name']} | {item['sector']} | "
            f"{pct(item['return_20'])} | {pct(item['return_60'])} | "
            f"{pct(item['return_120'])} | {pct(item['volatility_60'])} | "
            f"{pct(item['drawdown_252'])} |"
        )
    return lines


def sector_table(items: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 行业 | 股票数 | 20日 | 60日 | 60日均线以上 | 年化波动 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in items:
        lines.append(
            f"| {item['sector']} | {item['symbols']} | {pct(item['return_20'])} | "
            f"{pct(item['return_60'])} | {pct(item['breadth_60'])} | "
            f"{pct(item['volatility_60'])} |"
        )
    return lines


def write_report(result: dict[str, Any]) -> Path:
    lines = [
        "# 当前双市场量化与期权分析",
        "",
        f"生成时间：{result['generated_at']}",
        "",
        "> 当前截面研究，不是交易指令。免费行情用于原型，股票池存在幸存者偏差。",
        "",
    ]
    for market, title in (("us", "美股"), ("cn", "A股")):
        item = result["markets"][market]
        breadth = item["breadth"]
        lines.extend(
            [
                f"## {title}：{item['regime']}状态",
                "",
                f"数据截至 {item['as_of']}，分析 {item['symbols_analyzed']} 只；"
                "20/60/120日均线上方比例分别为 "
                f"{pct(breadth['above_ma20'])}、{pct(breadth['above_ma60'])}、{pct(breadth['above_ma120'])}。",
                "",
                f"横截面中位收益：20日 {pct(item['median_return']['20'])}，"
                f"60日 {pct(item['median_return']['60'])}，"
                f"120日 {pct(item['median_return']['120'])}；"
                f"60日中位年化波动 {pct(item['median_volatility_60'])}。",
                "",
                "### 强势行业",
                "",
                *sector_table(item["strong_sectors"]),
                "",
                "### 当前一致趋势候选",
                "",
                *candidate_table(item["candidates"]),
                "",
            ]
        )

    lines.extend(
        [
            "## 期权市场",
            "",
            f"快照日期：{result['option_snapshot_date']}。以下仅使用约45天到期的一档完整链。",
            "",
        ]
    )
    for symbol, item in result["options"].items():
        lines.extend(
            [
                f"### {symbol}（{item['expiration']}，DTE {item['dte']}）",
                "",
                f"现价 {item['spot']:.2f}，ATM {item['atm_strike']:.0f} "
                f"跨式中间价 {item['straddle_cost']:.2f}，"
                f"跨式价格对应到期盈亏平衡幅度约 ±{pct(item['implied_move'])}。",
                f"ATM Call/Put IV 为 {pct(item['atm_call_iv'])}/"
                f"{pct(item['atm_put_iv'])}；"
                f"25Δ Put-Call 偏斜 {pct(item['skew_25_delta'])}。",
                f"Put/Call 持仓量比 {item['put_call_open_interest']:.2f}，"
                f"成交量比 {item['put_call_volume']:.2f}；"
                f"液态合约 {item['liquid_contracts']}/{item['contracts']}，"
                f"中位报价宽度 {pct(item['median_spread'])}。",
                "",
            ]
        )

    backtest = result["backtest"]
    lines.extend(
        [
            "## 综合策略判断",
            "",
            "- 美股历史回测中月度等权年化 "
            f"{pct(backtest['us']['equal_weight_monthly']['annualized_return'])}、"
            f"Sharpe {backtest['us']['equal_weight_monthly']['sharpe']:.2f}，"
            "仍作为低换手核心基准。",
            "- A股120日月频动量年化 "
            f"{pct(backtest['cn']['momentum_120_monthly']['annualized_return'])}、"
            "总换手 "
            f"{backtest['cn']['momentum_120_monthly']['turnover_multiple']:.1f} 倍，"
            "比周频20日动量更适合作为当前研究信号。",
            "- 当前候选要求20/60/120日收益同时为正，并通过流动性和波动率过滤；"
            "候选池用于下一步组合回测，不直接下单。",
            "- 期权没有历史IV百分位，因此当前只能判断绝对隐含波动、偏斜和流动性，"
            "不能断言期权昂贵或便宜。",
            "- 期权Delta使用Black–Scholes近似、3.913%无风险利率且未单独估计股息；"
            "Put/Call比率不能单独解释为看空方向。",
            "",
        ]
    )
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"CURRENT_MARKET_ANALYSIS_{result['analysis_date']}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    bars = load_bars()
    markets = {market: analyze_market(group.copy()) for market, group in bars.groupby("market")}
    analysis_date = max(date.fromisoformat(item["as_of"]) for item in markets.values())
    option_frame = load_options()
    option_snapshot_date = pd.to_datetime(option_frame["retrieved_at"], utc=True).max().date()
    options = option_analysis(option_frame, option_snapshot_date)
    backtest = json.loads(BACKTEST_RESULTS.read_text(encoding="utf-8"))["strategies"]
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_date": analysis_date.isoformat(),
        "option_snapshot_date": option_snapshot_date.isoformat(),
        "markets": markets,
        "options": options,
        "backtest": backtest,
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    output = CACHE / f"analysis_{analysis_date.isoformat()}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report = write_report(result)
    print(json.dumps({"json": str(output), "report": str(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
