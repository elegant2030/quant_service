# CLAUDE.md — Quant Workbench 项目全景（给 Claude Code）

最后更新：2026-09-13。本文件是 Claude Code 进入本仓库的唯一入口，读完即可开工；细节再去读第 12 节列出的文档。

## 1. 这是什么项目，现在到哪一步

目标：一个本地运行的中低频量化研究平台，覆盖美股、A 股、美股期权、加密现货/永续，策略以基本面 × 消息面双主线为核心（财报、公告、政策、产业链传导、周期为条件先验），用 OpenAI 做有证据约束的财报/公告解读和利好利空判断。24 小时稳定运行，数据可追溯、作业可重跑、故障可隔离。

它不是什么：不是自动交易系统。所有输出是候选池和研究结论，不下单。

当前阶段：本地 MVP 已跑通——

| 已落地 | 未落地 |
|---|---|
| 美股 220 只 + A 股 220 只日线（2023-08-15 → 2026-09-11） | SEC EDGAR / 巨潮公告采集 |
| SPY/QQQ 期权链单日快照（2,117 合约） | point-in-time 财务表、一致预期 |
| 横截面动量策略 + 无未来函数回测引擎 | 事件层（taxonomy / 打分 / 事件研究） |
| Black–Scholes、Greeks、IV；永续资金费率计算基础 | 预期账本、产业链图谱、周期状态机 |
| SQLite 控制面、水位、来源健康、quarantine | provider 自动备源切换 |
| macOS LaunchAgent：pipeline(30min) / watchdog(15min) / backup(02:30) | Telegram 告警、异地备份 |
| OpenAI Responses API 客户端（结构化输出） | 加密行情（CCXT）、IBKR、模拟盘 |
| 13 个单测通过 | 首次 Git 提交（仓库全部未跟踪！） |

## 2. 两个目录，一条铁律

| 用途 | 路径 |
|---|---|
| 开发仓库（改代码只在这里） | `/Users/lucky/Workspace/Quant` |
| 开发 venv | `.venv/` |
| 开发数据（gitignored） | `data/cache/`、`data/lake/` |
| 后台运行时（不要直接改） | `/Users/lucky/Library/Application Support/QuantWorkbench` |
| 后台 venv（非 editable 安装） | `.../QuantWorkbench/venv` |
| 后台生产数据 | `.../QuantWorkbench/data/{canonical,raw,quarantine,state,health,logs,backups}` |
| 后台股票池配置 | `.../QuantWorkbench/config/universe_{us,cn}.csv` |

原因：LaunchAgent 读 Documents 下的 venv 会被 macOS 隐私保护拒绝，所以运行时整体复制到 Application Support。改完源码要生效，必须跑 `./ops/deploy_macos_runtime.sh`。检查后台数据时 CLI 必须带 `--root '<Application Support>/data'`，否则查的是开发数据湖。

## 3. 架构

```text
yfinance / BaoStock / AKShare（后续 EDGAR、巨潮、CCXT）
        │  下载 → 校验(pandera 风格) → 标准化
        ├─ raw/  gzip + manifest(sha256)
        ├─ quarantine/  校验失败的数据，不入 canonical
        ▼
canonical/  Parquet = 事实数据源
        ├─ DuckDB  只读分析
        ├─ Python  因子 / 回测 / 期权 / 报告
        └─ OpenAI  只做有证据的语义分析
SQLite(WAL)  作业、逐证券水位、来源健康、熔断、运行记录
LaunchAgent  pipeline / watchdog / backup
```

代码布局（`src/quant_workbench/`）：

- `data/` provider（yfinance、baostock、akshare、csv）、validation、universe
- `store/files.py` Parquet/raw/manifest/原子写；`store/state.py` SQLite 控制面
- `jobs/ingestion.py` 采集；`jobs/orchestrator.py` 按交易日历判断到期
- `ops/calendar.py`（XNYS/XSHG，最近已完成交易日）、`ops/health.py`、`ops/backup.py`、`ops/lock.py`
- `core/classification.py` PIT 板块/行业成员（`valid_from/valid_to` + `available_from` 知识时钟）；`backtest/`（引擎在除权日处理拆股、分红、因子再投资）、`strategy/`（横截面动量、板块动量 `SectorMomentum`、带时钟的 `ContextStrategy`）、`fundamentals/scoring.py`（评分模型基础，不是 PIT 数据）
- `derivatives/options.py`（BS/Greeks/IV）、`derivatives/perpetual.py`
- `ai/openai_client.py`（Responses API + 结构化输出；当前机器无 OPENAI_API_KEY，现有报告未调用 LLM）
- `cli.py`（入口 `quant-workbench`）；`scripts/run_strategy_research.py`、`scripts/run_current_market_analysis.py`
- `ops/launchd/` plist 模板、`ops/deploy_macos_runtime.sh`、`ops/disable_macos_agents.sh`
- `tests/`、`reports/`、`docs/`

## 4. 数据源现状与决策

免费为主，每类数据至少两个源，备源独立落地对账、不静默覆盖。

| 数据 | 主源 | 备源/计划 | 已知限制 |
|---|---|---|---|
| 美股日线、财务原型、期权链 | yfinance | Stooq 批量 CSV（待接）、IBKR 延迟行情（待接） | 非官方接口，限流/格式变更常见；返回的复权价会回溯变化 |
| 美股财报/8-K/Form 4 | —（待接） | SEC EDGAR via edgartools（MIT，无 key，10 req/s） | P0 |
| 美股一致预期 | —（待接） | yfinance 分析师预估每日快照自建 PIT | 免费无历史 |
| A 股日线 | BaoStock（免注册） | AKShare（东财）、Tushare 120 积分档 | BaoStock 无实时；AKShare 依赖网页，可能空表；Tushare 2025-08 曾停运 |
| A 股公告/快讯 | —（待接） | 巨潮（AKShare 封装）+ 自下载 PDF；财联社 | P0 |
| A 股行业分类 | AKShare | — | 无时间戳，需月度快照 |
| 加密 | —（待接） | CCXT 公共接口、Deribit 公共 API、DefiLlama | 日历需扩展为 UTC/8h |
| 宏观 | —（待接） | FRED、AKShare 宏观 | — |

合规边界：不自动抓取 CBOE 延迟报价网页。免费源不描述为有生产 SLA。

## 5. 目标设计（已定，代码尚未实现）

### 5.1 四层漏斗

```text
周期(regime) → 方向与仓位
  └ 产业链 → 标的池（一阶/二阶受益）
      └ 事件 → 时机与强度
          └ 基本面 → 筛选与估值锚
```

### 5.2 预期账本（Expectation Ledger）——基本面与消息面的共同量纲

每只股票一条随时间演化的记录：`fundamentals_state`、`consensus`（一致预期，自建 PIT 快照）、`own_estimate`（驱动因子模型：量×价×利润率）、`expectation_gap`、`revision_history`（每次修正回指 `source_event_id`）、`thesis`（持仓论点 + 假设 + 监控指标）、`valuation_anchor`。消息面引擎的输出是对 `own_estimate` 某个驱动项的修正，不是"利好 +1"。

### 5.3 事件层

- 分类枚举：corporate / industry_policy / macro / supply_chain / crypto，子类见 `.claude/skills/qw-event-scoring/references/taxonomy.md`
- 打分 schema：direction、magnitude、novelty、scope、horizon、certainty、affected_driver、driver_delta、maps_to_estimate、priced_in_hint、evidence、confidence、prompt_version、material_sha256
- 时间：`published_at` 精确到秒 → `tradable_at` 由日历算成下一可交易 bar；盘后消息下一开盘生效，回测实盘一致
- 事件研究：按 (event_type, market) 算 CAR [-5,+20]，样本 <200 不建模板；政策按阶段（吹风/征求意见/正式/细则/资金到位）分开统计
- 三张表：`events`、`event_scores`、`ledger_revisions`

### 5.4 财报流水线（两条主线的交汇）

一次财报产出：结构化数据更新 → 实际 vs 一致预期 vs 自估三方对比 → 指引解析 + 兑现率 → 电话会定性提取 → 论点检验。PEAD 是自然产物。

### 5.5 产业链与周期

- 图谱：节点（公司/环节/原料/终端需求/政策主题）、边（供应/客户/竞争/替代/持股）带权重、弹性、valid_from/valid_to；Postgres 边表可换成 Parquet + NetworkX；先做 2–3 条链（AI 算力、新能源车、半导体）
- 周期：宏观三维（增长/通胀/流动性）规则 + HMM；行业库存/capex/价格周期；只作条件先验，不直接产生个股信号

### 5.6 LLM 分层

- bulk（去重/分类/实体链接）：便宜模型或本地模型
- analysis（事件打分 + 基本面映射）：gpt-5-mini 或 DeepSeek
- deep（财报深度解读、论点维护）：强模型
- 接口 `ai/gateway.py`：`complete(schema, messages, tier, prompt_version, material_sha256) -> (result, CallRecord)`，OpenAI 为首个实现；每次调用落库 model/prompt_version/material_sha256/tokens/cost
- 评估：只用模型知识截止日之后的样本做主指标；历史样本脱敏仅参考；上线后所有输出落库形成 forward test；做校准表和一致性检查

### 5.7 首个完整策略

美股"预期差 + 催化剂"（EDGAR 数据最干净）；A 股"政策受益链"排第二。

## 6. 项目审查结论（2026-09-13，全文 docs/PROJECT_REVIEW_2026-09-13.md）

按严重度：

- 🔴 仓库无基线提交——先 `git add` + commit + tag `mvp-2026-09-13`（提交前 `git grep -i "sk-"` 查密钥；由用户决定执行）
- 🔴 日线存的是可回溯变化的复权价——改为 `close_raw` + `adj_factor`，读取时复权，回填已有 33 万行
- 🔴 回测无基准——报告加 SPY/000300 对照、超额、α/β；现有 27.7%/Sharpe 1.99 主要是牛市 beta
- 🟠 幸存者偏差修正数据应从今天开始积累：股票池月度快照、一致预期日快照、行业分类月快照、期权 IV 日快照
- 🟠 换手率定义不明；ADR（MUFG/HSBC）混在美股池；期权股息率为 0
- 🟠 无告警渠道；双运行时版本漂移不可见（health 加 build_sha）；Mac 睡眠导致缺口
- 🟠 事件 schema 和预期账本未进 P0；LLM 评估没有防泄漏设计
- 🟡 无网关层；日历不支持加密；测试覆盖薄（数据校验、日历边界无测试）

## 7. 已加入仓库的新内容（若 `.claude/skills/` 不存在，说明尚未安装，按 INSTALL.md 安装）

- `.claude/skills/qw-pit-data-provider` 新增数据源/数据集/快照/备源的规程
- `.claude/skills/qw-backtest-audit` 任何回测数字出门前的审计清单与报告模板
- `.claude/skills/qw-strategy-red-team` 策略对抗审查
- `.claude/skills/qw-filing-interpretation` 财报/公告解读的材料包、schema、落库、评估
- `.claude/skills/qw-event-scoring` 事件打分、taxonomy、事件研究（附枚举表）
- `.claude/skills/qw-options-snapshot-review` 期权快照流动性过滤、指标口径、IV 历史
- `.claude/skills/qw-ops-triage` 后台排查、部署、回滚、备份演练
- `docs/PROJECT_REVIEW_2026-09-13.md`、`docs/FRAMEWORK_INTEGRATION.md`
- `AGENTS.md`（已合并规程摘要）

做对应类型的任务前先读对应 skill。

## 8. 开源框架决策（全文 docs/FRAMEWORK_INTEGRATION.md）

| 引入 | 用途 | 时机 |
|---|---|---|
| edgartools（MIT） | EDGAR 采集与 XBRL 标准化；`accepted_at` 以 SEC submissions 的 acceptanceDateTime 为准 | 现在 |
| py_vollib（MIT） | IV/Greeks 加速 | 现在 |
| CCXT（MIT） | 加密 OHLCV / funding / OI，仅公共接口 | P2 |
| Riskfolio-Lib（BSD）+ skfolio（BSD） | 组合优化；LLM 观点作为 Black-Litterman views | P2 |
| ib_async | IBKR 只读账户 → paper | P2 |

只借设计：TradingAgents（结构化输出 Agent、provider registry、数据契约）、FinRobot（数值由算子、叙述由 LLM）、Jesse（零前视回测约束）。
延后：RD-Agent / Alpha-Agent / QuantaAlpha（需 Qlib 数据格式，PIT 财务表就绪后再接）；vnpy（A 股执行，用户授权后）。
不引入：Freqtrade / OctoBot（GPL）、ai-hedge-fund、NautilusTrader（过重）、Backtrader（无维护）、TradingAgents-CN 整库（单人维护）。

## 9. 红线（改代码时逐条对照）

1. 不自动下单；不给买卖指令、目标价、仓位
2. LLM 不算价格、收益率、Greeks、仓位、保证金、限额；数字来自确定性代码
3. 新数据集必带 `source, source_symbol, symbol, market, retrieved_at, effective_at, schema_version`；日线带 `adjustment`，存原始价 + 复权因子
4. 财务与事件按 `accepted_at` / `published_at` 做 PIT 截断；修订作为新行回指原件，不覆盖
5. 备源独立落地、对账、记录差异；不静默覆盖主源
6. DuckDB 只读 Parquet；状态在 SQLite WAL
7. 回测写明日历、信号时点、成交时点、复权、费用、滑点、不可成交约束，并给基准
8. 期权用 bid/ask、成交量、持仓量、乘数；中间价不是成交价
9. 免费源不描述为有生产 SLA
10. 改完在开发环境验证；需要后台生效才部署；不修改 Application Support 内文件

## 10. 现在该做什么（按顺序，每项有验收）

### T0 保护现状
提醒用户创建首次提交并打 tag；在此之前不做任何删除/重构。

### T1 日线改为原始价 + 复权因子（审查 2.1/2.4）— 已完成：开发湖 2026-09-13 验收，后台生产湖 2026-09-14 回填（旧分区在 `archive/2026-09-14/`），2026-09-21 核对后台日线全部为 schema 2、无重复键
- schema_version 2：`open/high/low/close/volume` 存真实成交价（yfinance 的拆股回溯已还原，含分红金额），`dividend`、`split_ratio`、`adj_factor`（单次事件后复权因子）、`adjustment=raw`；复权在读取时用 `data/adjust.py::apply_adjustment(mode="forward"|"back")` 计算。数据字典见 `docs/DATA_SOURCE_DECISION.md`
- `quant-workbench backfill-daily --market us|cn` 全量重拉并把旧 schema 1 分区移到 `archive/<日期>/`（不删除），输出新旧价格差异分布
- 验收（开发数据湖）：美股 167,471 行 / A 股 162,811 行，各 220 只，0 行隔离；同参数重跑两次原始收盘价逐行一致（最大差 0）；新前复权价与原 provider 复权价中位差 1e-7 量级、无一行超过 0.1%，即现有回测结论不受影响。已知来源特性：Yahoo 把 SCCO 股票股利记为 1.005 等小拆股；BaoStock 000002 在 2025-01-09 有一个 <1 的因子，导致其前复权价与 BaoStock 自家复权序列差 1.2%（唯一超过 0.1% 的股票）；停牌期间的除权日因子顺延到下一交易日（600027 2024-07-25）；BaoStock 匿名会话会被其他进程登录顶掉，provider 已自动重登

### T2 快照作业（审查 1.2/1.4/2.3）
- `snapshot-universe`（月初）、`snapshot-consensus`（每日）、`snapshot-industry`（月初）、期权快照固定 16:15 ET 后并写 `iv_history`
- 验收：raw gzip + canonical 各一份；orchestrator 到期规则；watchdog 新鲜度项

### T3 EDGAR 采集 + 事件三表 schema（原 P0-1 + 审查 4.1）
- `data/providers/edgar.py`（edgartools），`filings_index` / `filings_text` 数据集，水位用 `accession_no`
- `events/schemas.py`、`events/taxonomy.py` 用 pydantic 定义 `events` / `event_scores` / `ledger_revisions`
- 验收：用录制响应做 fixture 的单测；PIT 查询 `accepted_at <= t` 示例通过

### T4 回测报告加基准与换手定义（审查 1.1/1.3）
- `backtest/` 固定单边换手 Σ|Δw|/2；报告模板含基准行、超额、α/β、状态标签（描述性/样本外/可交易候选）
- 验收：重生成 `reports/STRATEGY_RESEARCH_<新日期>.md`，走 qw-backtest-audit

### T5 告警与版本可见（审查 3.3/3.4）— 已完成并验收 2026-09-13（后台已部署，alerts.env 已配置）
- `ops/alert.py` Telegram；watchdog 在 strict 失败、quarantine 新增、连续 2 次 exit≠0 时推送，每日心跳
- 打包写入 git sha；`health/latest.json` 输出 `build_sha`、`deployed_at`
- 验收：人为制造一次校验失败，收到告警
- 用法与规则见 `docs/ALERTING.md`；凭据只放后台 `config/alerts.env`（模板 `ops/alerts.env.example`）；测试命令 `quant-workbench alert-test`

### T6 巨潮公告采集（原 P0-2）
- `data/providers/cninfo.py`，`announcements_cn` 数据集，PDF 落 raw/，`supersedes_id` 回指更正公告

然后：`ai/gateway.py` 接口 + 事件打分 agent + 评估集（50 条/类人工标注）→ 首个"预期差 + 催化剂"策略回测 → P1/P2 按原顺序。

## 11. 常用命令

```bash
cd '/Users/lucky/Workspace/Quant'
source .venv/bin/activate

# 开工检查
git status --short
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src/quant_workbench/store src/quant_workbench/ops src/quant_workbench/jobs src/quant_workbench/cli.py tests/test_ops.py --select E,F,I,B

# 数据作业（开发数据湖）
quant-workbench ops-init
quant-workbench ingest-daily --market us
quant-workbench ingest-daily --market cn
quant-workbench snapshot-options --symbols SPY QQQ
quant-workbench run-due --strict
quant-workbench watchdog --full --strict
quant-workbench ops-backup

# 后台运行时（必须 --root）
'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' watchdog \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data' --full --strict
launchctl print gui/501/com.quantworkbench.pipeline | grep -E 'state|last exit|runs'

# 部署 / 停用
./ops/deploy_macos_runtime.sh
./ops/disable_macos_agents.sh
```

`ops-status` 输出 440 条水位，只看摘要，不要把全量贴进对话。整个旧代码库 `ruff check .` 仍有样式警告，不要声称全部干净。

## 12. 文档索引

- `docs/AGENT_HANDOFF.md` — 交接文档（现状、地址、红线全文、CLI）
- `docs/PROJECT_REVIEW_2026-09-13.md` — 审查与合并后的 P0
- `docs/FRAMEWORK_INTEGRATION.md` — 框架接入（含 EDGAR provider 骨架、表结构）
- `docs/ARCHITECTURE.md`、`docs/DATA_SOURCE_DECISION.md`、`docs/LOCAL_MVP_EXECUTION_PLAN.md`
- `reports/OPS_BOOTSTRAP_2026-09-13.md`、`reports/STRATEGY_RESEARCH_2026-09-13.md`、`reports/CURRENT_MARKET_ANALYSIS_2026-09-11.md`
- `.claude/skills/README.md` — skills 说明与可配合安装的外部 skill

## 13. 现有结论的可信度（引用时必须带这些限定）

- 历史策略（2023-08 → 2026-09，当前成分股回看）：美股月度等权年化 27.73% / Sharpe 1.99 / 回撤 -14.14%；A 股 120 日月频动量年化 21.47% / Sharpe 0.86。描述性回测：含幸存者偏差、无完整成本、无涨跌停停牌、无基准对照。
- 当前截面（2026-09-11）：美股"中性"、A 股"防守"；候选名单是候选池，不是指令。
- 期权（2026-09-13 快照）：无 IV 历史，不能判断贵贱；中间价非成交价；P/C 比不单独解释为看空。

## 14. 协作约定

- 中文交流；代码注释和标识符英文。
- 每次修改先跑测试与相关 ruff，再改文档；改动涉及 canonical 写入时先在开发数据湖跑通。
- 涉及删除数据、重置状态、部署到后台、创建/改写 Git 历史、调用付费 API 批量任务：先说明影响并等用户确认。
- 报告写"做了什么、没做什么、结论适用范围"，不写"建议买入"。

## 14.1 quant_service 原型并入（2026-09-20）

`/Users/lucky/Workspace/quant_service`（2026-08 的独立原型，非 git）已停止开发，只维护本仓库。已移植：PIT 分类存储、`StrategyContext` / `ContextStrategy`、板块动量基线、公司行为进回测账本；对照表和未移植原因见 `docs/legacy_quant_service/README.md`。注意：免费源没有历史板块成员，`classification_store_from_universe(..., backdate_to=...)` 属于前视假设，来源会标 `:backdated`，用它跑出的回测只能标"描述性"。真正的 PIT 成员要靠 T2 的 `snapshot-industry` / `snapshot-universe` 从现在开始积累。

## 15. 仓库实际状态核对（2026-09-13 由 Claude Code 核对）

- 基线提交 `02588f0`，标签 `mvp-2026-09-13`，远端 https://github.com/elegant2030/quant_service（公开仓库）。
- `.claude/skills/*`（24 个）、`AGENTS.md`、`docs/PROJECT_REVIEW_2026-09-13.md`、`docs/FRAMEWORK_INTEGRATION.md` 已在仓库中。
- 已核对为真：单测通过；选择性 ruff 通过；仓库无 `.env`、无密钥字符串；三个 LaunchAgent 已加载且最近 exit 0；后台 health `status=ok`，美股/A 股水位 2026-09-11，quarantine 为空。
- T5 已部署验收：后台 `health/latest.json` 带 `build_sha`、`deployed_at`、`alerts`；Telegram 测试消息、错误告警、去重、恢复消息均已实收。
- 部署脚本现在会强制重装包并比对源码，pip 因版本号不变而跳过安装的漂移问题已修（2026-09-13）。
- 2026-09-21：后台已部署到含 quant_service 并入和 CNInfo 修复的版本。A 股事件采集此前在大陆白天时段 100% 失败（AKShare 用默认 python-requests UA 被巨潮 403，且每只股票重下一次 60 万字节字典），已改用 `data/cninfo_client.py`：如实标识的 UA、字典每轮一次、0.25 秒限速、分页上限 10。后台现有 agent 共六个：pipeline / watchdog / backup / reports / fundamentals / events。
