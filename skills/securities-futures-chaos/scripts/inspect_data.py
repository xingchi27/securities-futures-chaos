# -*- coding: utf-8 -*-
"""inspect_data.py - 检查互联网行情源连通性与数据覆盖情况。

用法: python inspect_data.py
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_data as net  # noqa: E402


def _sample(rows, label):
    if not rows:
        print("   %s: 无K线" % label)
        return
    print("   %s: %d 根, 最新=%s, 最新收=%.3f" % (
        label, len(rows), rows[-1]["date"], rows[-1]["close"]))


def main():
    ap = argparse.ArgumentParser(description="互联网行情源健康检查")
    ap.add_argument("--stocks-limit", type=int, default=100,
                    help="A股清单只拉前 N 只用于快速检查")
    ap.add_argument("--stock-rank-field", choices=["volume", "amount"], default="volume",
                    help="A股清单排序字段")
    ap.add_argument("--max-workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20)
    args = ap.parse_args()

    ok = True
    print("== 数据源: 新浪财经公开行情接口 ==")
    try:
        stocks = net.load_stock_universe(
            stock_limit=args.stocks_limit,
            min_amount_yi=0.0,
            markets=("SHSE", "SZSE", "BJSE"),
            sort_by=args.stock_rank_field,
            timeout=args.timeout,
        )
        print("A股清单: OK, 前 %d 只样例:" % len(stocks))
        for it in stocks[:5]:
            print("   %s %s %s 成交量=%.3f亿股 成交额=%.2f亿" % (
                it["symbol"], it["name"], it["mkt"],
                it.get("volume", 0.0) / 1e8, it["amount_yi"]))
    except Exception as exc:
        ok = False
        print("A股清单: 失败 (%r)" % exc)
        stocks = []

    if stocks:
        try:
            rows = net.fetch_stock_daily(stocks[0]["symbol"], timeout=args.timeout)
            _sample(rows, "A股日K样例 %s %s" % (stocks[0]["symbol"], stocks[0]["name"]))
        except Exception as exc:
            ok = False
            print("A股日K样例: 失败 (%r)" % exc)

    try:
        futs = net.load_futures_universe(
            mode="main", timeout=args.timeout, max_workers=args.max_workers)
        print("期货主力连续清单: OK, 共 %d 个品种" % len(futs))
        for it in futs[:8]:
            print("   %s %s %s" % (it["symbol"], it["name"], it["mkt"]))
    except Exception as exc:
        ok = False
        print("期货主力连续清单: 失败 (%r)" % exc)
        futs = []

    if futs:
        try:
            rows = net.fetch_future_daily(futs[0]["symbol"], timeout=args.timeout)
            _sample(rows, "期货日K样例 %s %s" % (futs[0]["symbol"], futs[0]["name"]))
        except Exception as exc:
            ok = False
            print("期货日K样例: 失败 (%r)" % exc)
        try:
            spec = net.fetch_future_spec(futs[0]["symbol"], timeout=args.timeout)
            print("期货资金流参数样例 %s: 乘数=%.2f 保证金率=%.2f%%" % (
                futs[0]["symbol"], spec["multiplier"], spec["margin_rate"]))
        except Exception as exc:
            ok = False
            print("期货资金流参数样例: 失败 (%r)" % exc)

    print()
    print("健康状态: " + ("OK" if ok else "有失败项, 请检查网络后重试"))
    print("提示: 默认A股候选池为成交量前50名；")
    print("可用 --stock-rank-field amount 切换为成交额，或用 --stocks-limit 扩大候选池。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
