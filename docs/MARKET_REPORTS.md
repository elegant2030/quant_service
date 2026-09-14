# 盘前、盘中、盘后市场报告

后台任务 `com.quantworkbench.reports` 每 15 分钟检查一次是否有报告到期。它按
`America/New_York` 时区在美股交易日生成三份报告：

- 盘前：08:30 ET
- 盘中：12:30 ET
- 盘后：16:30 ET

定期检查而不是只设三个一次性闹钟，是为了电脑从睡眠恢复后可以补跑遗漏报告。每个
交易日、每个阶段只有一个固定文件，重复执行不会重复生成或推送。

每份报告包含综合美股和 A 股的 5 个热度板块、15 只研究候选股票，以及 SPY/QQQ
期权市场温度。评分由当日/20 日/60 日收益、量比、20 日突破位置和低波动因子构成，
先在各市场内部归一化，再合并排序。股票从前五板块中选择，每个板块最多五只，避免
榜单被单一行业完全占据。

盘中会尝试用 yfinance 5 分钟快照覆盖美股最新价和成交量，原始快照保存在
`data/reports/snapshots/`，不会写入 canonical。上游不可用时报告继续生成，但明确显示
“已完成日线（盘中降级）”。A 股使用本地最新完成日线。

完整报告保存在：

```text
data/reports/market/YYYY-MM-DD/premarket.{json,md}
data/reports/market/YYYY-MM-DD/midday.{json,md}
data/reports/market/YYYY-MM-DD/postmarket.{json,md}
```

Telegram 使用与运维告警相同的运行时密钥文件：
`/Users/lucky/Library/Application Support/QuantWorkbench/config/alerts.env`。

手工运行：

```bash
quant-workbench market-report --root data/lake --stage midday
quant-workbench market-reports-due --root data/lake
```

加 `--no-live` 可禁止联网，加 `--no-send` 可只生成文件。报告是量化研究候选，不是
交易指令；免费数据源没有生产 SLA，执行前仍需核验公告、财报、流动性和交易限制。
