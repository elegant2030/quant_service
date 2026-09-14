# 开源量化框架、交易策略与 Agent Skill 深度研究

## 执行摘要

本地个人研究场景不需要被单一“大而全”框架绑定。对 Quant Workbench 最合适的方案是保留现有 Parquet 数据湖、SQLite 控制平面和自主回测约束，在上面组合五类能力：

1. **快速策略实验：VectorBT Community**——适合 440 只股票、多参数、多周期的批量筛选。
2. **严谨中低频复核：PyBroker + 现有事件回测器**——PyBroker 擅长轮动、walk-forward、ML 和 bootstrap；现有引擎继续负责 A 股规则和项目自己的行为契约。
3. **因子与组合层：Qlib + Alphalens-reloaded + skfolio**——分别负责因子/ML 流程、因子诊断、组合优化与交叉验证。
4. **期权层：Optopsy + QuantLib，LEAN 作为第二验证引擎**——Optopsy 快速研究期权组合，QuantLib 校验定价，LEAN 验证更完整的订单、保证金和期权事件流程。
5. **Agent 层：优先审计后引入 ML4T Skills，再创建项目自己的 Skill**——让 Agent 自动检查未来函数、幸存者偏差、point-in-time、成本、walk-forward 和过拟合，而不是让 LLM 自己算收益或下单。

A 股模拟/执行层以后采用 **vn.py**，加密中低频采用 **Freqtrade**。NautilusTrader 很强，但现阶段引入成本偏高；Hummingbot 偏高频做市；FinRL 偏强化学习研究，都不应成为第一批依赖。

这不是“换掉 Quant Workbench”的建议。最优结构是让现有项目继续成为数据与审计中枢，第三方框架通过适配器读取同一份 canonical Parquet，并把结果写入独立实验目录。

## 1. 研究范围与判断标准

评估日期为 2026-09-13，目标环境是：

- 本地个人研究，不对外销售软件或提供 SaaS；
- Python 为主，macOS 当前运行，未来可能迁移 Ubuntu；
- 美股、A 股、期权、加密与永续；
- 以日线至小时级中低频为主，不追求微秒级高频；
- 需要 ChatGPT/Agent 辅助策略分析、财报和事件解读；
- 现有项目已经使用 Parquet、DuckDB、SQLite、交易所日历和 LaunchAgent。

评估标准按重要性排序：

1. 防止未来函数、幸存者偏差和数据泄漏的能力；
2. 与中低频横截面、组合、期权研究的匹配度；
3. 能否使用自有 Parquet 和自定义数据源；
4. 交易费用、滑点、订单时点和市场规则的表达能力；
5. walk-forward、样本外、因子诊断和风险分析能力；
6. 本地运行、维护活跃度、文档和测试；
7. Agent/LLM 集成便利度；
8. 许可证。由于项目不商用，Commons Clause、非商业限制和 copyleft 不作为淘汰条件，但仍记录。

## 2. 最终推荐组合

| 层级 | 首选 | 次选/复核 | 在本项目中的定位 |
|---|---|---|---|
| 数据与控制平面 | 现有 Quant Workbench | OpenBB 仅作可选数据适配 | 保持 Parquet/SQLite/来源追踪不变 |
| 快速向量化研究 | VectorBT Community | bt | 参数扫描、信号初筛、可视化 |
| 中低频事件回测 | PyBroker | 现有引擎、Zipline-reloaded | 轮动、ML、walk-forward、bootstrap |
| 因子/ML | Qlib | vn.py Alpha、FinRL 研究沙箱 | Alpha158、模型训练、滚动研究 |
| 因子诊断 | Alphalens-reloaded | Qlib 自带分析 | IC、分层收益、换手、衰减 |
| 组合优化 | skfolio | Riskfolio-Lib、PyPortfolioOpt | CV、风险预算、CVaR、约束和压力测试 |
| 美股期权研究 | Optopsy | LEAN、NautilusTrader | 多腿组合、DTE/Delta、止盈止损、滑点 |
| 衍生品定价 | QuantLib | 项目现有 Black–Scholes | 独立定价和 Greeks 校验 |
| A 股回测参考 | RQAlpha | vn.py Alpha | A 股/期货规则与事件驱动参考 |
| A 股模拟/执行 | vn.py | 券商官方接口 | 独立 sidecar，不能直接让 LLM 下单 |
| 加密中低频 | Freqtrade | Jesse | 下载、回测、dry-run、交易所接入 |
| 多资产高性能引擎 | NautilusTrader | LEAN | 等系统复杂度上升后再评估 |
| Agent 研究纪律 | ML4T Skills | PyBroker Skills、Trading Skills | 防偏差、验证、成本、研究门禁 |
| Agent 工程与风控 | Algo-Trading-Skills 精选 | 自建 Skill | 幂等、kill switch、风险与执行检查 |
| 财报/权益研究模板 | 自建 OpenAI workflow | FinRobot/financial-services 参考 | 只采用流程，不直接信任模型结论 |

## 3. 核心框架比较

### 3.1 LEAN：美股期权和多资产的独立验证引擎

LEAN 是事件驱动的多资产回测和实盘引擎，Python/C# 均可使用，核心代码采用 Apache-2.0。其期权模型包含合约 universe、Greeks/IV、组合订单、行权、手续费、滑点、保证金和可替换的 reality model；官方仓库也提供铁鹰等策略示例。[^1][^2][^3]

适合本项目的地方：

- 美股期权能力明显强于普通 Python 股票回测库；
- 回测和 live 使用接近的事件模型，可作为未来 IBKR paper 的验证路径；
- fee、fill、slippage、margin 等均有扩展点；
- 能把同一策略在第二引擎重跑，发现自研回测器的实现偏差。

限制：

- 核心是 .NET，Python 是策略层接口，集成和调试成本高于纯 Python；
- 免费开源的是引擎，不等于期权历史数据免费；官方 US 期权数据与便捷 CLI 流程可能需要组织套餐或数据购买；
- A 股并非默认强项；自有数据需要转换为 LEAN 格式或实现 data feed。

判断：**值得作为美股期权和多资产的第二验证引擎，不应替换现有数据湖。**

### 3.2 Qlib：横截面因子和机器学习研究主力

Microsoft Qlib 是面向量化投资的 AI/ML 平台，覆盖数据处理、模型训练、回测、风险模型、组合优化和在线滚动，采用 MIT 许可。其模块松耦合，可以只使用数据处理、Alpha158、workflow 或模型层。[^4][^5]

适合本项目的地方：

- 与 220+220 股票的横截面研究高度匹配；
- 内置大量监督学习、时序和强化学习研究范式；
- 比自己重复编写特征缓存、训练记录和模型滚动更成熟；
- A 股是其重要使用场景，vn.py Alpha 也直接借鉴了 Qlib 的 Alpha158。

限制：

- 它不是完整的美股期权回测器；
- 自带数据格式和 workflow 有学习成本；官方示例数据的可用性可能变化；
- 模型越复杂，越需要严格控制 point-in-time、标签重叠、数据泄漏和多重测试。

判断：**作为因子/ML 研究插件接入，不接管 canonical 数据。**先做 `Parquet -> Qlib Dataset` 适配器，再用相同截面日期对齐现有策略结果。

### 3.3 VectorBT Community：最快的策略筛选层

VectorBT 使用 pandas/NumPy，并通过 Numba 和可选 Rust 内核加速，可以把大量资产、参数和周期打包成数组并行研究；官方定位就是大规模参数扫描和交互式量化分析。当前社区版采用 Apache-2.0 + Commons Clause，个人本地使用可接受。[^6][^7]

适合本项目的地方：

- 440 只股票的动量周期、再平衡周期、行业约束和止损参数扫描；
- 快速画热力图、收益分布、回撤和参数稳定区间；
- 对现有 DataFrame/Parquet 适配简单。

关键风险：

- 向量化代码很容易无意中用到同一根 K 线的收盘信号和收盘成交；
- 复杂订单路径、A 股 T+1、涨跌停、部分成交、期权行权并非其最自然的强项；
- 参数跑得越快，越容易放大数据挖掘和多重测试问题。

判断：**首批接入，但定位必须是“初筛器”，结果要由事件驱动引擎复核。**

### 3.4 PyBroker：最贴近当前中低频与 Agent 工作流

PyBroker 提供 Numba 加速回测、多标的执行、排名和轮动、walk-forward、ML 模型训练、缓存和 bootstrap 指标；数据源包括 Alpaca、Yahoo Finance、AKShare 和自定义 provider。项目还直接提供 Agent Skills，例如轮动 skill 明确要求下一根 K 线成交、防 lookahead、检查订单和输出结构化结果。[^8][^9]

适合本项目的地方：

- 当前 20/60/120 日动量和行业轮动可以直接映射；
- walk-forward 和模型定期再训练比现有简单回测器完整；
- 原生 skills 对 Agent 编写可靠策略很有帮助；
- 自定义数据源可以读取项目自己的 Parquet。

限制：

- Apache-2.0 + Commons Clause，未来若改为对外销售服务需重新审查；
- 官方说明不模拟杠杆和各券商保证金，不能直接承担期权或融资回测；
- A 股涨跌停、T+1、整手和停牌仍需项目适配层或现有引擎复核。

判断：**比从零扩充自研回测器更适合近期中低频研究；建议与现有引擎做双跑。**

### 3.5 vn.py：A 股、国内期货与期权执行生态

vn.py 4.x 是 MIT 许可的事件驱动平台，具备 A 股、国内期货、ETF 期权、IB 等 gateway，以及 CTA、组合策略、价差、OptionMaster、风险管理、数据录制和模拟账户模块。其 Alpha 模块覆盖特征、模型、横截面/时序策略和研究流程。[^10][^11]

适合本项目的地方：

- 国内市场 gateway 与实际交易规则生态最完整；
- OptionMaster 能做波动率曲面、组合 Greeks、Delta 对冲；
- paper account、risk manager 和 algo trading 可用于未来模拟验证；
- 对中文资料和国内市场支持好。

限制：

- 它偏交易平台，不适合直接取代现有研究数据湖；
- 多个 gateway 依赖券商环境、账号、系统和行情权限；
- GUI、插件和运行状态较多，整体运维复杂度会明显上升。

判断：**在进入 A 股模拟盘时，以独立服务方式接入；目前先研究接口和事件模型。**

### 3.6 RQAlpha：A 股日频回测的实用参考

RQAlpha 是可扩展的 Python 回测/交易框架，支持股票、期货、日线和分钟线，具有 bundle、撮合和报告体系。其当前许可证允许个人非商业研究按 Apache-2.0 条件使用，但商业用途需要米筐授权。[^12][^13]

优点是 A 股语义成熟、入门快、可以用来交叉检查手续费、订单和组合行为。缺点是部分数据和扩展 API 与 RQData 绑定，期权与跨市场能力不如 LEAN/vn.py。

判断：**本地非商业条件下可用，适合作为 A 股基准回测器或代码参考。**

### 3.7 NautilusTrader：未来的高性能多资产候选

NautilusTrader 是 Rust 原生、Python 可用的确定性事件驱动引擎，使用 LGPL-3.0，统一 backtest、sandbox 和 live 组件，支持 Parquet catalog、纳秒时间、多 venue、多资产和一等期权模型；官方文档已覆盖传统与加密期权链回测。[^14][^15][^16]

优点是架构现代、性能强、回测与实时路径一致。缺点是概念多、接入成本高，A 股 gateway 生态弱于 vn.py。对当前以日线/周线为主的项目，它属于“能力过剩”。

判断：**暂缓接入。若未来统一加密期权、期货、订单簿和低延迟执行，再把它与 LEAN 二选一。**

### 3.8 Zipline-reloaded、Backtesting.py、bt

- **Zipline-reloaded** 延续 Quantopian 的事件驱动和 pipeline 风格，采用 Apache-2.0，适合传统股票日频研究；但数据 bundle 和资产模型的适配成本较高，期权和 A 股并非核心优势。[^17]
- **Backtesting.py** API 极简、适合单标的原型，采用 AGPL-3.0；对 440 只股票横截面、A 股规则和期权不足。[^18]
- **bt** 采用 MIT，强调可组合的 Algo 栈和资产配置树，适合资产配置策略；不应承担复杂订单或期权模拟。[^19]

判断：这些工具可以作为教学或局部实验库，但当前没有必要同时引入。

## 4. 期权工具专项

### 4.1 Optopsy

Optopsy 是针对期权策略的 Python 研究与回测库，采用 AGPL-3.0。当前版本提供 38 种内置策略，包括单腿、跨式、宽跨、价差、蝶式、铁鹰、领口、备兑、日历和对角；同时支持 Delta 选腿、DTE、止盈止损、持仓上限、佣金、四类滑点思路、组合模拟和风险指标。[^20][^21]

这是最适合现有期权快照结构的短期候选，但有一个决定性前提：**必须先积累或购买历史期权链**。当前只有一次 SPY/QQQ 快照，任何历史期权策略结果都不成立。

建议接法：

1. 将项目 canonical option-chain Parquet 映射为 Optopsy schema；
2. 首先验证 covered call、protective put、cash-secured put 和有限风险 vertical spread；
3. 使用 bid/ask 保守滑点，不使用裸 mid；
4. 增加 assignment、early exercise、股息和 pin risk 的项目级补充检查；
5. 再用 LEAN 对同一交易序列复核。

### 4.2 QuantLib

QuantLib 是 BSD 风格许可的成熟金融工程库，覆盖收益率曲线、波动率结构、随机过程和大量衍生品定价模型。它不是交易回测器，但很适合作为项目当前 Black–Scholes/Greeks 的独立数值验证器。[^22]

建议优先用 QuantLib 增加以下测试：

- 欧式期权价格、Delta/Gamma/Vega/Theta 对照；
- 股息率和利率曲线敏感性；
- 美式期权树模型与提前行权；
- 波动率曲面插值和情景冲击。

### 4.3 optopsy-mcp

optopsy-mcp 是 Rust 编写、自托管的 MCP 期权/股票回测服务，支持自然语言策略、walk-forward、置换检验、FDR、Monte Carlo、组合优化和本地 Parquet，技术方向与本项目非常契合。其仓库也明确说明仍在活跃开发，minor version 可能破坏兼容。[^23]

截至本次检索，仓库首页没有显示明确许可证文件。个人本地试验可以先阅读源码并隔离运行，但在许可证、工具权限和结果正确性审计前，**不要让它访问券商凭证，不要连接下单接口，也不要让 MCP 修改 canonical 数据。**

## 5. 因子、组合和风险工具

### 5.1 Alphalens-reloaded

Alphalens 用于因子收益、信息系数、分位数组合、换手和分组分析；维护中的 reloaded 版本采用 Apache-2.0。[^24] 它非常适合把现有 20/60/120 日动量从“看回测收益”升级为：

- 按行业中性化后的 IC/Rank IC；
- 1/5/20/60 日 forward return；
- 分位数组合单调性；
- 因子换手和衰减；
- 牛熊/波动 regime 稳定性。

### 5.2 skfolio

skfolio 采用 BSD-3-Clause，兼容 scikit-learn API，支持组合优化、因子模型、风险管理、交叉验证、交易成本和压力测试。官方文档包括 Combinatorial Purged CV、CVaR、风险预算、Black–Litterman、tracking error 和 out-of-sample 组合评估。[^25][^26]

它是本项目组合层的首选，因为其 fit/predict/cross-validation 思维比“对全历史一次求最优权重”更安全。建议先实现：等权、逆波动、风险预算、最大 10% 单股权重、行业上限和 turnover penalty 的样本外比较。

### 5.3 Riskfolio-Lib 与 PyPortfolioOpt

Riskfolio-Lib 采用 BSD-3-Clause，模型和风险度量非常丰富，适合后续做 CVaR、drawdown risk、risk parity、因子风险贡献和复杂约束。PyPortfolioOpt 采用 MIT，更轻量，覆盖均值方差、协方差收缩、Black–Litterman 和 HRP。[^27][^28]

判断：先上 skfolio；只有需要更复杂风险度量时再加入 Riskfolio-Lib。避免同一阶段并行维护三套组合优化 API。

### 5.4 QuantStats

QuantStats 采用 Apache-2.0，能快速生成收益和风险报告；其 2026 年仍有版本发布。[^29] 它适合补充现有报告，但指标定义必须与项目口径对齐，尤其是年化天数、无风险利率、回撤、benchmark 和缺失日期处理。

## 6. 加密与永续框架

### 6.1 Freqtrade：中低频首选

Freqtrade 是 GPL-3.0 的 Python 加密交易机器人，包含交易所数据下载、回测、费用、参数优化、lookahead/recursive analysis、dry-run、Web UI、Telegram 和 FreqAI。官方强调应先 dry-run，并说明动态交易对列表会损害历史复现性。[^30][^31][^32]

判断：**最符合本项目中低频加密需求。**作为独立服务使用，策略信号和回测结果导回 Quant Workbench，不共享交易密钥。

### 6.2 Hummingbot：只在做市/跨所时考虑

Hummingbot 采用 Apache-2.0，连接大量中心化和去中心化交易场所，重点是高频做市、套利和机器人部署。[^33] 当前项目主线是日线/中低频，因此没有必要优先引入。

### 6.3 Jesse：Agent 友好但仍是加密专用

Jesse 提供简洁策略语法、回测、优化、paper/live、通知和内置 MCP，可让 Agent 管理数据、回测、参数、显著性检验和 Monte Carlo。[^34] 若更看重自然语言交互，它可作为 Freqtrade 的替代试验；若更看重社区、交易所覆盖和成熟 dry-run，优先 Freqtrade。

## 7. AI/ML 与财报研究框架

### 7.1 FinRL

FinRL 是 MIT 许可的强化学习研究框架，原仓库当前定位偏教育、benchmark 和研究原型，并引导生产方向到 FinRL-X/FinRL-Trading。[^35][^36]

强化学习不是现阶段优先项：金融环境非平稳、奖励函数容易泄漏交易成本和尾部风险，策略结果也难解释。建议只把 FinRL 当对照实验，必须通过相同 walk-forward、成本、purging 和样本外门禁，不得因为训练收益高就进入 paper。

### 7.2 OpenBB

OpenBB Open Data Platform 采用 AGPL-3.0，提供多数据商统一 Python/API 接口，并面向量化环境、研究界面和 AI Agent 暴露数据。实际数据权限仍由各 provider 决定，部分数据源需要密钥或付费。[^37][^38]

判断：可以作为“数据连接器参考或可选 API 网关”，但不应取代现有来源追踪和 Parquet 数据湖。当前免费 yfinance/BaoStock 路径已经跑通，立即引入 OpenBB 的收益有限。

### 7.3 FinRobot、FinGPT 与金融服务 Skill 模板

FinRobot 采用 Apache-2.0，展示了多 Agent 财务分析、预测、估值、风险与报告生成流程，但示例依赖 Financial Modeling Prep 等商业 API。FinGPT 提供金融情绪与指令数据/模型。[^39][^40]

Anthropic 的 financial-services 仓库也公开了 earnings、DCF、comps、thesis、catalyst、sector 等大量 Skill/Agent 模板，采用 Apache-2.0；其中很多模板与运行时和付费数据连接器绑定，但工作流结构仍值得借鉴。[^41]

判断：**复用分析结构，不直接复用结论。**项目应继续使用 OpenAI 结构化输出，并把数值计算、PIT 数据、引用和材料哈希留在确定性管线中。

## 8. Agent Skills 专项推荐

### 8.1 第一优先：ML4T Skills

ML4T Skills 提供 61 个纯 Markdown Skill，采用 Apache-2.0，不包含可执行脚本、hook、MCP 或网络调用；主题覆盖 lookahead、PIT、幸存者偏差、成本、因子验证、purged CV、walk-forward、deflated Sharpe、组合暴露、压力测试、Agent 治理和生产门禁。[^42][^43]

建议首批只审计并安装以下 15 个到项目级 `.agents/skills/`：

1. `point-in-time`
2. `lookahead-bias`
3. `survivorship-bias`
4. `transaction-costs`
5. `define-universe`
6. `validate-data`
7. `evaluate-factor`
8. `walk-forward-cv`
9. `purging-embargo`
10. `deflated-sharpe`
11. `cost-model`
12. `sensitivity-analysis`
13. `exposure-analysis`
14. `stress-test`
15. `strategy-workflow`

不建议一次安装全部 61 个。原因不是磁盘空间，而是 skill 描述会增加 Agent 的路由噪声；先选择与当前缺口直接相关的能力。

### 8.2 第二优先：PyBroker 原生 Skills

PyBroker 仓库的 Skills 与其真实 API 同步，轮动 skill 已明确包含 next-bar、lookahead、自检、warmup、订单检查、结构化结果和 bootstrap 规则。[^9] 如果采用 PyBroker 做双跑，直接使用其官方 skills 比让 Agent 凭记忆写 API 更可靠。

### 8.3 精选使用：Algo-Trading-Skills

Algo-Trading-Skills 是 2026 年出现的大型社区仓库，采用 Apache-2.0，声称包含 501 个 skill、参考实现和独立测试，覆盖风控、执行、broker、合规和基础设施。官方自己也建议只安装一个 domain，不要加载全部库。[^44][^45]

建议只审计这类工程 skill：

- order placement idempotency；
- kill switch / drawdown circuit breaker；
- broker reconnection state；
- market data gap detection；
- order reconciliation；
- options Greeks aggregation；
- audit trail 与 secrets handling。

不要直接启用任何能访问账户、撤单、转账或 live order 的脚本。社区项目的测试通过只证明实现满足自己的测试，不证明金融语义、监管解释或券商行为一定正确。

### 8.4 可选：Trading Skills

Trading Skills 采用 MIT，规模较小，偏决策流程和风险纪律，包含 pre-trade check、earnings prep、thesis validation、execution plan、position sizing、portfolio risk 和 post-trade review。[^46] 它适合作为人机研究清单，但不应把其中主观 go/no-go 输出直接映射为订单。

### 8.5 应该自建的三个项目 Skill

第三方 Skill 不知道本项目的 schema、目录和市场规则，因此最终仍应自建：

#### `quant-data-contract`

- 校验 provenance 字段、schema version、时区、复权和交易日；
- 强制坏数据隔离；
- 禁止备源静默覆盖；
- 输出可复现的 manifest 和 hash。

#### `quant-backtest-review`

- 检查 signal timestamp 与 fill timestamp；
- 检查 PIT universe、退市样本、费用、滑点、换手和容量；
- A 股强制 T+1、整手、涨跌停和停牌；
- 期权强制 bid/ask、乘数、到期、行权、股息和保证金；
- 输出“通过/不通过/证据不足”，不输出买卖指令。

#### `filing-impact-analysis`

- 输入只能是已保存且带 `accepted_at/published_at` 的材料；
- 分开事实、管理层口径、推断和不确定性；
- 利好/利空必须标注时间尺度、基准预期和置信度；
- 所有数字来自程序化表格，不允许 LLM 自行运算；
- 保存材料 hash、引用、模型和提示词版本。

OpenAI 官方的 Skill 结构由 `SKILL.md`、可选 `scripts/`、`references/` 和 `assets/` 组成；触发主要依赖 frontmatter 的 name/description。[^47] 项目自建 skill 应尽量以 Markdown 规则为主，数值验证放在受测试的 Python 脚本中。

## 9. 交易策略方向与对应工具

| 策略族 | 当前优先级 | 推荐工具 | 进入研究的最低门槛 |
|---|---:|---|---|
| 横截面动量/趋势 | P0 | VectorBT → PyBroker/现有引擎 | 行业中性、成本、换手、walk-forward |
| 价值+质量+动量多因子 | P0 | Qlib + Alphalens + skfolio | PIT 财务、IC、分层单调性、暴露约束 |
| 低波/逆波动/风险预算 | P0 | skfolio | 样本外、turnover penalty、集中度 |
| 财报漂移/事件策略 | P1 | EDGAR/公告管线 + 自建 Skill | accepted_at、预期差、事件窗口 |
| 保护性 Put/备兑/领口 | P1 | Optopsy + QuantLib + LEAN | 至少 1–2 年历史链、bid/ask、股息 |
| 波动率期限结构/偏斜 | P1 | Optopsy/LEAN | 历史 IV surface、同口径快照 |
| 配对/统计套利 | P2 | statsmodels/Optopsy-MCP 隔离试验 | 协整稳定性、借券、成本、容量 |
| 加密趋势/轮动 | P2 | Freqtrade | 静态历史 universe、费用、资金费率 |
| 做市/跨所套利 | P3 | Hummingbot/Nautilus | 订单簿、延迟、库存与交易所风险 |
| 强化学习 | P3 | FinRL/Qlib | purged walk-forward、固定基准、可解释性 |

近期最值得做的不是寻找更多技术指标，而是把当前动量策略升级为：

1. 60/120/252 日多周期动量；
2. 1 个月反转过滤；
3. 行业中性排名；
4. 波动率目标和单股/行业上限；
5. 月度调仓与 hold band 降换手；
6. walk-forward 参数冻结；
7. 与等权、逆波和指数基准比较；
8. 使用 deflated Sharpe 或多重测试修正。

在没有 PIT 基本面前，不应发布“价值/质量因子历史表现”；在没有历史期权链前，不应发布“铁鹰/卖 Put 历史胜率”；在没有资金费率和交易所状态历史前，不应发布“永续套利年化”。

## 10. 不建议现在采用的项目

| 项目 | 原因 |
|---|---|
| 原始 Backtrader | 生态历史悠久，但主仓库长期缺乏现代维护；复杂横截面和期权不如当前候选 |
| PyAlgoTrade | 官方仓库已归档并明确 deprecated[^48] |
| 同时接入 Zipline、Backtesting.py、bt | 能力重叠，增加三套数据适配和指标口径 |
| FinRL 直接产生交易信号 | 数据量与验证门槛不足，容易过拟合且难解释 |
| Hummingbot 作为当前主框架 | 偏高频做市，与中低频目标错位 |
| OpenBB 替换现有数据湖 | provider 许可和字段仍需治理，不能解决 PIT 与来源审计 |
| optopsy-mcp 直接连券商 | 项目仍年轻、API 可能变化、许可证未明确、Agent 权限面过大 |
| 任何“现成盈利策略仓库” | 通常缺少 PIT universe、退市样本、真实成本和样本外证据 |

## 11. 与 Quant Workbench 的目标架构

```text
                         Agent / ChatGPT
                              │
          audited Skills + typed tools + evidence packages
                              │
                              ▼
┌──────────────── Quant Workbench 控制与审计平面 ────────────────┐
│ provenance │ PIT cutoff │ job state │ manifests │ approvals │
└──────────────────────────┬─────────────────────────────────────┘
                           │
                 canonical Parquet / DuckDB
                           │
       ┌───────────┬───────┼────────┬───────────┬──────────┐
       ▼           ▼       ▼        ▼           ▼          ▼
   VectorBT    PyBroker   Qlib   Alphalens   skfolio   Optopsy
   快速初筛    事件复核   ML因子   因子诊断    组合优化   期权研究
       │           │                              │          │
       └───────────┴──────── 实验结果/订单日志 ────┴──────────┘
                           │
                  独立二次验证 sidecars
              LEAN / QuantLib / RQAlpha / vn.py
                           │
                 人工批准后才允许 paper
```

第三方框架不得：

- 改写 canonical Parquet；
- 自行下载后绕过来源登记；
- 把模型输出直接变成订单；
- 把 API key 写进配置仓库；
- 使用当前成分股回看历史却不披露幸存者偏差；
- 将相同样本既用于参数选择又声称为样本外。

## 12. 分阶段落地路线

### 第 1 阶段：一周内完成的低风险增强

1. 建立独立 `research` optional dependencies，不污染后台 ingest 运行环境。
2. 接入 VectorBT，对当前 US/CN 动量策略做参数稳定性热力图。
3. 接入 Alphalens-reloaded，输出 IC、分位收益、换手和衰减。
4. 接入 skfolio，比较等权、逆波、风险预算和行业约束组合。
5. 接入 QuantStats 作为报告补充，并对每个指标做口径单测。
6. 审计并项目级安装 15 个 ML4T Skills。

验收标准：所有工具读取同一份 Parquet；相同基准的日收益和交易日数量一致；任何差异都有可解释对账表。

### 第 2 阶段：两到三周

1. 做 PyBroker Parquet provider；
2. 把 120 日月频动量改造成 ranking + hold band；
3. 做 5 段 expanding/rolling walk-forward；
4. 加入费用、滑点、A 股约束和 bootstrap 区间；
5. 用现有事件引擎与 PyBroker 双跑并逐笔对账；
6. 为 Qlib 建立 feature/label adapter，但不复制事实数据。

### 第 3 阶段：历史期权数据达到门槛后

1. Optopsy schema adapter；
2. 从 covered call、protective put、collar、vertical spread 开始；
3. QuantLib 定价/Greeks 对照；
4. LEAN sidecar 回放相同策略；
5. 加入 assignment、exercise、dividend、pin risk 与压力测试。

### 第 4 阶段：模拟执行

1. A 股使用 vn.py paper account 或券商模拟环境；
2. 加密使用 Freqtrade dry-run；
3. 美股/期权使用 LEAN + IBKR/Alpaca paper；
4. Agent 只能读取状态、生成研究建议和请求批准，不能持有下单凭证；
5. kill switch、最大亏损、最大名义敞口和订单幂等必须在 Agent 之外。

## 13. 试用框架的统一验收清单

任何框架在进入主项目前必须通过：

- [ ] 支持从指定 Parquet/DataFrame 读取，不隐式换数据源；
- [ ] 时间戳、时区、交易日和复权口径明确；
- [ ] 信号只使用当时已知数据，成交发生在可执行的下一时点；
- [ ] 费用、滑点和 turnover 可配置；
- [ ] 结果包含逐笔订单、持仓和每日权益，可与现有引擎对账；
- [ ] 固定随机种子、参数和数据 manifest 后可复现；
- [ ] A 股策略正确处理 T+1、整手、涨跌停和停牌；
- [ ] 期权策略处理 bid/ask、乘数、到期、行权与多腿原子性假设；
- [ ] 有 walk-forward 或外部样本，不只报告全样本最优参数；
- [ ] 许可证、依赖和 Python 版本记录在 ADR；
- [ ] 第三方 Skill 已逐文件审计，不读取凭证、不执行网络/下单动作；
- [ ] 引入失败时可完全移除，不破坏现有后台 ingest。

## 14. 结论

当前最优决策不是采用一个新框架重写项目，而是：

- **现在接入 VectorBT、Alphalens-reloaded、skfolio 和 QuantStats，快速提升研究效率与诊断能力；**
- **用 PyBroker 建立第二套中低频 walk-forward 回测，验证现有策略；**
- **用 Qlib 承接下一阶段因子和 ML，但必须先完成 PIT 基本面；**
- **等待历史期权链积累后接 Optopsy，QuantLib 做数值校验，LEAN 做事件级复核；**
- **A 股执行留给 vn.py，加密 dry-run 留给 Freqtrade；**
- **Skill 首选 ML4T 的偏差与验证类，随后把项目自己的数据契约、回测审查和财报影响分析固化成 Skill。**

如果只能选一个框架马上试：选 **PyBroker**，因为它与当前 440 股票、中低频轮动、walk-forward 和 Agent Skill 最贴合。  
如果只能选一个工具库马上接：选 **skfolio**，因为组合约束和样本外优化是当前策略从“信号”走向“组合”的最大缺口。  
如果只能选一组 Skill：选 **ML4T Skills 中与 PIT、偏差、成本和 walk-forward 有关的 15 个精选项**。

## Sources

[^1]: QuantConnect. “[LEAN Algorithmic Trading Engine](https://github.com/QuantConnect/Lean).” GitHub, accessed 2026-09-13.
[^2]: QuantConnect. “[LEAN Apache 2.0 License](https://github.com/QuantConnect/Lean/blob/master/LICENSE).” GitHub, accessed 2026-09-13.
[^3]: QuantConnect. “[Equity Option Universes](https://www.quantconnect.com/docs/v2/writing-algorithms/universes/equity-options)” and “[LEAN Backtest CLI](https://www.quantconnect.com/docs/v2/lean-cli/api-reference/lean-backtest).” Accessed 2026-09-13.
[^4]: Microsoft. “[Qlib Introduction](https://github.com/microsoft/qlib/blob/main/docs/introduction/introduction.rst).” GitHub, accessed 2026-09-13.
[^5]: Microsoft. “[Qlib MIT License](https://github.com/microsoft/qlib/blob/main/LICENSE).” GitHub, accessed 2026-09-13.
[^6]: VectorBT. “[VectorBT Documentation](https://vectorbt.dev/).” Accessed 2026-09-13.
[^7]: Oleg Polakow. “[VectorBT README and License](https://github.com/polakowo/vectorbt).” GitHub, accessed 2026-09-13.
[^8]: Edward West. “[PyBroker](https://github.com/edtechre/pybroker).” GitHub, accessed 2026-09-13.
[^9]: Edward West. “[PyBroker Rotational Trading Skill](https://github.com/edtechre/pybroker/blob/master/skills/pybroker-rotational-trading/SKILL.md).” GitHub, accessed 2026-09-13.
[^10]: VeighNa. “[vn.py README](https://github.com/vnpy/vnpy/blob/master/README_ENG.md).” GitHub, accessed 2026-09-13.
[^11]: VeighNa. “[vn.py MIT License](https://github.com/vnpy/vnpy/blob/master/LICENSE).” GitHub, accessed 2026-09-13.
[^12]: Ricequant. “[RQAlpha](https://github.com/ricequant/rqalpha).” GitHub, accessed 2026-09-13.
[^13]: Ricequant. “[RQAlpha License](https://github.com/ricequant/rqalpha/blob/master/LICENSE).” GitHub, accessed 2026-09-13.
[^14]: Nautech Systems. “[NautilusTrader](https://github.com/nautechsystems/nautilus_trader).” GitHub, accessed 2026-09-13.
[^15]: Nautech Systems. “[NautilusTrader Concepts Overview](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/concepts/overview.md).” GitHub, accessed 2026-09-13.
[^16]: Nautech Systems. “[NautilusTrader Options](https://github.com/nautechsystems/nautilus_trader/blob/develop/docs/concepts/options.md).” GitHub, accessed 2026-09-13.
[^17]: Stefan Jansen. “[Zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded).” GitHub, accessed 2026-09-13.
[^18]: Kernc. “[Backtesting.py](https://github.com/kernc/backtesting.py).” GitHub, accessed 2026-09-13.
[^19]: Philippe Morissette. “[bt](https://github.com/pmorissette/bt).” GitHub, accessed 2026-09-13.
[^20]: Goldspan Labs. “[Optopsy](https://github.com/goldspanlabs/optopsy).” GitHub, accessed 2026-09-13.
[^21]: Goldspan Labs. “[Optopsy Examples](https://github.com/goldspanlabs/optopsy/blob/main/docs/examples.md).” GitHub, accessed 2026-09-13.
[^22]: QuantLib. “[QuantLib License and Project](https://github.com/lballabio/QuantLib/blob/master/LICENSE.TXT).” GitHub, accessed 2026-09-13.
[^23]: Goldspan Labs. “[optopsy-mcp](https://github.com/goldspanlabs/optopsy-mcp).” GitHub, accessed 2026-09-13.
[^24]: cloudQuant. “[Alphalens](https://github.com/cloudQuant/alphalens).” GitHub, accessed 2026-09-13.
[^25]: skfolio. “[Portfolio Optimization in Python](https://skfolio.org/).” Accessed 2026-09-13.
[^26]: skfolio. “[Optimization User Guide](https://github.com/skfolio/skfolio/blob/main/docs/user_guide/optimization.rst).” GitHub, accessed 2026-09-13.
[^27]: Dany Cajas. “[Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib).” GitHub, accessed 2026-09-13.
[^28]: PyPortfolio. “[PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt).” GitHub, accessed 2026-09-13.
[^29]: Ran Aroussi. “[QuantStats](https://github.com/ranaroussi/quantstats).” GitHub, accessed 2026-09-13.
[^30]: Freqtrade. “[Freqtrade](https://github.com/freqtrade/freqtrade).” GitHub, accessed 2026-09-13.
[^31]: Freqtrade. “[Backtesting Documentation](https://github.com/freqtrade/freqtrade/blob/develop/docs/backtesting.md).” GitHub, accessed 2026-09-13.
[^32]: Freqtrade. “[FreqAI Documentation](https://github.com/freqtrade/freqtrade/blob/develop/docs/freqai.md).” GitHub, accessed 2026-09-13.
[^33]: Hummingbot Foundation. “[Hummingbot](https://github.com/hummingbot/hummingbot).” GitHub, accessed 2026-09-13.
[^34]: Jesse AI. “[Jesse](https://github.com/jesse-ai/jesse).” GitHub, accessed 2026-09-13.
[^35]: AI4Finance Foundation. “[FinRL](https://github.com/AI4Finance-Foundation/FinRL).” GitHub, accessed 2026-09-13.
[^36]: AI4Finance Foundation. “[FinRL MIT License](https://github.com/AI4Finance-Foundation/FinRL/blob/master/LICENSE).” GitHub, accessed 2026-09-13.
[^37]: OpenBB. “[Open Data Platform](https://github.com/OpenBB-finance/OpenBB).” GitHub, accessed 2026-09-13.
[^38]: OpenBB. “[OpenBB Platform Providers](https://github.com/OpenBB-finance/OpenBB/blob/develop/openbb_platform/README.md).” GitHub, accessed 2026-09-13.
[^39]: AI4Finance Foundation. “[FinRobot](https://github.com/AI4Finance-Foundation/FinRobot).” GitHub, accessed 2026-09-13.
[^40]: AI4Finance Foundation. “[FinGPT](https://github.com/AI4Finance-Foundation/FinGPT).” GitHub, accessed 2026-09-13.
[^41]: Anthropic. “[Financial Services Skills and Agents](https://github.com/anthropics/financial-services).” GitHub, accessed 2026-09-13.
[^42]: ML4T. “[ML4T Agent Skills](https://github.com/ml4t/skills).” GitHub, accessed 2026-09-13.
[^43]: ML4T. “[ML4T Skill Catalog](https://github.com/ml4t/skills#skill-catalog).” GitHub, accessed 2026-09-13.
[^44]: Himanshu Jangir. “[Algo-Trading-Skills](https://github.com/HimanshuJ16/Algo-Trading-Skills).” GitHub, accessed 2026-09-13.
[^45]: Himanshu Jangir. “[Algo-Trading-Skills Verification and Installation](https://github.com/HimanshuJ16/Algo-Trading-Skills#quick-start).” GitHub, accessed 2026-09-13.
[^46]: Marian. “[Trading Skills](https://github.com/marian2js/trading-skills).” GitHub, accessed 2026-09-13.
[^47]: OpenAI. “[Skill Creator: Anatomy of a Skill](https://github.com/openai/skills/blob/main/skills/.system/skill-creator/SKILL.md).” GitHub, accessed 2026-09-13.
[^48]: Gabriel E. C. “[PyAlgoTrade](https://github.com/gbeced/pyalgotrade).” GitHub archive, accessed 2026-09-13.
