# A 股与美股市场数据方案

> 状态：已确认的项目范围  
> 日期：2026-08-11  
> 目标：同时支持 A 股与美股，从板块研究开始，底层覆盖 ETF 与个股

## 1. 范围决策

系统不再以“ETF 单资产回测”为目标，而是按以下范围设计：

| 市场 | 第一阶段纳入 | 第一阶段暂不纳入 |
|---|---|---|
| A 股 | 沪深北普通股票、主流场内 ETF、申万一级行业 | B 股、可转债、期权、融资融券、退市整理期特殊撮合 |
| 美股 | NYSE/Nasdaq/NYSE American 普通股、主流 ETF | OTC、期权、权证、优先股、复杂 ETP、盘前盘后撮合 |

第一阶段分别生成 CNY 和 USD 两套组合结果，不直接把两个市场混成一个账户。完成 FX、跨市场日历和多币种账本后，再增加跨市场组合。

## 2. “从板块开始”的策略层次

板块不是替代个股，而是策略的第一层筛选：

```text
市场状态
  -> 板块强弱/估值/质量排序
  -> 选择目标板块
  -> 在板块内筛选 ETF 或个股
  -> 个股流动性、上市时间、风险过滤
  -> 目标权重
  -> 订单与成交
```

系统同时支持三类策略：

1. 板块 ETF 轮动；
2. 板块内个股轮动；
3. 全市场个股策略，板块仅作为中性化或风险约束。

## 3. 统一标识与市场模型

内部不直接使用 `600000`、`AAPL` 作为唯一标识，规范格式为：

```text
CN:XSHG:600000
CN:XSHE:000001
CN:XBEI:430047
US:XNAS:AAPL
US:XNYS:IBM
US:XASE:EXAMPLE
```

`instrument_master` 保存：

- 内部 ID、市场、交易所、当地代码和供应商代码；
- 证券类型、币种、上市日、退市日；
- 最小价格单位、最小交易单位；
- 公司稳定标识：A 股统一社会/证券主体映射，美股 CIK；
- 名称和代码变更的生效区间。

代码是可变化的显示属性，内部 ID 和公司标识才是稳定关联键。

## 4. 板块分类模型

### 4.1 原始分类

- A 股：以申万一级行业为正式研究分类，可保留二、三级行业。
- 美股：免费基线采用 SEC SIC；Yahoo sector/industry 仅作为便利标签，不作为历史真实值。
- ETF：另存基金类别、跟踪指数和底层资产类型，不强行混入公司行业。

分类关系必须是带有效期的事实表：

| 字段 | 说明 |
|---|---|
| `instrument_id` | 内部标的 ID |
| `taxonomy` | `SW_2021`、`SEC_SIC`、`INTERNAL_SECTOR_V1` 等 |
| `level` | 分类层级 |
| `classification_code` | 行业代码 |
| `valid_from/valid_to` | 经济上生效区间 |
| `available_from` | 本系统当时最早能知道的时间 |
| `source` | 数据来源 |
| `snapshot_id` | 原始快照 |

### 4.2 跨市场板块映射

为了比较 A 股与美股，增加版本化内部分类 `INTERNAL_SECTOR_V1`，初始采用约 10–12 个宽口径板块，例如科技、金融、医疗、可选消费、日常消费、工业、材料、能源、公用事业、通信和地产。

映射关系由 `SW -> INTERNAL`、`SIC -> INTERNAL` 两张显式表完成。原始分类永远保留；映射规则变化时发布新版本，禁止原地修改旧回测的分类结果。

## 5. 数据源组合

### 5.1 A 股

| 数据 | 免费探索源 | 增强/校验源 | 说明 |
|---|---|---|---|
| 股票/ETF 日线 | AKShare | Tushare | 下载未复权 OHLCV，复权因子分开存 |
| 当前股票列表 | AKShare | Tushare `stock_basic` | 每日保存完整快照 |
| 申万分类 | AKShare 当前截面 | Tushare `index_classify` | 分类标准带版本 |
| 历史行业成员 | 自开始采集后的每日快照 | Tushare `index_member_all` | Tushare 提供 `in_date/out_date`，需 2000 积分 |
| 公司行为 | AKShare | Tushare | 分红、送转、拆并股单独入表 |
| 财务数据 | AKShare | Tushare | 必须使用公告日作为 `available_time` |

AKShare 适合作为零费用启动源，但它聚合公开网页数据，字段、接口和复权结果都必须校验。Tushare 当前的申万历史成分接口包含纳入和剔除日期，适合后续提高历史板块回测可信度。

### 5.2 美股

| 数据 | 免费探索源 | 官方/校验源 | 说明 |
|---|---|---|---|
| 日线与公司行为 | yfinance | 未来可替换付费行情 | 仅用于个人研究，下载后冻结版本 |
| 当前上市证券 | Nasdaq Symbol Directory | SEC ticker/exchange 文件 | 每日保存快照，过滤测试证券和非普通股 |
| 公司稳定标识 | yfinance 映射 | SEC CIK/ticker/exchange | 以 CIK 关联公司和申报数据 |
| 财务报表 | yfinance 便利接口 | SEC EDGAR/XBRL API | SEC API 免费、无需 API key |
| 板块/行业 | Yahoo 当前标签 | SEC SIC | 免费正式基线采用 SIC |
| 历史板块成员 | 从采集日起保存快照 | 从历史 SEC filings 重建 SIC | 重建成本较高，单独里程碑实现 |

yfinance 是非官方 Yahoo 客户端，项目文档明确提示数据面向个人用途。它适合早期研究，不应作为未来商业产品的默认授权来源。

## 6. 数据可信度等级

每个回测结果必须标注数据等级：

| 等级 | 条件 | 可用于 |
|---|---|---|
| `C_EXPLORATORY` | 当前股票池/当前板块成员回看历史，可能有幸存者偏差 | 开发、演示、初步筛选 |
| `B_RESEARCH` | 历史价格和公司行为可靠，股票池或分类仍有局部缺口 | 研究假设比较，不用于正式决策 |
| `A_POINT_IN_TIME` | 历史股票池、分类、退市、公司行为和可获得时间齐全 | 严肃样本外评估 |

系统不会阻止 C 级回测，但报告标题、摘要和产物中必须显示醒目警告。C 级结果不能与 A 级结果直接排名。

## 7. 数据落盘原则

外部 API 只用于采集，回测永远只读取本地版本化数据：

```text
data/raw/<source>/<dataset>/<snapshot_date>/
    -> 原样响应 + request.json + response hash

data/curated/<market>/<dataset_version>/
    -> 规范化 Parquet + quality report + manifest

backtest
    -> 只引用不可变 dataset_version
```

必须同时保存：

- 原始未复权 OHLCV；
- 分红、拆股、复权因子；
- 当前和历史证券列表快照；
- 板块分类及成员快照；
- 请求参数、下载时间、源版本与文件哈希。

## 8. 数据质量门禁

### 8.1 通用检查

- 主键 `instrument_id + event_time` 唯一；
- 时间、时区和交易日历一致；
- `high >= max(open, close, low)`；
- `low <= min(open, close, high)`；
- 价格正数，成交量非负；
- 异常涨跌必须能由公司行为或市场规则解释；
- 退市后不得继续产生正常 Bar；
- 财务数据不得早于公告时间可见。

### 8.2 双源抽样

每次全量更新随机抽取标的和日期，与第二数据源比较：

- OHLC 相对误差；
- 成交量单位差异；
- 分红/拆股日期；
- 上市/退市状态；
- 板块归属。

超过阈值时该数据版本进入 `QUARANTINED`，不能被正式回测引用。

## 9. 市场规则差异

| 项目 | A 股 | 美股 |
|---|---|---|
| 币种 | CNY | USD |
| 交易日历 | 沪深北日历 | NYSE/Nasdaq 日历 |
| 交易单位 | 通常按手/规则数量 | 通常支持股，部分券商支持碎股 |
| 结算/卖出限制 | 需建模当地规则 | 需建模当地规则与账户类型 |
| 涨跌停/停牌 | 常见且必须建模 | LULD/交易暂停语义不同 |
| 费用 | 佣金、税费及市场规则 | 佣金、SEC/FINRA 等方向性费用 |
| 公司行为 | 分红、送转、配股 | 分红、split、spin-off 等 |

第一阶段按日频下一开盘成交，费用和交易单位使用市场适配器，禁止一套参数同时套用两个市场。

## 10. 第一阶段策略验收样例

实现一条跨市场共用逻辑、分别运行的“板块—个股双层动量”基线：

1. 每月最后一个交易日计算各板块过去约 6–12 个月动量；
2. 选择排名靠前且长期趋势为正的板块；
3. 在目标板块内筛选流动性合格、上市时间足够的股票；
4. 按个股动量/波动率进一步排序；
5. 等权或风险权重持有若干股票；
6. 下一交易日开盘调仓；
7. A 股和美股分别与本地市场基准比较。

这只是用于验证系统能力的 baseline，不把参数和收益作为投资结论。

## 11. 实施顺序

### D1：统一数据契约

- 建立 instrument、bar、corporate_action、classification、membership schema；
- 市场/交易所/币种统一标识；
- manifest、快照和数据质量等级。

### D2：A 股采集

- AKShare 股票、ETF、公司行为和当前行业快照；
- A 股代码表与交易日历；
- 可选 Tushare 交叉校验和历史申万成员。

### D3：美股采集

- Nasdaq/SEC 标的主数据；
- yfinance 日线、分红和拆股；
- SEC CIK、SIC 和 XBRL 财务数据。

### D4：多市场回测

- 两套市场适配器与费用/撮合规则；
- 板块聚合、板块内选股、独立基准；
- 数据等级和偏差警告进入报告。

### D5：历史可信度增强

- 退市标的和历史股票池；
- 历史行业成员与分类变更；
- 第二数据源校验和异常修复记录；
- 从 B/C 级升级为 A 级 point-in-time 数据。

## 12. 成本结论

- 系统计算与存储可以完全本地、免费；
- A 股和美股日频探索数据可以零费用启动；
- 免费路线可以完成系统开发和策略原型；
- 真正困难的是历史退市证券、历史板块成员和稳定商业授权；
- 正式研究前，最可能优先产生的小额成本是 A 股历史行业成员权限；
- 是否购买美股专业数据，应等免费版本跑通且策略值得继续研究后决定。

## 13. 官方参考

- [AKShare 项目说明](https://akshare.akfamily.xyz/introduction.html)
- [Tushare 申万行业历史成员](https://tushare.pro/document/2?doc_id=335)
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [SEC ticker/exchange 文件](https://www.sec.gov/file/company-tickers-exchange)
- [Nasdaq Symbol Directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [yfinance 文档与使用声明](https://ranaroussi.github.io/yfinance/index.html)

