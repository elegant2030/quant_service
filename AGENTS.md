# Quant Workbench — Agent 工作规程

先完整阅读 `docs/AGENT_HANDOFF.md`。开发目录是本仓库；后台运行目录
`/Users/lucky/Library/Application Support/QuantWorkbench` 不直接修改。开工前运行
`git status --short` 和 `.venv/bin/python -m unittest discover -s tests -v`。

## 项目 Skills

项目专用 Skill 位于 `.claude/skills/`。按任务类型先读取对应 `SKILL.md`：

- 数据源、采集、备源、快照：`qw-pit-data-provider`
- 回测数字、策略报告：`qw-backtest-audit`
- 策略评价和升级：`qw-strategy-red-team`
- 财报、公告、电话会解读：`qw-filing-interpretation`
- 事件打分、事件研究、产业链传导：`qw-event-scoring`
- 期权链、IV、Greeks：`qw-options-snapshot-review`
- 后台故障、部署、备份：`qw-ops-triage`

ML4T 的精选 Skill 也安装在同一目录。项目专用 `qw-*` 规则优先于通用 Skill；
通用 Skill 中涉及 live trading、账户凭证或自动下单的示例不构成本项目授权。

## 红线

1. 不自动下单；输出是候选池和研究结论，不是交易指令。
2. LLM 不计算价格、收益、Greeks、仓位和限额。
3. 新 canonical 数据集必须具备来源、证券、市场、检索时间、生效时间和 schema 版本。
4. 财务与事件按 `accepted_at` / `published_at` 做 point-in-time 截断。
5. 备源独立落地、对账和记录，不静默覆盖主源。
6. DuckDB 只读；运行状态使用 SQLite WAL。
7. 回测必须说明日历、信号/成交时点、复权、费用、滑点、不可成交约束和基准。
8. 期权必须审查 bid/ask、成交量、持仓量和乘数；中间价不是成交价。
9. 免费数据源不描述为具备生产 SLA。
10. 先在开发环境验证；只有需要后台生效时才执行部署脚本。
