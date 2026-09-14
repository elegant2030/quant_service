# GPT 板块与股票完整报告

Quant Workbench 会先用确定性程序完成市场内排名，再把 TOP 5 板块、TOP 15 股票、基本面、消息面和美股期权温度组成证据包，交给 GPT 生成中文研究解读。

## 设计边界

- 排名、分数、收益率、量比、基本面覆盖和消息评分全部由本地代码计算。
- GPT 只解释和综合，不重新计算分数，不改变候选名单。
- 输出使用严格 JSON Schema，固定包含 5 个板块和 15 只股票。
- 每次调用保存模型、提示词版本、证据包 SHA-256、响应 ID、token 用量和完整结构化结果。
- 禁止生成买入/卖出、目标价、仓位和保证收益式结论；“高/中/观察”只代表研究优先级。
- API 缺失或失败时，确定性报告仍会保存和推送，并明确标记 GPT 未调用或失败。

## 本地配置

本机默认使用已经登录 ChatGPT 的 Codex CLI，不读取或复制登录令牌。运行：

```bash
codex login status
```

应看到 `Logged in using ChatGPT`。运行时配置文件位于：

```text
/Users/lucky/Library/Application Support/QuantWorkbench/config/openai.env
```

内容如下：

```dotenv
QW_GPT_PROVIDER=codex_cli
QW_CODEX_BIN=/Users/lucky/.local/bin/codex
QW_CODEX_MODEL=
```

Codex CLI 使用临时会话、只读沙箱和独立空目录，且禁用项目规则读取。它消耗 ChatGPT/Codex 账号额度，不复用当前聊天窗口的上下文。登录失效、额度不足或调用失败时，确定性报告照常生成。

如需切换到独立计费、配额更可控的 OpenAI API，可改成：

```dotenv
QW_GPT_PROVIDER=openai_api
OPENAI_API_KEY=你的_API_Key
OPENAI_REPORT_MODEL=gpt-5-mini
```

建议只允许当前用户读取：

```bash
chmod 600 "/Users/lucky/Library/Application Support/QuantWorkbench/config/openai.env"
```

项目中的 `config/openai.env.example` 只有字段模板，不含密钥。不要把真实 `openai.env` 提交到 Git。

## 产物

每个市场、每个时点会写入四个文件：

```text
data/reports/market/{us|cn}/YYYY-MM-DD/{premarket|midday|postmarket}.json
data/reports/market/{us|cn}/YYYY-MM-DD/{premarket|midday|postmarket}.md
data/reports/market/{us|cn}/YYYY-MM-DD/{premarket|midday|postmarket}-gpt.json
data/reports/market/{us|cn}/YYYY-MM-DD/{premarket|midday|postmarket}-gpt.md
```

主 Markdown 会附上完整 GPT 解读；Telegram 会追加摘要和最多 5 个高优先级复核标的，以避免消息过长。

## 手工验证

```bash
quant-workbench market-report \
  --market us \
  --stage midday \
  --root "/Users/lucky/Library/Application Support/QuantWorkbench/data" \
  --no-live --no-send --strict
```

如果暂时不想调用 GPT，可加 `--no-gpt`。如需单次覆盖模型，可加 `--gpt-model MODEL_ID`。
