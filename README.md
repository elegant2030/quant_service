# Quant Workbench

一个面向美股、A 股、期权、加密资产与衍生品的**研究优先**量化工具底座。当前版本先把最容易出错、也最值得复用的部分做成可运行内核：统一资产模型、无未来函数的中低频回测、A 股交易约束、基本面评分、期权与永续合约分析，以及有证据约束的 ChatGPT 研究接口。

> 这是研究与决策支持软件，不是投资建议，也不是已通过券商风控认证的实盘交易系统。

## 已实现

- 美股、A 股、ETF、期权、期货、加密现货、永续合约的统一 `Instrument` 模型
- 日线/中低频事件驱动回测：收盘生成信号，下一可用 bar 开盘成交，避免前视偏差
- 手续费、滑点、合约乘数、整手交易、A 股 T+1 与涨跌停封板拒单
- 横截面动量参考策略和收益、年化、Sharpe、最大回撤指标
- 财务快照标准模型及质量/成长/估值/安全四维透明评分
- 欧式期权 Black–Scholes 价格、Greeks 与隐含波动率
- 加密永续合约未实现盈亏、资金费率和近似强平价
- OpenAI Responses API 结构化分析：摘要、利好/利空、时效、置信度、正反证据、催化剂、风险和数据缺口
- CSV 行情及财务数据适配器、CLI 和可选 FastAPI 接口

## 快速运行

项目的计算内核只依赖 Python 标准库：

```bash
PYTHONPATH=src python -m quant_workbench demo-backtest
PYTHONPATH=src python -m quant_workbench option \
  --type call --spot 100 --strike 105 --years 0.5 --rate 0.03 --vol 0.25
python -m unittest discover -s tests -v
```

使用 API 服务：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[api]'
uvicorn quant_workbench.api:app --reload
```

安装隔离的研究框架（只进入开发 `.venv`，后台数据采集运行时不自动安装）：

```bash
pip install -e '.[research,filings,options-research,crypto]'
.venv/bin/python scripts/check_optional_frameworks.py
```

当前接入 VectorBT、PyBroker、skfolio、QuantStats、edgartools、QuantLib、
Optopsy 和 CCXT。Alphalens-reloaded 与 Qlib 因 Python 3.14 依赖不兼容，
保留为后续 Python 3.13 独立环境，避免降级当前 pandas/peewee。

持续运行的数据作业：

```bash
quant-workbench ingest-daily --market us
quant-workbench ingest-daily --market cn
quant-workbench run-due --strict
quant-workbench watchdog --full --strict
quant-workbench ops-backup
```

## ChatGPT 研究分析

先在环境中设置密钥；不要把密钥写入仓库：

```bash
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5-mini'
PYTHONPATH=src python -m quant_workbench analyze \
  --subject AAPL \
  --task '解读财报，判断对未来1-2个季度的潜在影响' \
  --file ./earnings.txt
```

实现采用 OpenAI 的 [Responses API](https://platform.openai.com/docs/api-reference/responses) 与 [Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)。模型名称通过 `OPENAI_MODEL` 配置，便于按账号可用模型调整。AI 只接收调用方提交的材料；生产环境应同时保存材料哈希、来源 URL、发布时间、模型名、提示词版本和完整结果。

## 数据格式

行情 CSV：

```csv
timestamp,open,high,low,close,volume,previous_close
2025-01-02,100,103,99,102,1200000,99
```

财务 CSV 字段与 `FinancialSnapshot` 一致；比率用小数表示，如 `0.15` 表示 15%。所有原始数据必须保留 `as_of`/发布时间，回测时只允许使用当时已经公开的数据。

### 免费数据源试跑

原型默认选择 yfinance（美股）和 BaoStock（A股核心行情与股票池），AKShare 用于补充公告、原始财报和特色数据。安装并运行小样本质量探针：

```bash
pip install -e '.[data]'
PYTHONPATH=src python scripts/probe_free_data.py
```

小探针拉取 AAPL、600519 最近约 120 天日线、少量财务数据和当前期权到期日。大范围探针的 A股核心路径使用 BaoStock，避免 AKShare 网页上游在批量任务中的空表和长时间断连。所有免费源只适合原型，不应直接承担生产实盘数据职责。

大范围板块探针默认每个市场 220 只：美股覆盖 11 个行业，A股从沪深 300 中按 BaoStock 行业分类轮询抽样。结果可断点续跑并保存在 `data/cache/sector_probe/`：

```bash
.venv/bin/python scripts/probe_sector_universe.py --market all --target 220 --days 120
```

## 目录

```text
src/quant_workbench/
  core/            资产、行情、财务与成交领域模型
  data/            数据源协议和 CSV 适配器
  fundamentals/    基本面评分
  strategy/        策略接口和参考策略
  backtest/        回测、成交成本与市场规则
  derivatives/     期权和永续合约分析
  ai/              ChatGPT 结构化研究工作流
  api.py            可选 HTTP API
  cli.py            命令行入口
```

完整的生产架构、数据源选择和迭代路线见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
免费数据源的比较、实测结果和升级路线见 [docs/DATA_SOURCE_DECISION.md](docs/DATA_SOURCE_DECISION.md)。
优化后的本地运行方案和实施顺序见 [docs/LOCAL_MVP_EXECUTION_PLAN.md](docs/LOCAL_MVP_EXECUTION_PLAN.md)。
第三方框架与项目 Skill 的实际接入状态见 [docs/FRAMEWORK_AND_SKILL_STATUS.md](docs/FRAMEWORK_AND_SKILL_STATUS.md)。
