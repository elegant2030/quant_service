from __future__ import annotations

import unittest

from quant_workbench.fundamentals.pit import (
    SEC_TICKERS_URL,
    SecCompanyFactsProvider,
    validate_fundamental_row,
)


class SecProviderTests(unittest.TestCase):
    def test_companyfacts_join_uses_acceptance_time_and_accession(self) -> None:
        accession = "0000320193-25-000079"
        entry = {
            "accn": accession,
            "form": "10-Q",
            "filed": "2025-08-01",
            "start": "2025-03-30",
            "end": "2025-06-28",
        }
        payloads = {
            SEC_TICKERS_URL: {"0": {"ticker": "AAPL", "cik_str": 320193}},
            "https://data.sec.gov/submissions/CIK0000320193.json": {
                "filings": {
                    "recent": {
                        "accessionNumber": [accession],
                        "acceptanceDateTime": ["2025-08-01T16:31:02.000Z"],
                        "filingDate": ["2025-08-01"],
                    }
                }
            },
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json": {
                "facts": {
                    "us-gaap": {
                        "RevenueFromContractWithCustomerExcludingAssessedTax": {
                            "units": {"USD": [{**entry, "val": 100.0}]}
                        },
                        "NetIncomeLoss": {"units": {"USD": [{**entry, "val": 20.0}]}},
                        "Assets": {"units": {"USD": [{**entry, "val": 500.0}]}},
                        "Liabilities": {"units": {"USD": [{**entry, "val": 200.0}]}},
                        "StockholdersEquity": {
                            "units": {"USD": [{**entry, "val": 300.0}]}
                        },
                    }
                }
            },
        }
        provider = SecCompanyFactsProvider(lambda url: payloads[url], request_delay=0)
        row, raw = provider.fetch("AAPL")
        self.assertEqual(row["document_id"], accession)
        self.assertEqual(row["effective_at"], "2025-08-01T16:31:02+00:00")
        self.assertEqual(row["net_margin"], 0.2)
        self.assertEqual(row["debt_to_assets"], 0.4)
        self.assertEqual(validate_fundamental_row(row), [])
        self.assertIn("companyfacts", raw)
        self.assertEqual(raw["cik_mapping_source"], "sec_company_tickers")

    def test_cik_mapping_falls_back_without_changing_fact_source(self) -> None:
        accession = "0000320193-25-000079"
        entry = {
            "accn": accession,
            "form": "10-Q",
            "filed": "2025-08-01",
            "end": "2025-06-28",
        }

        def http_get(url: str) -> dict[str, object]:
            if url == SEC_TICKERS_URL:
                raise OSError("blocked ticker directory")
            if "submissions" in url:
                return {
                    "filings": {
                        "recent": {
                            "accessionNumber": [accession],
                            "acceptanceDateTime": ["2025-08-01T16:31:02.000Z"],
                            "filingDate": ["2025-08-01"],
                        }
                    }
                }
            return {
                "facts": {
                    "us-gaap": {
                        "Revenues": {"units": {"USD": [{**entry, "val": 100.0}]}},
                        "NetIncomeLoss": {
                            "units": {"USD": [{**entry, "val": 20.0}]}
                        },
                    }
                }
            }

        provider = SecCompanyFactsProvider(
            http_get, request_delay=0, cik_resolver=lambda _symbol: 320193
        )
        row, raw = provider.fetch("AAPL")
        self.assertEqual(row["source"], "sec_edgar")
        self.assertEqual(raw["cik_mapping_source"], "yfinance_sec_filing_metadata")


if __name__ == "__main__":
    unittest.main()
