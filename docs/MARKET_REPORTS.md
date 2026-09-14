# A 股与美股独立盘前、盘中、盘后报告

后台任务 `com.quantworkbench.reports` 每 15 分钟检查一次两个市场是否有报告到期。
两个市场使用各自的交易日历、当地时区和文件目录，不再合并排名。

| 市场 | 时区 | 盘前 | 盘中 | 盘后 |
|---|---|---:|---:|---:|
| A 股 | Asia/Shanghai | 09:00 | 11:30 | 15:30 |
| 美股 | America/New_York | 08:30 | 12:30 | 16:30 |

每个市场、每个交易日生成三份，因此正常交易日合计六份。每份独立包含该市场热度
最高的 5 个板块和 15 只研究候选股票。定期检查而不是只设固定闹钟，是为了电脑从
睡眠恢复后补跑遗漏报告。文件按市场、交易日和阶段去重，Telegram 临时失败会重试。

## 推荐依据与证据边界

当前评分只使用已有且可审计的数据：

- 技术面：20/60 日相对强度、20 日突破位置、MA20/MA60 状态。
- 量价面：当日涨跌、当前成交量相对 20 日均量。
- 风险调整：20 日实现波动率，较低波动获得小幅加分。
- 基本面：存在 PIT 财务数据时加入 ROE、利润率、增长、现金转化和负债率，权重 25%。
- 消息面：最近 14 日带原始链接和发布时间的公告/新闻；有方向性事件时叠加 15%，
  无方向事件只展示、不改变评分。
- 期权面：美股报告附 SPY/QQQ Put/Call 和中位 IV，仅作为市场级背景，不冒充个股信号。

每个板块和每只股票都输出 `推荐依据` 与 `具体理由`，直接列明属于技术面、量价面
、风险调整、基本面还是消息面，并展示对应数字或事件原文链接。基本面指标不足的股票
不会被按零分处罚，而会明确写“该股票尚无足够的 PIT 财务数据”。消息面没有方向性
关键词时也不会按利好或利空处理。

系统不会把价格上涨猜成基本面改善，也不会用没有来源的标题声称利好或利空。没有
OpenAI key 或结构化材料时，也不会伪造 LLM 解读。消息面当前是确定性规则评分，具体
边界见 `docs/EVENTS.md`；基本面详情见 `docs/FUNDAMENTALS.md`。

## 盘中快照

- 美股：yfinance 5 分钟盘前/盘中/盘后快照。
- A 股：盘中和盘后使用 AKShare/东方财富全市场快照；盘前只使用最新完成日线。

快照作为研究证据保存在 `data/reports/snapshots/`，不会写入 canonical。上游不可用时
报告继续生成，并明确显示“已完成日线（实时快照不可用）”。

## 文件地址

```text
data/reports/market/us/YYYY-MM-DD/premarket.{json,md}
data/reports/market/us/YYYY-MM-DD/midday.{json,md}
data/reports/market/us/YYYY-MM-DD/postmarket.{json,md}
data/reports/market/cn/YYYY-MM-DD/premarket.{json,md}
data/reports/market/cn/YYYY-MM-DD/midday.{json,md}
data/reports/market/cn/YYYY-MM-DD/postmarket.{json,md}
```

Telegram 使用运行时密钥文件：
`/Users/lucky/Library/Application Support/QuantWorkbench/config/alerts.env`。

手工运行：

```bash
quant-workbench market-report --root data/lake --market us --stage midday
quant-workbench market-report --root data/lake --market cn --stage postmarket
quant-workbench market-reports-due --root data/lake
```

加 `--no-live` 可禁止联网，加 `--no-send` 可只生成文件。报告是量化研究候选，不是
交易指令；免费数据源没有生产 SLA，执行前仍需核验公告、财报、流动性和交易限制。
