# 期权研究全景：策略证据、波动率建模、工具与数据、Agent 与 Skills

日期：2026-09-21。性质：只读网络调研的汇总，未改动任何代码。
适用范围：Quant Workbench 是研究平台，不下单。本文不含任何买卖建议、目标价或仓位。
方法：四路并行检索（策略证据、波动率与风控、开源工具与数据、AI agent 与 MCP 与 skills），优先论文和交易所原始文件，再由我交叉核对去重。

**可信度标注**：未加标注的条目已打开原文或仓库核对；`[仅摘要]` 表示只读到摘要或搜索片段；`[未核实]` 表示引用前必须复核。学术界横截面期权研究几乎全部使用 OptionMetrics，个人无法获得，因此"论文结论"不等于"你能复现"。

---

## 0. 一页结论

1. **对本平台最匹配的方向不是交易期权，而是把期权隐含信息当作选股与状态信号。** 平台主线是基本面乘消息面，期权隐含偏斜、看涨看跌 IV 差、期权成交量比，可以直接进入现有漏斗，只需要每只股票的每日快照。
2. **免费源没有期权历史，必须从现在开始自己存。** 这一点和 T2 的逻辑完全一样。意外发现：DoltHub 上有一份仍在更新的免费期权库，IV 历史从 2019 年起，覆盖 2,331 个标的，可以用来引导 IV 历史，但要单独标来源、不与自采快照混合。
3. **卖波动率是风险溢价，不是阿尔法。** PUT 指数 1986 到 2018 年夏普 0.65，但 2013 年后 BXM 每年落后标普 450 个基点以上。备兑收益里只有约三分之一来自卖波动率，其余是股票贝塔。
4. **三类方向因数据门槛对个人不可行**：0DTE 与 GEX、分散交易、横截面 delta 对冲加机器学习。
5. **建模上简单方法足够**：逐到期日样条加三项无套利检查；用日线高开低收的 Yang–Zhang 估计量加 HAR 模型预测已实现波动率。粗糙波动率、神经网络曲面、联合校准都不值得做。
6. **SPY 和 QQQ 期权是美式的。** 现在用 Black–Scholes 且股息率为 0，对实值看跌和除息前的实值看涨有系统性误差。只用虚值合约算 IV，实值部分用 QuantLib 的美式定价器。
7. **通用 AI 交易框架没有真正的期权能力。** TradingAgents、FinRobot、ai-hedge-fund 都不含期权链、Greeks 或波动率工具。可下单的 MCP 一律不装。期权专项 skill 没有现成可用的，需要自建。
8. **与 9 月 13 日生态调研的冲突**：那份报告推荐的 Optopsy 是 AGPL 许可，和项目"不引入 GPL"的原则冲突，应改为只借鉴其数据格式和参数设计。

---

## 1. 策略族的证据

### 1.1 波动率风险溢价收割：卖看跌、备兑、宽跨

- **机制**：风险溢价。隐含波动率长期高于已实现波动率，因为投资者为崩盘保险付费，卖方承担负偏的股票与波动率风险。
- **证据**：
  - Bondarenko 2019，1986-06 到 2018：PUT 指数年化 9.54%、波动 9.95%、夏普 0.65；买看跌保护的 PPUT 夏普只有 0.33。最大回撤 PUT -32.7%，标普 -50.9%。指数层面，不含真实成本。[来源](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)
  - Israelov 与 Nielsen《Covered Calls Uncovered》：备兑等于股票贝塔加卖波动率加一个没有补偿的反转择时暴露，卖波动率只贡献约三分之一收益。[来源](https://www.aqr.com/Insights/Research/Journal-Article/Covered-Calls-Uncovered)
  - Israelov《PutWrite vs BuyWrite》：PUT 比 BXM 每年多 1.1% 是指数编制的产物，到期日上午约四小时的股票暴露差异造成，不是阿尔法。[来源](https://images.aqr.com/-/media/AQR/Documents/Insights/White-Papers/AQR-PutWrite-vs-BuyWritevF.pdf)
  - 2013 年后衰减：标普道琼斯研究称 BXM 近年每年落后标普 450 个基点以上。[来源](https://www.spglobal.com/spdji/en/documents/research/research-seeking-income-cash-flow-distribution-analysis-of-sp-500-buy-write-strategies.pdf) `[具体窗口未核实]`
- **拥挤**：期权收益类基金规模从 2019 年约 200 亿美元涨到 1,200 亿以上。Cboe 认为没有压低波动率（[来源](https://www.cboe.com/insights/posts/are-option-income-funds-suppressing-volatility/)），但 Cboe 是利益相关方；Park 与 Kurucak 用持仓数据发现机械式卖看涨会压低次月隐含波动率和期权收益（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5705222)）`[仅摘要]`。两者结论相反。
- **失效场景**：2008、2018-02、2020-03、2025-04。V 型反弹时备兑封顶上行。
- **数据与可行性**：日频期权链含买卖价、无风险利率、股息即可。**日频可行。** Cboe 指数历史免费，可做基准。

### 1.2 期权隐含信息用于选股（最匹配本平台）

| 信号 | 文献 | 主要发现 |
|---|---|---|
| IV 偏斜 | Xing、Zhang、Zhao，JFQA 2010（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1107464)） | 偏斜最陡的股票每年跑输 10.9%，能预测盈利冲击 |
| 看涨减看跌 IV 差 | Cremers 与 Weinbaum，JFQA 2010（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968237)） | 每周约 50 个基点，原文已指出样本内在衰减 `[衰减细节未核实]` |
| 期权对股票成交量比 O/S | Johnson 与 So，JFE 2012（[来源](https://www.travislakejohnson.com/pdfs/Johnson%20So%20OS%202012%20(JFE).pdf)） | 低 O/S 每周跑赢高 O/S 0.34% |
| IV 变动 | An、Ang、Bali、Cakici，JF 2014（[来源](https://www.nber.org/papers/w19590)） | 看涨 IV 变动对看跌 IV 变动，月约 1% |
| 控制公司特征后幸存的少数信号 | Neuhierl 等，Management Science（[来源](https://pubsonline.informs.org/doi/10.1287/mnsc.2024.04720)） | 幸存者与错误定价、尾部收益、融券成本相关 `[仅摘要]` |

- **衰减**：因子发表后平均衰减约 42%（[来源](https://arxiv.org/pdf/2212.10317)）。这些效应集中在难融券的小盘股和周度周期，在 220 只大盘股里要预期弱得多。
- **"异常期权活动"**：没有找到严谨证据，看到的都是数据商营销。
- **可行性**：**可行。** 每日为股票池每只股票快照平值看涨和看跌 IV、25Δ 看跌 IV、期权与股票成交量。

### 1.3 财报波动率

- Gao、Xing、Zhang（JFQA 2018）：公告前三天买入平值跨式持有到公告日，收益 +3.34%，但集中在小盘、高波动、交易成本高的股票。[来源](https://www.ruf.rice.edu/~yxing/straddle_201305_03.pdf)
- de Silva、Smith、So《Losing is Optional》：散户在财报前买期权平均亏 5% 到 9%，高预期波动事件亏 10% 到 14%，原因是买贵、价差宽、离场慢。[来源](https://www.timdesilva.me/files/papers/losing_optional.pdf)
- **净判断**：多头跨式的毛收益在流动性差的股票上，成本会吃掉；流动性好的热门股上卖方平均赢但暴露于跳空。没有找到"卖财报跨式扣成本后系统性赚钱"的证据。
- **隐含财报波幅的算法**：Dubinsky 与 Johannes 把公告当作跳跃，从期限结构估计其方差（[来源](https://academic.oup.com/rfs/article-abstract/32/2/646/5001193)）；实务版本是事件方差等于两个到期日总方差之差减去无事件的远期方差（[来源](https://moontowermeta.com/how-an-option-trader-extracts-earnings-from-a-vol-term-structure/)）。
- **可行性**：**可行**，且与平台的 EDGAR 和财报流水线天然衔接。合适的产出是每只股票"隐含波幅对实际波幅"的历史账本，属于描述性结论。

### 1.4 期限结构与 VIX 期货

- Vasquez（JFQA 2017）：IV 期限结构斜率正向预测跨式收益。[来源](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/equity-volatility-term-structures-and-the-cross-section-of-option-returns/F0A40E99FD2458367DD9A56A89783D38)
- Eraker 与 Wu（JFE 2017）：恒定一个月期 VIX 期货 2006 到 2013 年每年亏约 30%。[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2340070)
- Cheng《The VIX Premium》：事前溢价在风险上升时反而下降，可作择时信号，代码公开。[代码](https://github.com/inghawcheng/vixpremium)
- 2018-02 教训：XIV 一天亏 96%，根源是杠杆和反向产品在集中市场里的收盘再平衡反馈（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3819342)）。展期收益不是免费利差，不能用历史波动率给卖波动率仓位定规模。
- **可行性**：VIX 与 VIX 期货期限结构日频免费，**可行**，适合作状态变量。

### 1.5 偏斜与风险反转

- Kozhan、Neuberger、Schneider（RFS 2013）：偏斜溢价解释标普 IV 曲线斜率的 40% 以上，但对冲掉方差暴露后偏斜溢价不显著。**偏斜交易大体是换了形式的波动率溢价交易。** [来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1571700)
- **可行性**：25Δ 偏斜可从 SPY 和 QQQ 链每日跟踪，作为**描述性状态变量可行**；构造可交易的偏斜头寸需要密集的行权价覆盖。

### 1.6 尾部对冲

- AQR：买看跌是可靠但昂贵的对冲，趋势跟踪长期更便宜。[来源](https://www.aqr.com/Insights/Research/White-Papers/Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies)
- Israelov 与 Nielsen《Still Not Cheap》：决定看跌成本的是波动率溢价，不是 IV 水平。[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2579232)
- 反方是 Spitznagel 与 Taleb：应按整个组合的几何收益评价对冲。Universa 2020 年一季度的 +4,144% 是自报数字，指对冲部分，未经独立审计。
- **可行性**：**可行**，日频期权链加现有价格数据算出的趋势信号即可。

### 1.7 横截面期权收益与 delta 对冲（论文很强，个人难复现）

- Goyal 与 Saretto（JFE 2009）：按已实现减隐含波动率排序，多空跨式毛收益每月约 22.7%，保证金和成本部分大幅削减。[来源](https://personal.utdallas.edu/~axs125732/CrossOptionsJFE.pdf) `[样本期未核实]`
- Cao 与 Han（JFE 2013）：特质波动率越高，delta 对冲收益越低，约每月 1.4%。[来源](https://www-2.rotman.utoronto.ca/facbios/file/Han_JFE_published.pdf)
- Bali 等（RFS 2023）：1996 到 2020 年 1,200 万观测、约 270 个特征，非线性机器学习扣成本后仍有效。[来源](https://academic.oup.com/rfs/article-abstract/36/9/3548/7056660)
- Heston 等《Option Momentum》（JF 2023）：跨式收益有 6 到 36 个月动量。[来源](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13279)
- **批评**：Duarte、Jones、Wang（JF 2024）发现微观结构噪声会让期权收益估计偏高，有时超过每天 50 个基点（[来源](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13365)）。Muravyev 与 Pearson 发现有效价差不到报价价差的 40%，但只对会择时执行的交易者成立（[来源](https://academic.oup.com/rfs/article-abstract/33/11/4973/5732665)）。
- **可行性**：**仅部分可行。** 可以从现在起积累前瞻面板，但无法复现论文。

### 1.8 不可行的三类

- **0DTE 与 GEX**：0DTE 在 2025 年占 SPX 成交量 59%（[来源](https://ir.cboe.com/news/news-details/2026/Cboe-Global-Markets-Reports-Trading-Volume-for-December-and-Full-Year-2025/default.aspx)）。散户在 0DTE 上合计每天亏约 18 到 24 万美元（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4404704)）。Dim、Eraker、Vilkov 发现 0DTE 的 gamma 不放大波动，反而有抑制作用（[来源](https://dx.doi.org/10.2139/ssrn.4692190)）。需要日内报价和区分客户与做市商的持仓；公开的 GEX 数字是先假设做市商站哪一边再算出来的。
- **分散交易**：Driessen、Maenhout、Vilkov（JF 2009）发现隐含相关性 46.7% 对已实现 28.7%（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2359380)）。交易需要 50 条以上个股 vega 腿。可以只跟踪 Cboe 隐含相关性指数作为状态变量。
- **Wheel 与现金担保看跌**：没有同行评审文献。机制上就是把 PUT 和 BXM 用到个股上，而个股的波动率溢价比指数更薄更噪。一个业余回测显示 SPY wheel 没跑赢买入持有，99% 以上收益来自 SPY 多头腿（[来源](https://spintwig.com/spy-wheel-45-dte-options-backtest/)，非同行评审）。散户期权交易平均收益 -0.9%（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4682388)）。

### 1.9 流行管理规则的证据状态

"50% 止盈、21 DTE 管理、2 倍止损"没有找到同行评审检验，只有数据商和博客回测。常见问题：中间价成交、2005 到 2020 年对卖方友好的样本、止损在跳空时无法成交。Zerodha 在 Nifty 上的复现发现止损触发频率高约三倍（[来源](https://inthemoneybyzerodha.substack.com/p/we-backtested-the-famous-45-dte-strategy)）。**应视为未经证实。**

---

## 2. 波动率建模与信息提取

### 2.1 曲面拟合

- 业界标准是 SVI 与 SSVI，闭式且保证无静态套利（Gatheral 与 Jacquier，[来源](https://arxiv.org/abs/1204.0646)）。
- **对 SPY 和 QQQ 的日频研究，逐到期日在总方差空间做平滑样条就够**，前提是三项检查通过：看涨价格对行权价凸；固定行权价下总方差随期限不减；看涨看跌平价反推的远期与现货加持有成本一致。
- 只有需要跨期限插值或外推翼部时才拟合 SSVI。

### 2.2 已实现波动率预测

- 基准是 HAR-RV。《HARd to Beat》（IJF 2025）覆盖 1,445 只股票：拟合得当的 HAR 在只用已实现波动率和 VIX 时胜过调参后的机器学习模型。[来源](https://arxiv.org/abs/2406.08041)
- 其他文献结论不一，机器学习的增益小而脆弱。评价必须用 QLIKE 损失并做多重检验校正。
- **只有日线高开低收时**：用 Yang–Zhang（与漂移无关、能处理开盘跳空，[来源](https://www.jstor.org/stable/10.1086/209650)）或 Garman–Klass 作为波动率代理，放进 1、5、22 日滞后的 HAR，再加 VIX 或平值 IV 作回归量。
- **路径依赖波动率**（Guyon 与 Lekeufack）：过去收益的两个幂律加权特征能解释指数隐含波动率约 90% 的方差和未来日度已实现波动率约 65%，只需要日线，很适合本平台。[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4174589)
- 已实现核需要日内数据，跳过。

### 2.3 粗糙波动率与联合校准

属于研究级。Abi Jaber 与 Li 发现 rough Bergomi 和 rough Heston 拟合不了 SPX 微笑的期限结构，两因子马尔可夫模型反而更好（[来源](https://arxiv.org/pdf/2401.03345)）。个人研究者能用的只有"把路径依赖特征当预测变量"，蒙特卡洛或神经网络校准不现实。

### 2.4 从期权价格提取信息

- **无模型隐含方差**：照 Cboe 的 VIX 数学方法实现。[方法文件](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_Volatility_Index_Mathematics_Methodology.pdf)
- **隐含偏度和峰度**：Bakshi、Kapadia、Madan 估计量，对行权价截断和报价质量敏感。
- **风险中性密度**：对拟合后的看涨曲线求二阶导，绝不对原始报价求。`[未核实]`
- **隐含股息与融券成本**：反解看涨看跌平价（[来源](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13129)）。美式期权用近平值配对或先去美式化。
- **波动率风险溢价预测力**：Bollerslev、Tauchen、Zhou 发现它预测市场收益，季度最强，但依赖无模型 IV 和高频已实现波动率（[来源](https://public.econ.duke.edu/~boller/Published_Papers/rfs_09.pdf)）。用日线区间估计时信号更弱，2020 年后的样本外更新没有找到 `[未核实]`。
- **IV 分位**：依赖回看窗口和市场状态。平台没有 IV 历史，攒满约一年快照前不输出。

### 2.5 美式行权

- SPY 和 QQQ 期权是美式。Black–Scholes 系统性低估美式期权，误差集中在正利率下的实值看跌和除息前的实值看涨。超过一半应当提前行权的看涨头寸没有被行权（[来源](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=972613)）。
- **做法**：只用虚值期权算 IV，那里提前行权溢价最小；实值期权和个股用带离散股息的二叉树或 BAW 定价器；标出剩余时间价值低于下次股息的空头看涨。
- SPX、XSP、NDX 为欧式现金结算、以及美国 1256 条款税务处理，这两点本次没有在原始来源核实。`[未核实]`

---

## 3. 风险管理与市场结构

### 3.1 风控

- **限额**：delta 用美元计；gamma 用每 1% 变动的美元计；vega 按期限分桶（0 到 7 天、8 到 30 天、31 到 90 天、90 天以上），跨期限直接相加会误导；theta 占净值比例。
- **压力网格**：现货 -20% 到 +10% 乘以随现货变动的波动率冲击，做完整重估，不用 Greeks 近似。
- **保证金**：组合保证金用 OCC 的 TIMS，在十个等距价格点重估，个股正负 15%，宽基指数 +6% 和 -8%（[来源](https://www.theocc.com/risk-management/customer-portfolio-margin)）。保证金是券商约束不是风险度量，危机时会被上调并触发强平。
- **卖波动率定规模**：用回测胜率做 Kelly 无效，因为亏损尾部没被采样到。应按压力网格的最坏格子不超过净值的某个比例，再加跳空场景。
- **执行**：压力下价差不对称扩大。报告盈亏时同时给中间价和自然价两个版本。

### 3.2 爆仓案例的共同点

2018-02 的 XIV 与 LJM、2018-11 的 OptionSellers 天然气裸卖看涨（亏损超过 1.5 亿美元）、2020-03 的安联 Structured Alpha（部分基金亏超 90%，且管理人篡改风险报告，[来源](https://www.sec.gov/newsroom/press-releases/2022-84)）、2024-08-05 盘前 VIX 到约 66（其中 85% 以上来自流动性差的虚值看跌报价变宽，[来源](https://www.bis.org/publ/bisbull95.htm)）。

共同点：用保证金而不是压力损失来衡量杠杆；对冲在压力下消失；按中间价估值。

### 3.3 2025 到 2026 年市场结构

- SPY 和 QQQ 自 2022-11 起周一到周五每天到期。七巨头、AVGO、IBIT 自 2026-01-26 起增加周一和周三到期（[来源](https://www.bloomberg.com/news/articles/2026-01-16/mag-7-stock-options-get-sec-nod-for-monday-wednesday-expiries)）。财报日当天纳斯达克可能跳过周三到期，会破坏简单的事件包夹算法。
- SPX 和 XSP 期权接近 24 乘 5 交易。Cboe 已获批对约 20 只个股期权延长交易时段。
- SPY 期权 16:15 ET 收盘，平台现有快照时点正确。
- 散户约占期权成交量 48%，三家批发商支付约 90% 的订单流回扣（Bryzgalova 等，JF 2023）。另有片段说 60%，无法对上 `[未核实]`。

---

## 4. 开源工具与数据源

### 4.1 定价、Greeks、曲面

| 项目 | 许可 | 结论 |
|---|---|---|
| [vollib / py_vollib](https://github.com/vollib/py_vollib) | MIT | **采用**，作为自有 IV 代码的单测基准 |
| [fast-vollib](https://github.com/raeidsaqur/fast-vollib) | MIT | **试用**，整条链向量化 IV；较新，锁版本并交叉校验 |
| [QuantLib](https://www.quantlib.org/license.shtml) | BSD-3 | **采用**，美式行权加离散股息，正好解决"期权股息率为 0"的已知问题 |
| [OIPD](https://github.com/tyrneh/options-implied-probability) | Apache-2.0 | **采用**，无套利微笑拟合加风险中性密度；喂自己的快照，不用它内置的 yfinance 连接器 |
| py_vollib_vectorized | MIT | 只借思路，2021 年后停更，导入时打猴子补丁 |
| FinancePy | **GPL-3.0** | 因许可避免 |
| JAX 和 torch 定价器、粗糙波动率校准工具 | 各异 | 日频用不上 |

### 4.2 回测框架

| 项目 | 许可 | 结论 |
|---|---|---|
| [lambdaclass options_backtester](https://github.com/lambdaclass/options_backtester) | MIT | **评估采用**。找到的唯一一个许可宽松、在维护、原生读 Parquet 的期权回测器。需要买卖价、成交量、持仓量、delta 列。等攒够一到两年期权链再接 |
| [optopsy](https://github.com/goldspanlabs/optopsy) | **AGPL-3.0** | **只借设计**（输入格式、按 delta 选腿、进出场 DTE 参数），不导入。**这修正了 9 月 13 日生态调研的推荐** |
| lumibot、optionlab | **GPL-3.0** | 因许可避免；lumibot 还面向实盘，违反红线 1 |
| LEAN | Apache-2.0 `[凭记忆]` | 太重，数据锁在其平台 |
| NautilusTrader、vectorbt、backtrader 插件 | 各异 | 不适合 |

### 4.3 数据源

| 来源 | 内容 | 成本 | 备注 |
|---|---|---|---|
| yfinance | 仅当前链，Yahoo 自算 IV 盘后不可靠，无 Greeks | 免费 | PIT 只能靠自己每日快照 |
| [DoltHub options](https://www.dolthub.com/repositories/post-no-preference/options) | IV 历史 2019-02 到今，2,331 个标的；期权链含买卖价和 Greeks，**无成交量和持仓量** | 免费 | **最佳免费历史引导源**。许可和上游来源未核实；早年每周约三个日期、只保留部分到期日和行权价 `[未核实]`。用 `dolt clone`，单独标 source，不与自采快照合并 |
| [OCC 批量报告](https://www.theocc.com/market-data/market-data-reports/other-market-data-info/batch-processing) | 每日成交量、持仓量、按账户类型成交量 | 免费 | 官方来源，可作 Yahoo 持仓量的独立对账源，符合红线 5 |
| Cboe 免费指数 CSV | VIX、VVIX、VIX9D、看跌看涨比；SKEW、PUT、BXM 在各指数页 `[今日未核实]` | 免费 | 不可再分发；不抓取延迟报价页 |
| [FRED](https://fred.stlouisfed.org/series/VIXCLS) | VIX 自 1990 年，国债利率和 SOFR 作利率输入 | 免费 | ALFRED 提供真正的数据版本，支持 PIT |
| [Theta Data](https://http-docs.thetadata.us/Articles/Getting-Started/Subscriptions.html) | 免费档：2023-06 起的日终报价和持仓量，延迟一天。付费约每月 40、80、160 美元 | 0 起 | 付费选项里性价比最高，需本地 Java 终端 |
| Massive（原 Polygon） | 免费档 2 年历史、每分钟 5 次 | 0 到约 199 美元 | 价格来自第三方摘要，需复核 |
| Tradier | 实时链带 ORATS 算的 Greeks 和 IV | 免费但需开券商账户 | 无历史链接口，仍需自己快照 |
| Alpaca | 期权历史只到 2024-02 | 免费 | 历史太短 |
| IBKR | 无已到期期权数据 | 账户加数据费 | 只能当快照源，放 P2 |
| ORATS、IVolatility | 日终数据自 2007 年 | 每月约 69 到 399 美元 | 质量最高，仅限内部使用 |
| OptionMetrics | 学术标准，自 1996 年 | 仅机构 | 个人无法获得 |

### 4.4 分析类

- VIX 复现：参考 [meixler/vix](https://github.com/meixler/vix) 自己重写约 150 行，用白皮书里的算例做单测，再和公布的 VIX 对照。
- GEX：现有开源仓库都抓取 Cboe 延迟报价，**违反项目合规边界**。公式本身简单，可在自己的快照上算，但结果必须标注"依赖做市商方向假设"。
- 预期波幅：平值跨式中间价乘 0.85，或波动率乘现货乘根号时间，不需要库。

---

## 5. AI Agent、MCP 与 Skills

### 5.1 多智能体框架

**结论：通用框架都没有真正的期权能力。**

| 项目 | 许可 | 实际情况 | 结论 |
|---|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | 分析师、辩论、交易员、风控，输出单只股票的买卖持有；无期权链或 Greeks `[仅摘要]` | 借设计：结构化输出、决策日志 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | Apache-2.0 `[凭记忆]` | 研报生成，无期权模块 | 借设计：数值由算子算，叙述交给 LLM |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) | MIT | 名人人格 agent，教育性概念验证 | 避免 |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | **AGPLv3** | 有统一的期权链字段标准 | 只借字段标准 |
| [RD-Agent](https://github.com/microsoft/RD-Agent) | MIT | 因子挖掘，绑定 Qlib，不涉及期权 | 维持"延后" |

没有找到成熟的开源"LLM 期权 agent"。

### 5.2 MCP 服务器

| 项目 | 性质 | 结论 |
|---|---|---|
| [mark-liu/ibkr-mcp](https://github.com/mark-liu/ibkr-mcp) | 社区，MIT，**只读**，在 API 层设 readonly，改代码也会被网关拒单 | **可试点**，与 P2 的 ib_async 只读路线一致；只有 13 个提交，接入前审代码 |
| [Massive 官方 MCP](https://github.com/massive-com/mcp_massive) | 官方，只读，需付费 key | **可试点**，先用它评估数据质量和费用 |
| [tastytrade-mcp](https://github.com/tastytrade/tastytrade-mcp) | 官方，MIT，84 个工具里 70 个只读 | **借设计**：只读环境变量直接拒绝全部写入工具；下单必须先试运行换取 60 秒一次性令牌。目前门控设计最好的参考 |
| [alpaca-mcp-server](https://github.com/alpacahq/alpaca-mcp-server) | 官方，**可下单**，含多腿期权下单 | 避免 |
| tasty-agent、TastyScanner、Bybit trading-mcp | 社区或官方，可下单 | 避免 |
| 各种 yfinance MCP、QuantConnect MCP | 只读或云端 | 避免：绕过自有 provider 就没有原始落地和校验；QuantConnect 有云端锁定 |

**安全要点**：
- 新闻和公告文本里的提示注入，加上可下单的 MCP，就是一条直接下单的路径。
- 只读必须在 API key 或券商网关层面强制，不能只靠提示词。
- MCP 直接喂数据会绕过红线 3 和红线 5。MCP 只适合探索性查询，进 canonical 的数据仍要走 provider。

### 5.3 现成 Skills

- [staskh/trading_skills](https://github.com/staskh/trading_skills)（MIT）：25 个期权向 skill，**计算全部放在脚本里由代码完成**，这是值得借的结构。其中有可下条件单的功能，要去掉。
- [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)（MIT，2.9k 星）：期权策略顾问、财报交易分析等，计算脚本化，偏主观交易风格。
- [anthropics/financial-services](https://github.com/anthropics/financial-services)：官方，投行和股票研究向，无期权内容，可借财报更新模板。
- **没有找到**成熟的"波动率曲面质检""Greeks 与风险限额审查""期权回测审计""财报波动率分析"skill，需要自建。

### 5.4 相关论文

- **知识泄漏**：[FinCAD](https://arxiv.org/abs/2605.24564) 发现抑制模型记忆后样本内收益最多降 67%，样本外几乎不变。这直接支持项目"只用知识截止日之后的样本做主指标"的设计。
- **财报电话会模型的负面结果**：[Same Company, Same Signal](https://arxiv.org/html/2412.18029) 发现模型很大程度学到的是公司身份而不是内容。
- **Deep hedging**：都是数值优化不是 LLM。重要反面证据：[深度对冲与 delta 对冲之差是否只是统计套利](https://arxiv.org/pdf/2407.14736)。
- **LLM 直接做期权定价或对冲**：没有检索到可信论文，对这类宣称应默认怀疑。
- 现有 agent 基准（InvestorBench、StockBench 等）都不覆盖期权。

### 5.5 安全设计模式

1. LLM 只调用确定性算子，输出里每个数字能回指到某次工具调用。
2. 结构化输出加证据引用，没有证据就输出"证据不足"。
3. 泄漏控制：主指标只用知识截止日之后的样本。
4. 前瞻测试账本：每次输出落库，事后用已实现波动率打分做校准表。
5. 人在环：想法、对抗审查、纸面记录、人工批准，不设"执行"状态。
6. 物理隔离下单能力：不装可下单 MCP；券商 key 只读；用权限拒绝列表屏蔽交易工具名。

---

## 6. 建议的方向排序（供你决定，未实施）

按"对个人研究者的可行性乘以与平台主线的契合度"排序：

1. **期权隐含选股信号**。每日为股票池快照平值看涨看跌 IV、25Δ 看跌 IV、期权与股票成交量。现在开始积累，因为没有免费历史。
2. **财报隐含波幅对实际波幅的账本**。与 EDGAR 和财报流水线衔接，产出是描述性的。
3. **指数波动率状态监测**。SPY 和 QQQ 的隐含减已实现、25Δ 偏斜、期限斜率、VIX 期货基差，以免费的 Cboe PUT、BXM 指数为基准。可兼作周期层的状态输入。
4. **卖看跌与领口策略的诚实成本复现**。按买卖价成交。需要至少 12 个月自采快照，或便宜的供应商历史。
5. **对冲成本比较**。滚动看跌或看跌价差对比趋势跟踪。理论基础好，可低频运行。

排除：0DTE 与 GEX、分散交易、横截面 delta 对冲机器学习。

### 6.1 建议实现的分析模块（按优先级）

| 序号 | 模块 | 输入 | 验证 |
|---|---|---|---|
| 1 | 报价清洗与流动性过滤 | 买卖价、成交量、持仓量 | 价差占比分布；无交叉或零买价；通过率稳定 |
| 2 | 每个到期日的隐含远期、股息、融券成本 | 近平值配对、利率曲线 | 隐含远期与现货加持有成本之差在报价价差内 |
| 3 | 考虑美式行权的 IV | 第 2 项、股息日程 | 重定价落在买卖价内；无股息虚值期权与 BS 一致 |
| 4 | 逐到期日微笑拟合与套利检查 | 第 3 项 | 蝶式函数非负；日历单调；残差小于半个价差 |
| 5 | 恒定期限平值 IV、25Δ 风险反转与蝶式、期限斜率，写入 IV 历史 | 第 4 项 | 30 天 SPY 序列与 VIX 高相关，水平差可解释 |
| 6 | 无模型隐含方差与隐含偏度 | 虚值期权带 | 在 SPX 或 SPY 带上能接近复现 VIX |
| 7 | 已实现波动率面板与 HAR 预测 | 日线高开低收 | 滚动样本外 QLIKE 对随机游走基线 |
| 8 | 波动率风险溢价 | 第 5 到 7 项 | 符号与量级和文献一致；对齐无前视 |
| 9 | 个股隐含财报波幅 | 期限结构、财报日历 | 隐含对实际波幅的校准图 |
| 10 | 假想结构的场景网格与保证金代理 | 第 4 项、头寸 | 小冲击下完整重估与 Greeks 一致 |

注意：第 1 项和 IV 历史与另一个 agent 正在做的期权数据采集重叠，需要先对齐分工。

### 6.2 建议新建的期权 Skills

全部与现有的 qw-options-snapshot-review 不重叠。

1. **qw-vol-surface-qa**：曲面入库前的无套利与数据质量检查。
2. **qw-options-backtest-audit**：按买卖价成交、快照与信号时点先后、提前行权与指派、合约调整、保证金、结算口径、合约选择前视、基准对照。
3. **qw-greeks-risk-limits-review**：Greeks 美元化、按期限分桶的 vega、场景网格、跳空、保证金压力、股息率与利率来源。
4. **qw-earnings-vol-analysis**：隐含波幅算法、公告时点到可交易时点的换算、IV 回落度量、排除公司身份泄漏、样本不足 200 不建模板。
5. **qw-short-vol-red-team**：2018-02、2020-03、2024-08 三条压力路径，负偏度下夏普失真，指派后资金占用，与股票贝塔的隐性叠加。
6. **qw-options-llm-guardrails**：数字到工具调用的溯源，禁止输出合约推荐、仓位、目标价，前瞻账本，MCP 白名单加只读验证，提示注入测试用例。

### 6.3 不值得做的事

校准粗糙波动率或联合 SPX 与 VIX 模型；神经网络曲面；没有日内数据时用 LSTM 或 Transformer 预测波动率；已实现核；用持仓量做"做市商 GEX"看板；对原始报价求风险中性密度；用每日一次快照做 0DTE 日内分析；没有 IV 历史就出 IV 分位；按中间价回测并用 Kelly 定规模的卖波动率策略。

---

## 7. 期权回测的常见坑

- **中间价成交**：应卖在买价、买在卖价。
- **陈旧或收盘报价**：期权收盘与标的收盘不同步，用固定快照时点。
- **微观结构噪声**：会让平均收益偏高，收益应从相对信号日滞后的报价计算。
- **美式提前行权与股息**：零股息的 BS 隐含波动率在除息日附近是错的；空头实值看涨有除息前被指派的风险。
- **钉住风险与结算**：SPX 上午按特殊开盘报价结算，SPY 下午实物交割。
- **保证金**：按权利金算的空头期权收益率没有意义。
- **期权链的幸存者偏差**：退市标的和随时间变化的挂牌（周度、0DTE）会从数据里消失，应快照整条链。
- **IV 曲面的前视**：供应商平滑过的曲面可能用了之后的信息；插值出的恒定期限点不是可交易合约。
- **重叠收益**：每日采样的月度持有会夸大 t 统计量。
- **用未来信息做筛选**：在出场而不是入场时应用持仓量或成交量过滤。
- **卖波动率的短样本**：任何不含 2008、2018、2020 或 2025-04 的样本都高估夏普。
- **多重检验**：跨行权价、期限、入场规则反复试验会夸大结果。

---

## 8. 本次未能核实的事项

- DoltHub 期权库的许可、上游来源、行权价与到期日的抽样方式。
- SPX、XSP、NDX 的欧式现金结算属性，上午与下午结算，1256 条款税务处理，交易所费率表。
- 日线数据上 GARCH 对 HAR 的权威比较；vega 按根号时间加权的惯例；成交模型里的系数取值。
- 波动率风险溢价预测力在 2020 年之后的样本外更新。
- 2025-04 关税事件的期权市场细节。
- "2026-07 的 0DTE 占比 66.2%"只见于搜索片段；散户占比 48% 与 60% 两个数字对不上。
- 多篇论文只读到摘要（Management Science、JFE 2025、Park 与 Kurucak 等）；若干 arXiv 编号凭记忆给出。
- Massive 的当前价格；Cboe 的 SKEW、PUT、BXM 是否仍提供 CSV 下载；LEAN 与 vectorbt 的许可；若干小型仓库的许可和最后提交日期。

引用任何一条用于决策前，请先打开原文复核。
