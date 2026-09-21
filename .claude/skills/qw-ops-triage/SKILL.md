---
name: qw-ops-triage
description: 排查和维护 Quant Workbench 的后台运行：LaunchAgent（pipeline/watchdog/backup）、health/latest.json、日志、quarantine、水位停滞、备份、部署脚本。用户说"数据没更新""作业失败""watchdog 报错""launchctl 状态不对""怎么部署""重新安装后台""备份恢复"或任何涉及 /Users/lucky/Library/Application Support/QuantWorkbench 的问题时，必须使用本 skill。
---

# 运维排查

两个铁律：
1. 源码只在 `/Users/lucky/Workspace/Quant` 改；`Application Support/QuantWorkbench` 里的是非 editable 安装副本，改了会被下次部署覆盖。
2. 不手改 Parquet / SQLite。修数据走 CLI 重跑或 quarantine 复核。

## 排查顺序

```bash
# 1. 三个 agent 是否加载、最近退出码
launchctl print gui/501/com.quantworkbench.pipeline | grep -E 'state|last exit|runs'
launchctl print gui/501/com.quantworkbench.watchdog | grep -E 'state|last exit|runs'
launchctl print gui/501/com.quantworkbench.backup  | grep -E 'state|last exit|runs'
# state = not running 对一次性任务是正常的，看 last exit code 和 runs

# 2. 健康快照
cat '/Users/lucky/Library/Application Support/QuantWorkbench/data/health/latest.json' | python3 -m json.tool | head -80

# 3. 日志（先看 error）
tail -n 100 '/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/pipeline-error.log'
tail -n 100 '/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/pipeline.log'
tail -n 50  '/Users/lucky/Library/Application Support/QuantWorkbench/data/logs/watchdog.log'

# 4. 隔离区是否有新东西
ls -lt '/Users/lucky/Library/Application Support/QuantWorkbench/data/quarantine' | head

# 5. 用运行时自己的 CLI 检查（一定带 --root，否则查的是开发数据湖）
'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' watchdog \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data' --full --strict
```

`ops-status` 会打印 440 条水位；只看摘要，用 `--root ... | python3 -c '...'` 解析，不要把全量输出贴进对话。

## 常见故障与处理

| 症状 | 最可能原因 | 处理 |
|---|---|---|
| 美股日线水位停在几天前，日志有 429/JSON 错误 | yfinance 限流或接口变更 | 不要循环重试；等 1 小时后单独跑 `ingest-daily --market us`；持续失败则升级 `yfinance` 版本并在开发环境验证 |
| A 股水位停滞，日志 BaoStock login/网络错误 | BaoStock 服务波动 | 重跑一次；连续两天失败切 AKShare 备源（独立分区，不覆盖） |
| AKShare 返回空表 | 上游网页改版 | 升级 akshare；空表必须被校验拦下进 quarantine，若没拦下先修校验 |
| 周末/夜间没有运行记录 | Mac 睡眠 | `pmset -g` 查睡眠设置；`run-due` 醒来后会补跑，确认补跑成功即可 |
| pipeline exit≠0 但日志无堆栈 | 锁文件残留或 venv 损坏 | 检查 `ops/lock.py` 的锁路径；`venv/bin/python -c "import quant_workbench"` |
| watchdog strict 失败：新鲜度 | 交易日历判断错（半日、节假日） | 对照 `ops/calendar.py`，用开发环境复现该日期 |
| quarantine 有新文件 | 校验失败 | 读 quarantine 内的原因文件；确认是数据问题还是校验太严；修完后重跑该作业，不手动搬回 canonical |
| 后台跑的是旧代码 | 忘记部署 | 对比开发仓库 HEAD 与运行时版本；执行部署脚本 |

## 部署与回滚

```bash
cd '/Users/lucky/Workspace/Quant'
.venv/bin/python -m unittest discover -s tests -v      # 先测
./ops/deploy_macos_runtime.sh                           # 再部署
# 验收
'/Users/lucky/Library/Application Support/QuantWorkbench/venv/bin/quant-workbench' watchdog \
  --root '/Users/lucky/Library/Application Support/QuantWorkbench/data' --full --strict
```

停用：`./ops/disable_macos_agents.sh`。回滚：切回上一个 git tag 后重新部署；数据不需要回滚，Parquet 是追加/原子替换的。

## 备份与恢复演练

- 备份目录 `data/backups/`，每天 02:30；每周一次恢复演练：从最近备份复制到临时目录，用 DuckDB 查行数与 canonical 对比。
- 异地备份未配置前，在健康报告里保持一条警告，不要移除。

## 修改后台行为时

- plist 模板在 `ops/launchd/`，改模板再部署，不直接编辑 `~/Library/LaunchAgents/` 下的文件。
- 新增作业：CLI 子命令 → orchestrator 到期规则 → plist（如需独立周期）→ 健康检查项 → 本文件的故障表。
- 任何会写 canonical 的改动，先在开发数据湖跑通，再部署。
