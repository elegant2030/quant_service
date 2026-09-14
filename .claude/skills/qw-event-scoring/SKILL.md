---
name: qw-event-scoring
description: 为 Quant Workbench 的事件层工作：给新闻、公告、政策文件、宏观数据、产业链事件打分（方向、强度、新颖度、是否已定价、影响的基本面驱动项），定义事件分类枚举，做事件研究（CAR、反应模板），把事件映射到产业链节点和预期账本。用户提到事件驱动、消息面、利好利空批量打分、政策解读、产业链传导、event study、novelty、priced-in、事件 taxonomy 或要建 events / event_scores 表时，必须使用本 skill。单份财报的深度解读用 qw-filing-interpretation。
---

# 事件打分与事件研究

事件层把"消息面"变成可回测的数值。原则：每条事件打分都能追溯到材料哈希和提示词版本；能量化到基本面的进预期账本，不能量化的只走短期反应模板；时间戳精确到秒并转成"下一个可交易 bar"。

## 分类枚举

见 `references/taxonomy.md`。LLM 只能从枚举里选，不能自创类型；新类型先改枚举再上线。

## 打分 schema

```
EventScore
  event_id, symbol(s), market, event_type(枚举), subtype(枚举)
  published_at(UTC), tradable_at(下一可交易 bar 开盘, 由日历计算, 不由 LLM 给)
  direction        -2..2
  magnitude        对基本面的影响量级: none | <1% | 1-5% | 5-15% | >15%（营收或利润口径）
  novelty          0..1  与同主体最近 30 天已知信息的差异度
  scope            single | chain | sector | market
  horizon          days | quarter | year
  certainty        rumor | likely | confirmed
  affected_driver  volume | price | margin | capex | share_count | multiple | none
  driver_delta     {low, high, fiscal_year}   仅 maps_to_estimate=true 时填
  maps_to_estimate true=写入预期账本; false=只走反应模板
  priced_in_hint   {pre_move_5d, iv_change, attention}   程序算好喂给 LLM, LLM 只引用
  affected_nodes[] {node_id, direction}  产业链图谱节点
  evidence[]       {quote(<=40字), position}
  confidence       0..1
  prompt_version, model, material_sha256
```

## 流水线

```
新材料 → 去重(标题+正文 simhash) → 实体链接(→instrument_id) → 分类+打分(LLM)
      → tradable_at(日历) → priced_in 特征(程序) → 落 event_scores
      → maps_to_estimate ? 写预期账本 revision : 只进反应模板
```

- 去重和实体链接优先规则（股票代码、公司全称/简称表），LLM 只处理规则失败的。
- 盘后消息（A 股 15:00 后、美股 16:00 ET 后）`tradable_at` 是下一交易日开盘；这条规则回测和实盘一致。
- 政策文件另加 `policy_stage`: signal | draft | official | detail | funded，以及 `issuer_level`: state_council | ministry | provincial | exchange。同一政策四个阶段的市场反应不同，回测要分开统计。

## 事件研究

- 对每个 `(event_type, market)` 计算 CAR 曲线，窗口 [-5, +20] 交易日，基准用市场或行业。
- 样本量 < 200 的类型不建反应模板，退化为方向 + 固定半衰期（默认 5 日）。
- 模板按周期状态条件化（同一"扩产"公告在行业上行/下行期方向相反）；周期标签来自 `regime/`。
- 信号 = `direction × magnitude_weight × novelty × (1 − priced_in) × template(t)`。
- 传导：命中产业链节点后一阶按边权，二阶乘 0.3–0.5 衰减，衰减系数用历史传导 CAR 校准，不凭逻辑画。

## 泄漏与评估

- 用历史事件给 LLM 打分会泄漏（模型知道后来的走势）。主指标只用模型知识截止日之后的事件；之前的事件脱敏（替换公司名与日期）后仅作参考。
- 上线后每条打分落库即形成 forward test；按 `tradable_at` 后 5/20 日超额收益计算 IC。
- 校准表：confidence 分桶 vs 方向命中率；`driver_delta` 与后续一致预期修正幅度比较。
- 一致性：抽样 5% 材料重复打分，方向不一致率 > 15% 时降低该模型在 bulk 档的使用。

## 存储

- `events`：原始材料索引（一行一条），带 raw 路径和 sha256。
- `event_scores`：一行一次打分；同一事件可多版本，查询取最新 `prompt_version`。
- `ledger_revisions`：`maps_to_estimate=true` 的打分写入，`source_event_id` 回指。
- 全部 Parquet 落地，作业状态和成本进 SQLite。

## 与代码的对应

- 新包 `src/quant_workbench/events/`：`taxonomy.py`、`schemas.py`、`linker.py`、`scorer.py`、`event_study.py`。
- LLM 调用走 `ai/` 的网关接口，tier=bulk；深度复核 tier=analysis。
- 数据采集（EDGAR 8-K、巨潮公告、政策网站）按 `qw-pit-data-provider` 立项，本 skill 不管采集。
