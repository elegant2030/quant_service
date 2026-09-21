from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from quant_workbench.data.cninfo_client import USER_AGENT, CninfoClient, CninfoError
from quant_workbench.events.pit import CninfoAnnouncementProvider


class FakeCninfo:
    def __init__(self, pages: dict[int, dict], forbidden: bool = False) -> None:
        self.pages = pages
        self.forbidden = forbidden
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []

    def __call__(self, request, timeout):  # type: ignore[no-untyped-def]
        body = urllib.parse.parse_qs((request.data or b"").decode())
        self.requests.append((request.get_method(), request.full_url, body))
        assert request.get_header("User-agent") == USER_AGENT
        if self.forbidden:
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)  # type: ignore[arg-type]
        if request.full_url.endswith("szse_stock.json"):
            return json.dumps(
                {"stockList": [{"code": "600519", "orgId": "gssh0600519", "zwjc": "贵州茅台"}]}
            ).encode()
        return json.dumps(self.pages[int(body["pageNum"][0])]).encode()


def announcement(identifier: int, title: str, stamp_ms: int) -> dict:
    return {
        "announcementId": str(identifier),
        "announcementTitle": title,
        "announcementTime": stamp_ms,
        "secCode": "600519",
        "secName": "贵州茅台",
        "orgId": "gssh0600519",
        "adjunctUrl": f"finalpage/2026-08-15/{identifier}.PDF",
    }


class CninfoClientTests(unittest.TestCase):
    def test_dictionary_fetched_once_pages_followed_and_requests_paced(self) -> None:
        stamp = int(datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
        fake = FakeCninfo(
            {
                1: {"announcements": [announcement(1, "半年度报告", stamp)], "hasMore": True},
                2: {"announcements": [announcement(2, "会计政策变更", stamp)], "hasMore": False},
            }
        )
        pauses: list[float] = []
        client = CninfoClient(opener=fake, pause_seconds=0.25, sleep=pauses.append)
        rows = client.announcements("600519", "20260701", "20260921")
        again = client.announcements("600519", "20260701", "20260921")
        self.assertEqual([row["公告标题"] for row in rows], ["半年度报告", "会计政策变更"])
        self.assertEqual(rows, again)
        # 2026-08-14 16:00 UTC is already 2026-08-15 in Shanghai.
        self.assertEqual(rows[0]["公告时间"], "2026-08-15 00:00:00")
        self.assertIn("announcementId=1", rows[0]["公告链接"])
        dictionary_calls = [r for r in fake.requests if r[1].endswith("szse_stock.json")]
        self.assertEqual(len(dictionary_calls), 1)
        self.assertEqual(client.requests_made, 5)
        self.assertEqual(pauses, [0.25] * 4)
        first_query = fake.requests[1][2]
        self.assertEqual(first_query["stock"], ["600519,gssh0600519"])
        self.assertEqual(first_query["seDate"], ["2026-07-01~2026-09-21"])

    def test_forbidden_and_unknown_symbol_raise_clear_errors(self) -> None:
        with self.assertRaisesRegex(CninfoError, "HTTP 403"):
            CninfoClient(opener=FakeCninfo({}, forbidden=True), pause_seconds=0).org_ids()
        client = CninfoClient(opener=FakeCninfo({1: {"announcements": []}}), pause_seconds=0)
        with self.assertRaisesRegex(CninfoError, "unknown CNInfo symbol"):
            client.announcements("999999", "20260701", "20260921")

    def test_non_json_body_is_reported(self) -> None:
        client = CninfoClient(
            opener=lambda request, timeout: b"<!doctype html>403", pause_seconds=0
        )
        with self.assertRaisesRegex(CninfoError, "non-JSON"):
            client.org_ids()

    def test_event_provider_accepts_plain_row_lists(self) -> None:
        stamp = int(datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc).timestamp() * 1000)
        fake = FakeCninfo({1: {"announcements": [announcement(7, "年度报告", stamp)]}})
        provider = CninfoAnnouncementProvider()
        provider._client = CninfoClient(opener=fake, pause_seconds=0)
        rows, raw = provider.fetch(
            "600519",
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "cninfo:7")
        self.assertEqual(rows[0]["source"], "cninfo")
        self.assertEqual(len(raw), 1)


if __name__ == "__main__":
    unittest.main()
