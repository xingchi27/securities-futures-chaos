# -*- coding: utf-8 -*-
"""scan_trend.py - 基于《证券混沌操作法》(Bill Williams) 的每日趋势扫描（联网版）。

从新浪财经公开互联网接口读取 A股 + 国内期货日K线，
股票候选池默认取成交量前50，期货候选池默认取资金流入/流出前5，
计算鳄鱼线/动量振荡器AO/加速度AC/分形 等混沌指标，
按趋势强度排序，输出趋势最强的前 N 只股票与期货品种。

仅做量化筛选与排序，不构成任何投资建议。
用法示例:
  python scan_trend.py --out console
  python scan_trend.py --no-stocks --top 5 --futures-mode main
  python scan_trend.py --stocks-limit 100 --no-futures --out markdown > report.md
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_data as net  # noqa: E402

DEFAULT_CONFIG = {
    "data_source": "internet",       # 当前版本只使用互联网行情源
    "stock_markets": ["SHSE", "SZSE", "BJSE"],
    "future_markets": ["CFFEX", "SHFE", "DCE", "CZCE", "INE", "GFEX"],
    "stock_rank_field": "volume",    # volume=成交量降序, amount=成交额降序
    "stock_limit": 50,               # 股票候选池：成交量(或成交额)前 N 只
    "min_bars": 120,
    "min_amount_yi": 0.5,            # 股票：快照最近成交额下限(亿元)
    "min_vol_lots": 1000,            # 期货：最近完整日成交量下限(手)
    "min_oi_lots": 800,              # 期货：最近完整日持仓量下限(手)
    "top": 3,
    "futures_mode": "main",          # main=每品种主力连续; all=全部当前合约
    "futures_pool_limit": 5,         # 期货资金流入/流出各取前 N 名作候选池
    "futures_include_outflow": True, # True=上涨看流入榜、下跌看流出榜
    "show_stock_bear": False,
    "max_workers": 8,                # 联网下载K线的并发线程数
    "timeout": 20,
    "date": None,                    # 基准日期 YYYY-MM-DD(留空=最近完整交易日)
}


# ------------------------------------------------------------------ 指标
def sma(vals, n):
    if len(vals) < n:
        return [None] * len(vals)
    out = [None] * len(vals)
    acc = sum(vals[:n])
    out[n - 1] = acc / n
    for i in range(n, len(vals)):
        acc += vals[i] - vals[i - n]
        out[i] = acc / n
    return out


def smma(vals, n):
    """Wilder 平滑(混沌操作法中鳄鱼线采用的 SMMA)。"""
    if len(vals) < n:
        return [None] * len(vals)
    out = [None] * len(vals)
    seed = sum(vals[:n]) / n
    out[n - 1] = seed
    for i in range(n, len(vals)):
        out[i] = (out[i - 1] * (n - 1) + vals[i]) / n
    return out


def fractals(high, low):
    """5根K线分形：up[i]/down[i] 仅在 i+2 根确认后为 True。"""
    n = len(high)
    up = [False] * n
    dn = [False] * n
    for i in range(2, n - 2):
        if high[i] > max(high[i - 2], high[i - 1], high[i + 1], high[i + 2]):
            up[i] = True
        if low[i] < min(low[i - 2], low[i - 1], low[i + 1], low[i + 2]):
            dn[i] = True
    return up, dn


class ChaosState:
    """单标的状态与评分(数值越大越强)。"""

    def __init__(self):
        self.net = 0.0
        self.bull = 0.0
        self.bear = 0.0
        self.dirn = "横盘"
        self.alligator = "缠绕"
        self.ao_txt = ""
        self.ac_txt = ""
        self.fx_txt = ""
        self.pct = 0.0
        self.detail = []


def _line_rising(seq, r, back=3):
    return seq[r] is not None and seq[r - back] is not None and seq[r] > seq[r - back]


def score_chaos(o, c, h, l, vol, r):
    """在基准索引 r(最后一根完整日线) 计算混沌趋势分。"""
    n = len(c)
    st = ChaosState()
    if r < 40 or r >= n:
        return st
    mp = [(h[i] + l[i]) / 2.0 for i in range(n)]

    s5 = sma(mp, 5)
    s34 = sma(mp, 34)
    ao = [None] * n
    for i in range(33, n):
        ao[i] = s5[i] - s34[i]
    ao5 = sma([x for x in ao if x is not None], 5)
    ac = [None] * n
    j = 0
    for i in range(33, n):
        if j >= 4 and ao5[j] is not None:
            ac[i] = ao[i] - ao5[j]
        j += 1

    jaw = smma(mp, 13)   # 位移8 -> jaw_t = jaw[i-8]
    teeth = smma(mp, 8)  # 位移5
    lips = smma(mp, 5)   # 位移3
    up_fx, dn_fx = fractals(h, l)

    if r < 21:
        return st

    def val(seq, i, d):
        ii = i - d
        return seq[ii] if 0 <= ii < len(seq) else None

    J, T, Lp = val(jaw, r, 8), val(teeth, r, 5), val(lips, r, 3)
    price = c[r]
    if None in (J, T, Lp):
        return st

    st.pct = (c[r] / c[r - 1] - 1.0) * 100.0 if c[r - 1] else 0.0

    bull = bear = 0.0
    sep = (Lp - J) / price if price else 0.0

    # ---- g1 鳄鱼结构与方向 (0..30)
    if price > Lp:
        bull += 10
        if Lp > T:
            bull += 6
        if T > J:
            bull += 6
        rise = sum([_line_rising(jaw, r), _line_rising(teeth, r), _line_rising(lips, r)])
        bull += rise * 8 / 3.0
    elif price < Lp:
        bear += 10
        if Lp < T:
            bear += 6
        if T < J:
            bear += 6
        fall = sum([_line_rising(jaw, r) is False and jaw[r] is not None and jaw[r - 3] is not None,
                    _line_rising(teeth, r) is False and teeth[r] is not None and teeth[r - 3] is not None,
                    _line_rising(lips, r) is False and lips[r] is not None and lips[r - 3] is not None])
        bear += fall * 8 / 3.0

    # ---- g2 鳄鱼张嘴幅度 (0..25)
    if price > Lp > T > J and sep > 0:
        bull += min(25.0, sep / 0.01 * 25.0)
    if price < Lp < T < J and sep < 0:
        bear += min(25.0, (-sep) / 0.01 * 25.0)

    # ---- g3 AO (0..15)
    if ao[r] is not None:
        if ao[r] > 0:
            bull += 5
        elif ao[r] < 0:
            bear += 5
        if ao[r] > ao[r - 1]:
            bull += 5
        elif ao[r] < ao[r - 1]:
            bear += 5
        # 近20根零轴穿越
        for i in range(max(33, r - 20), r):
            if ao[i] is not None and ao[i - 1] is not None and ao[i] > 0 >= ao[i - 1]:
                bull += 5
                break
            if ao[i] is not None and ao[i - 1] is not None and ao[i] < 0 <= ao[i - 1]:
                bear += 5
                break

    # ---- g4 AC (0..15)
    if ac[r] is not None:
        if ac[r] > 0:
            bull += 5
        elif ac[r] < 0:
            bear += 5
        if ac[r] > ac[r - 1]:
            bull += 5
            if ac[r - 1] > ac[r - 2]:
                bull += 5
        elif ac[r] < ac[r - 1]:
            bear += 5
            if ac[r - 1] < ac[r - 2]:
                bear += 5

    # ---- g5 分形突破 (0..15)
    last_up = last_dn = None
    for i in range(r - 2, 1, -1):
        if up_fx[i] and last_up is None:
            last_up = i
        if dn_fx[i] and last_dn is None:
            last_dn = i
        if last_up is not None and last_dn is not None:
            break
    if last_up is not None:
        bull += 5
        if price > h[last_up]:
            bull += 10
    if last_dn is not None:
        bear += 5
        if price < l[last_dn]:
            bear += 10

    bull = min(bull, 100.0)
    bear = min(bear, 100.0)
    st.bull, st.bear = bull, bear
    st.net = bull - bear
    st.dirn = "多头" if st.net > 5 else ("空头" if st.net < -5 else "横盘")

    # 文本摘要
    if price > Lp > T > J and sep > 0.0015:
        st.alligator = "多头张口"
    elif price < Lp < T < J and sep < -0.0015:
        st.alligator = "空头张口"
    elif price > Lp and price > T and price > J:
        st.alligator = "多头(线未张口)"
    elif price < Lp and price < T and price < J:
        st.alligator = "空头(线未张口)"
    else:
        st.alligator = "缠绕/震荡"
    st.ao_txt = ("AO>0" if ao[r] is not None and ao[r] > 0 else ("AO<0" if ao[r] is not None else "AO--"))
    st.ao_txt += "↑" if (ao[r] is not None and ao[r] > ao[r - 1]) else ("↓" if (ao[r] is not None and ao[r] < ao[r - 1]) else "")
    st.ac_txt = ("AC>0" if ac[r] is not None and ac[r] > 0 else ("AC<0" if ac[r] is not None else "AC--"))
    st.ac_txt += "↑" if (ac[r] is not None and ac[r] > ac[r - 1]) else ("↓" if (ac[r] is not None and ac[r] < ac[r - 1]) else "")
    if last_up is not None and last_dn is not None:
        st.fx_txt = "上分形%s / 下分形%s" % (
            "已破" if price > h[last_up] else "未破",
            "已破" if price < l[last_dn] else "未破")
    elif last_up is not None:
        st.fx_txt = "上分形%s" % ("已破" if price > h[last_up] else "未破")
    elif last_dn is not None:
        st.fx_txt = "下分形%s" % ("已破" if price < l[last_dn] else "未破")
    else:
        st.fx_txt = "无分形"
    return st


# ------------------------------------------------------------------ 扫描
def _slice_rows(rows, date=None):
    """date 指定时只保留该日前完整K线；否则去掉未收盘的当日K线。"""
    if not rows:
        return []
    if date:
        return [r for r in rows if r["date"] <= date]
    return net.drop_incomplete(rows)


def _to_item(meta, rows, date, is_stock, min_bars=120):
    rows = _slice_rows(rows, date)
    if len(rows) < min_bars:
        return None
    r = len(rows) - 1
    o = [x["open"] for x in rows]
    c = [x["close"] for x in rows]
    h = [x["high"] for x in rows]
    l = [x["low"] for x in rows]
    vol = [x["volume"] for x in rows]
    st = score_chaos(o, c, h, l, vol, r)
    base = {
        "mkt": meta.get("mkt", ""),
        "code": meta.get("code") or meta["symbol"],
        "name": meta.get("name") or meta["symbol"],
        "price": c[r],
        "pct": st.pct,
        "state": st,
        "date": rows[r]["date"],
    }
    if is_stock:
        amount_yi = meta.get("amount_yi", 0.0)
        if date:
            amount_yi = 10 ** 6  # 历史复盘时无法取得当日成交额，跳过流动性过滤
        base.update({"amount_yi": amount_yi, "vol": vol[r]})
    else:
        base.update({"vol": vol[r], "oi": rows[r].get("oi", 0.0)})
    return base


def scan_stocks(universe, cfg):
    pairs, errors = net.fetch_stock_daily_many(
        universe, max_workers=cfg["max_workers"], timeout=cfg["timeout"])
    items = []
    for meta, rows in pairs:
        it = _to_item(meta, rows, cfg.get("date"), True, cfg["min_bars"])
        if it is None:
            continue
        if not cfg.get("date") and it["amount_yi"] < cfg["min_amount_yi"]:
            continue
        items.append(it)
    items.sort(key=lambda x: x["state"].net, reverse=True)
    return items, errors


def scan_futures(universe, cfg):
    pairs, errors = net.fetch_future_daily_many(
        universe, max_workers=cfg["max_workers"], timeout=cfg["timeout"])
    specs = {}
    if cfg.get("futures_mode") == "main" and universe:
        specs = net.fetch_futures_specs(
            universe, max_workers=cfg["max_workers"], timeout=cfg["timeout"])
    scored = []
    for meta, rows in pairs:
        sliced = _slice_rows(rows, cfg.get("date"))
        if len(sliced) < cfg["min_bars"]:
            continue
        r = len(sliced) - 1
        if r < 1:
            continue
        oi_now = sliced[r].get("oi", 0.0) or 0.0
        oi_prev = sliced[r - 1].get("oi", 0.0) or 0.0
        settle_now = sliced[r].get("settle") or sliced[r]["close"]
        settle_prev = sliced[r - 1].get("settle") or sliced[r - 1]["close"]
        spec = specs.get(meta["symbol"], {})
        multiplier = spec.get("multiplier") or 1.0
        margin_rate = spec.get("margin_rate") or 1.0
        flow = (oi_now * settle_now - oi_prev * settle_prev) * multiplier * (margin_rate / 100.0)
        it = _to_item(meta, rows, cfg.get("date"), False, cfg["min_bars"])
        if it is None:
            continue
        if it["vol"] < cfg["min_vol_lots"] or it["oi"] < cfg["min_oi_lots"]:
            continue
        it["flow"] = flow
        it["flow_yi"] = flow / 1e8
        it["turnover_yi"] = (it["vol"] * settle_now * multiplier) / 1e8
        scored.append(it)

    if cfg.get("futures_mode") != "main":
        scored.sort(key=lambda x: abs(x["state"].net), reverse=True)
        return scored, errors

    limit = max(1, int(cfg.get("futures_pool_limit", 5)))
    include_outflow = bool(cfg.get("futures_include_outflow", True))
    inflows = sorted([x for x in scored if x["flow"] > 0],
                     key=lambda x: x["flow"], reverse=True)[:limit]
    outflows = sorted([x for x in scored if x["flow"] < 0],
                      key=lambda x: x["flow"])[:limit] if include_outflow else []
    items = inflows + outflows
    items.sort(key=lambda x: abs(x["state"].net), reverse=True)
    return items, errors


# ------------------------------------------------------------------ 报告
def _row(it, kind):
    st = it["state"]
    name = it.get("name") or it.get("code")
    code = it.get("code")
    label = "%s.%s" % (code, it.get("mkt") or "-")
    return label, name, it["price"], it["pct"], st


def render(items, kind, top, show_bear=False, mode="console"):
    if not items:
        return "(无结果)"
    lines = []
    head = "股票(做多候选, 按上涨趋势强度)" if kind == "stock" else "期货(按趋势强度, 含多空方向)"
    if mode == "markdown":
        lines.append("#### " + head)
        lines.append("| 代码 | 名称 | 收盘 | 当日% | 方向 | 趋势分(多/空) | 鳄鱼 | AO | AC | 分形 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        sel = [x for x in items if x["state"].dirn != "空头"][:top] if kind == "stock" else items[:top]
        if not sel:
            lines.append("(本期无多头候选)")
        else:
            for it in sel:
                label, name, price, pct, st = _row(it, kind)
                lines.append("| %s | %s | %.2f | %+.2f%% | %s | %d(%d/%d) | %s | %s | %s | %s |" % (
                    label, name, price, pct, st.dirn, round(st.net), round(st.bull), round(st.bear),
                    st.alligator, st.ao_txt, st.ac_txt, st.fx_txt))
        if kind == "stock" and show_bear:
            bear = [x for x in items if x["state"].dirn == "空头"][:top]
            if bear:
                lines.append("(仅供参考, A股不能做空个股) 下跌趋势榜：")
                for it in bear:
                    label, name, price, pct, st = _row(it, kind)
                    lines.append("| %s | %s | %.2f | %+.2f%% | %s | %d |" % (
                        label, name, price, pct, st.dirn, round(st.net)))
    else:
        lines.append("== %s ==" % head)
        sel = [x for x in items if x["state"].dirn != "空头"][:top] if kind == "stock" else items[:top]
        if not sel:
            lines.append("(本期无多头候选)")
        else:
            for it in sel:
                label, name, price, pct, st = _row(it, kind)
                lines.append("%-18s %-8s 收 %.2f  %+5.2f%%  方向:%-3s 分:%3d(多%2d/空%2d)  鳄鱼:%s  %s %s  %s" % (
                    label, name, price, pct, st.dirn, round(st.net), round(st.bull), round(st.bear),
                    st.alligator, st.ao_txt, st.ac_txt, st.fx_txt))
        if kind == "stock" and show_bear:
            bear = [x for x in items if x["state"].dirn == "空头"][:top]
            if bear:
                lines.append("(仅供参考, A股不能做空个股) 下跌趋势榜：")
                for it in bear:
                    label, name, price, pct, st = _row(it, kind)
                    lines.append("%-18s %-8s 收 %.2f  %+5.2f%%  方向:%-3s 分:%3d" % (
                        label, name, price, pct, st.dirn, round(st.net)))
    return "\n".join(lines)


def render_futures(items, top, mode="console"):
    if not items:
        return "(无结果)"
    bull = sorted([x for x in items if x["state"].dirn == "多头"],
                  key=lambda x: x["state"].net, reverse=True)[:top]
    bear = sorted([x for x in items if x["state"].dirn == "空头"],
                  key=lambda x: x["state"].net)[:top]
    lines = []
    heads = [("期货上涨趋势最强(多头)", bull), ("期货下跌趋势最强(空头)", bear)]

    def row_line(label, it):
        st = it["state"]
        flow = it.get("flow_yi", 0.0)
        return "| %s | %s | %.2f | %+.2f%% | %+.2f亿 | %d(%d/%d) | %s | %s | %s | %s |" % (
            label, it["name"], it["price"], it["pct"], flow,
            round(st.net), round(st.bull), round(st.bear),
            st.alligator, st.ao_txt, st.ac_txt, st.fx_txt)

    def console_line(label, it):
        st = it["state"]
        flow = it.get("flow_yi", 0.0)
        return "%-18s %-10s 收 %.2f  %+5.2f%%  资金:%+7.2f亿  分:%3d(多%2d/空%2d)  鳄鱼:%s  %s %s  %s" % (
            label, it["name"], it["price"], it["pct"], flow,
            round(st.net), round(st.bull), round(st.bear),
            st.alligator, st.ao_txt, st.ac_txt, st.fx_txt)

    for head, sel in heads:
        if mode == "markdown":
            lines.append("#### " + head)
            if not sel:
                lines.append("(本期无符合方向候选)")
                continue
            lines.append("| 代码 | 名称 | 收盘 | 当日% | 资金流(亿) | 趋势分(多/空) | 鳄鱼 | AO | AC | 分形 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
            for it in sel:
                label, name, price, pct, st = _row(it, "future")
                lines.append(row_line(label, it))
        else:
            lines.append("== %s ==" % head)
            if not sel:
                lines.append("(本期无符合方向候选)")
                continue
            for it in sel:
                label, name, price, pct, st = _row(it, "future")
                lines.append(console_line(label, it))
    return "\n".join(lines)


def _state_json(state):
    return {
        "net": state.net,
        "bull": state.bull,
        "bear": state.bear,
        "dirn": state.dirn,
        "alligator": state.alligator,
    }


def main():
    ap = argparse.ArgumentParser(description="混沌操作法每日趋势扫描(互联网行情数据)")
    ap.add_argument("--config", default=None, help="JSON 配置文件路径")
    ap.add_argument("--search-root", help=argparse.SUPPRESS)
    ap.add_argument("--stocks-root", help=argparse.SUPPRESS)
    ap.add_argument("--futures-root", help=argparse.SUPPRESS)
    ap.add_argument("--aliases", help=argparse.SUPPRESS)
    ap.add_argument("--top", type=int, default=None)
    ap.add_argument("--futures-mode", choices=["main", "all"], default=None)
    ap.add_argument("--stocks-limit", type=int, default=None,
                    help="股票候选池：成交量/成交额前 N 只(默认50)")
    ap.add_argument("--stock-rank-field", choices=["volume", "amount"], default=None,
                    help="股票候选池排序字段：volume=成交量, amount=成交额")
    ap.add_argument("--stocks-all", action="store_true", help="扫描全部A股(耗时更长)")
    ap.add_argument("--futures-pool-limit", type=int, default=None,
                    help="期货资金流入/流出各取前 N 名(默认5)")
    ap.add_argument("--futures-no-outflow", action="store_true",
                    help="期货只看资金流入榜，不另加资金流出榜")
    ap.add_argument("--max-workers", type=int, default=None, help="并发下载线程数")
    ap.add_argument("--timeout", type=float, default=None, help="单个HTTP请求超时(秒)")
    ap.add_argument("--no-stocks", action="store_true")
    ap.add_argument("--no-futures", action="store_true")
    ap.add_argument("--show-stock-bear", action="store_true")
    ap.add_argument("--date", default=None, help="基准交易日 YYYY-MM-DD(默认最近完整交易日)")
    ap.add_argument("--out", choices=["console", "markdown", "json"], default="console")
    args = ap.parse_args()

    cfg = dict(DEFAULT_CONFIG)
    if args.config and os.path.isfile(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    for k, v in [("top", args.top), ("futures_mode", args.futures_mode),
                 ("max_workers", args.max_workers), ("timeout", args.timeout),
                 ("date", args.date), ("stock_rank_field", args.stock_rank_field),
                 ("futures_pool_limit", args.futures_pool_limit)]:
        if v is not None:
            cfg[k] = v
    if args.futures_no_outflow:
        cfg["futures_include_outflow"] = False
    if args.stocks_limit is not None:
        cfg["stock_limit"] = args.stocks_limit
    if args.stocks_all:
        cfg["stock_limit"] = None
    if args.show_stock_bear:
        cfg["show_stock_bear"] = True
    if args.search_root or args.stocks_root or args.futures_root:
        print("[提示] 本版数据源已改为互联网，不再使用 --search-root/--stocks-root/--futures-root。")

    out = {"date": None, "stocks": [], "futures": []}
    sections = []
    total_errors = 0

    if not args.no_stocks:
        limit_txt = ("全部" if cfg.get("stock_limit") is None else cfg["stock_limit"])
        rank_field = cfg.get("stock_rank_field", "volume")
        rank_txt = "成交量" if rank_field == "volume" else "成交额"
        print("从新浪财经获取 A股清单(按%s降序, 取前 %s 只作为候选池)... "
              % (rank_txt, limit_txt))
        universe = net.load_stock_universe(
            stock_limit=cfg.get("stock_limit"),
            min_amount_yi=cfg.get("min_amount_yi", 0.0),
            markets=cfg.get("stock_markets", []),
            sort_by=rank_field,
            timeout=cfg["timeout"],
        )
        if not universe:
            print("[警告] 未能取得 A股清单，请检查网络或稍后重试。")
        else:
            print("A股清单 %d 只，开始下载日K并评分..." % len(universe))
            items, errs = scan_stocks(universe, cfg)
            total_errors += errs
            for it in items:
                it["kind"] = "stock"
            out["stocks"] = [{k: (v if k != "state" else _state_json(v)) for k, v in it.items()}
                             for it in items]
            sections.append(("stock", items, "新浪A股候选池(%s前%d), 成功评分=%d, 下载失败=%d"
                             % (rank_txt, len(universe), len(items), errs)))

    if not args.no_futures:
        mode = cfg.get("futures_mode", "main")
        print("从新浪财经获取国内期货%s连续/合约清单..." % ("主力" if mode == "main" else "全部"))
        universe = net.load_futures_universe(
            mode=mode, timeout=cfg["timeout"], max_workers=cfg["max_workers"])
        markets = set(cfg.get("future_markets", []))
        universe = [x for x in universe if x.get("mkt") in markets]
        if not universe:
            print("[警告] 未能取得期货清单，请检查网络或稍后重试。")
        else:
            print("期货清单 %d 个，开始下载日K并评分..." % len(universe))
            items, errs = scan_futures(universe, cfg)
            total_errors += errs
            for it in items:
                it["kind"] = "future"
            out["futures"] = [{k: (v if k != "state" else _state_json(v)) for k, v in it.items()}
                              for it in items]
            flow_note = "全部当前合约"
            if mode == "main":
                flow_note = ("资金流入/流出各前%d" % cfg.get("futures_pool_limit", 5)
                             if cfg.get("futures_include_outflow", True)
                             else "资金流入前%d" % cfg.get("futures_pool_limit", 5))
            sections.append(("future", items, "新浪国内期货(%s), 候选池=%s, 成功评分=%d, 下载失败=%d"
                             % (mode, flow_note, len(items), errs)))

    top = cfg["top"]
    base_date = _latest_date(out["stocks"], out["futures"])
    if args.out == "json":
        print(json.dumps({"date": base_date, "top": top,
                          "stocks": out["stocks"], "futures": out["futures"]},
                         ensure_ascii=False, indent=2))
        return 0 if total_errors == 0 else 2

    lines = []
    lines.append("混沌操作法·每日趋势扫描(互联网数据源)")
    lines.append("基准交易日: %s   Top %d   数据源: 新浪财经公开行情接口" % (base_date or "(无)", top))
    lines.append("-" * 72)
    for kind, items, note in sections:
        lines.append("[%s] %s" % (note, "评分标的数=%d" % len(items)))
        if kind == "future":
            lines.append(render_futures(items, top, args.out))
        else:
            lines.append(render(items, kind, top, cfg.get("show_stock_bear", False), args.out))
        lines.append("")
    if total_errors:
        lines.append("[提示] 本次有 %d 只标的日K下载失败，已在结果中跳过；可降低 --max-workers 后重试。"
                     % total_errors)
    lines.append("说明: 分数=鳄鱼结构/张嘴幅度/AO/AC/分形突破的量化合计(多空各0-100, 净分=多-空)。")
    lines.append("期货资金流为按持仓/结算价/乘数/保证金率的互联网估算值。")
    lines.append("数据来自新浪财经公开行情接口，仅供研究参考，非投资建议；下单前请人工复核K线。")
    print("\n".join(lines))
    return 0 if total_errors == 0 else 2


def _latest_date(*lists):
    ds = [it.get("date") for lst in lists for it in lst if it.get("date")]
    return max(ds) if ds else None


if __name__ == "__main__":
    sys.exit(main())
