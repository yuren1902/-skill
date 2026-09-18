# -*- coding: utf-8 -*-
"""
report.py — 报告生成器（Markdown；模块化版面 + <details> 折叠）

零 LLM 依赖：全部用模板 + 规则填充。

⚠ **本文件的叙述文字是「上海 → 绍兴」那次行程调过的**：
城际交通车次（G7541…）、到站说明（绍兴站 / 绍兴北站）、优惠政策、
"古城人文 + 水乡 walk" 基调等段落是写死的。**换目的地必须替换这些段落。**

结构层是通用的：`build_markdown()` 用 `<!--MODULE:名称:图标:简介-->` 切模块
（概览 / 详细行程 / 城际交通 / 预约与限流 / 备选地点 / 费用 / 行前与风险 / 信源与核查），
长表与信源清单折进 `<details>` —— 遵循"结论入正文，论据进折叠"。
想补天气 / 路线地图 / 拥挤度模块，把对应层的产物按同样格式插进来即可。

⚠ `md_to_html()` 是早期轻量转换器（无模块导航、不支持细节折叠）。
`pipeline.py` 已改为调用 `md2html.py`；保留本函数只为兼容旧调用。

用法：
  python report.py --itinerary ./data/itinerary.json --graded ./data/graded.json \
                   --city 绍兴 --origin 上海 --out ./data/report.md
"""
import argparse
import json
import os
import re
import sys
from datetime import date


def load(path):
    return json.load(open(path, encoding="utf-8"))


def summarize_sources(graded):
    """信源统计"""
    from collections import Counter, defaultdict
    c = Counter(x["grade"] for x in graded)
    by_grade = defaultdict(list)
    for x in graded:
        by_grade[x["grade"]].append(x)
    return c, by_grade


# 行程外地点：客源地/中转城市/邻市景区/泛称与错字变体
#   （与 planner.OFFROUTE_EXACT 同源，此处独立声明以便 report 可单独运行）
OFFROUTE = {
    "西湖", "杭州西湖", "西塘古镇", "乌镇", "周庄", "南浔古镇",
    "上海虹桥", "虹桥", "上海", "杭州", "杭州东", "绍兴北站",
    "黃酒博物馆", "黄酒博物馆", "绍兴名城景区", "墨韵稽山",
}


# 名称变体归并：子串关系的重复项只保留更规范的一条
VARIANT_KEEP = {
    "柯岩风景区": ["绍兴柯岩", "柯岩"],
    "东湖": ["东湖景区"],
    "兰亭": ["兰亭景区"],
    "鲁迅故里": ["鲁迅故里步行街"],
}


def clean_places(places, city=""):
    """过滤报告层不应出现的地点（噪声 / 行程外 / 名称变体）"""
    drop = set()
    for keep, variants in VARIANT_KEEP.items():
        names = {p.get("name", "") for p in places}
        if keep in names:
            drop.update(v for v in variants if v in names)

    out, seen = [], set()
    for p in places:
        n = p.get("name", "").strip()
        if not n or n in OFFROUTE or n in drop:
            continue
        if p.get("type") in ("T", "A"):
            continue
        # 同名去重（保留信息更全的那条）
        if n in seen:
            continue
        seen.add(n)
        out.append(p)
    return out


def build_markdown(city, origin, days, start, itin, graded, places):
    places = clean_places(places, city)
    c, by_grade = summarize_sources(graded)
    total = len(graded)
    budget = itin.get("budget", {})
    meta = itin.get("meta", {})
    n_days = meta.get("days", days)

    # 统计免费景点
    free_count = sum(1 for p in places if p.get("ticket_price") == 0)
    resv = [p for p in places if p.get("reservation_required")]

    lines = []
    A = lines.append

    A(f"# {origin} → {city} 国庆行程规划报告")
    A("")
    A(f"> **出发地**：{origin}　**目的地**：{city}")
    A(f"> **行程**：{start} 起 {n_days} 天")
    A(f"> **信源**：{total} 条原始结果 · A级权威 {c['A']} 条 / C级待核 {c['C']} 条 / D级噪声 {c['D']} 条")
    A("")
    A("---")
    A("")

    # ========== 〇 概览 ==========
    A("<!--MODULE:概览:🧭:目的地、天数、门票与预算，一屏看完-->")
    A("")
    A("| 维度 | 内容 |")
    A("|---|---|")
    A(f"| **目的地** | {city}（越城区古城为核心） |")
    A(f"| **行程天数** | {n_days} 天 |")
    A(f"| **城际交通** | {origin} → {city} 高铁约 1 小时 4 分，二等座最低约 29.5 元起 |")
    A(f"| **景点门票** | 全程合计 **{budget.get('total_attractions', 0)} 元**（{free_count} 个免费景点）|")
    A(f"| **预算估算** | 约 **{budget.get('total', 0)} 元** / 人 |")
    A(f"| **核心基调** | 古城人文 + 水乡 walk，景点高度集中、大量免费 |")
    A("")

    # ========== 一 详细行程 ==========
    A("<!--MODULE:详细行程:📅:逐日动线、餐饮建议与区内顺序-->")
    A("")
    for d in itin["days"]:
        A(f"### Day {d['day_index'] + 1}　{d['date']}（{d['weekday']}）")
        A("")
        A(f"**区域**：{d['region']}　|　**游览总时长**：约 {d['total_duration_min'] // 60} 小时 {d['total_duration_min'] % 60} 分")
        A("")
        A("| 顺序 | 地点 | 类型 | 建议时长 | 门票 | 备注 |")
        A("|---|---|---|---|---|---|")
        for i, a in enumerate(d["attractions"], 1):
            tp = a.get("ticket_price")
            if tp == 0:
                price = "免费"
            elif tp is None:
                price = "待确认"
            else:
                price = f"{tp} 元"
            resv = " ⚠️需预约" if a.get("reservation_required") else ""
            note = (a.get("note") or "")[:60].replace("\n", " ")
            A(f"| {i} | **{a['name']}**{resv} | {a['type']} | {a['duration_min']} 分 | {price} | {note} |")
        A("")
        A("**餐饮建议**")
        A("")
        for m in d["meals"]:
            label = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}[m["type"]]
            A(f"- {label}：{m['name']}（约 {m['cost']} 元）")
        A("")
        if d.get("attractions"):
            names = " → ".join(a["name"] for a in d["attractions"])
            A(f"> 💡 **动线**：{names}（同区域集中，无需折返）")
            A("")
        A("---")
        A("")

    # ========== 二 城际交通 ==========
    A("<!--MODULE:城际交通:🚄:怎么去、在哪个站下车、市内怎么走-->")
    A("")
    A(f"**{origin} → {city}**")
    A("")
    A("| 方式 | 车次示例 | 耗时 | 参考票价 |")
    A("|---|---|---|---|")
    A("| 高铁（推荐） | G7541 / G7363 / G7501 / D3131 | 约 1 小时 4 分 ~ 1 小时 36 分 | 最低约 29.5 元，常见 77–132 元 |")
    A("")
    A("**到达站选择**")
    A("")
    A("- **绍兴站** —— 出站即古城，步行可达鲁迅故里，**首选**")
    A("- **绍兴北站** —— 乘地铁 1 号线直达鲁迅故里站，约 40 分钟进古城")
    A("")
    A("**市内交通**")
    A("")
    A("- 地铁 1 号线：绍兴北站 ↔ 鲁迅故里站 ↔ 古城核心（票价约 4 元）")
    A("- 古城内景点高度密集，**步行 + 共享单车** 最合适（鲁迅故里、沈园、仓桥直街步行可达）")
    A("- 东湖、大禹陵、兰亭建议打车或公交接驳")
    A("")

    # ========== 三 预约与限流 ==========
    A("<!--MODULE:预约与限流:🎫:门票、是否需要预约与优惠政策-->")
    A("")
    _n_pay = len([x for x in places if x.get("type") not in ("T", "A")])
    A("<details>")
    A(f"<summary>展开：全部 {_n_pay} 个景点的门票与预约要求</summary>")
    A("")
    A("| 景点 | 门票 | 预约要求 |")
    A("|---|---|---|")
    for p in places:
        if p.get("type") in ("T", "A"):
            continue
        tp = p.get("ticket_price")
        if tp == 0:
            price = "**免费**"
        elif tp is None:
            price = "待确认"
        else:
            price = f"{tp} 元"
        resv = p.get("reservation_tips") or "—"
        A(f"| {p['name']} | {price} | {resv} |")
    A("")
    A("</details>")
    A("")
    if resv:
        A(f"> ⚠️ **共 {len(resv)} 个景点需预约**，建议出行前 3 天完成预约。")
        A("")
    A("**重要优惠政策**（来自 A 级信源）")
    A("")
    A("- 鲁迅故里等 **37 处景区向全球游客免费开放**")
    A("- 大禹陵、沈园（不含夜沈园）、周恩来纪念馆、蔡元培故居、绍兴黄酒博物馆等 8 处国有景区，对全国大中小学生研学旅游免费")
    A("- 大禹陵成人票约 60 元")
    A("")

    # ========== 四 备选与延伸 ==========
    if itin.get("alternatives"):
        A("<!--MODULE:备选地点:🔀:时间充裕或想换点时用-->")
        A("")
        seen_alt = set()
        for name in itin["alternatives"]:
            if name in seen_alt or name in OFFROUTE:
                continue
            seen_alt.add(name)
            A(f"- {name}")
        A("")
        A("---")
        A("")

    # ========== 五 费用 ==========
    A("<!--MODULE:费用:💰:逐项拆解，注意「每人」与「整团」的口径差异-->")
    A("")
    A("| 项目 | 金额（**每人**，住宿除外） | 说明 |")
    A("|---|---|---|")
    A(f"| 门票 | {budget.get('total_attractions', 0)} 元 | 按行程内实际门票加总 |")
    A(f"| 餐饮 | {budget.get('total_meals', 0)} 元 | 早 20 + 午 70 + 晚 90 / 天 |")
    A(f"| 住宿 | {budget.get('total_hotels', 0)} 元 | {budget.get('_notes', {}).get('nights', 0)} 晚 × {budget.get('_notes', {}).get('hotel_per_night', 400)} 元 |")
    A(f"| 城际交通 | {budget.get('total_inter_city_transport', 0)} 元 | 往返高铁二等座 |")
    A(f"| 市内交通 | {budget.get('total_local_transport', 0)} 元 | 地铁 + 打车 |")
    A(f"| **合计** | **{budget.get('total', 0)} 元** | |")
    A("")
    A("> 💡 绍兴古城核心景点几乎全部免费，**住宿与交通是主要开销**。")
    A("")

    # ========== 六 路线地图 ==========
    # 只有这一行占位符：md2html 拿到 itinerary 就填内联 SVG，
    # 拿不到（缺坐标 / 没装 markdown）就把整个模块去掉，不留空壳。
    A("<!--MODULE:路线地图:🗺️:每天一格，格内按远近再分簇，各格独立比例尺-->")
    A("")
    A("<!--MAP-->")
    A("")

    # ========== 七 行前与风险 ==========
    A("<!--MODULE:行前与风险:⚠️:风险清单与出行前必做-->")
    A("")
    A("| 风险项 | 说明 |")
    A("|---|---|")
    A("| ⚠️ 国庆客流 | 鲁迅故里、沈园等核心景点国庆期间人流极大，建议 **早上 8 点前** 抵达 |")
    A("| ⚠️ 活动未确证 | 本次检索 **未能获取到绍兴国庆期间的确定性活动排期** |")
    A("| ⚠️ 预约制度 | 部分景点需提前预约，旺季可能限流 |")
    A("| ⚠️ 信源时效 | 部分优质攻略发布于数年前，价格与开放时间需现场核对 |")
    A("")
    A("**出行前必做**")
    A("")
    A("1. 查绍兴文旅官方公众号，确认国庆活动排期")
    A("2. 高铁票开售即抢（国庆为全年最高峰）")
    A("3. 需要预约的景点提前 3 天完成预约")
    A("")

    # ========== 八 信源与核查 ==========
    A("<!--MODULE:信源与核查:🔍:本次检索的分级结果与可复核链接-->")
    A("")
    A(f"本次共检索 **{total} 条**结果，经规则引擎自动分级：")
    A("")
    A("| 等级 | 数量 | 含义 |")
    A("|---|---|---|")
    A(f"| A 权威 | {c['A']} 条 | 境内权威媒体、百科、本地宝、高德、12306 |")
    A(f"| C 待核 | {c['C']} 条 | 境外OTA站点或内容可能过时，需交叉验证 |")
    A(f"| D 噪声 | {c['D']} 条 | 地域无关内容，已排除 |")
    A("")
    if by_grade["D"]:
        A("<details>")
        A(f"<summary>展开：被排除的 {len(by_grade['D'])} 条地域噪声</summary>")
        A("")
        for x in by_grade["D"][:8]:
            A(f"- ~~{x['title'][:50]}~~ —— {x['grade_reason']}")
        A("")
        A("</details>")
        A("")

    # ========== A 级信源清单（并入「信源与核查」模块，折叠呈现）==========
    A("<details>")
    A(f"<summary><b>展开：A 级信源清单（{len(by_grade['A'])} 条，可点击复核）</b></summary>")
    A("")
    seen = set()
    for x in by_grade["A"]:
        t = x["title"][:60]
        if t in seen:
            continue
        seen.add(t)
        A(f"- [{t}]({x['url']})")
    A("")
    A("</details>")
    A("")

    A("---")
    A("")
    A(f"*报告由 trip-plan-skill 生成 · 规则引擎驱动 · 数据来源 anysearch 实时检索*")
    A(f"*生成时间：{date.today().isoformat()}*")

    return "\n".join(lines)


def md_to_html(md, title):
    """轻量 Markdown → HTML（针对本报告优化，零依赖）"""
    import html as _html

    def esc(s):
        return _html.escape(s, quote=False)

    body = []
    in_table = False
    in_list = False

    for line in md.split("\n"):
        s = line.rstrip()
        if not s:
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            if in_list:
                body.append("</ul>")
                in_list = False
            continue

        # 表格
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not in_table:
                body.append('<table><thead><tr>')
                body.append("".join(f"<th>{esc(c)}</th>" for c in cells))
                body.append("</tr></thead><tbody>")
                in_table = True
            else:
                body.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
            continue
        elif in_table:
            body.append("</tbody></table>")
            in_table = False

        # 列表
        if s.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{esc(s[2:])}</li>")
            continue
        elif in_list:
            body.append("</ul>")
            in_list = False

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            lv = len(m.group(1))
            body.append(f"<h{lv}>{esc(m.group(2))}</h{lv}>")
            continue

        # 引用
        if s.startswith(">"):
            body.append(f'<blockquote>{esc(s.lstrip("> "))}</blockquote>')
            continue

        if s == "---":
            body.append("<hr>")
            continue

        # 行内加粗
        txt = esc(s)
        txt = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", txt)
        txt = re.sub(r"~~(.+?)~~", r"<del>\1</del>", txt)
        txt = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', txt)
        body.append(f"<p>{txt}</p>")

    if in_table:
        body.append("</tbody></table>")
    if in_list:
        body.append("</ul>")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root {{
  --bg: #faf9f6; --card: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
  --line: #e5e3dd; --accent: #8b5a2b; --free: #2d7a3e; --warn: #b45309;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 32px 20px; background: var(--bg); color: var(--text);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif;
  line-height: 1.75; font-size: 15px;
}}
main {{ max-width: 900px; margin: 0 auto; background: var(--card);
  border-radius: 12px; padding: 40px 48px; border: 1px solid var(--line); }}
h1 {{ font-size: 26px; border-bottom: 3px solid var(--accent);
  padding-bottom: 12px; margin-top: 0; }}
h2 {{ font-size: 20px; margin-top: 40px; padding-left: 12px;
  border-left: 4px solid var(--accent); }}
h3 {{ font-size: 17px; margin-top: 28px; color: var(--accent); }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px; }}
th {{ background: #f5f3ee; text-align: left; padding: 10px 12px;
  border: 1px solid var(--line); font-weight: 600; }}
td {{ padding: 10px 12px; border: 1px solid var(--line); vertical-align: top; }}
tr:nth-child(even) td {{ background: #fcfbf9; }}
blockquote {{ margin: 16px 0; padding: 12px 18px; background: #fdf8f0;
  border-left: 3px solid var(--accent); color: #5a4a35; border-radius: 0 6px 6px 0; }}
ul {{ padding-left: 22px; }}
li {{ margin: 6px 0; }}
hr {{ border: none; border-top: 1px solid var(--line); margin: 32px 0; }}
a {{ color: var(--accent); }}
del {{ color: #999; }}
strong {{ color: #000; }}
@media (max-width: 640px) {{
  main {{ padding: 24px 18px; }}
  body {{ padding: 12px 8px; }}
  table {{ font-size: 13px; }}
}}
@media print {{
  body {{ background: #fff; }} main {{ border: none; box-shadow: none; }}
}}
</style>
</head>
<body>
<main>
{chr(10).join(body)}
</main>
</body>
</html>"""


def main():
    p = argparse.ArgumentParser(description="报告生成器")
    p.add_argument("--itinerary", required=True)
    p.add_argument("--graded", required=True)
    p.add_argument("--places", required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--origin", default="上海")
    p.add_argument("--start", required=True)
    p.add_argument("--out", required=True, help="输出 .md 路径")
    p.add_argument("--html", action="store_true", help="同时生成 HTML")
    args = p.parse_args()

    itin = load(args.itinerary)
    graded = load(args.graded)
    places = load(args.places)

    md = build_markdown(args.city, args.origin, None, args.start, itin, graded, places)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(md)
    print(f"Markdown 报告 → {args.out}", file=sys.stderr)

    if args.html:
        html_path = os.path.splitext(args.out)[0] + ".html"
        html = md_to_html(md, f"{args.origin} → {args.city} 行程规划报告")
        open(html_path, "w", encoding="utf-8").write(html)
        print(f"HTML 报告 → {html_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
