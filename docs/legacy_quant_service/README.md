# quant_service 原型归档（2026-09-20 并入）

`/Users/lucky/Workspace/quant_service` 是 2026-08 的独立原型（MVP 0.2，约 1,600 行，非 git 仓库）。2026-09-20 决定只维护 Quant Workbench 一套代码，原型中 Workbench 没有的能力已移植，原目录保留不动、不再开发。

## 移植对照

| 原型 | Workbench | 说明 |
|---|---|---|
| `domain/classification.py`（`ClassificationMembership` / `ClassificationStore`） | `core/classification.py` | 保留 `valid_from/valid_to` + `available_from` 双时钟；键改为 `Instrument.id`（如 `NASDAQ:AAPL`）；新增 `classification_store_from_universe`，回溯使用当前板块必须显式 `backdate_to`，来源标记 `:backdated` |
| `portfolio/strategy.py::StrategyContext` / `PortfolioStrategy` | `strategy/base.py::StrategyContext` / `ContextStrategy` | 引擎在收盘决策时给出知识时钟 `now`（会话日 UTC 日终）和 PIT 分类查询；旧的 `Strategy.targets(history)` 继续可用 |
| `portfolio/strategy.py::SectorMomentumStrategy` | `strategy/sector_momentum.py::SectorMomentum` | 板块中位数动量选板块，板块内选个股；收益用 `adjusted_return`（原始价 × 区间内 `adj_factor`） |
| `domain/market.py::CorporateAction`（契约，未进账本） | `backtest/engine.py::_apply_corporate_action` | 直接使用 schema 2 日线上的 `split_ratio` / `dividend` / `adj_factor`：拆股调整股数，现金分红入账，只有因子的来源（BaoStock）按无成本再投资处理；`BacktestResult.corporate_actions` 留痕 |

## 未移植及原因

- `portfolio/engine.py`、`engine.py`、`metrics.py`：Workbench 引擎已覆盖并多出 T+1、涨跌停封板、合约乘数、Decimal 记账。
- `domain/market.py` 的 `InstrumentId` / `PriceBar`（带 `available_time`、`is_suspended`）：Workbench 的 `Bar` 暂无 bar 级可得时间，日线按"收盘后可知、下一开盘成交"处理；停牌体现为当日无 bar。
- `data/providers/*`：Workbench 的 provider 已含校验、quarantine、水位和原始价还原。
- `ExecutionPolicy` 的买卖方向性费率（A 股印花税只在卖出收取）：Workbench `CostModel` 目前单一费率，列入 T4 成本模型一并处理。

## 本目录文件

- `backtesting_system_design.md`：原型的回测系统设计（时序不变量、撮合语义、验收标准），与 Workbench 引擎语义一致，可作设计参考。
- `market_data_plan_cn_us.md`：A 股 / 美股数据与板块方案（申万 / SIC、PIT 成员），T2 `snapshot-industry` / `snapshot-universe` 设计时参考。
