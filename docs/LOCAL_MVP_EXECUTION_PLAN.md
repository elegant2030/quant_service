# 本地量化平台：优化后的执行方案

## 目标与边界

第一阶段把现有研究内核升级为可持续积累数据、可重跑、可审计的本地平台。覆盖美股A股、美股日线和美股期权每日快照；暂不自动实盘，不把免费网页数据视当成有交易保障的行情。

验收分两层：

- 7 天冒烟：无重复写入、无静默漏跑、日报与告警按时产生。
- 30 天影子运行：作业成功率和准时率不低于 99%，完成一次备主源故障、备源切换和备份恢复演练。

## 数据与状态边界

```text
数据源 -> 原始响应 gzip -> 标校验/隔离 -> 原子 Parquet 分区 -> DuckDB 只读查询
                                      |
                                      +-> SQLite：作业、水位、源健康、LLM审计
```

- Parquet 是行情、财务、事件和期权的事实数据源。
- DuckDB 是查询层；多个定时进程不得共同写同一个 DuckDB 文件。
- SQLite 使用 WAL，只保存小型事务状态；备份使用 Online Backup/VACUUM INTO，不复制活动中的单个数据库文件。
- 同一数据集同一运行日只生成一个确定性文件名；重跑使用临时文件加 `os.replace` 原子替换。
- 每条标准化记录必须保留来源、源证券代码、抓取时间、有效时间、复权口径和 schema 版本。

## 免费数据源顺序

| 数据 | 第一阶段 | 备选/升级 | 限制 |
|---|---|---|---|
| 美股日线 | yfinance | Stooq 经同池验证后作为备源；正式阶段 IBKR/Alpaca/付费源 | 免费源仅研究使用，必须保存来源与原始响应 |
| 美股财报 | SEC EDGAR | 券商/付费标准化数据 | 以 acceptance time 做 PIT，保留 accession 与修订关系 |
| 美股期权 | yfinance 每日快照 | IBKR 延迟或订阅 OPRA | 不自动抓取 CBOE 延迟报价页面；不把中间价视为可成交价 |
| A股日线 | BaoStock | AKShare，Tushare 仅作补充 | 跨源先进入 staging 对账，不能静默覆盖 |
| A股公告 | 巨潮官方链路/合规接口 | AKShare 封装作补充 | 保存发布时间、修订和原文哈希 |
| 加密 | CCXT 抽象 | 第二交易所公共接口 | 按用户所在地和交易所权限选择，不假定 Binance/Deribit 可交易 |

## 作业规则

每个作业使用 `(job_name, idempotency_key)` 唯一键。成功作业再次执行时直接跳过；失败作业可安全重跑；运行超时后允许接管。每个源保存独立 watermark 和健康状态。备源切换只写 staging，质量对账通过后才能晋升。

调度使用交易所时区：A股 `Asia/Shanghai`，美股 `America/New_York`，存储时间统一为 UTC。macOS 首期使用 `launchd`，迁移到 Linux 后使用 systemd；Docker 仅用于需要隔离的服务。

## 六周实施顺序

1. 数据契约、Parquet 原子写、SQLite 状态、220+220 历史数据迁移；立即开始期权快照。
2. 增量行情、校验隔离、原始响应、watchdog、一致性备份与恢复测试。
3. EDGAR 与 A股公告，确定性去重、实体映射和事件时间。
4. 基本面 PIT、预期快照、LLM 结构化解读；建立 200–500 条分层评估集。
5. 滚动样本外回测、成本和冲击压力测试、组合风险预算。
6. IBKR paper、订单幂等与对账、熔断、7 天无人值守；随后进入 30 天影子运行。

## LLM 安全边界

- 收益、估值、Greeks、仓位和风控全部由确定性代码计算。
- LLM 只做材料归纳、证据映射、正反论点和不确定性表达。
- 输出保存模型、提示版本、输入材料哈希、成本、延迟和 schema 校验结果。
- 模型可以拒答或标记证据不足；未经样本外检验的情绪分数不能直接成为交易信号。

## 第一阶段实际命令

```bash
pip install -e '.[data,ops]'
quant-workbench ops-init
quant-workbench ingest-cache --market us
quant-workbench ingest-cache --market cn
quant-workbench snapshot-options --symbols SPY QQQ
quant-workbench ingest-daily --market us
quant-workbench ingest-daily --market cn
quant-workbench run-due --strict
quant-workbench watchdog --full --strict
quant-workbench ops-backup
quant-workbench ops-status
```

默认产物位于 `data/lake/`。该目录属于运行数据，不纳入 Git。

## macOS 调度

仓库内的 `ops/launchd/` 包含三个经过 `plutil` 校验的任务：

- `com.quantworkbench.pipeline`：每 30 分钟检查一次各市场是否出现新的完整交易日；未到期时幂等跳过。
- `com.quantworkbench.watchdog`：每 15 分钟检查数据新鲜度、逐证券覆盖率、源熔断和文件清单。
- `com.quantworkbench.backup`：每天 02:30 创建 SQLite 一致性副本和数据快照清单。

调度程序调用 `latest_completed_session`，按交易所日历和收盘后数据可用延迟判断，不使用固定 EST 时间。

由于 macOS 会限制后台任务访问 `Documents`，LaunchAgent 使用独立部署目录
`~/Library/Application Support/QuantWorkbench/`。其中包含非 editable Python 环境、股票池配置和运行数据；仓库仍作为开发源，不被后台进程直接读取。仓库更新后用 `ops/deploy_macos_runtime.sh` 重新部署；`ops/disable_macos_agents.sh` 可以停用任务但不会删除运行数据。
