# 免费数据源选择记录

评估日期：2026-09-13。目标是先验证美股/A股中低频行情、基本面和期权研究，不用于自动实盘。

## 决策

- **美股原型：yfinance**
- **A股批量核心：BaoStock；AKShare 作为公告、原始财报与特色数据补充**
- **后续付费升级：美股优先 Massive 或专业基本面数据商；A股优先 Tushare Pro 或持牌商业数据商**

## 取舍

| 数据源 | 免费能力 | 优点 | 关键限制 | 结论 |
|---|---|---|---|---|
| yfinance | 日/分钟行情、公司行动、财务、新闻、当前期权链 | 无密钥、接入最快、单库覆盖研究闭环 | 非 Yahoo 官方 SDK；个人/研究用途；网页接口可能变化；没有可用于历史期权回测的完整 point-in-time 链 | 美股原型首选 |
| Massive Basic | 美股 EOD、2 年历史、参考数据、公司行动，5 次/分钟 | 正式 API、字段和授权边界清楚 | 免费历史较短；免费股票方案不含财务指标；期权历史能力受套餐限制 | 第二验证源/后续升级 |
| Alpaca Basic | 美股/ETF，历史自 2016，200 次/分钟 | 正式 API，能顺滑升级模拟盘 | 免费实时股票仅 IEX、期权为 indicative feed；不解决公司基本面 | 模拟交易阶段接入 |
| BaoStock | A股历史行情、行业分类、指数成分、季度财务指标 | 匿名免费、自有数据服务；批量稳定性实测更好 | 没有完整公告原文和所有原始报表字段 | A股 200+ 标的核心源 |
| AKShare | A股日线、复权、公告、三张报表和大量特色数据 | 无 token、覆盖面最广 | 聚合公开网页；批量测试出现空表、断连和长时间无响应 | 作为补充源，不进入每日核心链路 |
| Tushare Pro | 基础日线可低积分试用 | 字段更标准、接口型服务、披露时间数据更适合研究 | 许多关键接口有积分门槛；`daily_basic` 当前要求至少 2000 积分 | A股第一升级路径 |

资料：[yfinance 文档及使用限制](https://ranaroussi.github.io/yfinance/)、[yfinance 财务与期权示例](https://ranaroussi.github.io/yfinance/reference/yfinance.ticker_tickers.html)、[BaoStock 0.9.3](https://pypi.org/project/baostock/)、[AKShare A股接口](https://akshare.akfamily.xyz/data/stock/stock.html)、[Massive 当前价格与免费额度](https://massive.com/pricing)、[Alpaca 数据套餐](https://docs.alpaca.markets/us/v1.1/docs/about-market-data-api)、[Tushare daily_basic 权限](https://tushare.pro/document/2?doc_id=32)。

## 本机小样本结果

首次单股探针：

- AAPL：81 根近期日线、OHLC 异常 0、零成交量 0、7 个财务期间、最新期间 13 个已映射财务字段、20 个期权到期日。
- 600519：84 根近期日线、OHLC 异常 0、零成交量 0；资产负债表/利润表/现金流量表分别为 103/103/99 行，147/83/71 列。
- 重复拉取时 AKShare 的新浪财务上游出现过 `RemoteDisconnected`，证实必须有重试、限速、本地原始快照和失败告警。

随后完成 440 只板块覆盖探针：

- 美股：220/220 成功，11 个行业各 20 只，股票级覆盖率 100%，异常 OHLC 0，零成交量 0。
- A股：220/220 成功，覆盖 41 个证监会行业，股票级覆盖率 100%，异常 OHLC 0，零成交量 0。
- AKShare/申万网页行业成分路径在连续请求时大量空表并长时间无响应；改用 BaoStock 的沪深 300 成分和全市场行业分类后，同一规模测试稳定完成。

这里的行数只证明“接口当前可用且字段可归一化”，不证明历史数据没有修订、幸存者偏差或复权错误。

## 日内报告快照

- 美股盘前/盘中/盘后报告使用 yfinance 5 分钟快照作为 best-effort 叠加。
- A 股盘中/盘后报告使用 AKShare 的东方财富全市场快照；盘前不拉实时快照。
- 两者都只保存到 `data/reports/snapshots/` 研究证据目录，包含 `retrieved_at`，不进入
  canonical、不推进数据水位，也不覆盖 BaoStock/yfinance 的日线主分区。
- 快照失败时报告明确降级到最新完成日线；因此 AKShare 仍不是每日 canonical 核心链路。

## 通过标准

在进入大规模回测前，应保持当前每边 220 只的行业均衡股票池连续运行两周：

1. 与交易所或第二来源抽查 OHLC、成交量和公司行动。
2. 记录成功率、延迟、缺失率、重复率、字段变化和复权跳点。
3. 财务数据同时保存报告期、首次披露时间、抓取时间和原始响应哈希。
4. 原始数据只追加不覆盖；标准化结果可以从原始快照重建。
5. 任何 AI 财报解读只能引用已保存的原文和数据快照。
