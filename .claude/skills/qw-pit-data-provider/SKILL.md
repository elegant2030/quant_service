---
name: qw-pit-data-provider
description: 在 Quant Workbench 仓库中新增或修改数据源（provider）、数据集、采集作业、备源切换或快照作业时的规程。任何涉及 yfinance / BaoStock / AKShare / SEC EDGAR / 巨潮 / CCXT / 一致预期快照 / 期权快照 / 股票池快照的开发，或用户提到 ingest、watermark、quarantine、point-in-time、PIT、effective_at、accepted_at、fallback、备胎、数据落地时，都必须先读本 skill。即使用户只是说"把 XX 数据拉下来"，也适用。
---

# 新增 point-in-time 数据源 / 数据集

本项目的事实数据是 Parquet，控制状态在 SQLite，异常数据进 quarantine。新增任何数据都要同时满足**可追溯、可重跑、可截断到历史某一时刻**三个条件。做不到第三条的数据不能进 canonical，只能进研究缓存。

## 开工前确认

1. 开发只在 `/Users/lucky/Workspace/Quant` 进行，不碰 `Application Support/QuantWorkbench`。
2. 先看 `src/quant_workbench/data/`、`store/files.py`、`store/state.py`、`jobs/ingestion.py`，沿用现有分区、原子写、水位和锁，不新造平行机制。
3. 想清楚这个数据集的三个时间：
   - `retrieved_at`：我们什么时候拉到的（总是有）。
   - `effective_at`：这条记录从什么时候起对市场可知。日线 = 交易日收盘时刻；财报 = 监管接受时刻 `accepted_at`；公告 = `published_at`；快照类 = 快照时刻。
   - `period_end`（如有）：数据描述的期间末。
   PIT 截断永远用 `effective_at`，绝不用 `period_end` 或 `retrieved_at`。

## 必备列

每个 canonical 数据集至少有：`source`、`source_symbol`、`symbol`、`market`、`retrieved_at`、`effective_at`、`schema_version`。日线另加 `adjustment`。

日线存**原始价 + 复权因子**（`close_raw`、`adj_factor` 或 `dividend`/`split`），复权在读取时算。原因：yfinance 和 BaoStock 的"已复权价"会在每次分红拆股后整体回溯变化，存复权价会让回测结果随时间漂移，也会让"与已有数据不一致"的校验大量误报。

修订类数据（10-K/A、更正公告、财报重述）作为新行入库并用 `supersedes_id` / `amends_accession_no` 回指原件，不覆盖原件。

## 作业结构

```
fetch(source, keys, since_watermark)  →  validate(rows)  →  raw gzip + manifest(sha256)
        →  canonical upsert（原子替换）  →  set_watermark  →  记录 provider health
                    ↘ 校验失败 → quarantine/<dataset>/<run_id>/ + 告警，不入 canonical
```

- 幂等：按自然键 upsert（日线 `(source, symbol, date)`；文件 `accession_no`；公告 `announcement_id`），重跑无副作用。
- 水位：逐证券、逐数据集。文件类用单调 id（accession_no），不用日期，防止同日多份漏采。
- 限流：每个 source 一个 token bucket；yfinance 日线每交易日只允许成功一次（SQLite 记 `last_success_date`）；SEC 默认 5 req/s。
- 失败：`tenacity` 指数退避 ≤ 5 次后整体失败，不允许半张表入库。
- 锁：复用 `ops/lock.py`。
- 日历：用 `ops/calendar.py` 的"最近一个已完成交易日"决定是否到期；加密数据需要先扩展日历为 UTC 日/8h 周期。

## 备源规则（红线 5）

备源数据永远落在独立分区 `source=<backup>`，然后：
1. 与主源按自然键对账，差异写 `reconcile/<dataset>/<date>.parquet`；
2. 差异超过阈值（价格 >0.5%、成交量 >20%、行数缺口）→ 告警，不晋升；
3. 晋升为"当前有效"只能由规则（主源连续 N 天失败）或人工触发，并在 SQLite 记录晋升原因。
静默覆盖主源是最严重的违规。

## 快照型数据集

没有免费历史的数据（一致预期、期权链、股票池成分、行业分类）从现在开始定期快照，就是自建 PIT：
- 每次快照一整份，`effective_at` = 快照时刻，不做 diff 入库；
- 固定时刻（美股 16:15 ET 后、A 股 17:00 CST 后、月初第一个交易日）；
- 分析时取"不晚于分析日的最近一份"。

## 校验清单（写进 `data/validation.py`）

- 主键唯一；`effective_at <= retrieved_at`；日期在交易日历内。
- 价格：`low <= open,close <= high`，非负，单日跳变 >50% 需有拆股记录，否则隔离。
- 与已入库同键值对比：原始价不应变化（允许 1e-6），变化即隔离并标记 `restated`。
- 财务：三表勾稽（资产 = 负债 + 权益，现金流三段之和 = 现金变化）容差 1%。
- 文本：非空、字符数 > 阈值、sha256 与 manifest 一致。

## 测试要求

- 用录制的响应做 fixture（`tests/fixtures/<source>/*.json.gz`），测试不打真实网络。
- 至少覆盖：正常增量、重跑幂等、校验失败进 quarantine、修订回指、水位推进。
- 跑 `.venv/bin/python -m unittest discover -s tests -v` 和针对改动文件的 `ruff check --select E,F,I,B`。

## 完成后

1. 在 `cli.py` 注册子命令，在 `jobs/orchestrator.py` 声明到期规则。
2. 更新 `docs/DATA_SOURCE_DECISION.md`（来源、限制、许可）和数据字典（时间字段含义）。
3. 需要后台跑时才执行 `./ops/deploy_macos_runtime.sh`，并用 `watchdog --root '<Application Support>/data' --full --strict` 验收。
4. 不要把免费源描述成有生产 SLA。

## 示例：EDGAR 文件索引

```
filings_index: accession_no(PK), cik, symbol, market='us', form, filed_at, accepted_at(UTC),
               period_end, items, is_amendment, amends_accession_no, raw_path, sha256,
               source='sec_edgar', retrieved_at, effective_at(=accepted_at), schema_version=1
水位: (sec_edgar, symbol, filings) -> 最大 accession_no
PIT 查询: WHERE accepted_at <= :t  且取同 period 最新已接受版本
```
