# 本地量化平台后台运行验收

验收日期：2026-09-13（America/Los_Angeles）

## 已部署

- 运行目录：`~/Library/Application Support/QuantWorkbench/`
- 美股日线：220 只，167,466 行，水位 2026-09-11
- A股日线：220 只，162,811 行，水位 2026-09-11
- SPY/QQQ 期权：2,117 条合约，保存原始 gzip 和标准化 Parquet
- SQLite 控制库、逐证券水位、来源健康分、原子 Parquet 写入和隔离区
- 一致性备份数据库与数据快照清单

## LaunchAgent

| Label | 周期 | 首次后台执行 |
|---|---:|---|
| `com.quantworkbench.pipeline` | 30 分钟 | exit 0 |
| `com.quantworkbench.watchdog` | 15 分钟 | exit 0 |
| `com.quantworkbench.backup` | 每天 02:30 | 手动 kickstart 验收 exit 0 |

第一次直接从 `Documents` 启动时被 macOS 后台隐私保护拒绝读取虚拟环境环境；已改用 Application Support 独立运行时，三项后台任务均验证成功。

## 验收结果

- 双市场逐证券新鲜度覆盖率：100%
- SQLite `integrity_check`：ok
- Parquet 清单和完整 SHA-256：通过
- 备份恢复清单：3 个数据集、2 个原始文件，验证通过
- 周末场景：正确识别上一交易日并幂等跳过
- 单元测试：包含原子写入、隔离、幂等、交易日历、熔断、备份和 watchdog

## 运行限制

LaunchAgent 需要用户登录，Mac 进入深度睡眠时不会真正做到 24 小时持续采集。当前适合作为 7 天本地冒烟运行；长期无人值守应使用不休眠的 Mac 或迁移到 Ubuntu/systemd。Telegram 告警和异地 rclone 备份尚未配置。
