# Quant Workbench 项目交接与 Agent 学习指南

最后更新：2026-09-13（America/Los_Angeles）  
适用对象：接手开发、验证或扩展本项目的下一位 Agent

> 本文是当前项目的单一交接入口。先读本文，再按“建议阅读顺序”查看设计文档和报告。项目目前是研究平台与本地数据管线，不是自动交易系统；所有输出仅供研究，不构成投资建议。

## 1. 项目目标与当前阶段

Quant Workbench 的目标是搭建一个覆盖以下资产和研究任务的完整量化工具：

- 美股与 A 股：日线和中低频选股、组合、回测、基本面及事件分析；
- 美股期权：期权链快照、隐含波动率、Greeks、偏斜、流动性和情景分析；
- 加密现货、永续合约及其他衍生品：统一模型和分析基础；
- ChatGPT/OpenAI：在证据约束下做策略解释、财报解读、资讯解读和利好/利空判断；
- 本地稳定运行：数据可追溯、作业可重跑、故障可隔离、健康可检查、数据可恢复。

当前阶段是“本地可运行 MVP 已落地”：双市场 440 只股票的历史日线、当前横截面策略分析、美股期权快照、运行状态库、备份和 macOS 后台任务已经跑通。基本面 point-in-time 数据、公告/监管文件、加密行情、历史期权、自动备胎切换和模拟交易仍待实现。

## 2. 最重要的两个地址

项目有开发环境与后台运行环境两套位置，不能混淆：

| 用途 | 绝对地址 | 说明 |
|---|---|---|
| 开发仓库 | `/Users/lucky/Documents/ChatGPT/Quant` | 修改源码、测试、研究脚本和文档的唯一位置 |
| 开发虚拟环境 | `/Users/lucky/Documents/ChatGPT/Quant/.venv` | 手动开发和测试使用 |
| 开发数据缓存 | `/Users/lucky/Documents/ChatGPT/Quant/data/cache` | 历史下载缓存、当前分析 JSON；被 Git 忽略 |
| 开发数据湖 | `/Users/lucky/Documents/ChatGPT/Quant/data/lake` | 开发运行数据；被 Git 忽略 |
| 后台独立运行时 | `/Users/lucky/Library/Application Support/QuantWorkbench` | LaunchAgent 实际运行位置；不要在这里直接改源码 |
| 后台运行虚拟环境 | `/Users/lucky/Library/Application Support/QuantWorkbench/venv` | 安装后的非 editable 包 |
| 后台生产数据 | `/Users/lucky/Library/Application Support/QuantWorkbench/data` | Parquet、原始快照、SQLite、健康状态、日志和备份 |
| 后台股票池配置 | `/Users/lucky/Library/Application Support/QuantWorkbench/config` | `universe_us.csv` 和 `universe_cn.csv` |

之所以拆开，是因为 macOS LaunchAgent 直接读取 `Documents` 下的虚拟环境时曾被系统后台隐私保护拒绝。解决方案是把后台运行所需的 Python 环境、配置和数据全部放到 `Application Support/QuantWorkbench`。源码修改后，需要通过仓库内的部署脚本重新安装并加载后台任务，不能只改运行目录中的副本。

## 3. 系统结构

```text
yfinance / BaoStock / AKShare / 后续正式数据源
                       │
                       ▼
              下载、校验、标准化
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
 raw gzip + 来源元数据       quarantine 隔离坏数据
          │
          ▼
 canonical Parquet（事实数据源）
          │
          ├── DuckDB：只读分析查询
          ├── Python：因子、回测、期权、报告
          └── OpenAI：只做有证据的语义分析和解释

SQLite（控制平面）：作业、水位、来源健康、熔断、运行记录
LaunchAgent：pipeline / watchdog / backup
```

核心数据原则：

1. Parquet 是行情、财务、事件和期权等事实数据的最终落地格式。
2. DuckDB 只用于读取和分析，不作为多进程写入状态库。
3. SQLite 使用 WAL，保存作业状态、逐证券水位和数据源健康状态。
4. 文件写入采用临时文件后原子替换；原始数据保留 gzip；清单保存 SHA-256。
5. 异常数据进入 quarantine，不允许静默覆盖有效数据。
6. 每个作业必须幂等、可重跑，并使用进程锁避免重复执行。
7. 时间口径使用交易所日历和“最近一个已完成交易日”，避免周末及未收盘数据误判。

详细设计见 [ARCHITECTURE.md](ARCHITECTURE.md) 和 [LOCAL_MVP_EXECUTION_PLAN.md](LOCAL_MVP_EXECUTION_PLAN.md)。

## 4. 当前数据源决策

| 市场/数据 | 当前源 | 定位 | 重要限制 |
|---|---|---|---|
| 美股行情、财务原型、当前期权链 | yfinance | 免费原型首选，接入快、覆盖面广 | 非交易级 SLA；接口可能变化；个人/研究用途；没有完整历史 point-in-time 期权链 |
| A 股历史日线 | BaoStock | 当前 A 股核心免费源，批量稳定性优于网页抓取 | 更新和字段能力有限；生产前需二次校验 |
| A 股补充字段 | AKShare | 行业、列表和补充数据 | 部分接口依赖网页上游，批量任务可能空表或断连 |
| 美股正式 API 候选 | Massive / Alpaca | 后续交叉验证、模拟盘或付费升级 | 免费套餐在财务、期权、实时性上均有限制 |
| 未来执行候选 | IBKR | 模拟/实盘及期权正式行情候选 | 尚未接入；需账号、权限、订阅和严格交易风控 |

合规边界：不自动抓取 CBOE 延迟报价网页。若使用备胎源，必须先分区落地、对账并记录来源，不得在无告警的情况下覆盖主源数据。完整评估见 [DATA_SOURCE_DECISION.md](DATA_SOURCE_DECISION.md)。

## 5. 已落地的数据规模

截至本次交接：

| 数据集 | 规模 | 时间/快照 |
|---|---:|---|
| 美股股票池 | 220 只、11 个板块 | 历史区间 2023-08-15 至 2026-09-11 |
| 美股日线 | 167,466 行 | 水位 2026-09-11 |
| A 股股票池 | 220 只、41 个行业 | 历史区间 2023-08-15 至 2026-09-11 |
| A 股日线 | 162,811 行 | 水位 2026-09-11 |
| SPY/QQQ 期权 | 2,117 条合约 | 快照 2026-09-13，原始 gzip + 标准化 Parquet |

股票池规模已经满足“至少 200 支并覆盖大部分板块”的初始要求。但历史回测使用当前成分股回看历史，存在幸存者偏差；在获得 point-in-time 成分股前，不能把结果当成严格可交易证据。

## 6. 已实现的代码能力

### 6.1 数据、存储与调度

- `src/quant_workbench/data/`：yfinance、BaoStock、AKShare、CSV provider、数据验证和股票池；
- `src/quant_workbench/store/files.py`：Parquet、原始文件、清单、原子写入；
- `src/quant_workbench/store/state.py`：SQLite 控制平面、水位和来源健康；
- `src/quant_workbench/jobs/ingestion.py`：历史缓存迁移、日线和期权链落地；
- `src/quant_workbench/jobs/orchestrator.py`：按交易日历决定任务是否到期；
- `src/quant_workbench/ops/calendar.py`：XNYS/XSHG 日历及最近已完成交易日；
- `src/quant_workbench/ops/health.py`：新鲜度、完整性和作业健康检查；
- `src/quant_workbench/ops/backup.py`：SQLite 在线备份、Parquet/原始文件清单和验证；
- `src/quant_workbench/ops/lock.py`：单实例运行锁。

### 6.2 研究、策略与衍生品

- `src/quant_workbench/backtest/`：无未来函数的中低频回测和指标；
- `src/quant_workbench/strategy/`：横截面动量与分配基础；
- `src/quant_workbench/fundamentals/`：基本面评分模型基础；
- `src/quant_workbench/derivatives/options.py`：Black–Scholes、Greeks、隐含波动率；
- `src/quant_workbench/derivatives/perpetual.py`：永续合约资金费率等分析基础；
- `scripts/run_strategy_research.py`：历史策略与期权研究报告；
- `scripts/run_current_market_analysis.py`：当前双市场截面与期权分析。

### 6.3 OpenAI 接口

`src/quant_workbench/ai/openai_client.py` 已使用 OpenAI Responses API 和结构化输出，目标是做：

- 财报及公告摘要；
- 事实、观点、风险和不确定性拆分；
- 利好/利空方向、影响周期和置信度判断；
- 策略结果解释与反证检查。

当前机器在生成已有报告时没有设置 `OPENAI_API_KEY`，因此报告中的行情指标和结论均由确定性 Python 计算生成，没有实际调用 ChatGPT。启用方式：

```bash
export OPENAI_API_KEY='你的密钥'
export OPENAI_MODEL='gpt-5-mini'
```

AI 只能负责语义理解和解释，禁止负责价格、收益、Greeks、仓位或风险限额等数值计算。调用时必须同时保存输入材料哈希、来源、发布时间、模型、提示词版本和完整结构化结果。

## 7. 当前量化分析结论

### 7.1 历史策略比较

完整报告：[STRATEGY_RESEARCH_2026-09-13.md](../reports/STRATEGY_RESEARCH_2026-09-13.md)

- 美股月度等权：年化 27.73%，Sharpe 1.99，最大回撤 -14.14%，总换手 4.4 倍；目前是低换手核心基准。
- 美股 20 日周频动量：年化 33.71%，但总换手 202.9 倍，约 65.9 倍/年；成本和冲击风险过高。
- A 股 120 日月频动量：年化 21.47%，Sharpe 0.86，最大回撤 -26.96%，总换手 28.1 倍；相对 20 日周频动量更值得继续做样本外验证。
- A 股 20 日周频动量：年化 21.78%，总换手 157.5 倍；收益优势很小，换手代价过大。

这些回测尚未加入完整交易成本、滑点、涨跌停无法成交、历史成分股和 point-in-time 基本面，因此只能用于筛选研究方向。

### 7.2 当前市场截面

完整报告：[CURRENT_MARKET_ANALYSIS_2026-09-11.md](../reports/CURRENT_MARKET_ANALYSIS_2026-09-11.md)  
机器可读结果：`/Users/lucky/Documents/ChatGPT/Quant/data/cache/current_analysis/analysis_2026-09-11.json`

- 美股状态为“中性”：218 只参与分析，20/60/120 日均线上方比例为 33.94%/48.17%/56.88%；相对强势板块是能源、金融和基础材料。
- 当前美股一致趋势候选包括 PSX、MPC、VLO、MUFG、HSBC 等；它们是候选池，不是买入指令。
- A 股状态为“防守”：220 只参与分析，20/60/120 日均线上方比例为 26.82%/37.73%/32.27%；相对强势行业是水运、油气、煤炭、保险和银行。
- 当前 A 股一致趋势候选包括 601169 北京银行、600919 江苏银行、600018 上港集团、600795 国电电力、601919 中远海控等；同样不直接下单。

### 7.3 当前期权截面

- QQQ：47 DTE、ATM 跨式中间价约为现货的 5.63%，25Δ put-call 偏斜 3.73%，OI put/call 3.17，成交量比 5.69。
- SPY：47 DTE、ATM 跨式中间价约为现货的 3.98%，25Δ put-call 偏斜 3.36%，OI put/call 3.14，成交量比 3.81。
- 尚无历史隐含波动率百分位，当前不能判断期权“贵”或“便宜”。
- 中间价不等于可成交价；必须过滤 bid/ask 宽度。Put/Call 比率也不能单独解释成看空方向。
- Delta 使用 Black–Scholes 近似和 3.913% 无风险利率，尚未对每个标的单独估计股息。

## 8. 后台运行与地址

### 8.1 LaunchAgent

| Label | 周期 | 安装文件 | 截至 2026-09-13 状态 |
|---|---:|---|---|
| `com.quantworkbench.pipeline` | 每 30 分钟 | `/Users/lucky/Library/LaunchAgents/com.quantworkbench.pipeline.plist` | 已加载，最近 exit 0 |
| `com.quantworkbench.watchdog` | 每 15 分钟 | `/Users/lucky/Library/LaunchAgents/com.quantworkbench.watchdog.plist` | 已加载，最近 exit 0 |
| `com.quantworkbench.backup` | 每天 02:30 | `/Users/lucky/Library/LaunchAgents/com.quantworkbench.backup.plist` | 已加载，人工验收 exit 0 |

`launchctl` 显示 `state = not running` 在这里通常代表一次性任务当前处于等待下一触发时间，并不等于任务未加载；应结合 `runs` 和 `last exit code` 判断。

模板位于：`/Users/lucky/Documents/ChatGPT/Quant/ops/launchd/`

### 8.2 运行数据目录

```text
/Users/lucky/Library/Application Support/QuantWorkbench/data/
├── canonical/   标准化 Parquet
├── raw/         原始 gzip 和来源数据
├── quarantine/  校验失败的隔离数据
├── state/       SQLite 控制状态
├── health/      latest.json 与历史健康快照
├── logs/        pipeline/watchdog/backup 日志
└── backups/     数据快照、SQLite 备份和恢复清单
```

常用绝对地址：

- 最新健康状态：`/Users/lucky/Library/Application Support/QuantWorkbench/data/health/latest.json`
- pipeline 日志：`/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/pipeline.log`
- pipeline 错误：`/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/pipeline-error.log`
- watchdog 日志：`/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/watchdog.log`
- backup 日志：`/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/backup.log`
- 后台验收报告：[OPS_BOOTSTRAP_2026-09-13.md](../reports/OPS_BOOTSTRAP_2026-09-13.md)

### 8.3 部署和停用

从开发仓库重新部署后台运行时：

```bash
cd '/Users/lucky/Documents/ChatGPT/Quant'
./ops/deploy_macos_runtime.sh
```

停用三个后台任务：

```bash
cd '/Users/lucky/Documents/ChatGPT/Quant'
./ops/disable_macos_agents.sh
```

限制：LaunchAgent 依赖用户保持登录；Mac 深度睡眠期间不能实现真正的 24 小时连续采集。长期无人值守应使用不休眠的 Mac，或迁移至 Ubuntu/systemd。

## 9. CLI 速查

在开发环境运行：

```bash
cd '/Users/lucky/Documents/ChatGPT/Quant'
source .venv/bin/activate

quant-workbench ops-init
quant-workbench ingest-cache --market us
quant-workbench ingest-cache --market cn
quant-workbench ingest-daily --market us
quant-workbench ingest-daily --market cn
quant-workbench snapshot-options --symbols SPY QQQ
quant-workbench run-due --strict
quant-workbench watchdog --full --strict
quant-workbench ops-backup
quant-workbench ops-status
```

直接检查后台生产数据时，应显式指定后台根目录，避免误查开发数据湖：

```bash
'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' watchdog \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data' \
  --full --strict

'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' ops-status \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data'
```

`ops-status` 会输出 440 只证券的逐证券水位，内容很长；自动检查时应解析 JSON 或过滤摘要，不要把完整输出直接放入对话。

## 10. 五分钟接手检查

下一位 Agent 开始修改前，建议依次执行：

```bash
cd '/Users/lucky/Documents/ChatGPT/Quant'
git status --short
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check \
  src/quant_workbench/store \
  src/quant_workbench/ops \
  src/quant_workbench/jobs \
  src/quant_workbench/cli.py \
  tests/test_ops.py \
  --select E,F,I,B
launchctl print gui/501/com.quantworkbench.pipeline
launchctl print gui/501/com.quantworkbench.watchdog
launchctl print gui/501/com.quantworkbench.backup
```

当前已知基线：13 个单元测试通过；新运维路径的选择性 Ruff 检查通过。整个旧代码库仍有样式警告，因此不能声称 `ruff check .` 全部干净。

## 11. 接手时必须遵守的设计红线

1. 不允许自动下单，除非用户以后明确授权模拟盘或实盘，并完成独立风控设计。
2. LLM 不得计算价格、收益率、Greeks、仓位、保证金或风险限额；这些必须由确定性代码完成。
3. 所有新数据集至少保留 `source`、`source_symbol`、`symbol`、`market`、`retrieved_at`、`effective_at`、`schema_version`；日线还要保留 `adjustment`。
4. 财务和事件研究必须按 `published_at`/监管机构 `accepted_at` 做 point-in-time 截断，禁止用后来的修订数据回填历史决策。
5. 备胎数据源不能静默覆盖主源；必须独立落地、对账、记录差异，失败数据进入 quarantine。
6. DuckDB 只读 Parquet；不要把它改造成多进程事务写库。运行状态继续放 SQLite WAL。
7. 任何回测都要明确交易日历、信号时点、成交时点、复权方式、费用、滑点和不可成交约束。
8. 美股期权报价必须考虑 bid/ask、成交量、持仓量和合约乘数；中间价不是成交保证。
9. 免费数据源只用于原型和研究，不应被描述为具备生产交易 SLA。
10. 修改代码只在开发仓库完成；验证后再运行部署脚本同步后台运行时。

## 12. 尚未完成的能力与优先级

### P0：让研究结论可被严格验证

1. 建立美股 SEC EDGAR 的 10-K、10-Q、8-K、6-K、Form 4、13D/G 增量采集，并保留 `accepted_at`。
2. 建立 A 股交易所/巨潮公告采集，保存公告原文、发布时间、修订关系和证券映射。
3. 建立 point-in-time 财务表，杜绝未来函数；现有 `fundamentals/scoring.py` 只有评分模型基础，不代表 PIT 数据已经生产可用。
4. 做 walk-forward/样本外回测，并加入费用、滑点、停牌、涨跌停和成分股历史。

### P1：补全数据可靠性

1. 真正实现 provider fallback：主源失败后分区拉取备源、字段映射、交叉验证、差异报告和人工/规则晋升。
2. 累积每日期权快照，构建 IV 历史、期限结构和百分位；目前只有当前快照，不能做历史期权回测。
3. 配置 Telegram 或其他告警渠道；目前 watchdog 只落本地健康结果和日志。
4. 配置 rclone 或其他异地备份；目前备份仍在同一台 Mac。

### P2：扩展资产和执行

1. 通过 CCXT 等正式接口接入加密现货和永续行情；当前只有统一模型与永续计算基础。
2. 接入 IBKR 模拟账户，先做只读账户/行情，再做 paper order；当前没有任何自动交易或订单能力。
3. 增加组合层：多币种现金、汇率、融资/借券、保证金、组合 Greeks、压力测试和 VaR/CVaR。
4. 为 ChatGPT 建立可审计的材料包、提示词版本、结构化输出存档和基于事实的评估集。

推荐下一位 Agent 的第一个开发任务：优先实现“SEC EDGAR point-in-time 文件采集 + 原文落地 + SQLite 水位 + 单元测试”，因为它同时补齐基本面和资讯解读所需的可信输入，而不会过早引入交易执行风险。

## 13. Git 状态提醒

截至本文生成时，仓库尚未建立基线提交，`git status --short` 中 `.env.example`、`.gitignore`、`README.md`、`docs/`、`ops/`、`pyproject.toml`、`reports/`、`scripts/`、`src/` 和 `tests/` 均显示为未跟踪文件。

下一位 Agent 必须把这些内容视为用户现有工作，禁止清理、重置或覆盖。开始大改前应先核对差异；是否创建首次提交由用户决定。

## 14. 建议阅读顺序

1. 本文：项目现状、地址和边界；
2. [README.md](../README.md)：功能和快速使用；
3. [ARCHITECTURE.md](ARCHITECTURE.md)：长期架构；
4. [DATA_SOURCE_DECISION.md](DATA_SOURCE_DECISION.md)：数据源选择依据；
5. [LOCAL_MVP_EXECUTION_PLAN.md](LOCAL_MVP_EXECUTION_PLAN.md)：本地落地规则；
6. [OPS_BOOTSTRAP_2026-09-13.md](../reports/OPS_BOOTSTRAP_2026-09-13.md)：后台运行验收；
7. [STRATEGY_RESEARCH_2026-09-13.md](../reports/STRATEGY_RESEARCH_2026-09-13.md)：历史策略结果；
8. [CURRENT_MARKET_ANALYSIS_2026-09-11.md](../reports/CURRENT_MARKET_ANALYSIS_2026-09-11.md)：当前市场判断；
9. [OPEN_SOURCE_QUANT_ECOSYSTEM_RESEARCH_2026-09-13.md](../reports/OPEN_SOURCE_QUANT_ECOSYSTEM_RESEARCH_2026-09-13.md)：第三方框架、策略工具和 Agent Skill 选型；
10. [PROJECT_REVIEW_2026-09-13.md](PROJECT_REVIEW_2026-09-13.md)：外部审查与调整后的 P0；
11. [FRAMEWORK_INTEGRATION.md](FRAMEWORK_INTEGRATION.md)：第三方框架的代码结合方式；
12. [FRAMEWORK_AND_SKILL_STATUS.md](FRAMEWORK_AND_SKILL_STATUS.md)：已经安装和暂缓的框架与 Skill；
13. `src/quant_workbench/cli.py` → `jobs/` → `store/` → `ops/`：从入口追踪生产数据链路；
14. `tests/`：理解当前行为契约和可验证边界。

## 15. 可直接交给下一位 Agent 的启动提示

```text
请先完整阅读：
/Users/lucky/Documents/ChatGPT/Quant/docs/AGENT_HANDOFF.md

项目开发目录是 /Users/lucky/Documents/ChatGPT/Quant；后台生产运行目录是
/Users/lucky/Library/Application Support/QuantWorkbench。不要直接修改后台运行目录。

开始工作前先运行 git status 和现有测试，保护所有未跟踪的用户文件。遵守文档中的
point-in-time、来源追踪、Parquet/SQLite 分工、LLM 不做数值计算和禁止自动下单等红线。
完成代码修改后，先在开发环境验证；只有需要同步后台服务时才运行部署脚本。
```
