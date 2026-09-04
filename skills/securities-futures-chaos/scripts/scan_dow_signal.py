# -*- coding: utf-8 -*-
"""scan_dow_signal.py - 道氏结构 + 混沌分形买卖点 + 资金惯性 强化扫描（联网版，支持盘中实时）

规则（用户自述，与《证券混沌操作法》/道氏理论对齐）：
  1) 趋势定义(道氏): 上涨=低点不断上移; 下跌=高点不断下降。
  2) 高低点=分形: 上分形=高点, 下分形=低点; 标准趋势图形=低点(或高点)一档档推进。
  3) 做多: 先出现"低点第一次上抬"(可能是上涨起点) -> 价格突破前一个高点 = 买点1;
     上涨后回调产生比前低更高的新低 = 继续买进/加仓点; 止损=前一个低点;
     只要不破前低就持有, 并背靠前低逐级加仓。
  4) 做空(镜像, 主看高点): 高点第一次下降 -> 价格跌破前一个低点 = 卖点1;
     下跌中反弹产生比前高更低的新高 = 反弹加仓点; 止损=前一个高点;
     只要不破前高就持有空头, 背靠前高逐级加仓。
  5) 破位=瞬时击穿(实时): 多头盘中价格瞬间下穿前低=趋势结束/平多; 空头瞬间上穿前高=趋势结束/平空。
     判定用K线/盘中最低最高价探测, 不依赖收盘价。
  6) 资金惯性: 有资金流入的品种趋势延续性/惯性更好; 结合资金流挑"最强趋势"。

用法:
  python scan_dow_signal.py --out console            # 收盘后/默认(最近完整交易日)
  python scan_dow_signal.py --intraday               # 盘中任何时刻实时查看(保留未收盘bar)
  python scan_dow_signal.py --intraday --no-futures  # 盘中只看A股
  python scan_dow_signal.py --top 5 --out markdown > dow_signal.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_data as net  # noqa: E402
import scan_trend as st  # noqa: E402  (复用 fractals / score_chaos)


# ------------------------------------------------------------------ 结构识别
def swing_pivots(high, low):
    """把5根K线分形整理成交替的摆动序列 [(idx, 'H'|'L'), ...]（时间正序）。"""
    up_fx, dn_fx = st.fractals(high, low)
    piv = []
    for i in range(len(high)):
        if up_fx[i]:
            if piv and piv[-1][1] == "H":
                if high[i] > high[piv[-1][0]]:
                    piv[-1] = (i, "H")
            else:
                piv.append((i, "H"))
        if dn_fx[i]:
            if piv and piv[-1][1] == "L":
                if low[i] < low[piv[-1][0]]:
                    piv[-1] = (i, "L")
            else:
                piv.append((i, "L"))
    return piv


def _consecutive(seq, key):
    """从最新往旧数, 连续满足 key(新,旧) 的段数。"""
    cnt = 0
    for j in range(len(seq) - 1, 0, -1):
        if key(seq[j], seq[j - 1]):
            cnt += 1
        else:
            break
    return cnt


def _cross(high, low, start, level, above):
    """start 之后第一根 上穿(above=True: high>level) / 下穿(above=False: low<level) 的bar下标。"""
    for i in range(start + 1, len(high)):
        if above and high[i] > level:
            return i
        if not above and low[i] < level:
            return i
    return None


def dow_analyze(rows, lookback=160, intraday=False, live_quote=None):
    """返回道氏结构/买卖点/止损。rows 时间升序日K; intraday=True 时末根为盘中实时bar;
    live_quote 可用腾讯/新浪实时快照覆盖末根(现价/今日最高/今日最低), 做瞬时击穿探测。"""
    rows = rows[-lookback:]
    n = len(rows)
    if live_quote and rows:
        last = dict(rows[-1])
        q = {k: _num(v) for k, v in live_quote.items() if k in ("price", "high", "low", "pre_close")}
        if q.get("price") is not None:
            last["close"] = q["price"]
        if q.get("high") is not None:
            last["high"] = q["high"]
        if q.get("low") is not None:
            last["low"] = q["low"]
        rows[-1] = last
    n = len(rows)
    px = rows[-1]["close"]
    pc = rows[-2]["close"] if n >= 2 and rows[-2]["close"] else 0.0
    pct = (px / pc - 1.0) * 100.0 if pc else 0.0
    if live_quote and _num(live_quote.get("pre_close")):
        pct = (px / _num(live_quote["pre_close"]) - 1.0) * 100.0
    out = {"n": n, "date": rows[-1]["date"], "price": px, "intraday": intraday, "pct": pct}
    if n < 40:
        out.update({"ok": False})
        return out
    h = [x["high"] for x in rows]
    l = [x["low"] for x in rows]
    c = [x["close"] for x in rows]
    d = [x["date"] for x in rows]
    piv = swing_pivots(h, l)
    lows = [(i, l[i], d[i]) for i, k in piv if k == "L"]
    highs = [(i, h[i], d[i]) for i, k in piv if k == "H"]
    r = n - 1
    price = c[r]

    rising = _consecutive(lows, lambda a, b: a[1] > b[1])    # 上涨=低点逐级抬高
    falling = _consecutive(highs, lambda a, b: a[1] < b[1])  # 下跌=高点逐级降低

    long_sig, long_stop, long_lvl, long_dt, long_fresh, long_trend, long_legs = \
        _long_view(lows, highs, h, l, d, r, price, rising)
    short_sig, short_stop, short_lvl, short_dt, short_fresh, short_trend, short_legs = \
        _short_view(lows, highs, h, l, d, r, price, falling)

    bull = min(100.0, 15 + 22 * min(long_legs, 4)
               + (18 if long_lvl is not None and long_dt is not None and h[r] > long_lvl else 0)
               + (10 if long_fresh else 0))
    bear = min(100.0, 15 + 22 * min(short_legs, 4)
               + (18 if short_lvl is not None and short_dt is not None and l[r] < short_lvl else 0)
               + (10 if short_fresh else 0))
    if long_sig.startswith("破前低"):
        bull = min(bull, 30.0)
    if short_sig.startswith("破前高"):
        bear = min(bear, 30.0)

    out.update({
        "ok": True,
        "pivots": piv, "lows": lows, "highs": highs,
        "rising_legs": rising, "falling_legs": falling,
        "long_trend": long_trend, "short_trend": short_trend,
        "long": {"signal": long_sig, "stop": long_stop, "break_lvl": long_lvl,
                 "break_dt": long_dt, "fresh": long_fresh, "legs": long_legs},
        "short": {"signal": short_sig, "stop": short_stop, "break_lvl": short_lvl,
                  "break_dt": short_dt, "fresh": short_fresh, "legs": short_legs},
        "bull": bull, "bear": bear,
        "live_low": l[r], "live_high": h[r],
        "last_low": lows[-1] if lows else None,
        "last_high": highs[-1] if highs else None,
    })
    return out


def _long_view(lows, highs, h, l, d, r, price, rising):
    """做多: 低点抬高为骨架; 突破前高(上分形)=买; 瞬时下穿前低=破位。"""
    nl = len(lows)
    if nl < 2:
        return "无上涨结构(低点不足)", None, None, None, False, "无上涨结构", 0
    Lc, La = lows[-1], lows[-2]
    between = [x for x in highs if La[0] < x[0] < Lc[0]]
    B = max(between, key=lambda x: x[1]) if between else None
    stop = La[1]
    if not (Lc[1] > La[1]):          # 最近低点未抬高 -> 无上涨结构
        sig = "无上涨结构(低点未上抬)"
        return sig, stop, None, None, False, "非上涨", 0
    # 1) 破位: 自 Lc 之后任意时刻(盘中瞬时)下穿前低
    if _cross(h, l, Lc[0], stop, False) is not None:
        return "破前低(瞬时击穿->多头结束/平多)", stop, None, None, False, "破位", rising
    # 2) 突破前高
    if B is not None:
        bar = _cross(h, l, Lc[0], B[1], True)
        if bar is not None:
            fresh = (r - bar) <= 5
            note = ""
            if bar == r and price <= B[1]:
                note = "(现价回落至突破位下方)"
            if rising >= 2:
                sig = "突破前高(延续买点)%s" % note if fresh else "上涨持有(已破前高)"
            elif rising == 1:
                sig = "买点1 低点首抬+突破前高%s" % note if fresh else "上涨启动(已破前高)"
            else:
                sig = "反弹突破前高(逆势, 不建议做多)"
            trend = "上涨(低点抬高x%d)" % rising
            return sig, stop, B[1], d[bar], fresh, trend, rising
    # 3) 未突破: 更高低点已现 -> 加仓/蓄势
    if rising >= 2:
        sig = "回调加仓点(更高低点, 背靠前低止损)"
    else:
        sig = "蓄势: 低点首抬, 等突破前高 %.2f" % (B[1] if B else 0)
    return sig, stop, (B[1] if B else None), None, False, "上涨(低点抬高x%d)" % rising, rising


def _short_view(lows, highs, h, l, d, r, price, falling):
    """做空(镜像, 主看高点): 高点降低为骨架; 跌破前低(下分形)=卖; 瞬时上穿前高=破位。"""
    nh = len(highs)
    if nh < 2:
        return "无下跌结构(高点不足)", None, None, None, False, "无下跌结构", 0
    Hc, Ha = highs[-1], highs[-2]
    between = [x for x in lows if Ha[0] < x[0] < Hc[0]]
    B = min(between, key=lambda x: x[1]) if between else None
    stop = Ha[1]
    if not (Hc[1] < Ha[1]):          # 最近高点未降低 -> 无下跌结构
        sig = "无下跌结构(高点未降低)"
        return sig, stop, None, None, False, "非下跌", 0
    # 1) 破位: 自 Hc 之后任意时刻(盘中瞬时)上穿前高
    if _cross(h, l, Hc[0], stop, True) is not None:
        return "破前高(瞬时上穿->空头结束/平空)", stop, None, None, False, "破位", falling
    # 2) 跌破前低
    if B is not None:
        bar = _cross(h, l, Hc[0], B[1], False)
        if bar is not None:
            fresh = (r - bar) <= 5
            note = ""
            if bar == r and price >= B[1]:
                note = "(现价收回跌破位上方)"
            if falling >= 2:
                sig = "跌破前低(延续卖点)%s" % note if fresh else "下跌持有(已破前低)"
            elif falling == 1:
                sig = "卖点1 高点首降+跌破前低%s" % note if fresh else "下跌启动(已破前低)"
            else:
                sig = "反弹跌破前低(逆势, 不建议做空)"
            trend = "下跌(高点降低x%d)" % falling
            return sig, stop, B[1], d[bar], fresh, trend, falling
    if falling >= 2:
        sig = "反弹加仓点(更低新高, 背靠前高止损)"
    else:
        sig = "蓄势: 高点首降, 等跌破前低 %.2f" % (B[1] if B else 0)
    return sig, stop, (B[1] if B else None), None, False, "下跌(高点降低x%d)" % falling, falling


def _num(x):
    try:
        return float(x) if x not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def fetch_tencent_quotes(codes, timeout=15, chunk=50):
    """腾讯 qt.gtimg.cn 批量实时(A股): 现价/今高/今低/昨收/成交额。失败返回空dict(调用方回落新浪)。"""
    if not codes:
        return {}
    syms = []
    for code in codes:
        c = str(code)
        if c.startswith(("6", "9")):
            syms.append("sh" + c)
        elif c.startswith(("0", "1", "2", "3")):
            syms.append("sz" + c)
        elif c.startswith(("4", "8")):
            syms.append("bj" + c)
        else:
            syms.append("sh" + c)
    out = {}
    for i in range(0, len(syms), chunk):
        url = "https://qt.gtimg.cn/q=" + ",".join(syms[i:i + chunk])
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("gbk", "replace")
            for m in re.finditer(r'v_\w+="([^"]*)"', raw):
                f = m.group(1).split("~")
                if len(f) < 38:
                    continue
                code = f[2]
                price, high, low = _num(f[3]), _num(f[33]), _num(f[34])
                pre_close, amt_wan = _num(f[4]), _num(f[37])
                if price is None:
                    continue
                out[code] = {"price": price,
                             "high": high if high is not None else price,
                             "low": low if low is not None else price,
                             "pre_close": pre_close,
                             "amount_yi": (amt_wan / 1e4) if amt_wan is not None else None}
        except Exception:
            continue
    return out


# ------------------------------------------------------------------ 资金惯性
def _slice(rows, date=None, intraday=False):
    if date:
        return [x for x in rows if x["date"] <= date]
    if intraday:
        return rows                       # 盘中: 保留未收盘的实时bar
    return net.drop_incomplete(rows)


def _flow_of(rows):
    r = len(rows) - 1
    oi0 = rows[r].get("oi", 0.0) or 0.0
    oi1 = rows[r - 1].get("oi", 0.0) or 0.0
    s0 = rows[r].get("settle") or rows[r]["close"]
    s1 = rows[r - 1].get("settle") or rows[r - 1]["close"]
    return (oi0 * s0 - oi1 * s1), oi0, s0


# ------------------------------------------------------------------ 主流程
def build(cfg):
    out = {"stocks": [], "futures_bull": [], "futures_bear": [], "date": None,
           "errors": 0, "intraday": bool(cfg.get("intraday")),
           "include_stocks": not cfg.get("no_stocks"),
           "include_futures": not cfg.get("no_futures")}
    intraday = out["intraday"]

    if not cfg.get("no_stocks"):
        uni = net.load_stock_universe(stock_limit=cfg.get("stock_limit", 50),
                                      min_amount_yi=cfg.get("min_amount_yi", 0.5),
                                      sort_by=cfg.get("stock_rank_field", "volume"))
        pairs, err = net.fetch_stock_daily_many(uni, max_workers=cfg["max_workers"],
                                                timeout=cfg["timeout"])
        out["errors"] += err
        tq = fetch_tencent_quotes([m["code"] for m in uni]) if intraday else {}
        for meta, rows in pairs:
            rows = _slice(rows, cfg.get("date"), intraday)
            if len(rows) < 80:
                continue
            lq = tq.get(meta["code"])
            a = dow_analyze(rows, intraday=intraday, live_quote=lq)
            if not a.get("ok"):
                continue
            rec = {"kind": "stock", "meta": meta, "price": a["price"], "pct": a["pct"],
                   "date": a["date"], "dow": a, "amount_yi": meta.get("amount_yi", 0.0),
                   "turnover": meta.get("turnover", 0.0)}
            if lq and lq.get("amount_yi") is not None:
                rec["amount_yi"] = lq["amount_yi"]
            out["stocks"].append(rec)
        out["date"] = out["stocks"][0]["date"] if out["stocks"] else None
        pool = {"volume": "成交量", "amount": "成交额", "turnover": "换手率"}.get(cfg.get("stock_rank_field"), "成交量")
        out["stock_pool_label"] = "%s前%d" % (pool, cfg.get("stock_limit", 50))
    else:
        out["stock_pool_label"] = "股票池"

    if not cfg.get("no_futures"):
        uni = net.load_futures_universe(mode="main")
        pairs, err = net.fetch_future_daily_many(uni, max_workers=cfg["max_workers"],
                                                 timeout=cfg["timeout"])
        out["errors"] += err
        scored = []
        for meta, rows in pairs:
            rows = _slice(rows, cfg.get("date"), intraday)
            if len(rows) < 80:
                continue
            raw, oi, settle = _flow_of(rows)
            a = dow_analyze(rows, intraday=intraday)
            if not a.get("ok"):
                continue
            scored.append({"meta": meta, "rows": rows, "raw": raw, "oi": oi,
                           "settle": settle, "price": a["price"], "pct": a["pct"],
                           "date": a["date"], "dow": a})
        scored.sort(key=lambda x: x["raw"], reverse=True)
        limit = cfg.get("futures_pool_limit", 5)
        inflow = scored[:limit]
        outflow = scored[-limit:] if cfg.get("futures_include_outflow", True) else []
        need = inflow + outflow
        specs = net.fetch_futures_specs([x["meta"] for x in need],
                                        max_workers=cfg["max_workers"], timeout=cfg["timeout"])
        for rec in need:
            sp = specs.get(rec["meta"]["symbol"], {})
            mult = sp.get("multiplier") or 1.0
            mr = sp.get("margin_rate") or 1.0
            rec["flow_yi"] = rec["raw"] * mult * (mr / 100.0) / 1e8
        out["futures_bull"] = inflow
        out["futures_bear"] = outflow
        if out["date"] is None:
            out["date"] = inflow[0]["date"] if inflow else None

    for rec in out["stocks"]:
        rec["score"] = rec["dow"]["bull"] + min(10.0, rec["amount_yi"] / 30.0)
    out["stocks"].sort(key=lambda x: (_sig_rank_long(x["dow"]), x["score"]), reverse=True)
    for rec in out["futures_bull"]:
        rec["score"] = rec["dow"]["bull"] + (12 if rec["flow_yi"] > 0 else -8)
    out["futures_bull"].sort(key=lambda x: (_sig_rank_long(x["dow"]), x["score"]), reverse=True)
    for rec in out["futures_bear"]:
        rec["score"] = rec["dow"]["bear"] + (12 if rec["flow_yi"] < 0 else -8)
    out["futures_bear"].sort(key=lambda x: (_sig_rank_short(x["dow"]), x["score"]), reverse=True)
    return out


def _sig_rank_long(d):
    s = d["long"]["signal"]
    if s.startswith(("买点1", "突破前高", "回调加仓点")):
        return 3
    if s.startswith("上涨"):
        return 2
    if s.startswith(("蓄势", "上涨启动")):
        return 1
    return 0


def _sig_rank_short(d):
    s = d["short"]["signal"]
    if s.startswith(("卖点1", "跌破前低", "反弹加仓点")):
        return 3
    if s.startswith("下跌"):
        return 2
    if s.startswith(("蓄势", "下跌启动")):
        return 1
    return 0


def _long_trend_label(d):
    s = d["long"]["signal"]
    if s.startswith("破前低"):
        return "破位(多头结束)"
    legs = d["long"]["legs"]
    return "上涨(低点抬高x%d)" % legs if legs >= 1 else "非上涨"


def _short_trend_label(d):
    s = d["short"]["signal"]
    if s.startswith("破前高"):
        return "破位(空头结束)"
    legs = d["short"]["legs"]
    return "下跌(高点降低x%d)" % legs if legs >= 1 else "非下跌"


def _fmt_sig_long(d):
    lo = d["long"]
    parts = [lo["signal"]]
    if lo["break_dt"]:
        parts.append("突破位 %.3f(%s)" % (lo["break_lvl"], lo["break_dt"]))
    parts.append("止损前低 %s" % ("%.3f" % lo["stop"] if lo["stop"] else "-"))
    return " | ".join(parts)


def _fmt_sig_short(d):
    so = d["short"]
    parts = [so["signal"]]
    if so["break_dt"]:
        parts.append("跌破位 %.3f(%s)" % (so["break_lvl"], so["break_dt"]))
    parts.append("止损前高 %s" % ("%.3f" % so["stop"] if so["stop"] else "-"))
    return " | ".join(parts)


def render(result, top=5, mode="console"):
    lines = []
    date = result.get("date") or "-"
    intraday = result.get("intraday")
    tag = "盘中实时" if intraday else "最近完整交易日"
    head = "道氏结构+分形买卖点+资金惯性 强化扫描  [%s] 基准日:%s" % (tag, date)
    lines.append("=" * 84 if mode == "console" else "")
    lines.append(head if mode == "console" else "## %s" % head)
    if mode == "console":
        lines.append("=" * 84)

    stocks, fbull, fbear = result["stocks"], result["futures_bull"], result["futures_bear"]
    pool_label = result.get("stock_pool_label") or "换手率前50"
    fut_on = result.get("include_futures", True)

    # --- A股(做多)
    if mode == "console":
        lines.append("\n[ A股 %s 池 | 做多候选: 上涨结构+资金活跃(换手) ]" % pool_label)
    else:
        lines.append("\n### A股 做多候选(%s)" % pool_label)
    if mode == "markdown":
        lines.append("| 代码 | 名称 | 现价/收盘 | 当日% | 换手% | 道氏结构 | 做多信号 | 强度 |")
        lines.append("|---|---|---|---|---|---|---|---|")
    shown = 0
    for rec in stocks:
        d = rec["dow"]
        if d["bull"] < 40 and _sig_rank_long(d) < 3:
            continue
        meta = rec["meta"]
        label = "%s.%s" % (meta["code"], meta["mkt"])
        to = rec.get("turnover") or 0.0
        if mode == "console":
            lines.append("%-16s %-8s 现价 %8.2f %+6.2f%%  换手%5.1f%%  结构:%-16s %s  强度:%3.0f (额%.0f亿)" % (
                label, meta["name"], rec["price"], rec["pct"], to, _long_trend_label(d),
                _fmt_sig_long(d), rec["score"], rec["amount_yi"]))
        else:
            lines.append("| %s | %s | %.2f | %+.2f%% | %.1f | %s | %s | %.0f |" % (
                label, meta["name"], rec["price"], rec["pct"], to, _long_trend_label(d),
                _fmt_sig_long(d).replace("|", "\\|"), rec["score"]))
        shown += 1
        if shown >= top:
            break
    if shown == 0:
        lines.append("(本期无符合上涨结构的做多候选)" if mode == "console"
                     else "| - | (本期无) | - | - | - | - | - | - |")

    # --- 期货多头
    if fut_on:
        if mode == "console":
            lines.append("\n[ 期货 资金流入前5 | 做多: 上涨结构+资金惯性 ]")
        else:
            lines.append("\n### 期货 做多候选(资金流入榜 + 上涨结构)")
        if mode == "markdown":
            lines.append("| 代码 | 名称 | 现价/收盘 | 当日% | 资金流(亿) | 道氏结构 | 做多信号 | 强度 |")
            lines.append("|---|---|---|---|---|---|---|---|")
        shown = 0
        for rec in fbull:
            d = rec["dow"]
            if d["bull"] < 40 and _sig_rank_long(d) < 3:
                continue
            meta = rec["meta"]
            label = "%s.%s" % (meta["symbol"], meta["mkt"])
            if mode == "console":
                lines.append("%-16s %-8s 现价 %10.1f %+6.2f%%  资金:%+7.2f亿  结构:%-16s %s  强度:%3.0f" % (
                    label, meta["name"], rec["price"], rec["pct"], rec["flow_yi"],
                    _long_trend_label(d), _fmt_sig_long(d), rec["score"]))
            else:
                lines.append("| %s | %s | %.1f | %+.2f%% | %+.2f | %s | %s | %.0f |" % (
                    label, meta["name"], rec["price"], rec["pct"], rec["flow_yi"],
                    _long_trend_label(d), _fmt_sig_long(d).replace("|", "\\|"), rec["score"]))
            shown += 1
            if shown >= top:
                break
        if shown == 0:
            lines.append("(本期无符合上涨结构的资金流入品种)" if mode == "console"
                         else "| - | (本期无) | - | - | - | - | - | - |")

    # --- 期货空头(镜像, 主看高点)
    if fut_on:
        if mode == "console":
            lines.append("\n[ 期货 资金流出前5 | 做空: 下跌结构(高点降低)+资金惯性(镜像规则) ]")
        else:
            lines.append("\n### 期货 做空候选(资金流出榜 + 下跌结构)")
        if mode == "markdown":
            lines.append("| 代码 | 名称 | 现价/收盘 | 当日% | 资金流(亿) | 道氏结构 | 做空信号 | 强度 |")
            lines.append("|---|---|---|---|---|---|---|---|")
        shown = 0
        for rec in fbear:
            d = rec["dow"]
            if d["bear"] < 40 and _sig_rank_short(d) < 3:
                continue
            meta = rec["meta"]
            label = "%s.%s" % (meta["symbol"], meta["mkt"])
            if mode == "console":
                lines.append("%-16s %-8s 现价 %10.1f %+6.2f%%  资金:%+7.2f亿  结构:%-16s %s  强度:%3.0f" % (
                    label, meta["name"], rec["price"], rec["pct"], rec["flow_yi"],
                    _short_trend_label(d), _fmt_sig_short(d), rec["score"]))
            else:
                lines.append("| %s | %s | %.1f | %+.2f%% | %+.2f | %s | %s | %.0f |" % (
                    label, meta["name"], rec["price"], rec["pct"], rec["flow_yi"],
                    _short_trend_label(d), _fmt_sig_short(d).replace("|", "\\|"), rec["score"]))
            shown += 1
            if shown >= top:
                break
        if shown == 0:
            lines.append("(本期无符合下跌结构的资金流出品种)" if mode == "console"
                         else "| - | (本期无) | - | - | - | - | - | - |")

    if mode == "console":
        lines.append("\n说明: 做多=低点逐级抬高, 突破前高买/更高低点加仓, 止损前低; 做空=镜像(主看高点: 高点逐级降低, 跌破前低卖/更低高点加仓), 止损前高。")
        lines.append("A股候选池默认换手率前50(高换手=资金进出活跃); 期货多头=资金流入+上涨结构, 空头=资金流出+下跌结构。")
        lines.append("破位=盘中瞬时: 多头瞬间下穿前低->趋势结束平多; 空头瞬间上穿前高->趋势结束平空 (盘中最低/最高探测, 不依赖收盘价)。")
        lines.append("期货资金流=(今结算x今持仓-昨结算x昨持仓)x乘数x保证金率(亿元口径)。盘中A股实时价用腾讯叠加, 期货实时用新浪。")
        lines.append("数据来自公开行情接口, 仅供研究参考, 非投资建议; 下单前请人工复核K线。")
    return "\n".join(lines)



def main():
    ap = argparse.ArgumentParser(description="道氏结构+分形买卖点+资金惯性 强化扫描(支持盘中)")
    ap.add_argument("--out", choices=["console", "markdown"], default="console")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--stocks-limit", type=int, default=50)
    ap.add_argument("--stock-rank-field", choices=["volume", "amount", "turnover"], default="turnover",
                    help="A股候选池排序: turnover=换手率前N(默认, 资金进出活跃) / volume=成交量 / amount=成交额")
    ap.add_argument("--no-stocks", action="store_true")
    ap.add_argument("--no-futures", action="store_true")
    ap.add_argument("--futures-no-outflow", action="store_true")
    ap.add_argument("--intraday", action="store_true", help="盘中模式: 保留未收盘实时bar, 现价判断")
    ap.add_argument("--max-workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    cfg = {
        "stock_limit": args.stocks_limit,
        "stock_rank_field": args.stock_rank_field,
        "min_amount_yi": 0.5,
        "max_workers": args.max_workers,
        "timeout": args.timeout,
        "date": args.date,
        "no_stocks": args.no_stocks,
        "no_futures": args.no_futures,
        "futures_include_outflow": not args.futures_no_outflow,
        "futures_pool_limit": 5,
        "intraday": args.intraday,
    }
    result = build(cfg)
    sys.stdout.write(render(result, top=args.top, mode=args.out) + "\n")


if __name__ == "__main__":
    main()
