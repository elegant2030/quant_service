---
name: qw-filing-interpretation
description: 用 LLM（本项目为 OpenAI Responses API + 结构化输出，见 src/quant_workbench/ai/openai_client.py）解读财报、10-K/10-Q/8-K、A 股年报/季报/业绩预告/业绩快报/公告/问询函、财报电话会和研报，输出利好/利空方向、影响周期、置信度、事实/观点/风险拆分。凡是用户要"解读""总结""判断利好利空""看看这份财报/公告说了什么"，或开发、修改 ai/ 下的提示词与 schema 时，必须使用本 skill。
---

# 财报与公告解读

硬约束（红线 2）：LLM 不计算任何数字。营收、增速、利润率、EPS、估值、Greeks 全部来自确定性代码；LLM 拿到的是已算好的数字表，只做理解、比对、解释和方向判断。

## 材料包（每次调用前组装）

```
material_pack:
  doc_id            accession_no / announcement_id
  symbol, market
  doc_type          10-K | 10-Q | 8-K | 6-K | cn_annual | cn_quarterly | cn_forecast | cn_express | cn_announcement | cn_inquiry | transcript | research
  published_at / accepted_at
  sections[]        {section_id, title, text}  只放需要的章节，不放整份文件
  metrics_table     确定性计算好的指标（TTM、同比、环比、与一致预期差、与自估差），带单位
  prior_doc_ref     上一期同类文件的关键结论（用于"变化"判断）
  sha256            全部材料的哈希
```

章节选择：
- 10-K/10-Q：MD&A（Item 7）、风险因素（Item 1A，只送与上期的 diff）、流动性、分部信息、指引相关段落。
- 8-K：item 编号 + EX-99.1 新闻稿全文。
- A 股年报/季报：管理层讨论与分析、主要会计数据、重要事项、股东变动；业绩预告/快报只有一两页，全送。
- 问询函/回复：全文；这类文件的利空权重通常高于表面。
- 电话会：管理层陈述 + Q&A，Q&A 中"回避的问题"是重点。

## 输出 schema（pydantic，字段名固定）

```
FilingInterpretation
  doc_id, symbol, market, doc_type
  facts[]            {statement, evidence: {section_id, quote(<=40字)}}   只写文件明确陈述的事
  opinions[]         {statement, holder: management|analyst|model}      区分谁的观点
  changes_vs_prior[] {topic, prior, current, direction}                  相对上一期
  guidance           {metric, low, high, period, vs_consensus: above|inline|below|na, credibility_note}
  red_flags[]        {type: accounting|liquidity|governance|customer_concentration|litigation|other, evidence}
  drivers[]          {driver: volume|price|margin|capex|share_count|multiple, direction: -2..2, horizon: days|quarter|year, rationale}
  direction          -2..2        综合利好/利空
  horizon            days|quarter|year
  confidence         0..1
  uncertainties[]    模型无法从材料判断的点
  questions_for_code[]  需要确定性代码核实的数值断言
  not_in_material[]  结论依赖但材料中没有的信息
```

规则：
- `facts` 每条必须带引用；引不出来就放 `opinions` 或 `uncertainties`。
- `direction` 的依据要能追溯到 `drivers`；没有 driver 支撑的方向判断置信度上限 0.4。
- 与一致预期或自估的比较只能用 `metrics_table` 里的数字；材料里没有的比较写进 `not_in_material`。
- 输出里不出现"建议买入/卖出"、目标价、仓位。

## 提示词要点

- 系统提示写明：你看到的数字已经算好；不要重算；不确定就说不确定；引用要短。
- 明确"新信息"标准：与 `prior_doc_ref` 相比新增或改变的内容才算 `changes_vs_prior`。
- 温度 0；同一材料重复三次取一致项做稳定性检查（开发阶段）。
- 中文材料用中文输出，英文材料英文输出，字段名不变。

## 落库（红线：可审计）

每次调用保存：`material_sha256`、来源 doc_id、`published_at`、`model`、`prompt_version`（`ai/prompts/<name>/v<N>.md` 的版本号）、完整结构化 JSON、token 与成本。存到 `event_scores` 或 `filing_interpretations` 数据集，并在 SQLite 记 CallRecord。

## 评估

- 评估集按 doc_type 分层，每类 50 条起，人工标注 `direction`、`guidance.vs_consensus`、`red_flags`。
- 只用模型知识截止日之后的文件做主指标；之前的文件做实体/日期脱敏后作参考。
- 校准：按 confidence 分桶，看方向命中率是否与置信度匹配。
- 每次改提示词版本都重跑评估集，结果写 `ai/evals/<prompt_version>.md`。

## 环境

```bash
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5-mini'    # 批量；深度解读换更强模型
```
没有 key 时报告必须注明"未调用 LLM，结论为确定性计算"，不得伪造解读。
