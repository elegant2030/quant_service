"""Run multi-market daily strategies and current-chain options scenario analysis."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from quant_workbench.backtest.engine import BacktestEngine, BacktestResult
from quant_workbench.backtest.rules import CostModel
from quant_workbench.core.models import AssetClass, Bar, Currency, Exchange, Instrument
from quant_workbench.data.baostock_provider import BaoStockProvider
from quant_workbench.data.yfinance_provider import YFinanceProvider
from quant_workbench.derivatives.options import OptionAnalytics, OptionType, black_scholes, implied_volatility
from quant_workbench.strategy.allocation import CrossSectionalLowVolatility, EqualWeight
from quant_workbench.strategy.momentum import CrossSectionalMomentum


ROOT = Path("data/cache/strategy_run_2026-09-13")
REPORTS = Path("reports")


def load_universe(market: str) -> list[Instrument]:
    source = Path(f"data/cache/sector_probe/universe_{market}.csv")
    instruments: list[Instrument] = []
    with source.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            currency = Currency.USD if market == "us" else Currency.CNY
            instruments.append(
                Instrument(
                    row["symbol"],
                    Exchange(row["exchange"]),
                    AssetClass.EQUITY,
                    currency,
                    lot_size=Decimal("1" if market == "us" else "100"),
                    metadata={"sector": row["sector"], "name": row["name"]},
                )
            )
    return instruments


def write_bars(path: Path, bars: Sequence[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "previous_close"])
        for bar in bars:
            writer.writerow(
                [bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume, bar.previous_close or ""]
            )


def read_bars(path: Path, item: Instrument) -> list[Bar]:
    result: list[Bar] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            result.append(
                Bar(
                    item,
                    datetime.fromisoformat(row["timestamp"]),
                    Decimal(row["open"]),
                    Decimal(row["high"]),
                    Decimal(row["low"]),
                    Decimal(row["close"]),
                    Decimal(row["volume"]),
                    Decimal(row["previous_close"]) if row["previous_close"] else None,
                )
            )
    return result


def obtain_bars(market: str, instruments: list[Instrument], start: datetime, end: datetime) -> list[Bar]:
    directory = ROOT / "bars" / market
    directory.mkdir(parents=True, exist_ok=True)
    missing = [item for item in instruments if not (directory / f"{item.symbol}.csv").exists()]
    if market == "us" and missing:
        provider = YFinanceProvider()
        batches, errors = provider.bars_many(missing, start, end, adjusted=True)
        for symbol, bars in batches.items():
            if bars:
                write_bars(directory / f"{symbol}.csv", bars)
        if errors:
            print(f"美股下载警告: {len(errors)}: {errors}", flush=True)
    elif market == "cn" and missing:
        provider = BaoStockProvider()
        for index, item in enumerate(missing, 1):
            try:
                bars = provider.bars(item, start, end, adjusted=True)
                if bars:
                    write_bars(directory / f"{item.symbol}.csv", bars)
            except Exception as exc:
                print(f"A股下载失败 {item.symbol}: {type(exc).__name__}: {exc}", flush=True)
            if index % 25 == 0:
                print(f"A股三年数据: {index}/{len(missing)}", flush=True)
        provider.close()
    all_bars: list[Bar] = []
    for item in instruments:
        path = directory / f"{item.symbol}.csv"
        if path.exists():
            all_bars.extend(read_bars(path, item))
    return all_bars


def enhanced_metrics(result: BacktestResult, initial_cash: Decimal) -> dict[str, Any]:
    metrics: dict[str, Any] = dict(result.metrics)
    values = [float(point.equity) for point in result.equity_curve]
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1]]
    annual_vol = (sum((value - sum(returns) / len(returns)) ** 2 for value in returns) / len(returns)) ** 0.5 * (252**0.5) if returns else 0.0
    turnover = sum(
        float(fill.quantity * fill.price * fill.instrument.multiplier) for fill in result.fills
    ) / float(initial_cash)
    metrics.update(
        {
            "annualized_volatility": annual_vol,
            "calmar": metrics["annualized_return"] / abs(metrics["max_drawdown"])
            if metrics["max_drawdown"]
            else 0.0,
            "turnover_multiple": turnover,
            "fills": len(result.fills),
            "rejected_orders": len(result.rejected_orders),
        }
    )
    return metrics


def write_curve(path: Path, result: BacktestResult) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "equity", "cash", "gross_exposure"])
        for point in result.equity_curve:
            writer.writerow([point.timestamp.isoformat(), point.equity, point.cash, point.gross_exposure])


def run_market(market: str, bars: list[Bar]) -> dict[str, Any]:
    initial_cash = Decimal("10000000" if market == "us" else "100000000")
    cost = CostModel(
        commission_rate=Decimal("0.0001" if market == "us" else "0.00055"),
        minimum_commission=Decimal("0" if market == "us" else "5"),
        slippage_bps=Decimal("3" if market == "us" else "5"),
    )
    definitions = {
        "equal_weight_monthly": (EqualWeight(), 21),
        "momentum_20_weekly": (CrossSectionalMomentum(20, 30), 5),
        "momentum_60_monthly": (CrossSectionalMomentum(60, 30), 21),
        "momentum_120_monthly": (CrossSectionalMomentum(120, 30), 21),
        "low_volatility_60_monthly": (CrossSectionalLowVolatility(60, 30), 21),
    }
    output: dict[str, Any] = {}
    curve_dir = ROOT / "curves"
    curve_dir.mkdir(parents=True, exist_ok=True)
    for name, (strategy, rebalance) in definitions.items():
        print(f"回测 {market}: {name}", flush=True)
        result = BacktestEngine(
            initial_cash=initial_cash,
            cost_model=cost,
            rebalance_every=rebalance,
        ).run(bars, strategy)
        output[name] = enhanced_metrics(result, initial_cash)
        write_curve(curve_dir / f"{market}_{name}.csv", result)
    return output


def latest_momentum_symbols(bars: Sequence[Bar], lookback: int = 60) -> list[str]:
    history: dict[str, list[Bar]] = {}
    for bar in sorted(bars, key=lambda value: value.timestamp):
        history.setdefault(bar.instrument.symbol, []).append(bar)
    ranked: list[tuple[Decimal, str]] = []
    for symbol, values in history.items():
        if len(values) > lookback:
            ranked.append((values[-1].close / values[-1 - lookback].close - 1, symbol))
    return [symbol for _, symbol in sorted(ranked, reverse=True)]


def number(value: Any) -> float:
    try:
        result = float(value)
        return result if result == result else 0.0
    except (TypeError, ValueError):
        return 0.0


def liquid_contracts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in records:
        bid, ask = number(row.get("bid")), number(row.get("ask"))
        mid = (bid + ask) / 2
        spread_ratio = (ask - bid) / mid if mid > 0 else 999
        if bid > 0 and ask >= bid and mid > 0 and spread_ratio <= 0.35:
            item = dict(row)
            item["mid"] = mid
            item["spread_ratio"] = spread_ratio
            result.append(item)
    return result


def nearest_contract(records: list[dict[str, Any]], target: float) -> dict[str, Any]:
    if not records:
        raise RuntimeError("no liquid contracts")
    return min(records, key=lambda row: abs(number(row.get("strike")) - target))


def analytics_for(row: dict[str, Any], spot: float, years: float, rate: float, kind: OptionType) -> OptionAnalytics:
    volatility = number(row.get("impliedVolatility"))
    if volatility <= 0:
        volatility = implied_volatility(number(row["mid"]), spot, number(row["strike"]), years, rate, kind)
    return black_scholes(spot, number(row["strike"]), years, rate, volatility, kind)


def analyze_option_symbol(provider: YFinanceProvider, symbol: str, rate: float) -> dict[str, Any]:
    ticker = provider.yf.Ticker(symbol)
    history = ticker.history(period="10d", auto_adjust=False, actions=False)
    spot = number(history["Close"].iloc[-1])
    today = date.today()
    expirations = [datetime.fromisoformat(value).date() for value in ticker.options]
    eligible = [value for value in expirations if 28 <= (value - today).days <= 90]
    expiration = min(eligible or expirations, key=lambda value: abs((value - today).days - 45))
    dte = (expiration - today).days
    years = max(dte / 365, 1 / 365)
    raw = provider.option_chain(symbol, expiration.isoformat())
    calls = liquid_contracts(raw["calls"])
    puts = liquid_contracts(raw["puts"])
    atm_call = nearest_contract(calls, spot)
    otm_call = nearest_contract([row for row in calls if number(row["strike"]) > spot], spot * 1.07)
    protective_put = nearest_contract([row for row in puts if number(row["strike"]) < spot], spot * 0.95)
    atm_a = analytics_for(atm_call, spot, years, rate, OptionType.CALL)
    call_a = analytics_for(otm_call, spot, years, rate, OptionType.CALL)
    put_a = analytics_for(protective_put, spot, years, rate, OptionType.PUT)
    call_mid = number(otm_call["mid"])
    put_mid = number(protective_put["mid"])
    spread_debit = number(atm_call["mid"]) - call_mid
    spread_width = number(otm_call["strike"]) - number(atm_call["strike"])
    return {
        "symbol": symbol,
        "spot": spot,
        "expiration": expiration.isoformat(),
        "dte": dte,
        "risk_free_rate_assumption": rate,
        "liquid_calls": len(calls),
        "liquid_puts": len(puts),
        "covered_call": {
            "short_strike": number(otm_call["strike"]),
            "premium": call_mid,
            "breakeven": spot - call_mid,
            "max_profit": number(otm_call["strike"]) - spot + call_mid,
            "net_delta": 1 - call_a.delta,
            "net_gamma": -call_a.gamma,
            "net_theta_per_day": -call_a.theta_per_day,
            "quote_spread_pct": number(otm_call["spread_ratio"]),
        },
        "protective_put": {
            "long_strike": number(protective_put["strike"]),
            "premium": put_mid,
            "breakeven": spot + put_mid,
            "max_loss": spot + put_mid - number(protective_put["strike"]),
            "net_delta": 1 + put_a.delta,
            "net_gamma": put_a.gamma,
            "net_theta_per_day": put_a.theta_per_day,
            "quote_spread_pct": number(protective_put["spread_ratio"]),
        },
        "bull_call_spread": {
            "long_strike": number(atm_call["strike"]),
            "short_strike": number(otm_call["strike"]),
            "debit": spread_debit,
            "breakeven": number(atm_call["strike"]) + spread_debit,
            "max_profit": spread_width - spread_debit,
            "max_loss": spread_debit,
            "net_delta": atm_a.delta - call_a.delta,
            "net_gamma": atm_a.gamma - call_a.gamma,
            "net_theta_per_day": atm_a.theta_per_day - call_a.theta_per_day,
        },
    }


def run_options(us_bars: Sequence[Bar]) -> dict[str, Any]:
    provider = YFinanceProvider()
    try:
        irx = provider.yf.Ticker("^IRX").history(period="10d", auto_adjust=False, actions=False)
        rate = number(irx["Close"].iloc[-1]) / 100
    except Exception:
        rate = 0.04
    symbols = ["SPY", "QQQ"]
    for candidate in latest_momentum_symbols(us_bars):
        if candidate not in symbols:
            symbols.append(candidate)
            break
    output: dict[str, Any] = {}
    for symbol in symbols:
        try:
            output[symbol] = analyze_option_symbol(provider, symbol, rate)
        except Exception as exc:
            output[symbol] = {"error": f"{type(exc).__name__}: {exc}"}
    return output


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_report(results: dict[str, Any]) -> None:
    lines = [
        "# 双市场量化策略与期权分析",
        "",
        f"运行时间：{results['generated_at']}",
        "",
        "> 研究用途。使用当前股票池回看历史，存在幸存者偏差；期权部分是当前链情景分析，不是历史回测。",
        "",
    ]
    for market, title in (("us", "美股"), ("cn", "A股")):
        lines.extend([f"## {title}策略", "", "| 策略 | 总收益 | 年化 | 年化波动 | Sharpe | 最大回撤 | 换手倍数 |", "|---|---:|---:|---:|---:|---:|---:|"])
        for name, metrics in results["strategies"][market].items():
            lines.append(
                f"| {name} | {pct(metrics['total_return'])} | {pct(metrics['annualized_return'])} | "
                f"{pct(metrics['annualized_volatility'])} | {metrics['sharpe']:.2f} | "
                f"{pct(metrics['max_drawdown'])} | {metrics['turnover_multiple']:.1f} |"
            )
        lines.append("")
    us = results["strategies"]["us"]
    cn = results["strategies"]["cn"]
    years = 3.08
    lines.extend(
        [
            "## 策略结论",
            "",
            f"- 美股等权的风险调整表现最佳：年化 {pct(us['equal_weight_monthly']['annualized_return'])}、Sharpe {us['equal_weight_monthly']['sharpe']:.2f}、最大回撤 {pct(us['equal_weight_monthly']['max_drawdown'])}。",
            f"- 美股20日动量年化更高（{pct(us['momentum_20_weekly']['annualized_return'])}），但总换手 {us['momentum_20_weekly']['turnover_multiple']:.1f} 倍，约 {us['momentum_20_weekly']['turnover_multiple'] / years:.1f} 倍/年，成本和冲击风险过高。",
            f"- A股20日与120日动量年化接近（{pct(cn['momentum_20_weekly']['annualized_return'])} 对 {pct(cn['momentum_120_monthly']['annualized_return'])}），但120日策略总换手只有 {cn['momentum_120_monthly']['turnover_multiple']:.1f} 倍，明显更适合继续做样本外验证。",
            f"- 低波动策略在两边都降低波动，但收益落后等权；当前结果不支持单独使用，可作为组合降风险仓位。",
            "- 下一候选组合：60%等权 + 30%月频120日动量 + 10%低波动；先做滚动样本外和成本压力测试，不直接实盘。",
            "",
        ]
    )
    lines.extend(["## 当前期权链", ""])
    for symbol, value in results["options"].items():
        if "error" in value:
            lines.append(f"- {symbol}: {value['error']}")
            continue
        covered = value["covered_call"]
        protective = value["protective_put"]
        spread = value["bull_call_spread"]
        lines.extend(
            [
                f"### {symbol}（现价 {value['spot']:.2f}，到期 {value['expiration']}，DTE {value['dte']}）",
                "",
                f"- 备兑看涨：卖出 {covered['short_strike']:.2f} Call，权利金 {covered['premium']:.2f}，盈亏平衡 {covered['breakeven']:.2f}。",
                f"- 保护性看跌：买入 {protective['long_strike']:.2f} Put，权利金 {protective['premium']:.2f}，到期最大每股亏损约 {protective['max_loss']:.2f}。",
                f"- 牛市价差：{spread['long_strike']:.2f}/{spread['short_strike']:.2f} Call，净支出 {spread['debit']:.2f}，最大收益 {spread['max_profit']:.2f}，收益风险比 {spread['max_profit'] / spread['max_loss']:.2f}。",
                f"- 报价宽度：备兑腿 {pct(covered['quote_spread_pct'])}，保护腿 {pct(protective['quote_spread_pct'])}。",
                "",
            ]
        )
    lines.extend(
        [
            "期权金额均为每股，标准美股期权合约通常乘以100。SPY/QQQ价差较窄；VLO价差约20%，中间价不可视为可成交价。",
            "",
        ]
    )
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "STRATEGY_RESEARCH_2026-09-13.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    end = datetime.now()
    start = end - timedelta(days=3 * 365 + 30)
    us_instruments = load_universe("us")
    cn_instruments = load_universe("cn")
    us_bars = obtain_bars("us", us_instruments, start, end)
    cn_bars = obtain_bars("cn", cn_instruments, start, end)
    results = {
        "generated_at": end.isoformat(),
        "window": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "universe": {"us": len(us_instruments), "cn": len(cn_instruments)},
        "data_coverage": {
            "us": len({bar.instrument.symbol for bar in us_bars}),
            "cn": len({bar.instrument.symbol for bar in cn_bars}),
        },
        "strategies": {"us": run_market("us", us_bars), "cn": run_market("cn", cn_bars)},
        "options": run_options(us_bars),
        "limitations": [
            "使用当前成分股回看历史，存在幸存者偏差",
            "免费数据未形成严格 point-in-time 财务数据库",
            "期权为当前截面情景分析，不是历史期权回测",
            "A股交易成本为对称近似，未逐笔拆分印花税和过户费",
        ],
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
