"""Minimal, polite client for CNInfo (巨潮资讯) announcement listings.

Why not AKShare here: ``ak.stock_zh_a_disclosure_report_cninfo`` sends requests with
the default ``python-requests`` User-Agent, which CNInfo answers with 403 during
mainland business hours, and it re-downloads the ~600 KB stock dictionary for every
symbol. This client identifies itself honestly, fetches the dictionary once per
instance, paces requests and caps pagination. It reads the public disclosure index
only; it does not try to look like a browser.
"""

from __future__ import annotations

import json
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
USER_AGENT = "QuantWorkbench/0.1 (personal research; low-frequency disclosure index reader)"
STOCK_DICTIONARY_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
DETAIL_URL = "https://www.cninfo.com.cn/new/disclosure/detail"
PAGE_SIZE = 30
MAX_PAGES = 10

Opener = Callable[[urllib.request.Request, float], bytes]


def _default_opener(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed host
        return response.read()


class CninfoError(RuntimeError):
    pass


class CninfoClient:
    def __init__(
        self,
        opener: Opener | None = None,
        pause_seconds: float = 0.25,
        timeout: float = 15.0,
        sleep: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self.opener = opener or _default_opener
        self.pause_seconds = pause_seconds
        self.timeout = timeout
        self.sleep = sleep
        self.requests_made = 0
        self._org_ids: dict[str, str] | None = None

    def _request_json(self, url: str, data: dict[str, Any] | None = None) -> Any:
        if self.requests_made and self.pause_seconds > 0:
            self.sleep(self.pause_seconds)
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            method="POST" if data is not None else "GET",
        )
        self.requests_made += 1
        try:
            payload = self.opener(request, self.timeout)
        except urllib.error.HTTPError as exc:
            raise CninfoError(f"HTTP {exc.code} from {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CninfoError(f"network error from {url}: {exc}") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CninfoError(f"non-JSON response from {url}: {payload[:80]!r}") from exc

    def org_ids(self) -> dict[str, str]:
        if self._org_ids is None:
            document = self._request_json(STOCK_DICTIONARY_URL)
            stocks = document.get("stockList") if isinstance(document, dict) else None
            if not stocks:
                raise CninfoError("stock dictionary is empty")
            self._org_ids = {
                str(item["code"]): str(item["orgId"])
                for item in stocks
                if item.get("code") and item.get("orgId")
            }
        return self._org_ids

    def announcements(self, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
        """Announcement rows for ``symbol`` between ``start`` and ``end`` (``YYYYMMDD``).

        Keys mirror the AKShare frame the event pipeline already parses:
        ``代码, 简称, 公告标题, 公告时间, 公告链接`` plus the raw millisecond timestamp.
        """
        org_id = self.org_ids().get(symbol)
        if org_id is None:
            raise CninfoError(f"unknown CNInfo symbol: {symbol}")
        window = f"{start[:4]}-{start[4:6]}-{start[6:]}~{end[:4]}-{end[4:6]}-{end[6:]}"
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            document = self._request_json(
                QUERY_URL,
                {
                    "pageNum": str(page),
                    "pageSize": str(PAGE_SIZE),
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{symbol},{org_id}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": window,
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                },
            )
            if not isinstance(document, dict):
                raise CninfoError("unexpected announcement payload")
            for item in document.get("announcements") or []:
                stamp = item.get("announcementTime")
                published = (
                    datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc).astimezone(SHANGHAI)
                    if stamp is not None
                    else None
                )
                announcement_id = str(item.get("announcementId") or "")
                query = urllib.parse.urlencode(
                    {
                        "stockCode": item.get("secCode") or symbol,
                        "announcementId": announcement_id,
                        "orgId": item.get("orgId") or org_id,
                    }
                )
                rows.append(
                    {
                        "代码": str(item.get("secCode") or symbol),
                        "简称": str(item.get("secName") or ""),
                        "公告标题": str(item.get("announcementTitle") or ""),
                        "公告时间": published.strftime("%Y-%m-%d %H:%M:%S") if published else "",
                        "公告链接": f"{DETAIL_URL}?{query}",
                        "announcement_time_ms": stamp,
                        "adjunct_url": item.get("adjunctUrl"),
                    }
                )
            if not document.get("hasMore"):
                break
        return rows
