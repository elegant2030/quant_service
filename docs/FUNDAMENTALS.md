# 基本面数据层

## 数据源

- 美股：SEC EDGAR 官方 `submissions` 与 `companyfacts` JSON API，无 API key。
- A 股：BaoStock 季频盈利、成长、偿债和现金流指标。

美股用 accession number 将 XBRL 数值连接到披露的 `acceptanceDateTime`；如果历史记录
没有接受时间，保守地使用 filing date 下一日 00:00 UTC。A 股使用 BaoStock `pubDate`
作为公开日期。任何报告查询都要求 `effective_at <= 报告生成时刻`。

## 数据集

`fundamental_metrics` canonical 的自然标识是
`(source, symbol, document_id, period_end)`，主要字段包括：

```text
source, source_symbol, symbol, market
retrieved_at, effective_at, period_end, document_id, form, schema_version
revenue, net_income, total_assets, total_equity, operating_cash_flow
roe, gross_margin, net_margin, debt_to_assets, cash_conversion
revenue_growth, earnings_growth, current_ratio
```

原始 SEC JSON 或 BaoStock 查询结果先压缩保存在 raw；校验通过后才进入 canonical。水位按
数据源、市场和股票记录最近 `effective_at`。

## 评分与报告

日报读取报告时点之前每只股票最新的一份基本面。至少有两项有效指标时才计算基本面
横截面分数；当前综合分为：

```text
综合分 = 75% 技术/量价/风险分 + 25% 基本面分
```

基本面分使用 ROE、毛利率、净利率、现金转化、营收增长、利润增长和低负债率的有效
指标均值。缺失值不按 0 分惩罚，指标不足的股票继续按纯量价分数排名，并明确标注
“基本面数据不足”。

## 调度

`com.quantworkbench.fundamentals` 每小时检查一次：

- A 股工作日 17:00（北京时间）后更新。
- 美股工作日 17:30（美东时间）后更新。

作业按市场本地日期幂等，同日重复检查不会重复抓取。免费源无生产 SLA；SEC 默认按
每次请求间隔 0.22 秒控制在约 5 req/s 以下。正式长期使用应在运行环境设置可联系的
`SEC_USER_AGENT`。

手工运行：

```bash
quant-workbench ingest-fundamentals --market us --symbols AAPL MSFT
quant-workbench ingest-fundamentals --market cn --symbols 600036 600900
quant-workbench fundamentals-due --root data/lake --universe-directory data/cache/sector_probe
```

SEC API 文档：<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>

默认 User-Agent 仅使用本机占位联系地址 `contact@localhost`。为遵守 SEC Fair Access
政策，长期运行前应在 LaunchAgent 环境或启动 shell 中设置可联系的
`SEC_USER_AGENT="QuantWorkbench/0.1 your-email@example.com"`；该值不进入报告正文。
