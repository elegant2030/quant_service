---
name: qw-thesis-tracker
description: 为 Quant Workbench 的候选标的建立和维护"研究论点"：一句话论点、3 到 5 个可证伪支柱（各带度量指标、预期和证伪条件）、失效风险、催化剂日历、证据登记、只追加的更新日志和支柱记分卡。对应 CLAUDE.md 5.2 预期账本里的 thesis 字段。用户说"给 XX 建个论点""更新 XX 的论点""这个论点还成立吗""加一条数据点""复盘我的关注列表"，或财报、公告、事件打分之后需要判断对论点的影响时使用。不输出目标价、止损、买卖动作或仓位。
---

# 研究论点跟踪（qw-thesis-tracker）

改编自 anthropics/financial-services 的 equity-research `thesis-tracker`（Apache-2.0，见 `LICENSE-UPSTREAM`）。

**相对上游的改动**：上游记录 Long/Short 仓位、目标价、止损触发条件，并在每次更新时给出"不变、加仓、减仓、退出"动作，这些违反本项目红线 1，已全部移除。替换为：预期差定位、描述性估值锚、研究状态和证据强度。另外新增了证据登记（发布时间加原文哈希，满足 PIT 与可追溯）、与事件层的 `source_event_id` 对接，以及一个确定性的校验脚本。

## 原则

1. **论点必须可证伪。** 每个支柱都要写明度量指标、预期值和证伪条件。如果没有任何事实能推翻它，它就不是论点。
2. **反面证据与正面证据同等记录。** 更新日志的 `disconfirming` 字段就是为此而设；连续四次更新都没有反面证据，校验脚本会提醒。
3. **数字来自确定性代码。** 支柱的当前值、估值区间、预期差都引用数据集或脚本输出；LLM 只负责把证据映射到支柱并写叙述（红线 2）。
4. **论点是研究记录，不是交易计划。** 状态只有：`active`（成立）、`weakened`（削弱）、`falsified`（证伪）、`watching`（观察中）、`retired`（归档）。
5. **只追加，不改写。** 更新日志按时间顺序追加。修正旧判断时追加一条新记录说明，不回头改旧条目。
6. **至少每季度复核一次**，即使没有大事发生。

## 记录结构

一只标的一个文件：`research/theses/<market>_<symbol>.yaml`。模板见 `references/thesis_template.yaml`。

| 区块 | 内容 | 与预期账本（CLAUDE.md 5.2）的对应 |
|---|---|---|
| `statement` | 一到两句话，对**基本面**的方向性看法 | `thesis` |
| `expectation_gap` | 论点押注的驱动项（量、价、利润率等）、自估、对照的一致预期快照 | `own_estimate`、`consensus`、`expectation_gap` |
| `pillars` | 3 到 5 个：`claim`、`metric`、`expectation`、`kill_criterion`、`current`、`trend`、`evidence` | `thesis` 的假设与监控指标 |
| `risks` | 3 到 5 条会让论点失效的风险 | — |
| `catalysts` | 日期、事件、检验哪些支柱、盘前或盘后发布对应的可交易时点说明 | 事件层 `tradable_at` |
| `valuation_anchor` | 方法和观察到的区间，仅作背景 | `valuation_anchor` |
| `evidence` | `id`、`kind`、`reference`、`published_at`、`material_sha256` | 材料包，红线 4 的 PIT 截断 |
| `updates` | `date`、`data_point`、`source_event_id`、`pillar_impacts`、`disconfirming`、`evidence_strength`、`note` | `revision_history` |

## 流程

### 1. 新建或载入

新建时依次确认：标的与市场；论点陈述；押注的是哪个驱动项、与一致预期差在哪里；3 到 5 个支柱，每个都问一句"什么结果出现就算错了"；3 到 5 条失效风险；未来的催化剂。

一致预期的对照值从 `consensus_estimates` 快照取（T2），引用时写明快照日期。没有快照的市场（A 股）如实写"无一致预期数据"。

### 2. 记录更新

每条新信息（财报、公告、事件打分结果、行业数据）：

- 先登记证据：来源引用、发布时间、原文哈希。没有落库的材料，哈希先留占位，校验会给 WARNING。
- 写 `data_point`：发生了什么，用事实陈述。
- 写 `pillar_impacts`：对哪个支柱，`strengthens`、`weakens`、`neutral` 还是 `falsifies`。
- 标 `disconfirming`：这条信息是不是与论点相反。
- 标 `evidence_strength`：`low`、`medium`、`high`，依据证据质量而不是结论方向。
- 如来自事件层，填 `source_event_id`。

某个支柱触发证伪条件时，把它的 `trend` 改为 `broken`，并把论点 `status` 改为 `weakened` 或 `falsified`。校验脚本不允许"有支柱 broken 而状态仍是 active"。

### 3. 记分卡

输出一张表：支柱、原始预期、当前值、趋势、最近一次影响它的证据。当前值必须能回指到数据集或文件。

### 4. 催化剂日历

列出未来事件、日期、它检验哪些支柱。盘后发布的事件注明"下一交易日开盘生效"，与回测口径一致。

### 5. 校验与输出

```bash
.venv/bin/python .claude/skills/qw-thesis-tracker/scripts/validate_thesis.py research/theses/us_ACME.yaml
```

退出码：0 通过（可有 WARNING），1 不合规，2 文件不可用。校验内容：

- 必填区块齐全；支柱缺 `metric`、`expectation` 或 `kill_criterion` 即不合规。
- **任何位置出现** `target_price`、`stop_loss`、`position_size`、`action`、`rating` 等字段，或"建议买入""目标价 150"这类措辞，即不合规（同一句带否定说明的除外）。
- 支柱和催化剂引用的证据与支柱编号必须存在；证据必须有 `published_at`。
- 更新日志按时间顺序、不得晚于今天、`evidence_strength` 取值合法。
- 状态一致性；超过 92 天未复核给 WARNING；四次以上更新没有任何反面证据给 WARNING。

给用户的输出用简明 Markdown：论点陈述、记分卡、最近更新、当前状态与证据强度、下一个催化剂。写完走一遍 `qw-report-data-check`。

## 不做的事

- 不给目标价、止损位、买卖或增减仓动作、仓位建议。
- 不由 LLM 估算任何财务数字或估值倍数。
- 不把样本内回测表现写成论点成立的证据；那是 `qw-backtest-audit` 和 `qw-residual-edge` 的范围。
- 不把论点状态自动同步成任何交易行为。

## 与其他 skill 的衔接

- `qw-filing-interpretation`：财报与公告解读的结构化输出，是更新日志最主要的输入。
- `qw-event-scoring`：事件打分里的 `affected_driver`、`driver_delta` 对应这里的 `expectation_gap.driver` 和 `pillar_impacts`。
- `qw-strategy-red-team`：新建论点后可请它专门找失效路径，补进 `risks` 和各支柱的证伪条件。
- `qw-report-data-check`：对外输出前的最后检查。

## 资源

- `references/thesis_template.yaml`：完整示例，可直接复制修改。
- `scripts/validate_thesis.py`：确定性校验，支持 YAML（需要环境里有 PyYAML）和 JSON。
- `scripts/tests/`：13 个测试，`.venv/bin/python -m pytest .claude/skills/qw-thesis-tracker/scripts/tests`。
