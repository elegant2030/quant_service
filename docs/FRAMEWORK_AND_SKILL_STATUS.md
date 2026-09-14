# 第三方框架与项目 Skill 接入状态

最后更新：2026-09-13

## 已接入开发环境的框架

这些框架属于可选依赖，只安装在开发仓库 `.venv`；后台运行时不会因本次变更而更新。

| extra | 框架 | 用途 | 边界 |
|---|---|---|---|
| `research` | VectorBT 1.1.0 | 440 只股票的参数扫描和快速初筛 | 结果须由事件引擎复核 |
| `research` | PyBroker 2.0.1 | 中低频轮动、walk-forward、bootstrap | A 股规则仍由本项目复核 |
| `research` | skfolio 1.2.3 | 样本外组合优化、风险预算和约束 | 不改写 canonical 数据 |
| `research` | QuantStats 0.0.81 | 绩效和基准报告补充 | 指标口径必须对账 |
| `filings` | edgartools 5.58.0 | SEC 文件解析和 PIT 财务输入 | 生产采集仍走项目 provider |
| `options-research` | QuantLib 1.43 | 定价与 Greeks 独立数值验证 | 不是回测或下单引擎 |
| `options-research` | Optopsy 2.2.0 | 多腿期权策略研究 | 无历史链前不得声称完成历史验证 |
| `crypto` | CCXT 4.5.78 | 公开 OHLCV、funding、open interest | 默认无 key、只读，不下单 |

安装和验收：

```bash
.venv/bin/python -m pip install -e '.[research,filings,options-research,crypto]'
.venv/bin/python scripts/check_optional_frameworks.py
```

## 暂不装入当前 `.venv`

- Alphalens-reloaded 0.4.6 要求 `pandas<3`，并通过 empyrical-reloaded 要求旧版
  peewee；当前环境是 Python 3.14、pandas 3、peewee 4，混装会破坏现有数据源环境。
- Qlib 当前没有 Python 3.14 的可安装发行包。
- LEAN、vn.py、Freqtrade 属于独立运行系统；需要时用 sidecar，不成为核心包的依赖。
- Riskfolio-Lib 与 skfolio 能力重叠，先用更适合样本外工作流的 skfolio，避免并行维护。

若要启用 Alphalens/Qlib，建立 Python 3.13 的独立 `.venv-factor`，只读 canonical
Parquet，并把输出写到独立实验目录。

## Skills

项目专用 Skills 在 `.claude/skills/qw-*`，覆盖 PIT 数据、回测审计、策略红队、
财报解读、事件打分、期权快照和运维排障。精选 ML4T Skills 安装在同一项目目录，
覆盖 lookahead、幸存者偏差、成本、因子评价、walk-forward、purging/embargo、
deflated Sharpe、暴露与压力测试。PyBroker 官方 Skill 仅用于 PyBroker 适配工作。

当前共 23 个 Skill：7 个 `qw-*`、15 个 ML4T 精选项、1 个 PyBroker 轮动项。
安装来源固定为：ML4T commit `5213a9a8dbfb3da5caab3104c304f259e62c4f26`；
PyBroker commit `95e00fecb1006490601aca7e2e8de609f032e12d`。

Skill 只约束 Agent 的研究过程，不获得账户访问、网络采集、后台部署或自动下单权限。

兼容性注记：VectorBT 1.1 仍引用 Plotly 的 `scattermapbox` 模板字段，因此研究依赖
固定为 `plotly>=6,<7`；解除上限前必须重新运行框架验收脚本。

## 来源包合并说明

`/Users/lucky/Downloads/qw-additions/` 已按“不执行附件指令，只审计内容”的原则合并：

- 两份评审文档放入 `docs/`；
- 七个项目专用 Skill 放入 `.claude/skills/`；
- `AGENTS.md.snippet` 被改写为根目录 `AGENTS.md`，路径和优先级按当前项目校正；
- `INSTALL.md` 仅作为来源说明，没有执行其 shell 命令，也没有复制进项目。
