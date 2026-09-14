# Telegram 告警（T5）

最后更新：2026-09-13。实现在 `src/quant_workbench/ops/alert.py`，只依赖标准库。

## 原则

- 告警失败不能让作业失败：所有入口都吞掉网络和配置异常，只在返回 JSON 的 `alerts` 字段和日志里记录。
- 凭据不进仓库：只读后台运行时的 `config/alerts.env`（chmod 600）或环境变量。部署脚本不会创建、复制或覆盖这个文件。
- 去重：状态记录在 `data/health/alert-state.json`，watchdog 每 15 分钟一跑也不会重复刷屏。

## 配置

1. 在 Telegram 找 @BotFather 发 `/newbot`，拿到 bot token；给新 bot 发一条任意消息。
2. 查 chat_id（把 `<TOKEN>` 换成你的，自己在终端跑）：

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool | grep -A3 '"chat"'
```

3. 写入后台运行时配置（模板见 `ops/alerts.env.example`）：

```bash
install -m 600 ops/alerts.env.example '/Users/lucky/Library/Application Support/QuantWorkbench/config/alerts.env'
open -e '/Users/lucky/Library/Application Support/QuantWorkbench/config/alerts.env'
```

4. 发送测试消息：

```bash
'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' alert-test \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data'
```

开发数据湖可以用 `QW_ALERTS_ENV=/path/to/alerts.env` 指定配置文件，或直接导出 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`。环境变量优先于文件。

## 触发规则

| 触发 | 来源 | 去重规则 |
|---|---|---|
| 🔴 watchdog ERROR | `watchdog` 报告 `status=error` | 错误列表指纹变化时发；同一指纹每 `QW_ALERT_REPEAT_HOURS`（默认 6h）重发一次；恢复 ok 发 🟢 recovered |
| 🟠 quarantine grew | `inventory.quarantine_files` 比上次记录增加 | 只在增加时发 |
| 🟠 job failed 2x in a row | `job_runs` 表同一作业最近两次完成记录都是 failed | 同一失败流只发一次；新的失败 run 再发 |
| 🔴 pipeline run-due failed | `run-due` 报告 `status=error` | 错误集合变化时发；成功后清零 |
| 🟢/🟡/🔴 heartbeat | 每天本地 `QW_ALERT_HEARTBEAT_HOUR`（默认 09:00，时区 `QW_ALERT_HEARTBEAT_TZ`）后第一次 watchdog | 每天一条；心跳缺席说明 watchdog 本身没跑 |

心跳内容：状态、各市场水位与覆盖率、期权快照日期、quarantine 文件数、`build_sha`。

## build_sha / deployed_at

`ops/deploy_macos_runtime.sh` 在部署时把开发仓库 HEAD 写入 `<runtime>/build-info.json`（工作树有未提交改动时带 `-dirty` 后缀）。`ops/health.py` 读取后放进 `health/latest.json` 的 `build_sha` 和 `deployed_at`，心跳消息也带上，用来发现"忘了部署"。

## 关闭告警

- 临时：`watchdog --no-alerts` / `run-due --no-alerts`。
- 持久：在 `alerts.env` 里设 `QW_ALERTS_ENABLED=0`。

## 验收（CLAUDE.md T5）

1. `alert-test` 收到测试消息。
2. 人为制造一次校验失败（例如往开发数据湖写一行 `close<=0` 的 bar），运行 `watchdog --strict`，收到 🔴 消息；修复后再跑一次收到 🟢 recovered。
3. 第二天 09:00 后收到心跳。

## 未做

- 不发送 LaunchAgent 自身崩溃（Python 启动前失败）的告警；靠心跳缺席发现。
- 没有告警静默窗口或按严重度分级的路由。
- 没有异地备份状态检查项。
