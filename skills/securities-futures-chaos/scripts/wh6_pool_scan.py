# -*- coding: utf-8 -*-
"""WH6 加权资金池 -> 道氏结构+分形买卖点 扫描
数据源: 候选池来自文华WH6“品种加权排名”页(资金流入榜/流出榜, OCR读取),
        趋势K线用新浪“连续”日线(免费、全品种、覆盖足够历史, 收盘后/盘中均可)。
说明: 加权合约本身新浪无免费源; WH6榜给出“哪些品种资金进出最猛”(候选池权威),
      本脚本负责对这些品种做趋势/买点/止损的结构化分析。
用法:
  python wh6_pool_scan.py --inflow "原油,BR橡胶,橡胶,20号胶,沪铜" --outflow "燃料油,乙二醇,豆粕,pvc,豆油" --out markdown
  python wh6_pool_scan.py --pool-file wh6_pool.txt          # 每行: inflow<TAB>原油
"""
import argparse
import io
import os
import sys

import net_data as net
import scan_dow_signal as sd

# WH6加权品种名(去掉“加权”后缀) -> (新浪连续symbol, 市场)
WH6_TO_SINA = {
    # 大商所
    "豆一": ("A0", "DCE"), "豆二": ("B0", "DCE"), "豆粕": ("M0", "DCE"),
    "豆油": ("Y0", "DCE"), "棕榈油": ("P0", "DCE"), "玉米": ("C0", "DCE"),
    "玉米淀粉": ("CS0", "DCE"), "淀粉": ("CS0", "DCE"), "鸡蛋": ("JD0", "DCE"),
    "生猪": ("LH0", "DCE"), "焦炭": ("J0", "DCE"), "焦煤": ("JM0", "DCE"),
    "铁矿石": ("I0", "DCE"), "塑料": ("L0", "DCE"), "聚乙烯": ("L0", "DCE"),
    "PVC": ("V0", "DCE"), "乙二醇": ("EG0", "DCE"), "苯乙烯": ("EB0", "DCE"),
    "聚丙烯": ("PP0", "DCE"), "液化石油气": ("PG0", "DCE"), "胶合板": ("BB0", "DCE"),
    "纤维板": ("FB0", "DCE"), "粳米": ("RR0", "DCE"), "原木": ("LG0", "DCE"),
    "纯苯": ("BZ0", "DCE"),
    # 上期所
    "沪铜": ("CU0", "SHFE"), "铜": ("CU0", "SHFE"), "沪铝": ("AL0", "SHFE"),
    "铝": ("AL0", "SHFE"), "沪锌": ("ZN0", "SHFE"), "锌": ("ZN0", "SHFE"),
    "沪铅": ("PB0", "SHFE"), "铅": ("PB0", "SHFE"), "沪镍": ("NI0", "SHFE"),
    "镍": ("NI0", "SHFE"), "沪锡": ("SN0", "SHFE"), "锡": ("SN0", "SHFE"),
    "黄金": ("AU0", "SHFE"), "沪金": ("AU0", "SHFE"), "白银": ("AG0", "SHFE"),
    "沪银": ("AG0", "SHFE"), "螺纹钢": ("RB0", "SHFE"), "螺纹": ("RB0", "SHFE"),
    "热卷": ("HC0", "SHFE"), "热轧卷板": ("HC0", "SHFE"), "线材": ("WR0", "SHFE"),
    "不锈钢": ("SS0", "SHFE"), "燃料油": ("FU0", "SHFE"), "沥青": ("BU0", "SHFE"),
    "天然橡胶": ("RU0", "SHFE"), "橡胶": ("RU0", "SHFE"), "BR橡胶": ("BR0", "SHFE"),
    "纸浆": ("SP0", "SHFE"), "氧化铝": ("AO0", "SHFE"), "铸造铝合金": ("AD0", "SHFE"),
    "胶版印刷纸": ("OP0", "SHFE"), "印刷纸": ("OP0", "SHFE"),
    # 能源中心
    "原油": ("SC0", "INE"), "上海原油": ("SC0", "INE"), "20号胶": ("NR0", "INE"),
    "低硫燃料油": ("LU0", "INE"), "国际铜": ("BC0", "INE"),
    # 郑商所
    "白糖": ("SR0", "CZCE"), "棉花": ("CF0", "CZCE"), "菜籽油": ("OI0", "CZCE"),
    "菜油": ("OI0", "CZCE"), "菜籽粕": ("RM0", "CZCE"), "菜粕": ("RM0", "CZCE"),
    "PTA": ("TA0", "CZCE"), "甲醇": ("MA0", "CZCE"), "玻璃": ("FG0", "CZCE"),
    "纯碱": ("SA0", "CZCE"), "烧碱": ("SH0", "CZCE"), "硅铁": ("SF0", "CZCE"),
    "锰硅": ("SM0", "CZCE"), "苹果": ("AP0", "CZCE"), "红枣": ("CJ0", "CZCE"),
    "尿素": ("UR0", "CZCE"), "棉纱": ("CY0", "CZCE"), "花生": ("PK0", "CZCE"),
    "短纤": ("PF0", "CZCE"), "动力煤": ("ZC0", "CZCE"), "强麦": ("WH0", "CZCE"),
    "早籼稻": ("RI0", "CZCE"), "晚籼稻": ("LR0", "CZCE"), "粳稻": ("JR0", "CZCE"),
    "菜籽": ("RS0", "CZCE"), "瓶片": ("PR0", "CZCE"), "对二甲苯": ("PX0", "CZCE"),
    "丙烯": ("PL0", "CZCE"), "PX": ("PX0", "CZCE"),
    # 广期所
    "碳酸锂": ("LC0", "GFEX"), "工业硅": ("SI0", "GFEX"), "多晶硅": ("PS0", "GFEX"),
    "铂": ("PT0", "GFEX"), "钯": ("PD0", "GFEX"),
    # 中金所(加权/股指)
    "沪深300": ("IF0", "CFFEX"), "上证50": ("IH0", "CFFEX"),
    "中证500": ("IC0", "CFFEX"), "中证1000": ("IM0", "CFFEX"),
    "10年期国债": ("T0", "CFFEX"), "5年期国债": ("TF0", "CFFEX"),
    "2年期国债": ("TS0", "CFFEX"),
}


def resolve(name):
    n = (name or "").strip()
    for suf in ("加权", "连续", "主力", "指数"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    n = n.strip()
    hit = WH6_TO_SINA.get(n)
    if hit:
        return hit
    # 拼音/别名小写兜底: pvc->PVC
    if n.lower() == "pvc":
        return ("V0", "DCE")
    return None


def load_pool_file(path):
    inflow, outflow = [], []
    with io.open(path, "r", encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.replace("，", ",").split("\t")
            if len(parts) == 2:
                side, nm = parts
            else:
                # 逗号分隔: inflow,原油
                pp = ln.replace("，", ",").split(",")
                if len(pp) == 2 and pp[0].strip() in ("inflow", "outflow"):
                    side, nm = pp[0].strip(), pp[1].strip()
                else:
                    continue
            nm = nm.strip()
            if side == "inflow":
                inflow.append(nm)
            elif side == "outflow":
                outflow.append(nm)
    return inflow, outflow


def main():
    ap = argparse.ArgumentParser(description="WH6加权资金池 -> 道氏/分形扫描")
    ap.add_argument("--inflow", default="", help="WH6资金流入榜品种名,逗号分隔(可带“加权”)")
    ap.add_argument("--outflow", default="", help="WH6资金流出榜品种名,逗号分隔")
    ap.add_argument("--pool-file", default=None, help="池文件: 每行 inflow<TAB>品种")
    ap.add_argument("--out", choices=["console", "markdown"], default="console")
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--max-workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    inflow, outflow = [], []
    if args.pool_file:
        inflow, outflow = load_pool_file(args.pool_file)
    if args.inflow:
        inflow = [x for x in args.inflow.replace("，", ",").split(",") if x.strip()]
    if args.outflow:
        outflow = [x for x in args.outflow.replace("，", ",").split(",") if x.strip()]

    def to_meta(pool):
        metas, bad = [], []
        for nm in pool:
            hit = resolve(nm)
            if not hit:
                bad.append(nm)
                continue
            metas.append({"symbol": hit[0], "mkt": hit[1], "name": nm, "wh6_name": nm})
        return metas, bad

    mi, bad_i = to_meta(inflow)
    mo, bad_o = to_meta(outflow)
    bad = bad_i + bad_o
    for b in bad:
        sys.stderr.write("!! 未识别WH6品种名: %s\n" % b)

    intraday = False
    rows_by_key = {}
    err = 0
    for meta in mi + mo:
        key = meta["symbol"]
        if key in rows_by_key:
            continue
        try:
            rows = net.fetch_future_daily(key, timeout=args.timeout)
        except Exception:
            rows = []
        rows = sd._slice(rows, args.date, intraday)
        rows_by_key[key] = rows
        if len(rows) < 80:
            err += 1

    def score_pool(metas, want_long):
        out = []
        for meta in metas:
            rows = rows_by_key.get(meta["symbol"], [])
            if len(rows) < 80:
                continue
            raw, oi, settle = sd._flow_of(rows)
            a = sd.dow_analyze(rows, intraday=intraday)
            if not a.get("ok"):
                continue
            rec = {"meta": meta, "raw": raw, "oi": oi, "settle": settle,
                   "price": a["price"], "pct": a["pct"], "date": a["date"], "dow": a}
            out.append(rec)
        specs = net.fetch_futures_specs([x["meta"] for x in out], max_workers=args.max_workers,
                                        timeout=args.timeout)
        for rec in out:
            sp = specs.get(rec["meta"]["symbol"], {})
            mult = sp.get("multiplier") or 1.0
            mr = sp.get("margin_rate") or 1.0
            rec["flow_yi"] = rec["raw"] * mult * (mr / 100.0) / 1e8
            rec["score"] = (rec["dow"]["bull"] if want_long else rec["dow"]["bear"]) + \
                           (12 if (rec["flow_yi"] > 0 if want_long else rec["flow_yi"] < 0) else -8)
        rk = sd._sig_rank_long if want_long else sd._sig_rank_short
        out.sort(key=lambda x: (rk(x["dow"]), x["score"]), reverse=True)
        return out

    bull = score_pool(mi, True)
    bear = score_pool(mo, False)
    date = None
    for rec in bull + bear:
        if rec["date"]:
            date = rec["date"]
            break

    # ---- 渲染 ----
    lines = []
    head = "道氏结构+分形买卖点 强化扫描 [WH6加权资金池] 基准日:%s" % (date or "-")
    if args.out == "markdown":
        lines.append("## %s" % head)
        lines.append("")
        lines.append("> 候选池来源: 文华WH6『品种加权排名』资金流入榜/流出榜(加权, 权威); 趋势K线=新浪连续(近似,免费).")
        lines.append("> 资金流(亿)为本脚本按持仓变动估算, 仅排序参考.")
        lines.append("")
    else:
        lines.append("=" * 84)
        lines.append(head)
        lines.append("=" * 84)

    def table(title, recs, kind):
        nonlocal lines
        if args.out == "markdown":
            lines.append("### %s" % title)
            if kind == "long":
                lines.append("| 品种 | 代码 | 现价/收盘 | 当日% | 资金流(亿,估) | 道氏结构 | 做多信号 | 强度 |")
                lines.append("|---|---|---|---|---|---|---|---|")
            else:
                lines.append("| 品种 | 代码 | 现价/收盘 | 当日% | 资金流(亿,估) | 道氏结构 | 做空信号 | 强度 |")
                lines.append("|---|---|---|---|---|---|---|---|")
        else:
            lines.append("\n[ %s ]" % title)
        shown = 0
        for rec in recs:
            d = rec["dow"]
            meta = rec["meta"]
            sym = "%s.%s" % (meta["symbol"], meta["mkt"])
            if kind == "long":
                label = sd._long_trend_label(d)
                sig = sd._fmt_sig_long(d).replace("|", "\\|")
                if d["bull"] < 40 and sd._sig_rank_long(d) < 3:
                    continue
            else:
                label = sd._short_trend_label(d)
                sig = sd._fmt_sig_short(d).replace("|", "\\|")
                if d["bear"] < 40 and sd._sig_rank_short(d) < 3:
                    continue
            flow = rec["flow_yi"]
            if args.out == "markdown":
                lines.append("| %s加权 | %s | %.2f | %+.2f%% | %+.2f | %s | %s | %.0f |" % (
                    meta["wh6_name"], sym, rec["price"], rec["pct"], flow, label, sig, rec["score"]))
            else:
                lines.append("%-8s加权 %-10s 现价 %10.2f %+6.2f%%  资金 %+7.2f亿  %s  %s  强度:%3.0f" % (
                    meta["wh6_name"], sym, rec["price"], rec["pct"], flow, label, sig, rec["score"]))
            shown += 1
            if shown >= args.top:
                break
        if shown == 0:
            lines.append("(池内品种无有效上涨/下跌结构或数据不足)" if args.out == "console"
                         else "| - | - | - | - | - | - | - | - |")

    table("期货 做多候选 (WH6加权资金流入榜 -> 上涨结构)", bull, "long")
    table("期货 做空候选 (WH6加权资金流出榜 -> 下跌结构)", bear, "short")
    if args.out == "markdown":
        lines.append("")
    txt = "\n".join(lines)
    if args.out == "markdown":
        outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "outputs")
        outdir = os.path.abspath(outdir)
        if not os.path.isdir(outdir):
            outdir = os.getcwd()
        fname = os.path.join(outdir, "wh6_pool_signal_%s.md" % (date or "na"))
        with io.open(fname, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        sys.stdout.write("已写入: %s\n" % fname)
    else:
        sys.stdout.write(txt + "\n")


if __name__ == "__main__":
    main()
