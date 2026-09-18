#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行程规划一键流水线

检索 → 分级 → 抽取 → 编排 → 出报告，全流程零 LLM 依赖。

用法：
    python pipeline.py --origin 上海 --city 绍兴 --days 5 --start 2026-10-01 --out ./out

产物（out/ 目录下）：
    graded.json        信源分级明细
    places.json        景点池
    itinerary.json     逐日行程
    报告_<出发地>到<目的地><N>日.md
    报告_<出发地>到<目的地><N>日.html

HTML 走 `md2html.py` 渲染（模块化版面 + <details> 折叠 + 内联 SVG 路线图）。
`md2html.py` 需要一个第三方包 `markdown`；**没装也能跑**，会自动退回
`report.py` 的轻量转换器（无模块导航、不折细节）——只是不好看，不会失败。

可选：
    --skip-search      复用 out/ 下已有的 graded.json，跳过检索（省时间，调参时用）
    --queries "a,b,c"  自定义查询词，覆盖默认 QUERY_PACKS
    --api-key KEY      anysearch API key（不传则匿名模式）
    --no-map           不把路线图内嵌进 HTML
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract as extract_mod
import planner as planner_mod
import report as report_mod
import research as research_mod


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(origin, city, days, start, out_dir, skip_search=False,
        queries=None, api_key=None, embed_map=True):
    os.makedirs(out_dir, exist_ok=True)

    graded_path = os.path.join(out_dir, "graded.json")
    places_path = os.path.join(out_dir, "places.json")
    itin_path = os.path.join(out_dir, "itinerary.json")
    base = f"报告_{origin}到{city}{days}日"

    # ---------- 1. 检索 + 分级 ----------
    if skip_search and os.path.exists(graded_path):
        log(f"跳过检索，复用 {graded_path}")
        with open(graded_path, encoding="utf-8") as f:
            graded = json.load(f)
    else:
        if queries:
            qs = [q.strip() for q in queries.split(",") if q.strip()]
        else:
            qs = research_mod.build_queries(city, origin)

        log(f"开始检索（{len(qs)} 条查询）")
        graded = []
        for i, q in enumerate(qs, 1):
            try:
                items = research_mod.search_and_grade(
                    q, max_results=8, api_key=api_key)
                graded.extend(items)
                log(f"  [{i}/{len(qs)}] {q[:36]}… → {len(items)} 条")
            except Exception as e:              # 单条失败不影响整体
                log(f"  [{i}/{len(qs)}] {q[:36]}… → 失败：{e}")
        with open(graded_path, "w", encoding="utf-8") as f:
            json.dump(graded, f, ensure_ascii=False, indent=2)
        c = {"A": 0, "C": 0, "D": 0}
        for g in graded:
            c[g.get("grade", "C")] = c.get(g.get("grade", "C"), 0) + 1
        log(f"检索完成：{len(graded)} 条（A{c['A']} / C{c['C']} / D{c['D']}）")

    # ---------- 2. 抽取景点 ----------
    log("抽取景点（规则引擎）")
    places = extract_mod.extract_from_graded(graded)
    with open(places_path, "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=2)
    hi = sum(1 for p in places if p.get("confidence") == "high")
    log(f"抽取完成：{len(places)} 个景点（高置信 {hi}）")

    # ---------- 3. 编排行程 ----------
    log(f"编排 {days} 天行程（规则引擎）")
    itin = planner_mod.plan_itinerary(
        places, days, start, city=city)
    with open(itin_path, "w", encoding="utf-8") as f:
        json.dump(itin, f, ensure_ascii=False, indent=2)
    n_sched = itin["stats"]["scheduled"]
    log(f"编排完成：{days} 天 / {n_sched} 个景点")
    for d in itin["days"]:
        names = " → ".join(a["name"] for a in d["attractions"])
        log(f"  D{d['day_index'] + 1} {d['date']} [{d['region']}] {names}")

    # ---------- 4. 生成报告 ----------
    log("生成报告")
    md = report_mod.build_markdown(
        city, origin, days, start, itin, graded, places)
    md_path = os.path.join(out_dir, base + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    log(f"Markdown → {md_path}")

    html_path = os.path.join(out_dir, base + ".html")
    _render_html(md_path, html_path, itin_path, origin, city,
                 embed_map=embed_map)

    log("完成")
    return {
        "graded": graded_path, "places": places_path,
        "itinerary": itin_path, "markdown": md_path, "html": html_path,
    }


def _itin_has_coords(itin_path):
    """单目的地 planner 默认不输出 lng/lat；有坐标才谈得上内嵌地图。"""
    try:
        with open(itin_path, encoding="utf-8") as f:
            itin = json.load(f)
        return any(a.get("lng") is not None and a.get("lat") is not None
                   for d in itin.get("days", []) for a in d.get("attractions", []))
    except Exception:
        return False


def _render_html(md_path, html_path, itin_path, origin, city, embed_map=True):
    """优先用 md2html（模块化版面 + 内联 SVG 地图）；缺 markdown 包则降级。

    注意 `md2html.convert()` 的 `map_itin` 要的是**已解析的 dict**，
    不是路径 —— 传错会得到 "'str' object has no attribute 'get'"，
    且异常被 md2html 内部吞掉，只表现为"报告里没有地图"。
    """
    map_itin = None
    if embed_map and _itin_has_coords(itin_path):
        try:
            with open(itin_path, encoding="utf-8") as f:
                map_itin = json.load(f)
        except Exception as e:
            log(f"  [提示] 读取 {itin_path} 失败，本次不内嵌地图：{e}")
    try:
        import md2html as md2html_mod
        md2html_mod.convert(md_path, html_path, map_itin=map_itin,
                            map_mode="svg" if map_itin else "none")
        log(f"HTML → {html_path}（模块化版面"
            + ("＋内嵌 SVG 路线图）" if map_itin else "）"))
        return
    except ImportError as e:
        log("  [降级] " + str(e).strip().splitlines()[0])
        log("         退回轻量转换器：无模块导航、细节不折叠。")
    except Exception as e:
        log(f"  [降级] md2html 渲染失败（{e}），退回轻量转换器。")

    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(report_mod.md_to_html(md, f"{origin} → {city} 行程规划"))
    log(f"HTML → {html_path}")


def main():
    ap = argparse.ArgumentParser(
        description="行程规划一键流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origin", required=True, help="出发地，如 上海")
    ap.add_argument("--city", required=True, help="目的地，如 绍兴")
    ap.add_argument("--days", type=int, required=True, help="行程天数")
    ap.add_argument("--start", required=True, help="出发日期 YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--skip-search", action="store_true",
                    help="复用 out/graded.json，跳过检索（调参时用）")
    ap.add_argument("--queries", default="", help="自定义查询词，逗号分隔")
    ap.add_argument("--api-key", default="", help="anysearch API key（可选）")
    ap.add_argument("--no-map", action="store_true",
                    help="不把路线图内嵌进 HTML（默认自动：行程有坐标就嵌）")
    args = ap.parse_args()

    run(args.origin, args.city, args.days, args.start, args.out,
        skip_search=args.skip_search,
        queries=args.queries or None,
        api_key=args.api_key or None,
        embed_map=not args.no_map)


if __name__ == "__main__":
    main()
