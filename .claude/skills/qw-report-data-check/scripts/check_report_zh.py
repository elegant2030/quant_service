#!/usr/bin/env python3
"""Quant Workbench report checker: upstream data-quality checks plus Chinese support.

Wraps the unmodified upstream ``check_data_quality.py`` (tradermonty, MIT) and adds:

- ``dates_zh``   Chinese date/weekday consistency: 2026年9月21日（周一）, 9月21日 星期一,
                 2026-09-21（周一）, 9/21（周一）.
- ``allocations`` also recognises Chinese headings and table headers (配置/权重/占比/配比).
- ``wording``    project red line 1: reports must not read as buy/sell instructions,
                 price targets or position sizing.
- ``caveats``    backtest performance figures should appear with the credibility
                 qualifiers required by CLAUDE.md section 13.

Advisory only: always exits 0 unless the input is unusable. Standard library only.
Prints a Markdown report to stdout; files are written only when --output-dir is given.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_data_quality as base  # noqa: E402

Finding = base.Finding

# --------------------------------------------------------------------------- allocations

ZH_ALLOCATION_HEADINGS = [
    "资产配置",
    "组合配置",
    "行业配置",
    "板块配置",
    "配置比例",
    "权重分配",
    "配比",
]
ZH_ALLOCATION_TABLE = ["权重", "占比", "配置比例", "配比", "比例"]
for _keyword in ZH_ALLOCATION_HEADINGS:
    if _keyword not in base.ALLOCATION_HEADING_KEYWORDS:
        base.ALLOCATION_HEADING_KEYWORDS.append(_keyword)
for _keyword in ZH_ALLOCATION_TABLE:
    if _keyword not in base.ALLOCATION_TABLE_KEYWORDS:
        base.ALLOCATION_TABLE_KEYWORDS.append(_keyword)

# --------------------------------------------------------------------------------- dates

WEEKDAY_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
WEEKDAY_ZH_NAME = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_WD = r"(?:周|星期|礼拜)([一二三四五六日天])"
_OPEN = r"\s*[（(,，]?\s*"

ZH_DATE_PATTERNS = [
    # 2026年9月21日（周一） / 9月21日 星期一
    (re.compile(rf"(?:(\d{{4}})年)?(\d{{1,2}})月(\d{{1,2}})日{_OPEN}{_WD}"), "ymd"),
    # 2026-09-21（周一） / 2026/09/21 周一
    (re.compile(rf"(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}}){_OPEN}{_WD}"), "iso"),
    # 9/21（周一） — parentheses required to avoid matching fractions
    (re.compile(rf"(?<![\d/-])(\d{{1,2}})/(\d{{1,2}})\s*[（(]\s*{_WD}\s*[）)]"), "md"),
]


def check_dates_zh(
    content: str, as_of: date | None = None, filepath: str | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    for pattern, kind in ZH_DATE_PATTERNS:
        for match in pattern.finditer(content):
            span = (match.start(), match.end())
            if any(start <= span[0] < end for start, end in seen):
                continue
            seen.add(span)
            if kind == "md":
                year_text, month_text, day_text, weekday_char = None, *match.groups()
            else:
                year_text, month_text, day_text, weekday_char = match.groups()
            month, day = int(month_text), int(day_text)
            inferred = year_text is None
            year = (
                int(year_text)
                if year_text
                else base.infer_year(month, day, as_of, content, filepath)
            )
            line_number = content[: match.start()].count("\n") + 1
            try:
                actual = date(year, month, day)
            except ValueError:
                findings.append(
                    Finding(
                        "ERROR", "dates", f"不存在的日期：{match.group(0).strip()}", line_number
                    )
                )
                continue
            stated = WEEKDAY_ZH[weekday_char]
            if stated != actual.weekday():
                note = f"（推断年份 {year}）" if inferred else ""
                findings.append(
                    Finding(
                        "WARNING",
                        "dates",
                        f"日期与星期不符：{match.group(0).strip()} 实际是"
                        f"{WEEKDAY_ZH_NAME[actual.weekday()]}{note}",
                        line_number,
                    )
                )
    return findings


# ------------------------------------------------------------------------------- wording

# Red line 1: no buy/sell instructions, price targets or position sizing.
INSTRUCTION_TERMS = [
    "建议买入",
    "建议卖出",
    "建议加仓",
    "建议减仓",
    "建议持有",
    "建议建仓",
    "建议清仓",
    "强烈推荐",
    "强烈建议买",
    "推荐买入",
    "买入评级",
    "卖出评级",
    "增持评级",
    "减持评级",
    "目标价",
    "目标价位",
    "止盈位",
    "止损位",
    "建议仓位",
    "仓位建议",
    "满仓",
    "重仓买入",
    "strong buy",
    "price target",
    "target price",
    "buy rating",
    "sell rating",
]
# A line that is itself stating the prohibition, or quoting vendor fields, is fine.
NEGATION_MARKERS = [
    "不",
    "非",
    "禁止",
    "不得",
    "不构成",
    "不写",
    "不给",
    "不含",
    "不输出",
    "并非",
    "无",
    "not ",
    "never",
    "no ",
    "without",
    "字段",
    "列",
    "column",
    "field",
    "rating_",
    "target_",
    # Describing vendor/analyst data is not the report issuing its own target.
    "分析师",
    "一致预期",
    "快照",
    "数据集",
    "analyst",
    "consensus",
    "snapshot",
]


def check_wording(content: str) -> list[Finding]:
    findings: list[Finding] = []
    in_code = False
    for number, line in enumerate(content.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        lowered = line.lower()
        for term in INSTRUCTION_TERMS:
            if term.lower() not in lowered:
                continue
            if any(marker in lowered for marker in NEGATION_MARKERS):
                continue
            findings.append(
                Finding(
                    "WARNING",
                    "wording",
                    f"疑似交易指令或目标价表述“{term}”；报告只写研究结论与适用范围（红线 1）",
                    number,
                    line.strip()[:120],
                )
            )
            break
    return findings


# ------------------------------------------------------------------------------- caveats

PERFORMANCE_PATTERN = re.compile(
    r"(年化|annualized|sharpe|夏普|最大回撤|max(?:imum)? drawdown)[^\n]{0,40}?-?\d+(?:\.\d+)?\s*%?",
    re.IGNORECASE,
)
CAVEAT_TERMS = [
    "幸存者偏差",
    "survivorship",
    "描述性",
    "descriptive",
    "样本内",
    "in-sample",
    "in sample",
    "无基准",
    "未含",
    "不含",
    "未计",
    "成本",
    "cost",
    "滑点",
    "前视",
    "look-ahead",
    "lookahead",
    "回溯",
    "当前成分",
    "不构成",
    "冒烟",
    "smoke",
]


def check_caveats(content: str) -> list[Finding]:
    """Flag documents that quote backtest performance without any credibility qualifier."""
    matches = list(PERFORMANCE_PATTERN.finditer(content))
    if not matches:
        return []
    lowered = content.lower()
    if any(term.lower() in lowered for term in CAVEAT_TERMS):
        return []
    first = matches[0]
    return [
        Finding(
            "WARNING",
            "caveats",
            f"出现 {len(matches)} 处回测绩效数字，但全文没有任何可信度限定语"
            "（幸存者偏差、样本内、成本、基准等，见 CLAUDE.md 第 13 节）",
            content[: first.start()].count("\n") + 1,
            first.group(0)[:120],
        )
    ]


# ---------------------------------------------------------------------------------- run

EXTRA_CHECKS = {"dates_zh": check_dates_zh, "wording": check_wording, "caveats": check_caveats}
ALL_CHECK_NAMES = [*base.ALL_CHECKS, *EXTRA_CHECKS]


def run_checks(
    content: str,
    checks: list[str] | None = None,
    as_of: date | None = None,
    filepath: str | None = None,
) -> list[Finding]:
    selected = checks or ALL_CHECK_NAMES
    unknown = [name for name in selected if name not in ALL_CHECK_NAMES]
    if unknown:
        raise ValueError(f"unknown checks: {unknown}; available: {ALL_CHECK_NAMES}")
    # Normalise full-width percent/tilde the same way upstream does for its own checks.
    findings = base.run_checks(
        content, [name for name in selected if name in base.ALL_CHECKS], as_of, filepath
    )
    for name in selected:
        if name == "dates_zh":
            findings.extend(check_dates_zh(content, as_of, filepath))
        elif name in EXTRA_CHECKS:
            findings.extend(EXTRA_CHECKS[name](content))
    findings.sort(key=lambda item: item.sort_key())
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", required=True, type=Path, help="Markdown report to check")
    parser.add_argument("--checks", help=f"Comma-separated subset of: {', '.join(ALL_CHECK_NAMES)}")
    parser.add_argument("--as-of", help="Reference date for year inference (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=Path, help="Also write JSON and Markdown files here")
    args = parser.parse_args(argv)

    if not args.file.is_file():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        return 1
    as_of = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"Error: invalid --as-of: {args.as_of}", file=sys.stderr)
            return 1
    checks = [item.strip() for item in args.checks.split(",")] if args.checks else None
    try:
        findings = run_checks(
            args.file.read_text(encoding="utf-8"), checks, as_of, filepath=str(args.file)
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = base.generate_report(findings, str(args.file))
    print(report)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        (args.output_dir / f"report_check_{stamp}.md").write_text(report, encoding="utf-8")
        (args.output_dir / f"report_check_{stamp}.json").write_text(
            json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
