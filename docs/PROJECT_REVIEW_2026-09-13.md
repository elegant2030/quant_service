# Quant Workbench 项目审查（基于 AGENT_HANDOFF.md，2026-09-13）

审查对象：`docs/AGENT_HANDOFF.md` 所描述的现状。结论先行：**架构方向和红线都是对的**（Parquet 事实源 / SQLite 控制面 / quarantine / 幂等作业 / LLM 不做数值），主要问题集中在四类：结论可信度、数据口径、运行稳定性、设计缺口。按严重度排序，每条给出修法和建议归入的优先级。

严重度：🔴 会导致错误结论或数据损坏 · 🟠 会造成静默漂移或运维事故 · 🟡 影响效率或后续扩展

---

## 1. 结论可信度

### 🔴 1.1 回测没有基准对照，无法区分 alpha 和 beta
美股月度等权年化 27.73%、Sharpe 1.99，区间 2023-08 到 2026-09，用的是当前成分股。这个区间美股大盘本身是强牛市，等权 220 只大盘股跑出高 Sharpe 主要是 beta。报告里没有 SPY / 沪深300 同区间对照、没有超额收益、没有 β。
- 修：所有策略报告固定增加基准行（美股 SPY，A 股 000300 或等权池自身）、超额收益、年化 α、β、信息比率；报告标题标注"描述性回测，含幸存者偏差"。
- 归入 P0-4（walk-forward 任务）。

### 🔴 1.2 幸存者偏差已知但没有开始积累修正数据
文档承认用当前成分股回看历史。修正它不需要付费数据：从今天起每月把 `universe_us.csv` / `universe_cn.csv` 连同 `as_of` 快照落盘，一年后就有 12 个 PIT 股票池。同理一致预期、期权链、行业分类都应从现在开始每日/每月快照。
- 修：新增 `snapshot-universe` 作业（月频）、`snapshot-consensus` 作业（日频，yfinance 分析师预估 + AKShare 盈利预测），都走 raw gzip + canonical。
- 归入 P0，成本一天。

### 🟠 1.3 换手率定义不明确
"总换手 202.9 倍 / 约 65.9 倍每年"没有说明是单边还是双边、是否包含再平衡的自然漂移。不同定义差一倍，直接影响成本估计。
- 修：在 `backtest/` 里固定定义（建议单边换手 = Σ|Δw|/2），报告中注明。

### 🟠 1.4 期权指标缺少可比基准
QQQ ATM 跨式 5.63%、25Δ 偏斜 3.73% 这些数字没有历史分位就无法解释，文档也说了。但至少可以给出同一快照内的期限结构（多个到期日的 ATM IV）和跨标的对比（SPY vs QQQ）。
- 修：期权快照每天固定时间（美东 16:15 后）跑，从第一天起存 ATM IV、25Δ RR/BF 到独立 `iv_history` 数据集；快照分析报告加期限结构表。
- 另：Delta 用 3.913% 无风险利率但股息为 0，SPY/QQQ 股息率约 1% 量级，对 47 DTE 的 Delta 影响不大但对更长期限有影响；把 `dividend_yield` 作为输入字段而不是常量 0。

### 🟡 1.5 美股候选池混入 ADR
一致趋势候选中有 MUFG、HSBC（ADR）。ADR 有汇率成分和母市场交易时段错位，与本土股票放一起排序会失真。
- 修：`universe_us.csv` 增加 `instrument_type`（common / adr / etf）列，横截面分析默认过滤或单列。

---

## 2. 数据口径

### 🔴 2.1 yfinance 复权价会回溯变化，破坏可重现性
yfinance 返回的历史价在每次拆股/分红后整体重算，同一 `(symbol, date)` 的 close 今天和下个月拉到的值不同。文档要求日线保留 `adjustment` 字段，但如果存的是"已复权价"而不是"原始价 + 复权因子"，回测结果会随时间漂移，且 quarantine 的"与已有数据不一致"检查会大量误报。
- 修：canonical 日线存 `close_raw`、`adj_factor`（或 `close_raw` + `dividend` + `split`），复权在读取时计算；`adjustment` 字段取值固定为 `raw` / `adj_by_factor`；对已入库的 167k 行做一次回填。
- 归入 P0，这是所有后续回测的地基。

### 🟠 2.2 美股主源单一，30 分钟调度会放大限流风险
yfinance 是唯一美股源，pipeline 每 30 分钟触发。即使有 watermark 门控，一旦某天判定"未到期"逻辑出错，220 只×多次请求会触发限流甚至封 IP。
- 修：(a) 日线作业只允许每交易日成功一次（SQLite 记 `last_success_date`，同日不再发起请求）；(b) 接入 Stooq 批量 CSV 作为美股日线备源，实现 P1-1 的分区落地 + 对账；(c) 财务数据尽快切到 EDGAR（P0-1），不再用 yfinance 财务原型。

### 🟠 2.3 A 股行业分类没有时间戳
AKShare 的行业/概念分类是"当前"口径，申万行业每年调整。用当前行业做历史行业中性化或"相对强势行业"判断会有前视。
- 修：分类表加 `as_of`，月度快照；历史分析用不晚于分析日的最近快照。

### 🟠 2.4 BaoStock 复权口径要固定
BaoStock 的 `adjustflag` 有前复权/后复权/不复权三种，前复权同样会回溯变化。
- 修：只存不复权 + 复权因子（与 2.1 同一套逻辑），A 股的 `adj_factor` 用 BaoStock 的 `query_adjust_factor`。

### 🟡 2.5 时间字段语义要写进数据字典
红线 3 要求 `retrieved_at`、`effective_at`，但没有定义 `effective_at` 对不同数据集的含义（日线 = 交易日收盘？财报 = 报告期末？公告 = 发布时刻？）。P0 要做的 EDGAR/巨潮采集会立刻遇到 `published_at` / `accepted_at` / `period_end` 三个时间共存的问题。
- 修：在 ARCHITECTURE.md 加"时间字段字典"，每个数据集显式列出 PIT 截断用哪一列。见 `qw-pit-data-provider` skill。

---

## 3. 运行稳定性

### 🔴 3.1 仓库没有基线提交
所有源码、文档、ops 脚本都是未跟踪状态，一次误操作就没了。这是当前最大的单点风险。
- 修：确认 `.gitignore` 覆盖 `data/`、`.env`、`.venv` 后创建首次提交并打 tag `mvp-2026-09-13`；提交前 `git grep -i "sk-"` 确认没有密钥。这一步由用户决定执行，但应作为接手第一件事提醒。

### 🟠 3.2 macOS 睡眠 = 数据缺口
LaunchAgent 在深度睡眠时不触发，周末/夜间的加密采集（未来）和美股盘后作业会丢。
- 短期：`sudo pmset -c sleep 0 disksleep 0`（接电源时不睡眠）+ `pmset repeat wakeorpoweron MTWRFSU 15:55:00`；或用 `caffeinate -s` 包裹 pipeline。
- 中期：迁 Ubuntu/systemd（文档已提）。此外 launchd 的 `KeepAlive`/`StartInterval` 在睡眠后不会补跑错过的触发，orchestrator 的 `run-due` 已经能覆盖"补跑"，这点设计是对的。

### 🟠 3.3 没有告警渠道
watchdog 只写本地 JSON。无人看的健康检查等于没有。
- 修：Telegram Bot 约 30 行（`ops/alert.py`），watchdog 在 strict 失败、quarantine 新增、作业连续 2 次 exit≠0 时推送；每日一条心跳消息（"今天 3 个作业成功，0 隔离"）防止"静默死亡"。

### 🟠 3.4 双运行时的版本漂移不可见
开发仓库和 `Application Support` 是两份代码，后者是非 editable 安装。如果忘了跑部署脚本，后台跑的是旧版本而没人知道。
- 修：打包时把 git sha 写入包元数据，`health/latest.json` 输出 `build_sha` 和 `deployed_at`；watchdog 比较开发仓库 HEAD 与运行时 sha，不一致时在健康报告里标黄。

### 🟡 3.5 备份在同一块盘
P1 已列。rclone 到任意免费云盘或外置盘，每晚一次，保留 30 天，且每周做一次恢复演练（从备份重建 DuckDB 查询并比对行数）。

### 🟡 3.6 测试覆盖偏薄
13 个单测覆盖 ops 路径，数据校验、日历边界（NYSE 半日、A 股节假日、停牌）、复权回填都没有测试。P0 的 EDGAR 采集建议用录制的响应做 fixture，避免测试打真实网络。

---

## 4. 设计缺口（相对已定方案）

### 🟠 4.1 事件模型和预期账本没有进 P0
P0 只写了"采集 EDGAR / 巨潮 / PIT 财务表"，但没有定义采集之后的事件 schema、事件分类枚举、预期账本表结构。没有 schema，LLM 输出就没有落点，采集完仍然只是原文堆积。
- 修：P0 增加 "events + event_scores + expectation_ledger 三张表的 schema 与迁移"，先用 pydantic 定义，再落 Parquet/SQLite。`qw-event-scoring` skill 附带了枚举和字段定义。

### 🟠 4.2 LLM 评估没有防知识泄漏的设计
`openai_client.py` 目标包含"利好/利空判断"，但没有评估集设计。用历史公告测 LLM 会因为模型知道后续走势而高估。
- 修：评估集只用模型知识截止日之后的样本做主指标；上线后所有 LLM 输出落库形成 forward test；历史样本仅做脱敏后的参考。

### 🟡 4.3 OpenAI 直连没有网关层
只有 OpenAI 一个 provider。批量分类任务（去重、实体链接、事件初筛）用 gpt-5-mini 也会累积成本，且模型不可替换。
- 修：`ai/gateway.py` 定义 `LLMGateway.complete(schema, messages, tier)` 接口，OpenAI 为第一实现；`tier` 分 bulk / analysis / deep，方便后续接本地 Qwen 或 DeepSeek。不必现在引入 LiteLLM，但接口先留好。

### 🟡 4.4 加密日历与交易日历不兼容
`ops/calendar.py` 只有 XNYS/XSHG。加密 7×24，"最近一个已完成交易日"的概念要改成"最近一个已完成 UTC 日"或按 8h funding 周期。CCXT 接入前先扩展日历抽象。

---

## 5. 文档层面

- "Massive / Alpaca" 作为美股正式 API 候选：确认 Massive 的免费额度和许可范围后再写入决策文档，避免下一位 Agent 误以为已评估。
- `ops-status` 输出 440 条水位过长的问题，建议加 `--summary` 参数，默认只输出分市场最小/最大水位和落后证券数。
- "13 个单元测试通过"和"ruff 部分通过"应写进 `health/latest.json` 或 CI，而不是只在交接文档里。

---

## 6. 建议的 P0 调整（合并原 P0 与本审查）

1. 首次 Git 提交 + tag（用户执行）
2. 日线改为原始价 + 复权因子存储并回填（2.1 / 2.4）
3. 股票池、一致预期、行业分类、期权 IV 的定期快照作业（1.2 / 1.4 / 2.3）
4. EDGAR 增量采集（原 P0-1）+ events / event_scores / expectation_ledger schema（4.1）
5. 巨潮公告采集（原 P0-2）
6. 回测报告加基准与换手定义（1.1 / 1.3）
7. Telegram 告警 + build_sha 健康字段（3.3 / 3.4）

其余按原 P1 / P2 顺序。
