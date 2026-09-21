# Quant Workbench Agent Skills

本目录包含项目级 Agent Skills，随仓库和代码审查一起维护。

- `qw-*`：本项目专用规则，来自已审计的 `qw-additions` 包。
- `ml4t-*`：从 `ml4t/skills` 精选安装的通用量化研究纪律。
- `pybroker-*`：仅在使用 PyBroker API 时加载的官方 Skill。

项目专用规则优先。任何第三方 Skill 中出现的实时交易、账户连接、凭证读取或
下单示例都只是上游材料，不代表用户授权；本项目默认保持研究和只读模式。

完整路由与红线见仓库根目录 `AGENTS.md` 和 `docs/AGENT_HANDOFF.md`。

## 2026-09-21 新增

### 从 ml4t/skills 原样复制的 12 个（Apache-2.0，上游提交 f0ea019，2026-09-20）

| 目录 | 用途 |
|---|---|
| `risk-metrics`、`tearsheet` | 回测报告的回撤、VaR、CVaR、尾部指标与绩效报告（T4） |
| `backtest-overfitting`、`data-leakage`、`information-coefficient`、`non-stationarity` | 回测与因子评估的方法论纪律 |
| `regime-awareness` | 市场状态只作条件变量、不作择时信号（对应 CLAUDE.md 5.5） |
| `calendar-ops` | 交易日历对齐与节假日感知的滚动窗口 |
| `agent-governance`、`agent-tool-contracts`、`agent-state-memory` | 设计 `ai/gateway.py` 时的工具契约、溯源、审批与可回放 |
| `factor-research` | 因子研究全流程 |

说明：

- 这些 skill 的"生产代码"示例引用 `ml4t.*` 库，本项目没有安装。按上游说明库是可选的，它们在这里只作方法论指引，示例代码不能照抄，实现仍用本项目的 `backtest/`、`store/`、`data/`。
- 有 4 处依赖引用指向未安装的 skill，不影响阅读：`tearsheet → run-backtest`（与自有引擎冲突，有意不装）；`factor-research → feature-families、feature-validation、horizon-design`（需要时再装）。
- 有意不装：`live-trading`、`kill-switch`、`rl-execution`、`position-sizing`（触及红线 1）；`fetch-data`、`canonical-schema`、`feature-store`、`registry-system`、`polars-patterns`（与自有存储层冲突）。
- `agent-governance` 的示例里出现 `submit_order`，是作为"必须人工审批的高影响工具"的例子。本项目没有任何下单工具。

### 改造后加入的 3 个 `qw-*`

| 目录 | 上游 | 许可 | 改了什么 |
|---|---|---|---|
| `qw-residual-edge` | tradermonty/claude-trading-skills `residual-edge-analyzer`（提交 999402e，2026-09-19） | MIT | 分析器脚本、测试、references 原样保留；新增 `curve_to_returns.py`（项目回测曲线和价位序列 → 对齐收益 CSV）；SKILL.md 按本项目重写（同池等权作主基准、四项声明的如实填法、复权要求、与 `qw-backtest-audit` 的衔接）；去掉 `agents/openai.yaml` |
| `qw-report-data-check` | 同上仓库 `data-quality-checker` | MIT | 上游脚本与测试原样保留；新增包装脚本 `check_report_zh.py`：中文日期与星期四种格式、中文配置表关键词、红线 1 措辞检查、回测数字限定语检查；默认只打印不写文件 |
| `qw-thesis-tracker` | anthropics/financial-services equity-research `thesis-tracker`（提交 fca3cc8，2026-09-18） | Apache-2.0 | **移除**仓位方向、目标价、止损触发、增减仓动作；**新增**预期差定位、证据登记（发布时间与原文哈希）、`source_event_id`、研究状态、YAML 模板和确定性校验脚本 |

每个目录里的 `LICENSE-UPSTREAM` 是上游许可原文。三个 skill 的计算和校验全部由脚本完成，只用标准库（`qw-thesis-tracker` 读 YAML 时用环境里已有的 PyYAML，也接受 JSON），不联网、不取数、不下单。

运行 skill 自带测试（不在项目 `unittest discover -s tests` 范围内）：

```bash
.venv/bin/python -m pytest -q .claude/skills/qw-residual-edge/scripts/tests \
  .claude/skills/qw-report-data-check/scripts/tests \
  .claude/skills/qw-thesis-tracker/scripts/tests
```

### 评估过但没有引入的

- staskh/trading_skills：直接调 yfinance 绕过 provider 与 PIT 落库，含可下单的 IB 止损，要求 Python 3.12 与 uv。只借鉴"SKILL.md 加脚本"的结构。
- tradermonty 整库：多数依赖付费的 FMP key，偏主观交易。
- anthropics/financial-services 整库：核心 skill 依赖 FactSet、标普等付费 MCP。
- dongzhuoyao/finance-option-skills：1 星、5 次提交。
- 所有开源交易 agent（TradingAgents、FinRobot、ai-hedge-fund 等）：无期权能力，回测区间有知识泄漏，维持"只借设计"。
