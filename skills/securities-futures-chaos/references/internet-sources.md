# 互联网行情数据源：新浪财经公开接口

> 当前版本已经不再依赖文华财经本地 `Data/` 目录。所有行情都从新浪财经公开接口读取。

## 1. 接口列表

| 用途 | 地址/说明 |
|---|---|
| A股清单 | `Market_Center.getHQNodeData`，参数 `node=hs_a`；默认按 `volume`(成交量) 降序，可改按 `amount`(成交额) 降序 |
| A股日K | `CN_MarketDataService.getKLineData`，参数 `symbol=sh600519&scale=240&ma=no&datalen=300` |
| 期货品种节点 | `qihuohangqing.js`，包含 czce/dce/shfe/cffex/gfex 的行情品种节点 |
| 期货合约清单 | `Market_Center.getHQFuturesData`，参数 `node=<品种>&base=futures` |
| 期货主力/合约日K | `InnerFuturesNewService.getDailyKLine`，参数 `symbol=RB0` |
| 期货交易单位/保证金 | `finance.sina.com.cn/futures/quotes/RB0.shtml`，用于资金流估算 |

## 2. A股

- 新浪 `hs_a` 节点返回全部沪深京A股；默认按最近成交量降序读取前 50 只，
  配置 `stock_rank_field=amount` 时按成交额排序。
- 日K返回 `day/open/high/low/close/volume`，程序取最近 300 根。
- 成交量/成交额来自行情快照，若在盘中运行会带有当日未收盘数据。

## 3. 国内期货

- `qihuohangqing.js` 是 JS 风格对象，不是标准 JSON；`net_data.py`
  只按国内 5 个市场分组提取其中的品种节点代码，不改动文件。
- 每个品种节点调 `getHQFuturesData` 获取该品种当前合约；
  主力连续筛选 `symbol` 形如 `RB0`、`IF0`、`AU0`。
- 新浪把上海能源中心品种（`sc`、`nr`、`lu`、`bc`、`ec` 等）也放在 SHFE
  对应的节点内，但每行有真实 `exchange` 字段，程序会保留 `ine` 标记。
- 日K返回 `d/o/h/l/c/v/p/s`：日期、开高低收、成交量、持仓量、动态结算价。
- 期货候选池的资金流按
  `(今结算价×今持仓量 − 昨结算价×昨持仓量) × 合约乘数 × 保证金率`
  估算；合约乘数和保证金率从新浪对应品种资料页实时解析。
  这是公开互联网数据下的近似口径，可能和新浪/文华软件展示略有差异。

## 4. 完整交易日判断

新浪日K不附带日内时间。程序按北京时间判断：

- 交易日 15:00 前运行时，若最后一根K线日期等于当天，则去掉该未收盘K线。
- 15:00 后或非交易日运行，默认最后一行就是最近完整交易日。
- 指定 `--date YYYY-MM-DD` 时，只使用该日期或之前的数据，用于复盘。

## 5. 排错

- **“请求失败”**：检查网络，或调低 `max_workers`、调大 `timeout`。
- **股票数量少**：检查代理能否访问 `*.sina.com.cn`；
  `inspect_data.py` 可确认 A股清单是否正常。
- **期货品种缺失**：新浪品种节点本身在持续更新；可再次运行
  `inspect_data.py` 查看当前期货主力连续数量。
- **接口被限流**：脚本已有重试；若连续失败，等待几分钟再跑，
  不要同时开多个扫描进程。
- **不要用旧的本地参数**：`--search-root`、`--stocks-root`、
  `--futures-root` 已不再有效，脚本会提示并忽略。
