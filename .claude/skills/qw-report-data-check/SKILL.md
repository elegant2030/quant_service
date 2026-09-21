---
name: qw-report-data-check
description: 在 Quant Workbench 的 Markdown 报告出门前做确定性的数据与表述检查：日期与星期是否相符（中文、英文、日文格式）、配置或权重合计是否约 100%、价格量级是否混淆（ETF 与期货）、标的写法是否一致、基点与百分比是否混用，以及两条项目专属规则：是否出现买卖指令或目标价措辞（红线 1）、回测绩效数字是否带可信度限定语（CLAUDE.md 第 13 节）。凡是要写或修改 reports/ 下的报告、市场简报、策略研究、发给 Telegram 的文字，或用户说"检查一下这份报告"时使用。检查由脚本完成，不靠模型目测。
---

# 报告数据检查（qw-report-data-check）

改编自 tradermonty/claude-trading-skills 的 `data-quality-checker`（MIT，见 `LICENSE-UPSTREAM`）。
`scripts/check_data_quality.py` 与 `references/` 是上游原文件，未改动；`scripts/check_report_zh.py` 是本项目新增的包装脚本，导入上游函数并追加中文与项目规则。只用标准库，不联网。

**建议性工具**：发现问题只提示，永远 exit 0（输入不可用时才 exit 1）。不自动改写报告。

## 用法

```bash
.venv/bin/python .claude/skills/qw-report-data-check/scripts/check_report_zh.py \
  --file reports/STRATEGY_RESEARCH_2026-09-13.md --as-of 2026-09-13
```

- 默认只把 Markdown 结果打印到标准输出，**不写文件**，避免污染 `reports/`。需要留档时加 `--output-dir <目录>`。
- `--as-of` 用于没有写年份的日期（"9月21日（周一）"）推断年份；不给时依次从正文、文件名、当前年份推断。
- `--checks` 选子集，逗号分隔：`price_scale, notation, dates, allocations, units, dates_zh, wording, caveats`。

## 八项检查

| 检查 | 来源 | 内容 | 级别 |
|---|---|---|---|
| `dates_zh` | 本项目 | `2026年9月21日（周一）`、`9月21日 星期一`、`2026-09-21（周一）`、`9/21（周一）`；周、星期、礼拜三种写法；不存在的日期（2月30日）报 ERROR | WARNING / ERROR |
| `dates` | 上游 | 英文 `January 1, 2026 (Thu)`、日文 `1月1日（木）` | WARNING |
| `allocations` | 上游加中文关键词 | 标题或表头含 配置、权重、占比、配比、allocation、weight 的段落，百分比合计应约为 100% | WARNING |
| `price_scale` | 上游 | GLD 与 GC、SPY 与 SPX、SLV 与 SI、USO 与 CL 的价格位数和比例是否合理 | WARNING |
| `notation` | 上游 | 同一标的多种写法混用 | WARNING |
| `units` | 上游 | 标的与涨跌词同现却没有单位（$、%、bp）的裸数字 | INFO |
| `wording` | 本项目 | 建议买入、目标价、止损位、仓位建议、strong buy、price target 等措辞。同一行含否定或"字段、分析师、快照"等描述性词时不报；代码块内不报 | WARNING |
| `caveats` | 本项目 | 全文出现年化、Sharpe、最大回撤等绩效数字，却没有任何限定语（幸存者偏差、样本内、描述性、成本、基准、前视等） | WARNING |

## 已知误报（看到后判断，不必强行改文）

- **`notation` 会把 SPX 和 SPY 当成同一标的的两种写法。** 在期权语境里它们是不同产品（欧式现金结算对美式实物交割），同时出现是正确的。
- `wording` 是关键词匹配。引用第三方观点、描述数据字段时，如果那一行没有豁免词，会被报出；确认不是本报告自己给出指令即可。
- `caveats` 是全文级别的粗检查：只要全文有一处限定语就放行，不保证每个数字旁边都有限定。细查仍要走 `qw-backtest-audit`。
- `allocations` 只认标题或表头带关键词的段落。

## 与其他 skill 的分工

- 数字的**口径**对不对（复权、成本、基准、前视）：`qw-backtest-audit`。
- 数字**是不是只是 beta**：`qw-residual-edge`。
- 本 skill 只管**写出来的文字和数字有没有低级错误、有没有越红线**，是出门前最后一道。

## 资源

- `scripts/check_report_zh.py`：入口脚本（本项目新增）。
- `scripts/check_data_quality.py`：上游检查实现（原文件）。
- `scripts/tests/`：上游 51 个测试加本项目 7 个，`.venv/bin/python -m pytest .claude/skills/qw-report-data-check/scripts/tests`。
- `references/common_data_errors.md`、`references/instrument_notation_standard.md`：上游参考。
