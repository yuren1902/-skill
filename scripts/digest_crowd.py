# -*- coding: utf-8 -*-
"""从 crowd/graded.json 提炼"拥挤度相关句子"

拥挤度信息散落在长篇攻略摘要里。整篇读不动，所以：
  1. 只取 A / U 级（A=权威媒体，U=UGC 具体笔记页）
  2. 把摘要按句切开，只保留含拥挤/错峰关键词的**句子**
  3. 按景点分组，标出平台与来源

产出 crowd_digest.md，供人工研判后写 crowd_notes.json。
"""
import json
import os
import re
import sys

SRC = os.path.dirname(os.path.abspath(__file__))

# 拥挤度信号词（出现任一即保留该句）
KEYS = [
    "人多", "排队", "排长队", "拥堵", "堵车", "挤", "爆满", "人满",
    "错峰", "避开", "早点", "早去", "早上", "几点", "人少", "清静",
    "停车", "车位", "限流", "预约", "峰值", "高峰", "旺季", "国庆",
    "小长假", "提前", "最佳时间", "冷门", "淡季", "周几", "周末",
    "游客", "人次", "建议", "耗时", "一天", "小时",
]

# 明确无关的信号（出现则丢弃该句）
DROP = ["招聘", "考试", "招标", "楼盘", "房价", "股价", "汽车报价"]

SPLIT = re.compile(r"[。！？；\n]")


def sentences(text):
    out = []
    for s in SPLIT.split(text or ""):
        s = s.strip()
        if len(s) < 8:
            continue
        if any(d in s for d in DROP):
            continue
        if any(k in s for k in KEYS):
            out.append(s)
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="把拥挤度检索结果提炼成可读句子（供人工写 crowd_notes.json）")
    ap.add_argument("--graded", default=os.path.join(SRC, "crowd", "graded.json"),
                    help="crowd_research.py 产出的 graded.json 路径")
    ap.add_argument("--out", default=os.path.join(SRC, "crowd_digest.md"),
                    help="输出 Markdown 路径")
    a = ap.parse_args()

    gpath = a.graded
    if not os.path.exists(gpath):
        raise SystemExit(
            "找不到 %s\n"
            "  请先跑拥挤度检索层：\n"
            "    python scripts/crowd_research.py --spots \"景点A,景点B\" \\\n"
            "        --query-city 城市 --out ./crowd\n"
            "  或用 --graded 指定已有的 graded.json。" % gpath)
    with open(gpath, encoding="utf-8") as f:
        data = json.load(f)

    by_spot = {}
    for x in data:
        if x.get("grade") not in ("A", "U"):
            continue
        spot = x.get("spot") or "?"
        seen = set()
        for s in sentences((x.get("title", "") or "") + "。" +
                           (x.get("summary", "") or "")):
            if s in seen:
                continue
            seen.add(s)
            by_spot.setdefault(spot, []).append({
                "g": x["grade"],
                "plat": x.get("platform") or "",
                "url": x.get("url", ""),
                "title": (x.get("title") or "")[:60],
                "s": s[:220],
            })

    out = a.out
    L = []
    total = 0
    for spot in sorted(by_spot, key=lambda k: -len(by_spot[k])):
        items = by_spot[spot]
        total += len(items)
        L.append("\n## %s（%d 句）\n" % (spot, len(items)))
        for it in items:
            tag = it["plat"] or it["g"]
            L.append("- [%s] %s  \n  `<%s>`" % (tag, it["s"], it["url"]))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("→ %s（%d 个景点，%d 句）" % (out, len(by_spot), total))
    for spot in sorted(by_spot, key=lambda k: -len(by_spot[k])):
        print("   %-14s %4d 句" % (spot, len(by_spot[spot])))


if __name__ == "__main__":
    main()
