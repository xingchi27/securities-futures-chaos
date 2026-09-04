# -*- coding: utf-8 -*-
"""net_data.py - 新浪财经公开行情接口的只读取数工具库（仅用 Python 标准库）。

数据源（全部为互联网公开接口，不需要本地行情软件）：
  - A股代码/快照：Sina Market_Center.getHQNodeData(node=hs_a)
  - A股日K：       Sina CN_MarketDataService.getKLineData(scale=240)
  - 国内期货品种： Sina qihuohangqing.js + Market_Center.getHQFuturesData
  - 期货主力/合约日K：Sina InnerFuturesNewService.getDailyKLine

说明：脚本只读行情、不交易、不下单。新浪接口可能因网络或对方调整而不可用，
请求带重试并在失败时抛出可读错误；扫描程序会跳过单只失败标的。
"""
from __future__ import annotations

import datetime
import html
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SINA_STOCK_REF = "https://finance.sina.com.cn/"
SINA_FUTURE_REF = "https://finance.sina.com.cn/futuremarket/"

STOCK_LIST_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
STOCK_DAILY_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
)
FUT_SUBSCRIBE_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/view/js/qihuohangqing.js"
)
FUT_DISPLAY_URL = (
    "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQFuturesData"
)
FUT_DAILY_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_t=/InnerFuturesNewService.getDailyKLine"
)
FUT_SPEC_URL = "https://finance.sina.com.cn/futures/quotes/{symbol}.shtml"

DEFAULT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "securities-chaos-cache")
DOMESTIC_FUT_EXCHANGES = ("czce", "dce", "shfe", "cffex", "gfex")
MKT_BY_PREFIX = {"sh": "SHSE", "sz": "SZSE", "bj": "BJSE"}

# 新浪合约清单偶尔漏列但日K接口仍可用的连续合约，作为主连兜底。
FUT_FALLBACK_MAIN = (
    {"symbol": "T0", "name": "10年期国债期货连续", "mkt": "CFFEX"},
    {"symbol": "WR0", "name": "线材连续", "mkt": "SHFE"},
    {"symbol": "ZC0", "name": "动力煤连续", "mkt": "CZCE"},
)


# ---------------------------------------------------------------- HTTP
def http_get_text(url, encoding="utf-8", referer=SINA_STOCK_REF, timeout=20, retries=3):
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            try:
                return raw.decode(encoding or "utf-8")
            except (UnicodeDecodeError, LookupError):
                return raw.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(1.0 * attempt)
    raise RuntimeError("请求失败 %s: %r" % (url[:120], last))


def _get_json(url, encoding="utf-8", referer=SINA_STOCK_REF, timeout=20):
    return json.loads(http_get_text(url, encoding=encoding, referer=referer, timeout=timeout))


def _strip_jsonp(text):
    """把 Sina 的 var _x=([...]); 响应裁成纯 JSON。"""
    s = text.find("([")
    e = text.rfind("])")
    if s < 0 or e <= s + 1:
        raise RuntimeError("接口返回不是预期 JSONP 格式")
    return text[s + 1:e + 1]


# ---------------------------------------------------------------- A股
def _stock_code_ok(prefix, code):
    if prefix == "sh":
        return code.startswith(("600", "601", "603", "605", "688"))
    if prefix == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if prefix == "bj":
        return code.startswith(("4", "8", "9")) and len(code) == 6
    return False


def load_stock_universe(stock_limit=None, min_amount_yi=0.0,
                        markets=("SHSE", "SZSE", "BJSE"),
                        sort_by="volume",
                        timeout=20):
    """按新浪快照排序取 A 股清单（含成交量、成交额、换手率）。

    sort_by="volume" 按成交量(股)降序，sort_by="amount" 按成交额(元)降序，
    sort_by="turnover" 按换手率(%)降序；
    stock_limit 为 None 时扫描全部；min_amount_yi>0 时过滤成交额不足的股票。
    """
    allowed_markets = set(markets)
    sort_field = {"volume": "volume", "amount": "amount", "turnover": "turnoverratio"}.get(sort_by, "volume")
    out = []
    page = 1
    page_size = 100
    while True:
        params = urllib.parse.urlencode({
            "page": page,
            "num": page_size,
            "sort": sort_field,
            "asc": 0,
            "node": "hs_a",
        })
        rows = _get_json(STOCK_LIST_URL + "?" + params, timeout=timeout)
        if not rows:
            break
        for row in rows:
            symbol = row.get("symbol") or ""
            prefix = symbol[:2]
            code = row.get("code") or ""
            mkt = MKT_BY_PREFIX.get(prefix)
            if not mkt or mkt not in allowed_markets or not _stock_code_ok(prefix, code):
                continue
            amount = row.get("amount") or 0
            try:
                amount_yi = float(amount) / 1e8
            except (TypeError, ValueError):
                amount_yi = 0.0
            if sort_field == "amount" and amount_yi < min_amount_yi:
                return out
            if amount_yi < min_amount_yi:
                continue
            try:
                volume = float(row.get("volume") or 0)
            except (TypeError, ValueError):
                volume = 0.0
            try:
                turnover = float(row.get("turnoverratio") or 0)
            except (TypeError, ValueError):
                turnover = 0.0
            out.append({
                "symbol": symbol,
                "code": code,
                "name": row.get("name") or code,
                "mkt": mkt,
                "amount_yi": amount_yi,
                "volume": volume,
                "turnover": turnover,
            })
            if stock_limit and len(out) >= stock_limit:
                return out
        if len(rows) < page_size:
            break
        page += 1
    return out


def parse_stock_daily(text):
    arr = json.loads(_strip_jsonp(text))
    rows = []
    for x in arr:
        try:
            rows.append({
                "date": x["day"],
                "open": float(x["open"]),
                "close": float(x["close"]),
                "high": float(x["high"]),
                "low": float(x["low"]),
                "volume": float(x["volume"]),
                "amount": 0.0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_stock_daily(symbol, timeout=20, datalen=300):
    params = urllib.parse.urlencode({
        "symbol": symbol,
        "scale": 240,
        "ma": "no",
        "datalen": datalen,
    })
    text = http_get_text(STOCK_DAILY_URL + "?" + params, timeout=timeout)
    return parse_stock_daily(text)


def fetch_stock_daily_many(items, max_workers=8, timeout=20):
    """并发拉取多只 A 股日K。返回 ([(item, rows)], 失败数)。"""
    results = []
    errors = 0

    def one(it):
        return it, fetch_stock_daily(it["symbol"], timeout=timeout)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(one, it) for it in items]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception:
                errors += 1
    return results, errors


# ---------------------------------------------------------------- 期货
def _subscribe_nodes(exchanges=DOMESTIC_FUT_EXCHANGES):
    """读取新浪期货行情 JS 中 5 个国内市场的品种节点。"""
    text = http_get_text(FUT_SUBSCRIBE_URL, encoding="gbk", referer=SINA_FUTURE_REF)
    starts = [(m.group(1), m.start(), m.end()) for m in re.finditer(
        r"^\s*([A-Za-z_]+)\s*:\s*\[", text, re.M
    ) if m.group(1) in set(exchanges)]
    if not starts:
        raise RuntimeError("无法从新浪期货行情 JS 中识别品种列表")
    nodes = []
    for idx, (name, start, end) in enumerate(starts):
        seg_end = starts[idx + 1][1] if idx + 1 < len(starts) else text.rfind("};")
        if seg_end < 0:
            seg_end = len(text)
        seg = text[end:seg_end]
        found = re.findall(r"\[\s*'[^']*'\s*,\s*'([^']+)'", seg)
        nodes.extend((name, node) for node in found)
    return nodes


def _display_contracts(node, timeout=20):
    params = urllib.parse.urlencode({
        "page": 1,
        "num": 200,
        "sort": "position",
        "asc": 0,
        "node": node,
        "base": "futures",
    })
    return _get_json(FUT_DISPLAY_URL + "?" + params,
                     referer=SINA_FUTURE_REF, timeout=timeout)


def load_futures_universe(mode="main", timeout=20, max_workers=8):
    """返回期货标的清单。main=每个品种的主力连续；all=所有当前挂牌合约。"""
    exchange_nodes = _subscribe_nodes()
    metas = []

    def one(node):
        out = []
        try:
            rows = _display_contracts(node, timeout=timeout)
        except Exception:
            return out
        for row in rows:
            symbol = (row.get("symbol") or "").upper()
            name = row.get("name") or symbol
            mkt = (row.get("exchange") or "").upper()
            if mode == "main":
                if re.fullmatch(r"[A-Z]{1,5}0", symbol):
                    out.append({"symbol": symbol, "name": name, "mkt": mkt})
            elif re.fullmatch(r"[A-Z]{1,5}\d{3,4}", symbol):
                out.append({"symbol": symbol, "name": name, "mkt": mkt})
        return out

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(one, node) for _, node in exchange_nodes]
        for fut in as_completed(futs):
            metas.extend(fut.result())
    seen = set()
    uniq = []
    for it in metas:
        key = it["symbol"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    uniq.sort(key=lambda x: x["symbol"])
    if mode == "main":
        have = {x["symbol"] for x in uniq}
        for fallback in FUT_FALLBACK_MAIN:
            if fallback["symbol"] not in have:
                uniq.append(fallback)
        uniq.sort(key=lambda x: x["symbol"])
    return uniq


def parse_future_daily(text):
    arr = json.loads(_strip_jsonp(text))
    rows = []
    for x in arr:
        try:
            date = x.get("d") or ""
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            try:
                settle = float(x["s"]) if x.get("s") not in (None, "") else 0.0
            except (TypeError, ValueError):
                settle = 0.0
            rows.append({
                "date": date,
                "open": float(x["o"]),
                "high": float(x["h"]),
                "low": float(x["l"]),
                "close": float(x["c"]),
                "volume": float(x["v"]),
                "oi": float(x["p"]) if x.get("p") not in (None, "", "0") else 0.0,
                "settle": settle if settle else float(x["c"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def fetch_future_daily(symbol, timeout=20):
    params = urllib.parse.urlencode({"symbol": symbol})
    text = http_get_text(FUT_DAILY_URL + "?" + params,
                         referer=SINA_FUTURE_REF, timeout=timeout)
    return parse_future_daily(text)


def fetch_future_daily_many(items, max_workers=8, timeout=20):
    results = []
    errors = 0

    def one(it):
        return it, fetch_future_daily(it["symbol"], timeout=timeout)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(one, it) for it in items]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception:
                errors += 1
    return results, errors


# ---------------------------------------------------------------- 合约规格
def _clean_html_field(raw):
    if raw is None:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ").strip()


def _find_spec_field(text, label):
    m = re.search(label + r"\s*</th>\s*<td>(.*?)</td>", text, re.S)
    return _clean_html_field(m.group(1)) if m else ""


def _spec_multiplier(symbol, unit, quote_unit):
    s = (symbol or "").upper().rstrip("0123456789")
    if s in ("IF", "IH"):
        return 300.0
    if s in ("IC", "IM"):
        return 200.0
    if s == "EC":
        return 50.0
    if s in ("T", "TF", "TS"):
        return 10000.0
    if "每点" in unit and "300" in unit:
        return 300.0
    if "每点" in unit and "200" in unit:
        return 200.0
    if "100万元" in unit or "面值" in unit:
        return 10000.0

    m_tons = re.search(r"(\d+(?:\.\d+)?)\s*吨/手", unit)
    m_kgs = re.search(r"(\d+(?:\.\d+)?)\s*千克/手", unit)
    m_grams = re.search(r"(\d+(?:\.\d+)?)\s*克/手", unit)
    m_barrels = re.search(r"(\d+(?:\.\d+)?)\s*桶/手", unit)
    m_m3 = re.search(r"(\d+(?:\.\d+)?)\s*立方米/手", unit)
    m_sheets = re.search(r"(\d+(?:\.\d+)?)\s*张/手", unit)
    if m_tons:
        tons = float(m_tons.group(1))
        if "500" in quote_unit and "千克" in quote_unit:
            return tons * 1000 / 500.0
        return tons
    if m_kgs:
        return float(m_kgs.group(1))
    if m_grams:
        return float(m_grams.group(1))
    if m_barrels:
        return float(m_barrels.group(1))
    if m_m3:
        return float(m_m3.group(1))
    if m_sheets:
        return float(m_sheets.group(1))
    # 页面写“每手X”或其它简写时，取第一个数字；拿不到则按1处理。
    m_num = re.search(r"(\d+(?:\.\d+)?)", unit)
    return float(m_num.group(1)) if m_num else 1.0


def _spec_margin_rate(raw):
    if not raw:
        return 0.0
    m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
    if m_pct:
        return float(m_pct.group(1))
    nums = re.findall(r"\d+(?:\.\d+)?", raw)
    if not nums:
        return 0.0
    val = float(nums[0])
    return val * 100.0 if val <= 1.0 else val


def fetch_future_spec(symbol, timeout=20):
    url = FUT_SPEC_URL.format(symbol=symbol)
    text = http_get_text(url, encoding="gbk", referer=SINA_FUTURE_REF, timeout=timeout)
    unit = _find_spec_field(text, "交易单位")
    quote_unit = _find_spec_field(text, "报价单位")
    margin = _find_spec_field(text, "最低交易保证金")
    multiplier = _spec_multiplier(symbol, unit, quote_unit)
    margin_rate = _spec_margin_rate(margin)
    # 新浪EC资料页不返回完整乘数/保证金字段，按交易所标准合约补全。
    if (symbol or "").upper().startswith("EC"):
        multiplier = 50.0
        margin_rate = margin_rate or 12.0
    return {
        "multiplier": multiplier,
        "margin_rate": margin_rate,
    }


def fetch_futures_specs(items, max_workers=8, timeout=20):
    specs = {}

    def one(it):
        return it["symbol"], fetch_future_spec(it["symbol"], timeout=timeout)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = [pool.submit(one, it) for it in items]
        for fut in as_completed(futs):
            try:
                sym, spec = fut.result()
                specs[sym] = spec
            except Exception:
                pass
    return specs


# ---------------------------------------------------------------- 完整日线判定
def drop_incomplete(rows, now=None):
    """去掉“尚未收盘”的最后一根网络日K（按本地北京时间 15:00 判断）。"""
    if not rows:
        return []
    if now is None:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    try:
        last_date = datetime.datetime.strptime(rows[-1]["date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return rows
    is_trade_day = now.weekday() < 5
    if is_trade_day and now.hour < 15 and last_date == now.date():
        return rows[:-1]
    return rows


def latest_date(rows):
    return rows[-1]["date"] if rows else None
