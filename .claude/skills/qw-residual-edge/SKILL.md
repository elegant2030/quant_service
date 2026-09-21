---
name: qw-residual-edge
description: 把 Quant Workbench 策略的收益序列拆成"基准暴露"和"残差边际"：带截距 OLS、Newey–West（HAC）标准误、年化 alpha 与 t 值、基准 R²、滚动稳定性、备选基准敏感性、分状态拆解。凡是要回答"这个策略的收益是不是只是 beta""相对 SPY / 沪深300 / 同池等权有没有独立超额""回撤来自基准还是策略本身"，或者 T4 回测报告要写超额、α/β 时，必须使用本 skill。只接受带日期的收益序列，不接受年化、Sharpe 这类汇总数字。不做持仓层面的 Brinson 归因。
---

# 残差边际分析（qw-residual-edge）

改编自 tradermonty/claude-trading-skills 的 `residual-edge-analyzer`（MIT，见 `LICENSE-UPSTREAM`）。
`scripts/analyze_residual_edge.py` 和 `references/` 为上游原文件，未改动；`scripts/curve_to_returns.py` 与本文件是本项目新增。
脚本只用 Python 标准库，不联网、不取数、不下单。

定位：`qw-backtest-audit` 审完数字口径之后的**证伪关卡**。结论只是研究证据，不是买卖依据（红线 1）。

## 什么时候用

- 回测报告要写"超额收益、α、β"（CLAUDE.md T4、审查 1.1）。
- 有人引用"年化 27.7% / Sharpe 1.99"这类数字，需要判断其中多少来自基准暴露。
- 解释一段回撤：是基准跌了，还是策略自身行为。

## 流程

### 1. 先声明，再看结果

用一句话写下"声称的独立边际是什么"。然后选定：

- **主基准**：最像"这个策略的简单复制品"的那个。横截面选股策略的主基准应是**同股票池等权**，不是大盘指数；只用大盘指数做基准不能声称有选股 alpha。
- **至少一个备选基准模型**：例如 `同池等权 + 低波动`，或 `SPY`（美股）、`000300`（A 股）。

四项声明必须写进 config 的 `data_declarations`，缺一项报告就降级为 `REVIEW_REQUIRED`：

| 字段 | 取值 | 本项目的如实填法 |
|---|---|---|
| `baseline_selection` | `predeclared` | 看结果前定好；事后换基准属于数据挖掘 |
| `strategy_return_basis` / `baseline_return_basis` | 同为 `gross` 或同为 `net` | 引擎曲线已扣佣金和滑点 → `net`；指数价格序列是 `gross`，两者混用必须在报告里说明 |
| `analysis_scope` | `out_of_sample` / `live` / `in_sample` | 现有回测全部是 `in_sample` |
| `universe_data` | `point_in_time` / `current_constituents` / `not_applicable` | 在 T2 快照积累出历史之前，一律是 `current_constituents` |

不许因为某个基准给出更好看的残差就选它。

### 2. 准备收益序列

分析器要一张对齐的 CSV：`date, strategy_return, <基准>_return, ...`，简单收益，小数表示。

项目的回测曲线是 `timestamp,equity,cash,gross_exposure`（`scripts/run_strategy_research.py::write_curve`）。用辅助脚本转换并对齐：

```bash
.venv/bin/python .claude/skills/qw-residual-edge/scripts/curve_to_returns.py \
  --strategy data/cache/strategy_run_2026-09-13/curves/us_momentum_120_monthly.csv \
  --baseline equal_weight=data/cache/strategy_run_2026-09-13/curves/us_equal_weight_monthly.csv \
  --baseline low_vol=data/cache/strategy_run_2026-09-13/curves/us_low_volatility_60_monthly.csv \
  --output /tmp/qw_resid/returns.csv
```

- `--baseline 名称=路径[:列名]` 可重复；输出列名是 `<名称>_return`。
- 先在**共同日历**上对齐价位再算收益，避免某条序列缺一天时把两日涨跌当成一日收益。
- 指数基准（SPY、000300）用价位 CSV 传入。canonical 日线是原始价，必须先用 `data/adjust.py::apply_adjustment` 复权后再导出，否则除权日会出现假跳变。
- 契约要求：日期唯一、收益有限且大于 -100%、策略与基准同频率。细节见 `references/input-contract.md`。

没有带日期的收益序列就停止，报告"输入不足"，不要根据汇总指标编造观测值。

### 3. 运行分析器

```bash
.venv/bin/python .claude/skills/qw-residual-edge/scripts/analyze_residual_edge.py \
  --input /tmp/qw_resid/returns.csv \
  --config /tmp/qw_resid/config.json \
  --output-json /tmp/qw_resid/report.json \
  --output-markdown /tmp/qw_resid/report.md
```

config 模板见 `references/input-contract.md`。日频 `rolling_window` 建议 126；`hac_lags` 用 `auto`；阈值要在看结果前固定。

残差边际比 = 年化 alpha ÷ 年化残差波动率。不要用 OLS 残差的均值算 Sharpe，带截距时它恒为零。

### 4. 解读

四个诊断状态：

- `RESIDUAL_EDGE`：alpha、残差边际比、滚动稳定性都过阈值。
- `BASELINE_EXPLAINED`：基准 R² 高而残差证据弱。
- `RESIDUAL_FRAGILE`：未过某项稳健性关卡，或换基准后结论改变，或滚动分析不完整，或没有提供备选基准。
- `INSUFFICIENT_EVIDENCE`：样本低于下限。

`decision_eligibility` 要单独看：统计上有意思的结果，只要存在来源、成本口径、样本或多重共线性方面的严重警告，仍然是 `REVIEW_REQUIRED`。在本项目现状下（样本内、当前成分股），**任何结果都会是 `REVIEW_REQUIRED`，这是正确的**。

依次检查：主模型与敏感性模型状态；年化 alpha 与 HAC t 值；残差边际比与残差自相关；滚动 alpha 稳定性；多因子模型的 VIF；预先声明的状态分组下的主动收益。

### 5. 写进报告

- 报告里同时给出：基准载荷（β）、年化 alpha 及 t 值、R²、状态标签、全部 HIGH 级证据警告。
- 结论措辞用"相对 X 基准未发现/发现独立超额的证据"，并带上 CLAUDE.md 第 13 节的限定语（幸存者偏差、样本内、无完整成本）。
- 基准选择、样本外、稳定性方面的发现回交 `qw-backtest-audit`；反复出现的失效状态交 `qw-strategy-red-team`。
- 不根据结果自动改变任何仓位、暴露或订单。本项目不下单。

## 边界

- 这不是持仓贡献分析。Brinson 的配置、选择、交互效应需要历史持仓、基准权重和成分收益。
- 只用大盘指数做基准时，不得声称有选股 alpha。
- 不得用当前成分股构造等权基准却标成 point-in-time。
- 样本内的残差边际不算已确认的 alpha。
- 不要在看到亏损之后再挖很多种状态定义。预先声明少数几种，样本外再确认。
- R² 高不等于策略没价值；容量、尾部行为、成本和实现价值需要单独的证据。

## 参考实测（2026-09-21，仅说明用法，不是结论）

美股 120 日月频动量对同池等权，2023-08-16 至 2026-09-11 共 771 个交易日：β 1.24，年化 alpha -0.53%，HAC t 值 -0.06，R² 0.43，状态 `RESIDUAL_FRAGILE`，`REVIEW_REQUIRED`，并带"当前成分股基准有幸存者偏差""非样本外"两条 HIGH 警告。含义：该策略相对同池等权没有独立超额的证据，收益主要来自放大的基准暴露。

## 资源

- `scripts/analyze_residual_edge.py`：确定性的 CSV → JSON / Markdown 分析器（上游原文件）。
- `scripts/curve_to_returns.py`：项目曲线和价位序列 → 对齐收益 CSV（本项目新增）。
- `scripts/tests/`：上游 26 个测试加本项目 4 个测试，`.venv/bin/python -m pytest .claude/skills/qw-residual-edge/scripts/tests`。
- `references/input-contract.md`：CSV 与 config 契约、可运行示例。
- `references/methodology.md`：统计定义、解读与局限。
