---
name: securities-futures-chaos
description: 基于《证券混沌操作法》(Bill Williams 鳄鱼线/AO/AC/分形)，通过新浪财经互联网行情接口联网读取 A股与国内期货日线。股票从成交量/成交额前50名候选，期货从资金流入/流出前5名候选，再用混沌指标寻找上涨与下跌趋势最强品种。适用于“今天/明天哪些股票、期货趋势最强”“从热门量价品种里找趋势最强”“用混沌操作法做每日趋势筛选/排序”等请求；不用于下单、交易执行或实时行情推送。
metadata:
  short-description: 新浪互联网行情 + 热门量价候选 + 混沌趋势扫描
---

# 证券期货 · 混沌趋势扫描（互联网数据源）

用**新浪财经公开行情接口**联网读取 **A股** 与 **国内期货** 的日K线。
每天先收窄“热门候选池”，再用《证券混沌操作法》(Trading Chaos, Bill Williams)
的趋势指标筛选：

- 股票候选：**成交量前 50 名**（也可切到成交额前 50 名）
- 期货候选：**资金流入前 5 名 + 资金流出前 5 名**
- 输出：A股上涨趋势最强、期货上涨趋势最强、期货下跌趋势最强

## 何时使用

- “今天/明天哪些股票、期货趋势最强？”
- “用混沌操作法筛一下最有趋势的标的 / 每日选股选品种”
- “跑一下每日趋势扫描 / 更新一下 Top3 榜单”
- “盘中随时看趋势状况 / 某品种现在是不是破位了”（用 `scan_dow_signal.py --intraday`）

**边界**：只通过互联网读取公开行情并计算、排序，不交易、不下单。
输出是研究参考，不是投资建议；下单前请人工复核。

**数据源（默认）**：新浪财经公开接口，无需本地文华财经安装、
无需本地行情文件、无需 API Token。

- A股代码/成交量/成交额快照：`Market_Center.getHQNodeData`
- A股日K：`CN_MarketDataService.getKLineData(scale=240)`
- 国内期货合约清单：`qihuohangqing.js` + `Market_Center.getHQFuturesData`
- 期货主力连续/具体合约日K：`InnerFuturesNewService.getDailyKLine`
- 期货合约乘数/保证金率：`finance.sina.com.cn/futures/quotes/<合约>.shtml`

## 工作原理

程序联网获取热门候选清单 → 下载候选标的最近约 300 根日K →
计算期货“持仓资金变动”并选出资金流入/流出前 5 名 →
对候选股用混沌指标打“趋势分” → 输出榜单。

期货资金流的口径按文华“资金流向”思路估算：
`(今结算价×今持仓量 − 昨结算价×昨持仓量) × 合约乘数 × 保证金率`，
乘数和保证金率从新浪品种资料页实时读取。

**每次扫描都从互联网取最新数据**，不依赖电脑里以前打开过哪些行情。

## 强化模式: 道氏结构 + 分形买卖点 + 资金惯性（scan_dow_signal.py）

把道氏趋势定义与混沌分形合并成**可直接执行的进出场规则**（与用户口径一致）：

- **趋势定义(道氏)**：上涨 = 低点不断上移；下跌 = 高点不断下降。
- **高低点 = 分形**：上分形=高点，下分形=低点；标准趋势图形 = 低点(上涨)/高点(下跌)一档档推进。
- **做多**：先见“低点第一次上抬”（可能是上涨起点）→ 价格**突破前一个高点(上分形)** = 买点1；
  上涨后回调产生**比前低更高的新低** = 加仓点；**止损=前一个低点**；不破前低就持有并背靠前低逐级加仓。
- **做空 = 做多镜像，主看高点**：高点第一次下降 → 价格**跌破前一个低点(下分形)** = 卖点1；
  下跌中反弹产生**比前高更低的新高** = 反弹加仓点；**止损=前一个高点**；不破前高就持有空头并背靠前高加仓。
- **破位 = 盘中瞬时击穿（实时判定，不依赖收盘价）**：多头盘中价格**瞬间下穿前低** = 趋势结束、平多；
  空头盘中价格**瞬间上穿前高** = 趋势结束、平空。程序用K线/盘中最低/最高价做瞬时探测。
- **资金惯性/资金面**：期货看资金流——资金流入多的品种延续性/惯性更好；以“资金流入榜+上涨结构”挑最强多头、
  “资金流出榜+下跌结构”挑最强空头（资金流=(今结算×今持仓−昨结算×昨持仓)×乘数×保证金率，亿元口径）。
  **A股用换手率作资金进出代理**：默认候选池=换手率前50（高换手=资金进出活跃），可用 `--stock-rank-field volume|amount|turnover` 切换。
- **盘中数据源结论（联网调研+实测）**：A股实时价/今日最高/最低用**腾讯 qt.gtimg.cn**批量叠加最稳最快（约3-5秒、可一次50只；
  新浪约5-8秒且易触发风控/偶发滞后），候选池排序仍用新浪快照（支持服务端按 turnoverratio 排序）；**期货实时保持新浪**——
  免费且国内商品/股指主力连续覆盖最全（东财免费期货为约15分钟延时行情，不适合盘中破位判定）。

用法（脚本在 scripts/scan_dow_signal.py）：

```
python <skill>/scripts/scan_dow_signal.py --out console            # 收盘后：最近完整交易日
python <skill>/scripts/scan_dow_signal.py --intraday               # 盘中任何时刻实时查看（保留未收盘bar）
python <skill>/scripts/scan_dow_signal.py --intraday --no-futures  # 盘中只看A股
python <skill>/scripts/scan_dow_signal.py --no-stocks --top 5      # 只看期货
python <skill>/scripts/scan_dow_signal.py --stock-rank-field volume  # A股池改按成交量/成交额排序
python <skill>/scripts/scan_dow_signal.py --out markdown > dow_signal.md
```

输出分三块：A股做多候选（上涨结构+换手率）、期货做多（资金流入+上涨结构）、期货做空（资金流出+下跌结构镜像）。
每条给出：道氏结构（低点抬高xN / 高点降低xN / 破位）、当前信号（买点1/加仓点/突破/持有/破前低 等，
空头对称：卖点1/反弹加仓点/跌破/持有/破前高）、突破位/跌破位与触发日期、**止损位（前低/前高）**、强度分与资金流。

**盘中破位纪律**：信号若显示“破前低(瞬时击穿->多头结束/平多)”或“破前高(瞬时上穿->空头结束/平空)”，
意味着盘中最值已击穿前低/前高，按规则应平掉对应头寸；随后需重新出现“更高低点+突破前高”（或镜像）才算新一轮进场。

## 标准流程

1. **检查网络与数据源**（首次/怀疑接口有问题时）：
   `python <skill>/scripts/inspect_data.py`
   它会检查 A股清单、A股日K、期货主力连续清单、期货日K是否可正常获取。
2. **运行扫描**（建议每日收盘后运行；盘中运行会自动剔除未收盘的当日K线）：
   `python <skill>/scripts/scan_trend.py --out console`
   - 只看期货：`python <skill>/scripts/scan_trend.py --no-stocks`
   - 扫全部当前期货合约而非主力连续：
     `python <skill>/scripts/scan_trend.py --no-stocks --futures-mode all`
   - 股票候选池改按成交额排序：
     `python <skill>/scripts/scan_trend.py --no-futures --stock-rank-field amount`
   - 期货只看资金流入榜，不并入资金流出榜：
     `python <skill>/scripts/scan_trend.py --no-stocks --futures-no-outflow`
   - 需要时仍可扩大股票候选池：`python <skill>/scripts/scan_trend.py --stocks-limit 200`
   - 指定基准日复盘：`python <skill>/scripts/scan_trend.py --date 2026-09-01`
   - 存成 Markdown：`python <skill>/scripts/scan_trend.py --out markdown > trend_top3.md`
3. **解读输出**：榜单含方向(多/空)、趋势分(多分/空分)、鳄鱼状态、
   AO/AC、分形突破摘要；期货榜还会显示估算资金流(亿元)。
   - A股榜默认给“做多候选”；`--show-stock-bear` 可额外显示下跌榜(仅参考)。
   - 期货榜拆成“上涨趋势最强”和“下跌趋势最强”两块。
   - 分形突破是其中最直观的技术确认：价格突破最近一根上分形=多头延续，
     跌破最近一根下分形=空头延续。
   - 趋势分是把书中定性规则量化的启发式打分，数值只用于同池排序，
     不是胜率或目标价。
4. **复核**：挑出的标的回到行情软件人工核对K线/鳄鱼线/AO/AC是否与输出一致，
   再自行决策。

## 扫描范围与性能

- A股默认取**成交量最高的前 50 只**（`stock_rank_field: amount` 可改为按成交额）。
- 期货默认取各品种**主力连续**，按上面口径先算每个品种的资金变动，
  再取**资金流入前 5 名和资金流出前 5 名**作候选池；
  期货乘数与保证金率来自新浪品种页，首次扫描需多下载约数十个资料页。
- `--futures-no-outflow` 可让期货只保留资金流入榜。
- `--futures-mode all` 会改为扫描当前挂牌合约，资金流估算不做重点推荐。
- 单个接口暂时失败时程序会自动重试并跳过失败标的，最终提示失败数量。

## 可配置项

默认配置在 `assets/config.example.json`（A股数量、最低成交额/成交量/持仓量、
Top N、并发数、超时、日期等）。把示例复制为 `config.json` 后改参数，
用 `--config config.json` 传入。

- `stock_rank_field`：`volume`=按成交量排名；`amount`=按成交额排名。
- `stock_limit`：股票候选池数量，默认 50。
- `stock_markets` / `future_markets`：是否包含北交所、各期货交易所。
- `futures_pool_limit`：资金流入/流出各取前 N 名，默认 5。
- `futures_include_outflow`：`false` 时只保留资金流入榜。
- `max_workers`：并发下载线程数，网络不稳时降低，例如 `4`。
- `timeout`：单个HTTP请求超时秒数。
- `min_amount_yi`：A股最近成交额下限（亿元）。
- `min_vol_lots` / `min_oi_lots`：期货最近完整日成交量/持仓量下限（手）。
- `futures_mode`：`main`(默认) 或 `all`。
- `date`：指定历史复盘日。

## 文件

- `scripts/net_data.py`      新浪公开行情读取库（仅标准库，含A股/期货清单与日K）
- `scripts/scan_trend.py`    趋势扫描 CLI（联网取数 + 混沌指标 + 排序 + 报告）
- `scripts/scan_dow_signal.py`  强化扫描 CLI（道氏结构+分形买卖点+资金惯性，支持 `--intraday` 盘中实时）
- `scripts/inspect_data.py`  互联网数据源健康检查 CLI
- `references/chaos-method.md` 混沌指标公式、趋势分构成与局限
- `references/internet-sources.md` 接口地址、数据字段、切换/排错说明
- `assets/config.example.json` 示例配置

## 运行环境

脚本仅依赖 Python 3 标准库（无 pandas/numpy/akshare 要求），
但**必须能联网访问新浪财经行情域名**。如果通过代理上网，
请先确认代理对 `*.sina.com.cn` 可用。

## 风险与合规提示

- 结果来自互联网公开历史数据的机械排序，**不构成投资建议**；
  市场有风险，决策前请自行核实。
- 若运行时刻处于交易时段，程序会自动剔除未收盘的当日K线并提示基准日。
- 新浪公开接口可能限流或变更；脚本带重试，但无法保证永久可用。
## WH6 加权资金榜联动（可选，精确“加权”口径，需要本机装有文华WH6）

默认期货资金榜用“主力连续”近似计算。若本机装有**文华财经 WH6**（已登录、行情连接正常），
可让 skill 直接读 WH6 的**“品种加权排名”页**拿“加权”资金流入/流出榜做候选池，再叠加新浪连续K线做趋势/买卖点分析。
步骤（一次自动完成，可盘中随时调用）：

1. 在 WH6 打开“品种加权排名”页（键盘按 Esc 回行情列表后进入，或从行情功能进入），保持窗口在前台；
2. 运行（会在 scripts 目录生成截图/词表/候选池/报告）：
   ```
   powershell -ExecutionPolicy Bypass -File <skill>/scripts/wh6_snapshot.ps1 -Png wh6_rank.png -Words wh6_words.txt
   python <skill>/scripts/wh6_ocr_pool.py wh6_words.txt wh6_pool.txt     # OCR右侧加权资金流入/流出榜 -> 候选池
   python <skill>/scripts/wh6_pool_scan.py --pool-file wh6_pool.txt --out markdown
   ```
   或跳过 OCR，直接给榜（榜上前5按顺序填）：
   ```
   python <skill>/scripts/wh6_pool_scan.py --inflow "原油,BR橡胶,橡胶,20号胶,沪铜" --outflow "燃料油,乙二醇,豆粕,pvc,豆油" --out markdown
   ```
3. 输出：做多候选=WH6加权资金流入榜∩上涨结构（突破前高/回调加仓+止损前低）；做空候选=流出榜∩下跌结构（镜像）。

说明：WH6 加权榜单是“候选池权威”，脚本负责对其逐品种做道氏/分形/止损分析；趋势K线仍取新浪连续
（免费、历史全）。WH6 本身无程序化导出接口，故用“截图+Windows OCR”读取榜单文字（只读操作）。
OCR 偶发会把个别字读错（如“焦煤”读成“隹煤”），已在解析器内置纠错表；若某品种漏读，重截一次或手动补名即可。
资金流(亿)列为本脚本按持仓变动估算，仅作同池排序参考。

