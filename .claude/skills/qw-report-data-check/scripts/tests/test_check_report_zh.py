"""Tests for the Quant Workbench Chinese extension of the data quality checker."""

from datetime import date

import check_report_zh as zh


def categories(findings):
    return [(item.category, item.severity) for item in findings]


def test_chinese_weekday_mismatch_in_four_formats():
    # 2026-09-21 is a Monday.
    good = "2026年9月21日（周一）、9月21日 星期一、2026-09-21（周一）、9/21（周一）"
    assert zh.check_dates_zh(good, as_of=date(2026, 9, 21)) == []
    bad = (
        "报告日 2026年9月21日（周二）\n截至 9月21日 星期三\n"
        "快照 2026-09-21（周五）\n复盘 9/21（周日）"
    )
    findings = zh.check_dates_zh(bad, as_of=date(2026, 9, 21))
    assert len(findings) == 4
    assert all(item.category == "dates" and "实际是周一" in item.message for item in findings)
    assert [item.line_number for item in findings] == [1, 2, 3, 4]


def test_each_date_is_reported_once_and_impossible_dates_are_errors():
    assert len(zh.check_dates_zh("2026年9月21日（周二）")) == 1
    impossible = zh.check_dates_zh("2026年2月30日（周一）")
    assert categories(impossible) == [("dates", "ERROR")]
    assert zh.check_dates_zh("胜率 9/21 周一样本") == []  # fraction without parentheses


def test_year_is_inferred_from_as_of():
    # 2025-09-21 was a Sunday, 2026-09-21 a Monday.
    assert zh.check_dates_zh("9月21日（周一）", as_of=date(2026, 9, 25)) == []
    flagged = zh.check_dates_zh("9月21日（周一）", as_of=date(2025, 9, 25))
    assert "推断年份 2025" in flagged[0].message


def test_instruction_wording_is_flagged_but_prohibitions_and_code_are_not():
    text = "\n".join(
        [
            "## 结论",
            "综合来看建议买入 PSX。",
            "目标价 150 美元。",
            "报告不写“建议买入”，也不给目标价。",
            "字段 target_mean 保存分析师目标价。",
            "```",
            "strong buy = 6",
            "```",
        ]
    )
    findings = zh.check_wording(text)
    assert [(item.line_number, item.category) for item in findings] == [
        (2, "wording"),
        (3, "wording"),
    ]


def test_performance_numbers_need_a_credibility_qualifier():
    bare = "美股月度等权年化 27.73%，Sharpe 1.99，最大回撤 -14.14%。"
    assert categories(zh.check_caveats(bare)) == [("caveats", "WARNING")]
    qualified = bare + " 描述性回测，含幸存者偏差，无完整成本。"
    assert zh.check_caveats(qualified) == []
    assert zh.check_caveats("本文不含绩效数字。") == []


def test_chinese_allocation_tables_are_summed():
    text = (
        "## 行业配置\n\n| 行业 | 权重 |\n|---|---|\n"
        "| 能源 | 40% |\n| 金融 | 40% |\n| 材料 | 10% |\n"
    )
    findings = zh.run_checks(text, ["allocations"])
    assert any(item.category == "allocations" for item in findings)
    balanced = text.replace("10%", "20%")
    assert not [
        item for item in zh.run_checks(balanced, ["allocations"]) if item.severity != "INFO"
    ]


def test_run_checks_merges_upstream_and_extension_and_rejects_unknown(tmp_path, capsys):
    text = "报告日 2026年9月21日（周二）\n建议买入 AAPL\n"
    names = {item.category for item in zh.run_checks(text)}
    assert {"dates", "wording"} <= names
    try:
        zh.run_checks(text, ["nope"])
    except ValueError as exc:
        assert "unknown checks" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown check accepted")

    report = tmp_path / "r.md"
    report.write_text(text, encoding="utf-8")
    assert zh.main(["--file", str(report), "--as-of", "2026-09-21"]) == 0
    assert "日期与星期不符" in capsys.readouterr().out
    assert list(tmp_path.glob("report_check_*")) == []  # no files unless --output-dir
    assert zh.main(["--file", str(report), "--output-dir", str(tmp_path / "out")]) == 0
    assert len(list((tmp_path / "out").glob("report_check_*"))) == 2
    assert zh.main(["--file", str(tmp_path / "missing.md")]) == 1
