# -*- coding: utf-8 -*-
"""把 WH6『品种加权排名』页面 OCR 文本 -> 资金流入/流出 候选池"""
import io
import sys

CORRECT = {"隹煤": "焦煤", "隹炭": "焦炭", "較": "", "较": "", "﹒": "",
           "＼": "", "泸": "沪", "栒": "沪", "祊": ""}


def parse_words(path):
    rows = {}
    for line in io.open(path, encoding="utf-8-sig"):
        line = line.rstrip("\n")
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        text = parts[2].strip()
        if not text or text in ("资", "金"):
            continue
        if y < 150:                      # 跳过标题/工具栏
            continue
        if not (1300 <= x <= 1520):      # 只取右侧名称列
            continue
        band = y // 6 * 6
        rows.setdefault(band, []).append((x, text))
    lines = []
    for band in sorted(rows):
        toks = sorted(rows[band])
        y = band + 3
        txt = "".join(t for _, t in toks)
        lines.append((y, txt))
    return lines


def clean(name):
    n = name.replace(" ", "")
    for k, v in CORRECT.items():
        n = n.replace(k, v)
    for suf in ("加权", "连续"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        sys.stderr.write("usage: wh6_ocr_pool.py <words.txt> [pool_out.txt]\n")
        return 1
    out = sys.argv[2] if len(sys.argv) > 2 else None
    lines = parse_words(path)
    inflow, outflow = [], []
    for y, txt in lines:
        c = clean(txt)
        if len(c) < 2:
            continue
        if y < 700:
            inflow.append(c)
        else:
            outflow.append(c)

    def uniq(seq):
        seen, o = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x)
                o.append(x)
        return o

    inflow, outflow = uniq(inflow), uniq(outflow)
    if out:
        with io.open(out, "w", encoding="utf-8") as f:
            for nm in inflow:
                f.write("inflow\t%s\n" % nm)
            for nm in outflow:
                f.write("outflow\t%s\n" % nm)
    sys.stdout.write("inflow(%d): %s\noutflow(%d): %s\n" % (
        len(inflow), ",".join(inflow), len(outflow), ",".join(outflow)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
