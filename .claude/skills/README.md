# Quant Workbench Agent Skills

本目录包含项目级 Agent Skills，随仓库和代码审查一起维护。

- `qw-*`：本项目专用规则，来自已审计的 `qw-additions` 包。
- `ml4t-*`：从 `ml4t/skills` 精选安装的通用量化研究纪律。
- `pybroker-*`：仅在使用 PyBroker API 时加载的官方 Skill。

项目专用规则优先。任何第三方 Skill 中出现的实时交易、账户连接、凭证读取或
下单示例都只是上游材料，不代表用户授权；本项目默认保持研究和只读模式。

完整路由与红线见仓库根目录 `AGENTS.md` 和 `docs/AGENT_HANDOFF.md`。
