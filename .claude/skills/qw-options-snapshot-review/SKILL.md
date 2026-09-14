---
name: qw-options-snapshot-review
description: 分析或扩展 Quant Workbench 的美股期权链快照（当前 SPY/QQQ，src/quant_workbench/derivatives/options.py 与 snapshot-options 作业）：隐含波动率、Greeks、25Δ 偏斜、期限结构、跨式价格、put/call 比率、流动性过滤、IV 历史与百分位的构建。用户提到期权、option chain、IV、Greeks、skew、straddle、DTE、期权贵不贵、covered call/wheel、卖方策略，或要新增期权数据集时，必须使用本 skill。
---

# 期权快照分析

当前状态：只有单日快照，没有 IV 历史，所以**不能**判断期权贵或便宜；报价是中间价，不是可成交价。任何期权结论都要先过流动性过滤，再谈指标。

## 快照前的流动性过滤

对每张合约先算再筛：
- `bid > 0` 且 `ask > bid`；
- 相对价差 `(ask − bid) / mid`：ATM 附近 ≤ 5%，虚值 ≤ 15%，超过的合约标 `illiquid`，指标计算时排除；
- `open_interest ≥ 100` 或 `volume ≥ 10`（SPY/QQQ 可以更严）；
- 合约乘数（美股标准 100）写进行；
- 记录快照时刻与标的现货价、无风险利率来源、股息率（SPY/QQQ 不是 0，按 ETF 近 12 个月分红/现价计算，作为输入字段）。

## 指标定义（写进代码注释，避免口径漂移）

- ATM IV：现货最近的两个行权价按距离加权插值；每个到期日一个值。
- 25Δ 偏斜：`IV(25Δ put) − IV(25Δ call)`，Delta 用 Black–Scholes（欧式 ETF 期权可用；单股美式期权需 BAW/二叉树，标明近似）。
- 风险反转 RR25 = 上式；蝶式 BF25 = `(IV(25Δ put) + IV(25Δ call)) / 2 − ATM IV`。
- 期限结构：各到期日 ATM IV 序列；标注 contango / backwardation。
- 跨式价格占比：`(ATM call mid + ATM put mid) / spot`，同时给用 bid 和用 ask 的版本。
- Put/Call：分别给 OI 比和成交量比，且说明 SPY/QQQ 的 put 端天然偏高（对冲需求），不能单独解释为看空。
- 财报隐含波幅（单股）：最近到期的 ATM 跨式 / 现货，与历史实际财报波幅比较（有历史后）。

## IV 历史（P1，从现在开始积累）

每日固定时间（美东 16:15 后）快照，除原始链外另存 `iv_history` 数据集：

```
iv_history: snapshot_at, symbol, expiry, dte, spot, rf, div_yield,
            atm_iv, rr25, bf25, straddle_pct_mid, straddle_pct_ask,
            oi_put_call, vol_put_call, n_liquid_contracts, source, schema_version
```

- 至少 250 个交易日后才计算 IV rank / percentile；此前报告写"样本不足，不做贵贱判断"。
- 快照缺失的日子不插值，留空并在健康检查里报告。
- 与已实现波动率（20/60 日）并列，给出 IV − RV 差。

## 报告模板

```
# <标的> 期权快照 <日期>
快照时刻 / 现货 / rf / 股息率 / 过滤后合约数（过滤前）
## 期限结构表（到期日 | DTE | ATM IV | RR25 | BF25 | 跨式% (mid/ask)）
## 流动性说明（被排除的合约数与原因）
## 可比对象（同快照内 SPY vs QQQ；有历史后加分位）
## 不能得出的结论（明确列出，如"无法判断贵贱"）
```

## 策略层提醒

- covered call / wheel 等卖方策略的回测必须用 bid 成交、ask 平仓，并考虑被指派与提前行权（美式）。
- 卖方策略的 Greeks 限额（Δ、Γ、Vega）在组合层设置，不在单策略里。
- 数据合规：不自动抓取 CBOE 延迟报价网页（项目决策）；来源仍是 yfinance 快照，后续 IBKR 延迟行情。
- 中间价、Delta、IV 全部由 `derivatives/options.py` 确定性计算，LLM 只做文字解释。
