# 消息面事件数据与评分

消息面从 2026-09-14 起进入报告。目标不是从标题生成交易指令，而是把每个结论绑定到
发布时间、抓取时间、原始链接和材料哈希，使报告可以回查并从任意历史时点截断。

## 免费数据源

| 市场 | 当前来源 | 内容 | 限制 |
|---|---|---|---|
| A 股 | 巨潮资讯，经 AKShare `stock_zh_a_disclosure_report_cninfo` | 上市公司公告标题、公告日期、公告 ID、原始链接 | 免费接口没有生产 SLA；列表接口通常只有日期，没有精确发布时间 |
| 美股 | Yahoo Finance，经 yfinance `Ticker.get_news` | 新闻标题、摘要、媒体、发布时间、原始媒体链接；另查 SPY/QQQ/TLT 捕捉市场级主题 | 聚合源，覆盖和字段可能变化；只适合个人研究 |
| 美股主源预留 | SEC EDGAR submissions | 8-K/6-K 等监管文件及接受时间 | 当前本机访问 SEC 返回 403，尚未启用为生产主源 |

巨潮只返回公告日期时，`effective_at` 保守设为次日 00:00（Asia/Shanghai）；同日抓取
则不早于实际 `retrieved_at`。美股新闻使用来源 `published_at`。每条事件另算
`tradable_at`，盘后材料顺延到下一工作日开盘。节假日精确顺延仍应在下一版接入交易日历。

## canonical schema

数据集：`canonical/dataset=events/market=<us|cn>/year=YYYY/month=MM/`

核心字段：

- 自然键：`(event_id, symbol)`；重复抓取时报告查询保留最新 `retrieved_at`。
- 时点：`published_at`、`retrieved_at`、`effective_at`、`tradable_at`。
- 证据：`title`、`summary`、`publisher`、`url`、`material_sha256`。
- 分类：`event_type`、`subtype`、`direction (-2..2)`、`confidence`、`horizon`。
- 审计：`scoring_method`、`taxonomy_version`、`schema_version`。

原始响应压缩保存在 `raw/source=<source>/dataset=events/`。规范化行只有在 URL、哈希、
时间顺序和方向范围校验通过后才进入 canonical；来源成功率低于 80% 时整批失败。

## 当前评分边界

第一版使用固定的中英文标题/摘要词典，方法名为
`deterministic_title_summary_keywords_v1`：

- 回购、增持、中标、上调指引等映射为偏多；
- 减持、立案、处罚、下调指引、召回等映射为偏空；
- 没有明确命中则为中性，只展示证据，不改变股票分数；
- 事件信号按 5 日半衰期衰减，汇总最近 14 日；
- 存在方向性事件时，在技术面与基本面得分之后叠加 15% 消息面权重。

第二版增加 `deterministic_macro_taxonomy_v2`，识别 Fed/FOMC、加息/降息、CPI、PPI、
非农、失业率等宏观标题。宏观事件标为 `scope=market`，单列在报告的宏观栏目，不参与
任何单只股票的 15% 消息分；`expects/forecast/likely/may/ahead/预期/可能` 等措辞标为
`certainty=likely` 和“预期/预测”，只有明确的政策决定措辞才标“已确认”。这避免把
“市场预计美联储加息”误写成“美联储已经加息”。

这不是 ChatGPT 解读。报告会明确标记“规则评分”，并给出原文链接。LLM 层必须在保存
公告/新闻正文、建立评估集并落 `prompt_version`、`model`、输入哈希后才可启用。

## 作业

```bash
quant-workbench ingest-events --market us --symbols AAPL MSFT --lookback-days 14
quant-workbench ingest-events --market cn --symbols 600900 600036 --lookback-days 14
quant-workbench events-due --root data/lake --universe-directory data/cache/sector_probe
```

后台 `com.quantworkbench.events` 每小时检查一次，并在每份报告前选择最近到期阶段，对完整
220 支股票池刷新；美股额外查询 SPY、QQQ、TLT 三个宏观代理。A 股阶段起点为
08:00、10:30、14:30；美股为 07:30、11:30、15:30
（各自市场当地时间）。每个市场、阶段、交易日只成功运行一次，电脑唤醒后会补跑最新阶段。
