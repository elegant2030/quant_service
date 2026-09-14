# 事件分类枚举（v1）

LLM 只能从下列 `event_type.subtype` 中选择。新增类型需先修改本文件与 `events/taxonomy.py`，并提升 `taxonomy_version`。

## corporate（公司事件）

| subtype | 说明 | 常见来源 |
|---|---|---|
| earnings_release | 财报发布（含业绩快报） | 8-K item 2.02 / EX-99.1；A 股定期报告、业绩快报 |
| earnings_preannounce | 业绩预告/预警 | A 股业绩预告；美股 pre-announcement |
| guidance_change | 指引上调/下调/撤回 | 8-K、电话会 |
| ma_announce | 并购/分拆/资产重组 | 8-K item 1.01/2.01；A 股重组公告 |
| buyback | 回购启动/完成 | 8-K；A 股回购公告 |
| insider_holding | 大股东/高管增减持、解禁 | Form 4、13D/G；A 股增减持公告、限售解禁 |
| capital_raise | 增发、可转债、配股 | S-1/S-3；A 股定增 |
| dividend | 分红政策变化 | 公告 |
| product_order | 新产品、大额订单、产能投产 | 新闻稿、公告 |
| capacity_capex | 扩产/缩产、资本开支变化 | 公告、电话会 |
| management_change | 董事长/CEO/CFO 变动 | 8-K item 5.02；A 股公告 |
| litigation_regulatory | 诉讼、处罚、问询函、立案 | 8-K item 8.01；交易所问询函、证监会立案 |
| trading_halt | 停牌/复牌/ST/退市风险 | 交易所公告 |
| index_inclusion | 指数纳入/剔除 | 指数公司公告 |
| other_corporate | 其余 | — |

## industry_policy（行业/政策事件）

| subtype | 说明 |
|---|---|
| industrial_policy | 产业政策（规划、目标、补贴） |
| market_access | 准入、牌照、配额 |
| tariff_trade | 关税、出口管制、制裁 |
| price_regulation | 指导价、限价、电价/药价调整 |
| standard_release | 行业标准、技术规范 |
| antitrust | 反垄断、反不正当竞争 |
| environmental_safety | 环保、安监、限产 |
| other_policy | 其余 |

附加字段：`policy_stage`（signal / draft / official / detail / funded）、`issuer_level`（state_council / ministry / provincial / exchange / foreign_gov）。

## macro（宏观事件）

| subtype | 说明 |
|---|---|
| central_bank | 利率、准备金、公开市场操作、前瞻指引 |
| fiscal | 财政刺激、专项债、税收 |
| data_release | CPI/PPI/PMI/就业/社融等超预期或低预期 |
| geopolitical | 地缘冲突、选举、制裁 |
| fx_liquidity | 汇率、跨境资金、北向资金异常 |
| other_macro | 其余 |

## supply_chain（供应链事件）

| subtype | 说明 |
|---|---|
| upstream_price | 上游原料/组件涨跌价、短缺 |
| customer_order_change | 大客户加单/砍单 |
| substitute_technology | 替代技术/路线切换 |
| accident_disruption | 事故、停产、物流中断 |
| capacity_shift | 产能转移、新进入者 |
| other_chain | 其余 |

## crypto（加密事件，暂列）

| subtype | 说明 |
|---|---|
| protocol_upgrade | 主网升级、硬分叉 |
| token_unlock | 解锁、增发 |
| listing_delisting | 交易所上/下架 |
| regulation | ETF 审批、监管执法、牌照 |
| exchange_incident | 交易所安全事件、暂停提币 |
| other_crypto | 其余 |

## 方向与量级口径

- `direction`：-2 明确重大利空 / -1 偏空 / 0 中性或不确定 / +1 偏多 / +2 明确重大利好。
- `magnitude`：以对未来 12 个月营收或利润的影响百分比估计，取区间；无法估计填 `none` 并在 `uncertainties` 说明。
- `scope`：single（单一公司）/ chain（沿产业链传导）/ sector（板块）/ market（全市场）。
