"""Point-in-time quarterly fundamental adapters for SEC EDGAR and BaoStock."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from datetime import time as day_time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from quant_workbench.store import DatasetStore, StateStore

DATASET = "fundamental_metrics"
SCHEMA_VERSION = 1
SEC_BASE = "https://data.sec.gov"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FORMS = {"10-K", "10-Q", "20-F", "40-F"}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def validate_fundamental_row(row: dict[str, Any]) -> list[str]:
    required = {
        "source",
        "source_symbol",
        "symbol",
        "market",
        "retrieved_at",
        "effective_at",
        "period_end",
        "document_id",
        "schema_version",
    }
    errors = [f"missing:{field}" for field in sorted(required) if row.get(field) in (None, "")]
    try:
        effective = datetime.fromisoformat(str(row["effective_at"]).replace("Z", "+00:00"))
        retrieved = datetime.fromisoformat(str(row["retrieved_at"]).replace("Z", "+00:00"))
        if effective > retrieved:
            errors.append("effective_after_retrieved")
    except (KeyError, ValueError):
        errors.append("invalid_timestamp")
    metrics = [
        row.get(name)
        for name in (
            "revenue",
            "net_income",
            "roe",
            "gross_margin",
            "net_margin",
            "revenue_growth",
            "earnings_growth",
        )
    ]
    if not any(value is not None for value in metrics):
        errors.append("no_financial_metrics")
    return errors


HttpGet = Callable[[str], dict[str, Any]]


class SecCompanyFactsProvider:
    """Official no-key SEC API adapter with filing acceptance-time joins."""

    def __init__(
        self,
        http_get: HttpGet | None = None,
        request_delay: float = 0.22,
        cik_resolver: Callable[[str], int] | None = None,
    ) -> None:
        self.user_agent = os.environ.get(
            "SEC_USER_AGENT", "QuantWorkbench/0.1 contact@localhost"
        )
        self.http_get = http_get or self._get
        self.request_delay = request_delay
        self.cik_resolver = cik_resolver or self._yfinance_cik
        self._ticker_map: dict[str, int] | None = None

    def _get(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                # data.sec.gov currently rejects urllib's minimal default request,
                # while accepting the same declared user agent with normal HTTP
                # content-negotiation headers.  Keep the identity explicit and do
                # not masquerade as a browser.
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip

                payload = gzip.decompress(payload)
            return json.loads(payload.decode("utf-8"))

    def ticker_map(self) -> dict[str, int]:
        if self._ticker_map is None:
            try:
                payload = self.http_get(SEC_TICKERS_URL)
                self._ticker_map = {
                    str(item["ticker"]).upper().replace(".", "-"): int(item["cik_str"])
                    for item in payload.values()
                }
            except Exception:
                # Some networks block www.sec.gov while allowing data.sec.gov.
                # Cache the failure and use yfinance only for identifier resolution;
                # all financial facts and acceptance timestamps still come from SEC.
                self._ticker_map = {}
        return self._ticker_map

    @staticmethod
    def _yfinance_cik(symbol: str) -> int:
        import yfinance as yf

        filings = list(yf.Ticker(symbol).get_sec_filings() or [])
        for filing in filings:
            candidates = [str(filing.get("edgarUrl") or "")]
            candidates.extend(str(value) for value in (filing.get("exhibits") or {}).values())
            for value in candidates:
                match = re.search(r"(?:_|/)(\d{6,10})(?:/|$)", value)
                if match:
                    return int(match.group(1))
        raise KeyError(f"SEC CIK not found in filing metadata for {symbol}")

    def resolve_cik(self, symbol: str) -> tuple[int, str]:
        normalized = symbol.upper().replace(".", "-")
        cik = self.ticker_map().get(normalized)
        if cik is not None:
            return cik, "sec_company_tickers"
        return self.cik_resolver(symbol), "yfinance_sec_filing_metadata"

    @staticmethod
    def _acceptance_by_accession(submissions: dict[str, Any]) -> dict[str, str]:
        recent = submissions.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber") or []
        accepted = recent.get("acceptanceDateTime") or []
        filed = recent.get("filingDate") or []
        result: dict[str, str] = {}
        for index, accession in enumerate(accessions):
            value = accepted[index] if index < len(accepted) else None
            if value:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            else:
                filed_date = date.fromisoformat(str(filed[index]))
                parsed = datetime.combine(
                    filed_date + timedelta(days=1), day_time.min, tzinfo=timezone.utc
                )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result[str(accession)] = parsed.astimezone(timezone.utc).isoformat()
        return result

    @staticmethod
    def _facts_by_accession(companyfacts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for taxonomy, concepts in (companyfacts.get("facts") or {}).items():
            if taxonomy not in {"us-gaap", "ifrs-full"}:
                continue
            for concept, detail in concepts.items():
                for unit, facts in (detail.get("units") or {}).items():
                    for fact in facts:
                        accession = fact.get("accn")
                        if accession and fact.get("form") in SEC_FORMS:
                            result.setdefault(str(accession), []).append(
                                {**fact, "concept": concept, "unit": unit}
                            )
        return result

    @staticmethod
    def _metric(facts: list[dict[str, Any]], concepts: tuple[str, ...]) -> float | None:
        candidates = [fact for fact in facts if fact["concept"] in concepts]
        if not candidates:
            return None
        candidates.sort(
            key=lambda fact: (
                str(fact.get("end") or ""),
                str(fact.get("filed") or ""),
            ),
            reverse=True,
        )
        return _number(candidates[0].get("val"))

    def fetch(self, symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
        cik, mapping_source = self.resolve_cik(symbol)
        if self.request_delay:
            time.sleep(self.request_delay)
        submissions = self.http_get(f"{SEC_BASE}/submissions/CIK{cik:010d}.json")
        if self.request_delay:
            time.sleep(self.request_delay)
        companyfacts = self.http_get(f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json")
        accepted = self._acceptance_by_accession(submissions)
        by_accession = self._facts_by_accession(companyfacts)
        usable = [accession for accession in by_accession if accession in accepted]
        if not usable:
            raise ValueError(f"no accepted SEC financial facts for {symbol}")
        accession = max(usable, key=lambda value: accepted[value])
        facts = by_accession[accession]
        period_end = max(str(fact.get("end") or "") for fact in facts)
        revenue = self._metric(
            facts,
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
                "Revenue",
            ),
        )
        net_income = self._metric(facts, ("NetIncomeLoss", "ProfitLoss"))
        assets = self._metric(facts, ("Assets",))
        liabilities = self._metric(facts, ("Liabilities",))
        equity = self._metric(
            facts,
            (
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                "Equity",
            ),
        )
        gross_profit = self._metric(facts, ("GrossProfit",))
        operating_cash_flow = self._metric(
            facts, ("NetCashProvidedByUsedInOperatingActivities",)
        )
        row = {
            "source": "sec_edgar",
            "source_symbol": str(cik),
            "symbol": symbol,
            "market": "us",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "effective_at": accepted[accession],
            "period_end": period_end,
            "document_id": accession,
            "form": str(facts[0].get("form") or ""),
            "revenue": revenue,
            "net_income": net_income,
            "total_assets": assets,
            "total_equity": equity,
            "operating_cash_flow": operating_cash_flow,
            "roe": net_income / equity if net_income is not None and equity else None,
            "gross_margin": (
                gross_profit / revenue if gross_profit is not None and revenue else None
            ),
            "net_margin": net_income / revenue if net_income is not None and revenue else None,
            "debt_to_assets": liabilities / assets if liabilities is not None and assets else None,
            "cash_conversion": (
                operating_cash_flow / abs(net_income)
                if operating_cash_flow is not None and net_income not in (None, 0)
                else None
            ),
            "revenue_growth": None,
            "earnings_growth": None,
            "schema_version": SCHEMA_VERSION,
        }
        return row, {
            "cik_mapping_source": mapping_source,
            "submissions": submissions,
            "companyfacts": companyfacts,
        }


class BaoStockFundamentalProvider:
    def __init__(self) -> None:
        import baostock as bs

        self.bs = bs
        response = bs.login()
        if response.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {response.error_msg}")

    def close(self) -> None:
        self.bs.logout()

    @staticmethod
    def _rows(result: Any) -> list[dict[str, str]]:
        if result.error_code != "0":
            raise RuntimeError(result.error_msg)
        rows: list[dict[str, str]] = []
        while result.next():
            rows.append(dict(zip(result.fields, result.get_row_data(), strict=True)))
        return rows

    def fetch(
        self, symbol: str, exchange: str, year: int, quarter: int
    ) -> tuple[dict[str, Any], Any]:
        code = f"{'sh' if exchange == 'SSE' else 'sz'}.{symbol}"
        profit = self._rows(self.bs.query_profit_data(code=code, year=year, quarter=quarter))
        if not profit:
            raise ValueError(f"no BaoStock fundamentals for {symbol} {year}Q{quarter}")
        queries = {
            "growth": self.bs.query_growth_data,
            "balance": self.bs.query_balance_data,
            "cash_flow": self.bs.query_cash_flow_data,
        }
        raw = {"profit": profit, **{
            name: self._rows(query(code=code, year=year, quarter=quarter))
            for name, query in queries.items()
        }}
        merged: dict[str, Any] = {}
        for rows in raw.values():
            if rows:
                merged.update(rows[0])
        if not merged:
            raise ValueError(f"no BaoStock fundamentals for {symbol} {year}Q{quarter}")
        published = date.fromisoformat(str(merged["pubDate"]))
        # BaoStock exposes only the publication date, not the exact announcement time.
        # Make the row usable from 00:00 China time on the following day to avoid
        # accidentally treating an evening announcement as known during that session.
        effective = datetime.combine(
            published + timedelta(days=1),
            day_time.min,
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).astimezone(timezone.utc)
        row = {
            "source": "baostock",
            "source_symbol": code,
            "symbol": symbol,
            "market": "cn",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "effective_at": effective.isoformat(),
            "period_end": str(merged["statDate"]),
            "document_id": f"{code}:{merged['statDate']}:{merged['pubDate']}",
            "form": "cn_quarterly",
            "revenue": _number(merged.get("MBRevenue")),
            "net_income": _number(merged.get("netProfit")),
            "total_assets": None,
            "total_equity": None,
            "operating_cash_flow": None,
            "roe": _number(merged.get("roeAvg")),
            "gross_margin": _number(merged.get("gpMargin")),
            "net_margin": _number(merged.get("npMargin")),
            "debt_to_assets": _number(merged.get("liabilityToAsset")),
            "cash_conversion": _number(merged.get("CFOToNP")),
            "revenue_growth": None,
            "earnings_growth": _number(merged.get("YOYNI")),
            "current_ratio": _number(merged.get("currentRatio")),
            "schema_version": SCHEMA_VERSION,
        }
        return row, raw


def _yfinance_research_snapshot(symbol: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Current-only fallback; it is deliberately stored outside canonical."""
    from quant_workbench.core.models import AssetClass, Currency, Exchange, Instrument
    from quant_workbench.data.yfinance_provider import YFinanceProvider

    snapshots = YFinanceProvider().financials(
        Instrument(symbol, Exchange.NASDAQ, AssetClass.EQUITY, Currency.USD)
    )
    if not snapshots:
        raise ValueError(f"no yfinance financial snapshot for {symbol}")
    latest = max(snapshots, key=lambda item: item.as_of)
    retrieved = datetime.now(timezone.utc)

    def value(name: str) -> float | None:
        return _number(getattr(latest, name))

    row = {
        "source": "yfinance_research",
        "source_symbol": symbol,
        "symbol": symbol,
        "market": "us",
        "retrieved_at": retrieved.isoformat(),
        "effective_at": retrieved.isoformat(),
        "period_end": latest.as_of.isoformat(),
        "document_id": f"yfinance:{symbol}:{latest.as_of.isoformat()}:{retrieved.date()}",
        "form": "research_snapshot",
        "revenue": value("revenue"),
        "net_income": value("net_income"),
        "total_assets": value("total_assets"),
        "total_equity": value("total_equity"),
        "operating_cash_flow": value("operating_cash_flow"),
        "roe": (
            value("net_income") / value("total_equity")
            if value("net_income") is not None and value("total_equity")
            else None
        ),
        "gross_margin": value("gross_margin"),
        "net_margin": (
            value("net_income") / value("revenue")
            if value("net_income") is not None and value("revenue")
            else None
        ),
        "debt_to_assets": (
            value("total_debt") / value("total_assets")
            if value("total_debt") is not None and value("total_assets")
            else None
        ),
        "cash_conversion": (
            value("operating_cash_flow") / abs(value("net_income"))
            if value("operating_cash_flow") is not None and value("net_income") not in (None, 0)
            else None
        ),
        "revenue_growth": value("revenue_growth"),
        "earnings_growth": value("earnings_growth"),
        "schema_version": SCHEMA_VERSION,
        "evidence_quality": "research_snapshot_no_filing_acceptance_time",
    }
    raw = [
        {
            name: str(getattr(snapshot, name))
            for name in snapshot.__slots__
        }
        for snapshot in snapshots
    ]
    return row, raw


def _universe(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def ingest_fundamentals(
    store: DatasetStore,
    state: StateStore,
    market: str,
    universe_path: Path,
    run_date: date,
    symbols: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    members = _universe(universe_path)
    if symbols:
        wanted = {symbol.upper() for symbol in symbols}
        members = [row for row in members if row["symbol"].upper() in wanted]
    if limit:
        members = members[:limit]
    identity = f"{market}:{run_date.isoformat()}:{','.join(row['symbol'] for row in members)}"
    lease = state.acquire_job("ingest_fundamentals", identity, {"market": market})
    if not lease.acquired:
        return {"acquired": False, "reason": lease.reason, "rows": 0, "errors": []}
    assert lease.run_id is not None
    source = "sec_edgar" if market == "us" else "baostock"
    rows: list[dict[str, Any]] = []
    research_rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {}
    errors: list[str] = []
    provider: Any = SecCompanyFactsProvider() if market == "us" else BaoStockFundamentalProvider()
    try:
        for member in members:
            symbol = member["symbol"]
            try:
                if market == "us":
                    row, payload = provider.fetch(symbol)
                else:
                    row = payload = None
                    for offset in range(0, 8):
                        month = run_date.month - offset * 3
                        year = run_date.year + (month - 1) // 12
                        quarter = ((month - 1) % 12) // 3 + 1
                        try:
                            row, payload = provider.fetch(
                                symbol, member["exchange"], year, quarter
                            )
                            break
                        except ValueError:
                            continue
                    if row is None:
                        raise ValueError("no recent quarterly fundamentals")
                row_errors = validate_fundamental_row(row)
                if row_errors:
                    raise ValueError(",".join(row_errors))
                rows.append(row)
                raw[symbol] = payload
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
                if market == "us":
                    try:
                        research_row, research_raw = _yfinance_research_snapshot(symbol)
                        if not validate_fundamental_row(research_row):
                            research_rows.append(research_row)
                            raw[f"{symbol}:yfinance_research"] = research_raw
                    except Exception as fallback_exc:
                        errors.append(
                            f"{symbol} fallback: {type(fallback_exc).__name__}: {fallback_exc}"
                        )
        store.write_raw(
            source,
            DATASET,
            market,
            run_date,
            {"symbols": raw, "errors": errors},
            identity.replace(":", "-")[:180],
            {"requested": len(members), "succeeded": len(rows)},
        )
        for row in research_rows:
            store.write_json(
                Path("research")
                / "fundamentals"
                / "market=us"
                / f"symbol={row['symbol']}"
                / "latest.json",
                row,
            )
        if not rows and not research_rows:
            raise RuntimeError(f"no fundamental rows collected: {errors[:3]}")
        result = (
            store.write_rows(
                DATASET,
                market,
                source,
                run_date,
                rows,
                f"{source}-{run_date.isoformat()}-{len(rows)}",
                schema_version=SCHEMA_VERSION,
                validator=validate_fundamental_row,
            )
            if rows
            else None
        )
        if rows:
            for row in rows:
                state.set_watermark(source, DATASET, market, row["effective_at"], row["symbol"])
            state.set_watermark(
                source, DATASET, market, max(row["effective_at"] for row in rows)
            )
            state.record_source_success(source)
        elif research_rows:
            state.record_source_failure(source, "SEC unavailable; used yfinance research fallback")
        if research_rows:
            state.record_source_success("yfinance_research")
            for row in research_rows:
                state.set_watermark(
                    "yfinance_research", DATASET, market, row["effective_at"], row["symbol"]
                )
        state.complete_job(
            lease.run_id,
            len(rows) + len(research_rows),
            {"errors": errors[:20], "research_rows": len(research_rows)},
        )
        return {
            "acquired": True,
            "reason": lease.reason,
            "rows": len(rows),
            "research_rows": len(research_rows),
            "requested": len(members),
            "errors": errors,
            "path": str(result.path) if result else None,
        }
    except Exception as exc:
        state.record_source_failure(source, f"{type(exc).__name__}: {exc}")
        state.fail_job(lease.run_id, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if market == "cn":
            provider.close()


def run_due_fundamentals(
    store: DatasetStore,
    state: StateStore,
    universe_directory: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh quarterly metrics once per local weekday after each market closes."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    schedules = {
        "cn": (ZoneInfo("Asia/Shanghai"), day_time(17, 0)),
        "us": (ZoneInfo("America/New_York"), day_time(17, 30)),
    }
    output: dict[str, Any] = {"status": "ok", "results": {}, "errors": []}
    for market, (market_timezone, cutoff) in schedules.items():
        local = current.astimezone(market_timezone)
        if local.weekday() >= 5 or local.time() < cutoff:
            output["results"][market] = {"due": False, "local_time": local.isoformat()}
            continue
        try:
            result = ingest_fundamentals(
                store,
                state,
                market,
                universe_directory / f"universe_{market}.csv",
                local.date(),
            )
            output["results"][market] = {"due": True, **result}
        except Exception as exc:
            output["status"] = "error"
            message = f"{market}: {type(exc).__name__}: {exc}"
            output["errors"].append(message)
            output["results"][market] = {"due": True, "error": message}
    store.write_json("health/last-fundamentals-run.json", output)
    return output
