# 开源框架与 Quant Workbench 的结合方案

原则：**只引入能直接填补 P0/P1 缺口且许可证兼容（MIT / Apache / BSD）的库；多 Agent 框架只借 schema 和设计，不整库依赖；GPL 项目只做参考。** 下表是结论，后面是每一项的接法。

| 缺口（来自 AGENT_HANDOFF §12） | 引入 | 方式 | 时机 |
|---|---|---|---|
| P0-1 EDGAR 采集 + `accepted_at` | **edgartools**（MIT） | 新 provider `data/providers/edgar.py` | 现在 |
| P0-2 巨潮公告 | AKShare 巨潮接口 + 自写 PDF 落地 | 新 provider `data/providers/cninfo.py` | 现在 |
| P0-3 PIT 财务表 | edgartools XBRL 标准化输出 | `fundamentals/pit.py` | 现在 |
| P0-4 walk-forward + 成本 | 沿用自有 `backtest/`；参考 Jesse 的零前视约束、skfolio 的时序 CV 设计 | 不引库 | 现在 |
| 事件 schema / 利好利空 | TradingAgents 的结构化输出 Agent 设计；FinRobot 的"数值由算子、叙述由 LLM"原则 | 借 schema，写入 `events/` 与 `ai/` | 现在 |
| P1-1 provider fallback | 自写 | `data/fallback.py` | 之后 |
| P1-2 IV 历史 | 自有 `derivatives/options.py` + py_vollib（MIT）加速 | 增 `iv_history` 数据集 | 现在起累积 |
| P2-1 加密行情 | **CCXT**（MIT） | `data/providers/ccxt_ohlcv.py`、`ccxt_funding.py` | P2 |
| P2-2 IBKR | ib_async | `execution/ibkr_readonly.py` | P2 |
| P2-3 组合层 | **Riskfolio-Lib**（BSD-3）为主，skfolio（BSD）做 CV | `portfolio/` | P2 |
| P2-4 LLM 审计材料包 | 自写 gateway；prompt 版本化 | `ai/gateway.py`、`ai/prompts/` | 现在留接口 |
| 策略研究 Agent（未来） | microsoft/RD-Agent（需 Qlib 数据格式） | 写 Parquet→Qlib bin 转换脚本后接 | 半年后 |
| A 股执行（未来） | vnpy（MIT） | 只用网关层 | 用户授权后 |

---

## 1. edgartools → `data/providers/edgar.py`

edgartools 直连 SEC，不需要 key，能把 10-K/10-Q/8-K/Form 4/13D 解析成对象，并有 `text()`/`markdown()` 输出适合 RAG。接入时把它当"解析器"，`accepted_at` 以 SEC submissions JSON 里的 `acceptanceDateTime` 为准（这是监管时间戳，PIT 截断只认它）。

数据集设计（两张表，均按红线 3 带齐通用列）：

```
filings_index
  accession_no (PK), cik, symbol, market='us', form, filed_at(date),
  accepted_at(ts, UTC), period_end, primary_doc_url, items(8-K item 列表),
  is_amendment, amends_accession_no, raw_path, sha256,
  source='sec_edgar', retrieved_at, effective_at(=accepted_at), schema_version

filings_text
  accession_no, section_id (item_1a / item_7 / item_7a / ex99_1 / cn_mdna ...),
  section_title, text, char_len, sha256
```

采集作业骨架（放在 `jobs/ingestion.py` 里注册为 `ingest-filings`）：

```python
# 伪代码，字段名以 edgartools 当前版本文档为准
from edgar import set_identity, Company
set_identity("Your Name your@email")          # SEC 要求 User-Agent

def ingest_filings(symbol, cik, since_accession: str | None):
    company = Company(cik)
    filings = company.get_filings(form=["10-K","10-Q","8-K","6-K","4","SC 13D","SC 13G"])
    for f in filings:                           # 按 accepted 时间正序
        if since_accession and f.accession_no <= since_accession:
            continue
        raw = f.html() or f.text()              # 原文 gzip 落 raw/
        write_raw(raw, key=f.accession_no)      # 复用 store/files.py 原子写
        row = to_index_row(f)                   # accepted_at 来自 f.acceptance_datetime 或 submissions API
        sections = split_sections(f)            # 10-K/10-Q: Item 1A/7/7A；8-K: item + EX-99.1
        upsert_parquet("filings_index", [row]); upsert_parquet("filings_text", sections)
        set_watermark("sec_edgar", symbol, "filings", f.accession_no)
```

要点：
- 水位用 `accession_no`（单调递增）而不是日期，避免同日多份文件漏采。
- 修订（10-K/A）作为新行入库并回指原件，**不覆盖**原件；PIT 查询默认取截止时点前最新已接受版本。
- XBRL 财务用 `company.get_financials()` 标准化后写 `fundamentals_pit`，每行带 `accepted_at`，回测只取 `accepted_at <= t`。
- SEC 限速 10 req/s，provider 里加 token bucket，默认 5 req/s。
- edgartools 附带 MCP server 和 skills；本地研究时可以让 Claude Code 直接查文件，但**生产采集不走 MCP**，走上面的作业。

## 2. 巨潮公告 → `data/providers/cninfo.py`

AKShare 的 `stock_zh_a_disclosure_report_cninfo` 只给列表，原文 PDF 要按返回的 URL 自行下载落 raw/。

```
announcements_cn
  announcement_id (巨潮 id, PK), symbol, market='cn', title, category(公告类型),
  published_at(ts, Asia/Shanghai→UTC), pdf_url, raw_path, sha256,
  supersedes_id(更正公告回指), source='cninfo', retrieved_at, effective_at(=published_at)
```

规则：盘后公告（15:00 后）在下一交易日开盘才可交易，`effective_tradable_at` 在事件层计算，不在采集层猜。AKShare 断连时作业整体失败重试，不允许半张表入库（quarantine 已有机制）。

## 3. 事件层 → 新包 `events/`

借 TradingAgents 的做法：每个 Agent 的输出是严格 schema（它在 v0.2.4 引入了结构化输出 Agent 和持久化决策日志），但不用它的图编排。落地为：

```
events/
  taxonomy.py     事件枚举（见 qw-event-scoring skill references/taxonomy.md）
  schemas.py      pydantic: EventScore, DriverDelta, Evidence
  linker.py       实体链接：标题/正文 → instrument_id（先规则，后 LLM）
  scorer.py       调用 ai.gateway，输入材料包，输出 EventScore
  event_study.py  CAR 计算、反应模板
```

存储：`events`（原始事件，一行一条材料）、`event_scores`（一行一次打分，带 `prompt_version`、`model`、`input_sha256`），Parquet 落地，SQLite 记作业与成本。

## 4. FinRobot → `fundamentals/valuation.py`

FinRobot 的核心原则与本项目红线 2 完全一致：DCF、WACC、蒙特卡洛等由纯 Python 算子生成并带溯源，LLM 只做解释。它的算子可以直接抄写为本项目的确定性函数（注意其许可证，按文件保留版权头）。预期账本里 `own_estimate` 的估值锚由这些算子算，LLM 只提供驱动项文字。

## 5. `ai/gateway.py`

不引 LiteLLM，先定义接口，OpenAI Responses API 是第一实现：

```python
class LLMGateway(Protocol):
    def complete(self, *, schema: type[BaseModel], messages: list[dict],
                 tier: Literal["bulk","analysis","deep"], prompt_version: str,
                 material_sha256: str) -> tuple[BaseModel, CallRecord]: ...
```

`CallRecord` 落 SQLite：model、prompt_version、material_sha256、tokens、cost、latency、raw_json_path。这样以后换 DeepSeek 或本地 Qwen 只加一个实现，评估集不用改。

## 6. Riskfolio-Lib / skfolio → `portfolio/`

- Riskfolio-Lib 基于 CVXPY，直接支持 Black-Litterman、风险平价、HRP、26 种凸风险度量、换手和跟踪误差约束。LLM 对个股/板块的观点（带置信度）作为 BL views 进入优化，是把 LLM 判断量化融合最干净的接法。
- skfolio 提供 scikit-learn 风格 API 和时序感知的交叉验证，适合把"组合构建参数"也放进 walk-forward。
- 接法：`portfolio/optimizer.py` 输入 `expected_returns`（来自预期账本）、`views`（来自事件层）、约束（单票/行业/币种/Greeks），输出目标权重；成本项复用现有 Almgren-Chriss。

## 7. CCXT → `data/providers/ccxt_*.py`

- 只用公共接口（OHLCV、funding rate、open interest），不配置 API key。
- 日历抽象要先改：加密没有"最近一个已完成交易日"，改为"最近一个已完成 UTC 小时/日"和 8h funding 周期。
- 币安/OKX 各自作为独立 `source`，同一 `symbol` 两源落两个分区再对账，与红线 5 一致。

## 8. RD-Agent（延后）

RD-Agent 的 `fin_factor_report` 场景可以从财报文件夹直接抽取并实现因子，且默认用 LiteLLM 后端，和"从财报/研报里找因子"这个需求正对。但它依赖 Qlib 的数据目录格式（calendars/、features/、instruments/）。
- 前置：写 `scripts/export_qlib.py`，把 canonical 日线转成 Qlib bin（Qlib 自带 `dump_bin.py`）。
- 时机：PIT 财务表和 EDGAR 原文都有了之后再接，否则它挖出来的因子无法验证。
- 同类可替代：LLMQuant/Alpha-Agent（基于 RD-Agent(Q) 原则）、QuantaAlpha（LLM + 进化）。

## 9. 明确不引入的

- TradingAgents / TradingAgents-CN 整体：逐票分析形态，与流水线不匹配；TradingAgents-CN 长期由单人维护，风险高。只借其新闻过滤思路。
- ai-hedge-fund：投资人格辩论，工程价值低。
- Freqtrade / OctoBot：GPL-3.0，且是完整 bot，与本项目"研究平台、禁止自动下单"定位冲突。
- NautilusTrader：生产级但过重，现阶段 Python 自有引擎够用。
- Backtrader：多年无维护。

## 10. Skills 的结合

本包附带 7 个项目专属 skill（见 `skills/README.md`）。它们不是代码库，而是给 Claude Code / Codex 等 Agent 的领域规程，作用是让 Agent 在写 provider、回测、报告和 LLM 解读时自动遵守红线。外部可直接安装的 skill 与本包的关系也在 README 里说明。
