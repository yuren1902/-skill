# -*- coding: utf-8 -*-
"""自驾行程 HTML 生成器：时间轴 + 驾驶段落 + 表格

⚠ **定制样例**：时间轴（`TIMELINE`）与数据来自「上虞-绍兴-新昌 6 日自驾」
那次行程。换目的地需替换 `TIMELINE`，其余渲染逻辑可复用。
依赖 `report_multi.py` 提供行程数据与信源表。

用法：
  python report_drive.py --itin itinerary.json --out 报告.html
"""
import argparse
import html
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_multi as RM

ITIN = RM.itin          # 延迟加载：真正取数时再定位 itinerary.json
esc = lambda s: html.escape(str(s), quote=False)

# 每日时间轴（手工编排，覆盖自动生成的景点顺序 —— 加入起止时间与驾驶段）
TIMELINE = {
    0: [
        ("19:00", "上海家中出发", "车程约 2h30m · 187–230 km", "drive"),
        ("21:00", "嘉兴服务区休息", "五芳斋粽子 / 咖啡，歇 20 分钟", "rest"),
        ("22:00", "抵达上虞城区", "入住酒店，放行李", "stay"),
        ("22:30", "宵夜：老曹娥馄饨面", "峰山路167号 · 肉丝拌面 + 烤饺，馄饨 6 元/碗", "eat"),
        ("23:30", "回酒店休息", "次日需早起上山", "stay"),
    ],
    1: [
        ("07:00", "早餐：恒利菜场老汤面店", "笋干肉丝面 + 手工艾饺（38 年老店）", "eat"),
        ("08:00", "出发前往覆卮山", "车程约 50m · 32 km 山路", "drive"),
        ("09:00", "覆卮山千年梯田", "油菜花期已过，但梯田+冰川石浪仍壮观 · 登顶约 2–3h", "spot"),
        ("12:00", "下山，前往丰惠古镇", "车程约 30m", "drive"),
        ("12:40", "午餐：丰惠陈家野菜馆", "32 年 · 马兰头香干、春笋炒腊肉", "eat"),
        ("14:00", "丰惠古镇漫步", "千年古县城八街四十弄 · 祝英台故里 · 九狮桥/丰惠桥", "spot"),
        ("16:30", "返回上虞城区", "车程约 25m", "drive"),
        ("17:15", "曹娥庙 + 中华孝德园", "江南第一庙 · 孝德文化核心", "spot"),
        ("18:30", "曹娥江畔散步看日落", "江畔绿道 · 渔舟唱晚", "spot"),
        ("19:30", "晚餐：曹娥铁皮棚鲜馆", "35 年码头江鲜 · 葱姜炒螺蛳、清蒸江白虾", "eat"),
        ("21:00", "曹娥里十三弄夜游", "寻宝记入驻 · 越剧+黄酒主题街区", "spot"),
    ],
    2: [
        ("07:30", "早餐：沈德记蒸菜馆", "百官蒋家弄 · 春笋蒸土猪、酱鸭蒸笋（30 年老店）", "eat"),
        ("09:00", "上虞城区出发 → 绍兴古城", "车程约 55m · 45 km", "drive"),
        ("10:00", "鲁迅故里", "5A · 免费需预约 · 早到人少", "spot"),
        ("12:00", "午餐：寻宝记绍兴菜", "⭐ 十登央视 · 绍三鲜、黄酒布丁、花雕醉蟹", "eat"),
        ("13:30", "仓桥直街快逛", "臭豆腐、奶油小攀、黄酒棒冰 · 走到八字桥", "spot"),
        ("13:30", "⚠️ 必须离开绍兴", "本日最紧节点：14:00 前上高速", "warn"),
        ("14:00", "绍兴 → 新昌", "车程 1h30m–1h45m · 100 km · 过路费 45–54 元", "drive"),
        ("15:45", "抵达新昌，入住酒店", "建议住县城（海洋城/世贸广场附近）· 连住 3 晚", "stay"),
        ("16:30", "新昌大佛寺", "江南第一大佛 · 日场 6:00–17:30，需抓紧", "spot"),
        ("18:30", "晚餐：新昌老街", "镬拉头、芋饺、春饼", "eat"),
        ("20:00", "新昌博物馆（若开放）", "免费 · 唐诗之路文化陈列", "spot"),
    ],
    3: [
        ("07:00", "早餐：新昌炒年糕", "一溜下/老北京等老店 · 薄片年糕加肉丝笋干", "eat"),
        ("08:00", "出发去十九峰", "车程约 30m · 16 km · 日场 7:00–17:00", "drive"),
        ("08:40", "穿岩十九峰", "4A · 线上 53–56 元 · 可乘小火车（25–30元）", "spot"),
        ("10:00", "飞龙栈道（玻璃栈道）", "垂直山体视角 · 武侠剧取景地", "spot"),
        ("12:00", "午餐：景区周边农家菜", "或大佛寺素斋（10–20 元/份）", "eat"),
        ("13:00", "千丈幽谷", "丹霞峡谷 + 崩塌巨石 · 多部武侠剧取景地", "spot"),
        ("15:30", "重阳宫", "道教第十洞天 · 《射雕英雄传》取景地", "spot"),
        ("17:00", "返回新昌县城", "车程约 30m", "drive"),
        ("18:30", "晚餐：新昌榨面 + 米海茶", "新昌粉丝、小京生花生", "eat"),
    ],
    4: [
        ("07:00", "早餐：酒店 / 老街春饼", "带足干粮，天姥山山上餐饮少", "eat"),
        ("08:00", "出发去天姥山", "车程约 50m · 30 km · ⚠️ 需提前预约", "drive"),
        ("09:00", "天姥山古道登山", "李白《梦游天姥吟留别》原型地 · 主峰北斗尖俯瞰群山", "spot"),
        ("12:00", "午餐：半山农家面", "或自带干粮", "eat"),
        ("13:30", "前往沃洲湖", "车程约 40m · 转场镜岭方向", "drive"),
        ("14:30", "沃洲湖游船", "白居易『沃洲天下稀』· 约 60 元含船票", "spot"),
        ("16:30", "斑竹古村", "唐诗之路驿站 · 司马悔桥、古驿道", "spot"),
        ("18:00", "晚餐：古村民宿农家菜", "溪鱼、土鸡、笋干", "eat"),
        ("19:30", "返回新昌县城", "车程约 45m", "drive"),
    ],
    5: [
        ("07:30", "早餐：酒店 / 新昌炒年糕", "退房，行李装车", "eat"),
        ("09:00", "新昌老街收尾", "补买伴手礼：大佛龙井、小京生花生、芋饺真空装", "spot"),
        ("11:00", "午餐：新昌县城", "出发前解决，避免服务区排队", "eat"),
        ("12:30", "⚠️ 出发返沪", "避开 14:00–19:00 返程高峰", "warn"),
        ("13:00", "新昌 → 上海", "3h–3h30m · 约 250 km · 过路费 105–125 元（国庆免费）", "drive"),
        ("15:00", "服务区休息", "自备水与干粮", "rest"),
        ("16:30", "抵达上海", "行程结束", "stay"),
    ],
}

ICON = {"drive": "🚗", "spot": "📍", "eat": "🍜", "stay": "🏨", "rest": "☕", "warn": "⚠️"}
CLS = {"drive": "tl-drive", "spot": "tl-spot", "eat": "tl-eat",
       "stay": "tl-stay", "rest": "tl-rest", "warn": "tl-warn"}


def timeline_html(day_idx):
    items = TIMELINE.get(day_idx, [])
    if not items:
        return ""
    rows = []
    for t, title, desc, kind in items:
        rows.append(f"""
        <div class="tl-item {CLS.get(kind,'')}">
          <div class="tl-time">{esc(t)}</div>
          <div class="tl-dot">{ICON.get(kind,'•')}</div>
          <div class="tl-body">
            <div class="tl-title">{esc(title)}</div>
            <div class="tl-desc">{esc(desc)}</div>
          </div>
        </div>""")
    return '<div class="timeline">' + "".join(rows) + "</div>"


def drive_table():
    rows = []
    for t, route, km, dur, road, toll, tip in RM.DRIVE:
        rows.append(
            f"<tr><td><strong>{esc(t)}</strong></td><td>{esc(route)}</td>"
            f"<td class='num'>{esc(km)}</td><td class='num'>{esc(dur)}</td>"
            f"<td class='mono'>{esc(road)}</td><td class='num'>{esc(toll)}</td>"
            f"<td class='tip'>{esc(tip)}</td></tr>")
    return "".join(rows)


def day_section(d):
    idx = d["day_index"]
    if not d["attractions"]:
        return f"""
        <div class="day-nodrama">
          <span class="nd-tag">Day {idx}</span>
          <strong>{esc(d['date'])}（{esc(d['weekday'])}）</strong> · {esc(d['label'])}
          <div class="nd-anchor">{esc(d['anchor'])}</div>
        </div>"""
    # 景点表
    att_rows = []
    for i, a in enumerate(d["attractions"], 1):
        p = a["ticket_price"]
        price = ("<b class='free'>免费</b>" if p == 0
                 else (f"{p} 元" if p else "待确认"))
        att_rows.append(
            f"<tr><td class='num'>{i}</td><td><strong>{esc(a['name'])}</strong></td>"
            f"<td>{esc(a['type'])}</td><td class='num'>{a['duration_min']} 分</td>"
            f"<td>{price}</td><td class='note'>{esc(a['note'])}</td></tr>")
    meals = RM.MEALS.get(idx, {})
    meal_html = ""
    if meals:
        li = "".join(
            f"<li><span class='meal-tag'>{esc(tag)}</span>{esc(name)}"
            f"<span class='meal-cost'>{esc(cost)}</span></li>"
            for tag, name, cost in meals["items"])
        meal_html = (f"<div class='meals'><div class='meals-head'>🍜 餐饮建议"
                     f"　<span class='meals-note'>{esc(meals['note'])}</span></div>"
                     f"<ul>{li}</ul></div>")
    return f"""
    <section class="day">
      <div class="day-head">
        <span class="day-badge">Day {idx}</span>
        <div class="day-meta">
          <div class="day-date">{esc(d['date'])}　{esc(d['weekday'])}</div>
          <div class="day-label">{esc(d['label'])}</div>
        </div>
        <div class="day-stats">
          <span>{len(d['attractions'])} 个景点</span>
          <span>{d['total_duration_min']//60}h{d['total_duration_min']%60:02d}m</span>
          <span>跨度 {d['max_span_km']} km</span>
          <span>门票 {d['tickets']} 元</span>
        </div>
      </div>
      <div class="anchor-line">{esc(d['anchor'])}</div>
      <div class="day-grid">
        <div class="tl-col">
          <h4>⏱ 当日时间轴</h4>
          {timeline_html(idx)}
        </div>
        <div class="detail-col">
          <h4>📍 景点明细　<span class="region">{esc(d['region'])}</span></h4>
          <table class="tbl">
            <thead><tr><th>#</th><th>地点</th><th>类型</th><th>时长</th><th>门票</th><th>备注</th></tr></thead>
            <tbody>{''.join(att_rows)}</tbody>
          </table>
          {meal_html}
        </div>
      </div>
    </section>"""


def build():
    total_tickets = sum(d["tickets"] for d in ITIN()["days"])
    n_spots = sum(len(d["attractions"]) for d in ITIN()["days"])
    days_html = "".join(day_section(d) for d in ITIN()["days"])

    alt_rows = "".join(
        f"<tr><td><strong>{esc(a['name'])}</strong></td><td>{esc(a['district'])}</td>"
        f"<td>{'免费' if a['price']==0 else (str(a['price'])+' 元' if a['price'] else '待确认')}</td>"
        f"<td class='note'>{esc(a['note'][:60])}</td></tr>"
        for a in ITIN()["alternatives"][:22])

    hotel_rows = "".join(
        f"<tr><td>{esc(d)}</td><td>{esc(loc)}</td><td class='num'>{esc(n)}</td>"
        f"<td>{esc(rec)}</td><td class='note'>{esc(why)}</td></tr>"
        for d, loc, n, rec, why in RM.HOTELS)

    src_a = "".join(f"<li><a href='{esc(u)}' target='_blank'>{esc(t)}</a>"
                    f"<span class='src-why'>{esc(w)}</span></li>"
                    for t, u, w in RM.SOURCES_A)
    src_c = "".join(f"<li><a href='{esc(u)}' target='_blank'>{esc(t)}</a>"
                    f"<span class='src-why'>{esc(w)}</span></li>"
                    for t, u, w in RM.SOURCES_C)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>上海 → 上虞 → 绍兴 → 新昌 自驾 6 日行程</title>
<style>
:root {{
  --bg:#f6f4ef; --card:#fff; --text:#1c1a17; --muted:#6d6862; --line:#e3ded4;
  --ink:#2c3e50; --accent:#b45309; --accent2:#0f766e;
  --drive:#2563eb; --spot:#0f766e; --eat:#b45309; --stay:#7c3aed;
  --rest:#0891b2; --warn:#dc2626; --free:#15803d;
}}
*{{box-sizing:border-box;}}
body{{margin:0;padding:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,"PingFang SC","Microsoft YaHei","Source Han Sans SC",sans-serif;
  line-height:1.7;font-size:15px;-webkit-font-smoothing:antialiased;}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 20px 80px;}}

/* ---------- Hero ---------- */
.hero{{background:linear-gradient(135deg,#1e293b 0%,#334155 55%,#0f766e 100%);
  color:#fff;padding:52px 44px 44px;border-radius:0 0 20px 20px;margin-bottom:32px;
  box-shadow:0 10px 40px rgba(15,23,42,.18);}}
.hero h1{{margin:0 0 8px;font-size:31px;letter-spacing:-.5px;font-weight:700;}}
.hero .sub{{opacity:.86;font-size:15px;margin-bottom:24px;}}
.route{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:22px 0 26px;}}
.route .node{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28);
  padding:8px 16px;border-radius:999px;font-size:14px;font-weight:600;backdrop-filter:blur(4px);}}
.route .node small{{display:block;font-weight:400;opacity:.75;font-size:11px;}}
.route .arrow{{opacity:.6;font-size:15px;}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:12px;}}
.kpi{{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);
  border-radius:12px;padding:14px 16px;}}
.kpi .k{{font-size:11px;opacity:.72;letter-spacing:.5px;}}
.kpi .v{{font-size:20px;font-weight:700;margin-top:3px;}}
.kpi .v small{{font-size:12px;font-weight:400;opacity:.75;}}

/* ---------- Section ---------- */
h2{{font-size:21px;margin:44px 0 16px;padding-left:13px;border-left:4px solid var(--accent);}}
h4{{font-size:14px;margin:0 0 12px;color:var(--muted);font-weight:600;
  letter-spacing:.3px;text-transform:uppercase;}}
.region{{font-weight:400;text-transform:none;color:var(--accent2);font-size:12px;}}

/* ---------- Day ---------- */
.day{{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:26px 28px;margin-bottom:24px;box-shadow:0 2px 12px rgba(28,26,23,.05);}}
.day-head{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;
  padding-bottom:16px;border-bottom:2px solid #f1ede5;margin-bottom:14px;}}
.day-badge{{background:var(--ink);color:#fff;padding:7px 15px;border-radius:9px;
  font-weight:700;font-size:14px;letter-spacing:.5px;flex-shrink:0;}}
.day-meta{{flex:1;min-width:200px;}}
.day-date{{font-size:17px;font-weight:700;}}
.day-label{{font-size:13px;color:var(--muted);}}
.day-stats{{display:flex;gap:7px;flex-wrap:wrap;}}
.day-stats span{{background:#f4f1ea;border:1px solid var(--line);padding:4px 10px;
  border-radius:7px;font-size:12px;color:var(--muted);white-space:nowrap;}}
.anchor-line{{background:#fdf8f0;border-left:3px solid var(--accent);
  padding:9px 14px;border-radius:0 7px 7px 0;font-size:13.5px;color:#5a4a35;margin-bottom:20px;}}
.day-grid{{display:grid;grid-template-columns:minmax(280px,340px) 1fr;gap:26px;}}
@media(max-width:900px){{.day-grid{{grid-template-columns:1fr;}}}}

/* ---------- Timeline ---------- */
.timeline{{position:relative;padding-left:4px;}}
.tl-item{{display:grid;grid-template-columns:48px 22px 1fr;gap:0;position:relative;
  padding-bottom:14px;}}
.tl-item:not(:last-child)::after{{content:'';position:absolute;left:57px;top:22px;
  bottom:-2px;width:2px;background:#eae5db;}}
.tl-time{{font-size:12px;font-variant-numeric:tabular-nums;color:var(--muted);
  font-weight:600;padding-top:2px;}}
.tl-dot{{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;
  font-size:10px;background:#f0ece3;z-index:1;flex-shrink:0;border:2px solid #fff;
  box-shadow:0 0 0 1.5px #eae5db;}}
.tl-body{{padding-left:12px;}}
.tl-title{{font-size:13.5px;font-weight:600;line-height:1.45;}}
.tl-desc{{font-size:12px;color:var(--muted);line-height:1.55;margin-top:1px;}}
.tl-drive .tl-dot{{background:#dbeafe;box-shadow:0 0 0 1.5px #93c5fd;}}
.tl-drive .tl-title{{color:var(--drive);}}
.tl-spot .tl-dot{{background:#ccfbf1;box-shadow:0 0 0 1.5px #5eead4;}}
.tl-spot .tl-title{{color:var(--spot);}}
.tl-eat .tl-dot{{background:#fef3c7;box-shadow:0 0 0 1.5px #fcd34d;}}
.tl-eat .tl-title{{color:var(--eat);}}
.tl-stay .tl-dot{{background:#ede9fe;box-shadow:0 0 0 1.5px #c4b5fd;}}
.tl-stay .tl-title{{color:var(--stay);}}
.tl-rest .tl-dot{{background:#cffafe;box-shadow:0 0 0 1.5px #67e8f9;}}
.tl-warn .tl-dot{{background:#fee2e2;box-shadow:0 0 0 1.5px #fca5a5;}}
.tl-warn .tl-title{{color:var(--warn);font-weight:700;}}

/* ---------- Table ---------- */
.tbl{{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:16px;}}
.tbl th{{background:#f5f2ec;text-align:left;padding:9px 11px;border:1px solid var(--line);
  font-weight:600;font-size:12px;color:var(--muted);}}
.tbl td{{padding:9px 11px;border:1px solid var(--line);vertical-align:top;}}
.tbl td.num{{text-align:center;font-variant-numeric:tabular-nums;white-space:nowrap;}}
.tbl td.note{{color:var(--muted);font-size:12px;line-height:1.55;}}
.tbl td.mono{{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;}}
.tbl td.tip{{font-size:12px;color:#7c2d12;}}
.free{{color:var(--free);}}

/* ---------- Meals ---------- */
.meals{{background:#fffdf7;border:1px solid #f3e9d4;border-radius:11px;padding:15px 18px;}}
.meals-head{{font-size:13px;font-weight:700;color:var(--eat);margin-bottom:10px;}}
.meals-note{{font-weight:400;font-size:12px;color:var(--muted);}}
.meals ul{{margin:0;padding-left:0;list-style:none;}}
.meals li{{font-size:13px;padding:6px 0;border-bottom:1px dashed #f0e8d8;
  display:flex;gap:9px;flex-wrap:wrap;align-items:baseline;}}
.meals li:last-child{{border-bottom:none;}}
.meal-tag{{background:#fef3c7;color:#92400e;font-size:11px;padding:2px 8px;
  border-radius:5px;font-weight:600;white-space:nowrap;flex-shrink:0;}}
.meal-cost{{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap;}}

/* ---------- No-drama day ---------- */
.day-nodrama{{background:var(--card);border:1px dashed var(--line);border-radius:14px;
  padding:20px 24px;margin-bottom:24px;color:var(--muted);font-size:14px;}}
.nd-tag{{background:#f4f1ea;padding:4px 11px;border-radius:7px;font-weight:700;
  color:var(--ink);font-size:12px;margin-right:10px;}}
.nd-anchor{{margin-top:7px;font-size:13px;color:#5a4a35;}}

/* ---------- Generic card ---------- */
.card{{background:var(--card);border:1px solid var(--line);border-radius:15px;
  padding:24px 26px;margin-bottom:22px;box-shadow:0 2px 10px rgba(28,26,23,.04);}}
.card h3{{margin:0 0 14px;font-size:15px;color:var(--ink);}}

/* ---------- Route diagram ---------- */
.route-diagram{{background:#0f172a;color:#e2e8f0;border-radius:14px;padding:26px 28px;
  font-family:ui-monospace,Menlo,monospace;font-size:12.5px;line-height:2;
  overflow-x:auto;white-space:pre;margin-bottom:22px;}}
.route-diagram .hl{{color:#5eead4;font-weight:700;}}
.route-diagram .warn{{color:#fbbf24;}}

/* ---------- Alerts ---------- */
.alert{{border-radius:12px;padding:15px 19px;margin-bottom:14px;font-size:13.5px;
  display:flex;gap:11px;align-items:flex-start;}}
.alert.warn{{background:#fef2f2;border-left:4px solid var(--warn);color:#7f1d1d;}}
.alert.info{{background:#eff6ff;border-left:4px solid var(--drive);color:#1e3a8a;}}
.alert.tip{{background:#f0fdf4;border-left:4px solid var(--free);color:#14532d;}}
.alert .ic{{font-size:16px;line-height:1.4;flex-shrink:0;}}

/* ---------- Risk table ---------- */
.risk td:first-child{{font-weight:600;white-space:nowrap;color:#7f1d1d;}}

/* ---------- Sources ---------- */
.sources{{display:grid;grid-template-columns:1fr 1fr;gap:26px;}}
@media(max-width:820px){{.sources{{grid-template-columns:1fr;}}}}
.sources ul{{margin:0;padding-left:0;list-style:none;}}
.sources li{{font-size:12.5px;padding:7px 0;border-bottom:1px solid #f2eee6;}}
.sources a{{color:var(--accent2);text-decoration:none;font-weight:500;}}
.sources a:hover{{text-decoration:underline;}}
.src-why{{display:block;color:var(--muted);font-size:11.5px;margin-top:1px;}}

footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:48px;
  padding-top:24px;border-top:1px solid var(--line);}}

@media print{{
  body{{background:#fff;}} .hero{{border-radius:0;box-shadow:none;}}
  .day,.card{{box-shadow:none;break-inside:avoid;}}
}}
</style>
</head>
<body>
<div class="hero">
  <h1>上海 → 上虞 → 绍兴 → 新昌</h1>
  <div class="sub">自驾 6 日 · 2026 年 9 月 30 日夜出发 ～ 10 月 5 日返沪</div>
  <div class="route">
    <div class="node">上海<small>9/30 19:00 出发</small></div>
    <div class="arrow">→</div>
    <div class="node">上虞<small>9/30 夜宿 · 10/1 全天</small></div>
    <div class="arrow">→</div>
    <div class="node">绍兴<small>10/2 午餐</small></div>
    <div class="arrow">→</div>
    <div class="node">新昌<small>10/2 下午 – 10/5</small></div>
    <div class="arrow">→</div>
    <div class="node">上海<small>10/5 16:30 到家</small></div>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="k">行程天数</div><div class="v">6 天 5 晚</div></div>
    <div class="kpi"><div class="k">驾驶里程</div><div class="v">~800 <small>km</small></div></div>
    <div class="kpi"><div class="k">主景点</div><div class="v">{n_spots} <small>个</small></div></div>
    <div class="kpi"><div class="k">门票合计</div><div class="v">{total_tickets} <small>元</small></div></div>
    <div class="kpi"><div class="k">总预算</div><div class="v">5.7–8k <small>元/2人</small></div></div>
    <div class="kpi"><div class="k">信源</div><div class="v">120 <small>条检索</small></div></div>
  </div>
</div>

<div class="wrap">

<div class="alert tip">
  <span class="ic">🎉</span>
  <div><strong>国庆高速免费</strong>：10/1 00:00 – 10/8 24:00，7 座及以下小客车免费通行，
  可直接省下 250 元以上过路费。注意 <strong>9/30 夜上高速时尚未免费</strong>，
  上虞段约 85–105 元需自付——若 10/1 零点后下高速则按免费判定。</div>
</div>

<h2>一、逐日行程</h2>
{days_html}

<h2>二、自驾路线与费用</h2>
<div class="card">
  <h3>全程路线骨架</h3>
  <div class="route-diagram">上海 ──<span class="hl">G60沪昆 / 杭甬</span>──► 上虞       <span class="hl">[9/30 夜宿, 10/1 全天]</span>
                                    │
                                    ├─ 10/2 上午  上虞 → 绍兴古城    <span class="hl">45 km · 55m</span>   🍜 午餐
                                    │
                                    └─ 10/2 下午  绍兴 → 新昌        <span class="hl">100 km · 1h45m</span>
                                                    │
                                    10/3 西线  十九峰 / 千丈幽谷 ◄───┤
                                    10/4 南线  天姥山 / 沃洲湖 ◄─────┤
                                                    │
                                    10/5 返程 ──常台高速──► 上海    <span class="hl">250 km · 3h+</span></div>

  <h3 style="margin-top:28px">关键路段明细</h3>
  <table class="tbl">
    <thead><tr><th>时段</th><th>路线</th><th>里程</th><th>耗时</th>
    <th>主要路段</th><th>过路费</th><th>提示</th></tr></thead>
    <tbody>{drive_table()}</tbody>
  </table>
</div>

<div class="card">
  <h3>费用估算</h3>
  <table class="tbl">
    <thead><tr><th>项目</th><th>估算</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td><strong>油费</strong></td><td class="num">500 – 650 元</td>
        <td>总里程 ~800 km，百公里 8L，7.5 元/L</td></tr>
      <tr><td><strong>过路费</strong></td><td class="num">0 – 360 元</td>
        <td>国庆 10/1–10/8 高速免费；9/30 夜上虞段约 85–105 元自付</td></tr>
      <tr><td><strong>停车费</strong></td><td class="num">60 – 150 元</td>
        <td>景区停车 10–20 元/次，酒店多含免费停车</td></tr>
      <tr><td><strong>合计</strong></td><td class="num"><strong>650 – 1160 元</strong></td>
        <td>高速免费可直接省 250 元以上</td></tr>
    </tbody>
  </table>
</div>

<h2>三、门票与预约</h2>
<div class="card">
  <table class="tbl">
    <thead><tr><th>景点</th><th>门票</th><th>片区</th><th>预约要求</th></tr></thead>
    <tbody>
      <tr><td><strong>上虞段（全部免费）</strong></td><td><b class="free">免费</b></td>
        <td>上虞</td><td>—</td></tr>
      <tr><td>覆卮山</td><td><b class="free">免费</b></td><td>上虞</td><td>—</td></tr>
      <tr><td>丰惠古镇 / 曹娥庙 / 中华孝德园</td><td><b class="free">免费</b></td>
        <td>上虞</td><td>—</td></tr>
      <tr><td>鲁迅故里</td><td><b class="free">免费</b></td><td>绍兴</td>
        <td>建议提前在官方公众号预约</td></tr>
      <tr><td><strong>新昌大佛寺</strong></td><td>线上 66–70 元<br><small>现场 80 元</small></td>
        <td>新昌</td><td>—</td></tr>
      <tr><td><strong>十九峰</strong>（含飞龙栈道/千丈幽谷）</td>
        <td>线上 53–56 元<br><small>现场 60 元</small></td><td>新昌</td><td>—</td></tr>
      <tr><td>十九峰小火车</td><td>25–30 元</td><td>新昌</td><td>—</td></tr>
      <tr><td>沃洲湖</td><td>约 60 元（含船票）</td><td>新昌</td><td>—</td></tr>
      <tr><td><strong>天姥山</strong></td><td><b class="free">免费</b></td><td>新昌</td>
        <td>⚠️ <strong>需提前预约</strong>，16:30 闭园</td></tr>
      <tr><td>越剧小镇</td><td>约 68 元</td><td>新昌</td><td>部分演出可免费观看</td></tr>
    </tbody>
  </table>
  <div class="alert warn" style="margin-top:16px">
    <span class="ic">⚠️</span>
    <div><strong>购票避坑</strong>：新昌大佛寺在售大量「人工讲解服务」票（128–587 元），
    <strong>不要买错</strong>，只需景区门票。线上购票比现场便宜 12–14 元/人。</div>
  </div>
  <div class="alert info">
    <span class="ic">💡</span>
    <div><strong>上虞是这次行程的性价比高点</strong>——覆卮山、丰惠古镇、曹娥庙、中华孝德园
    全部免费，且游客密度远低于绍兴古城与新昌。</div>
  </div>
</div>

<h2>四、住宿安排</h2>
<div class="card">
  <table class="tbl">
    <thead><tr><th>日期</th><th>位置</th><th>晚数</th><th>建议</th><th>理由</th></tr></thead>
    <tbody>{hotel_rows}</tbody>
  </table>
  <div class="alert tip" style="margin-top:16px">
    <span class="ic">💡</span>
    <div><strong>住处选择逻辑</strong>：新昌连住 3 晚不换酒店。县城（海洋城/世贸广场一带）
    连锁酒店 150–300 元，去大佛寺 1.1 km、十九峰 16 km、天姥山 16 km，车程均在 30 分钟内——
    比住景区民宿更灵活也更便宜。</div>
  </div>
</div>

<h2>五、备选地点</h2>
<div class="card">
  <table class="tbl">
    <thead><tr><th>地点</th><th>片区</th><th>门票</th><th>说明</th></tr></thead>
    <tbody>{alt_rows}</tbody>
  </table>
</div>

<h2>六、预算明细</h2>
<div class="card">
  <h3>按 2 人 1 车计</h3>
  <table class="tbl">
    <thead><tr><th>项目</th><th>金额</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td>景点门票</td><td class="num">{total_tickets*2} 元</td>
        <td>{n_spots} 个主景点 × 2 人（上虞段免费）</td></tr>
      <tr><td>餐饮</td><td class="num">3,200 – 4,000 元</td>
        <td>6 天 × 2 人，含上虞三臭/江鲜、绍兴寻宝记、新昌炒年糕</td></tr>
      <tr><td>住宿</td><td class="num">1,600 – 2,400 元</td><td>5 晚（上虞 2 + 新昌 3）</td></tr>
      <tr><td>油费</td><td class="num">500 – 650 元</td><td>全程约 800 km</td></tr>
      <tr><td>过路费</td><td class="num">0 – 360 元</td><td>国庆免费，9/30 夜段自付</td></tr>
      <tr><td>停车费</td><td class="num">60 – 150 元</td><td>景区停车</td></tr>
      <tr style="background:#fdf8f0"><td><strong>合计</strong></td>
        <td class="num"><strong>5,700 – 8,000 元</strong></td>
        <td>人均 2,850 – 4,000 元</td></tr>
    </tbody>
  </table>
  <h3 style="margin-top:26px">按 4 人 1 车计（更划算）</h3>
  <table class="tbl">
    <thead><tr><th>项目</th><th>金额</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td>景点门票</td><td class="num">{total_tickets*4} 元</td><td>4 人</td></tr>
      <tr><td>餐饮</td><td class="num">6,400 – 8,000 元</td><td>4 人</td></tr>
      <tr><td>住宿</td><td class="num">3,200 – 4,800 元</td><td>需 2 间房 × 5 晚</td></tr>
      <tr><td>油费+过路费+停车</td><td class="num">560 – 1,160 元</td>
        <td>4 人分摊，人均仅 140–290 元</td></tr>
      <tr style="background:#fdf8f0"><td><strong>合计</strong></td>
        <td class="num"><strong>10,500 – 14,500 元</strong></td>
        <td><strong>人均 2,600 – 3,600 元</strong></td></tr>
    </tbody>
  </table>
  <div class="alert tip" style="margin-top:16px">
    <span class="ic">💰</span>
    <div>人越多自驾越省——油费过路费是固定成本，4 人分摊后人均交通降到 290 元以下。</div>
  </div>
</div>

<h2>七、时效与风险提示</h2>
<div class="card">
  <table class="tbl risk">
    <thead><tr><th>风险项</th><th>说明与对策</th></tr></thead>
    <tbody>
      <tr><td>⚠️ 9/30 夜驾疲劳</td>
        <td>单程 190–230 km，约 2.5 小时。建议 19:00 前出发，中途嘉兴服务区休息一次；
        若超过 22:30 抵达，考虑嘉兴或绍兴先住一晚再走</td></tr>
      <tr><td>⚠️ 10/2 连续转场</td>
        <td>全程最紧的一天：上虞→绍兴（45 km）→新昌（100 km），合计 145 km + 午餐。
        <strong>务必 13:30 前离开绍兴</strong>，否则 16:00 前到不了新昌</td></tr>
      <tr><td>⚠️ 国庆高速拥堵</td>
        <td>10/1 上午出城方向、10/5 下午返程方向最堵。10/5 建议
        <strong>12:30 前上高速</strong>，避开 14:00–19:00 高峰</td></tr>
      <tr><td>⚠️ 覆卮山山路</td>
        <td>城区→覆卮山约 32 km 山路，弯多。国庆上午车流集中，
        建议 8:00 前上山，停车位有限</td></tr>
      <tr><td>⚠️ 天姥山预约</td>
        <td>天姥山<strong>需提前预约</strong>且 16:30 闭园。国庆名额紧张，出行前务必在官方渠道预约</td></tr>
      <tr><td>⚠️ 新昌票价差异</td>
        <td>大佛寺线上 66 元 vs 现场 80 元；十九峰线上 53 元 vs 现场 60 元。
        建议提前在携程/同程线上购票</td></tr>
      <tr><td>⚠️ 活动未确证</td>
        <td>检索到绍兴「国庆全民绍兴秀」街头艺术、新昌「贺中秋迎国庆」文艺汇演等官方活动，
        但<strong>具体排期以官方公众号为准</strong>，本次未获取到逐日时间表</td></tr>
      <tr><td>⚠️ 十九峰体力</td>
        <td>含飞龙栈道（玻璃栈道）+ 千丈幽谷，步行较多。穿防滑运动鞋，带足水。
        10 月浙东午后仍可能 28℃+</td></tr>
      <tr><td>⚠️ 高速免费时间差</td>
        <td>免费从 10/1 00:00 起算，按<strong>下高速时间</strong>判定。9/30 夜上高速、
        10/1 后下高速可免费；若 10/1 前下高速则需付费</td></tr>
    </tbody>
  </table>
  <h3 style="margin-top:26px">出行前必做</h3>
  <ol style="font-size:13.5px;line-height:2;padding-left:22px;margin:0">
    <li><strong>预约天姥山</strong>（官方渠道，国庆名额紧张）</li>
    <li><strong>线上购买新昌大佛寺 + 十九峰门票</strong>（比现场省 12–14 元/人）</li>
    <li><strong>确认住宿</strong>：上虞 2 晚 + 新昌 3 晚，尽早订（国庆涨价快）</li>
    <li><strong>查绍兴文旅 / 新昌文旅官方公众号</strong>，确认国庆活动排期</li>
    <li><strong>车辆检查</strong>：轮胎、胎压、刹车、雨刮、机油（10 月浙东多雨）</li>
    <li><strong>下载离线地图</strong>：新昌山区（天姥山、十九峰）信号可能不稳</li>
  </ol>
</div>

<h2>八、信源质量说明</h2>
<div class="card">
  <p style="font-size:14px">本次共检索 <strong>120 条</strong>结果，经规则引擎自动分级：</p>
  <table class="tbl">
    <thead><tr><th>等级</th><th>数量</th><th>含义</th></tr></thead>
    <tbody>
      <tr><td><strong>A 权威</strong></td><td class="num">31 条</td>
        <td>新华网、新昌县政府、本地宝、高德、知乎精品攻略、12306</td></tr>
      <tr><td><strong>C 待核</strong></td><td class="num">81 条</td>
        <td>携程/同程/去哪儿/境外OTA，票价与时效需现场核对</td></tr>
      <tr><td><strong>D 噪声</strong></td><td class="num">8 条</td>
        <td>地域无关内容（境外活动表、无关诉讼案等），已排除</td></tr>
    </tbody>
  </table>
  <div class="sources" style="margin-top:24px">
    <div>
      <h4>A 级信源清单（可点击复核）</h4>
      <ul>{src_a}</ul>
    </div>
    <div>
      <h4>C 级信源（内容可用，价格需核对）</h4>
      <ul>{src_c}</ul>
    </div>
  </div>
  <div class="alert warn" style="margin-top:20px">
    <span class="ic">⚠️</span>
    <div><strong>票价核对提醒</strong>：新昌各景区票价在不同平台差异较大
    （大佛寺 66/70/80，十九峰 53/56/60），本报告取线上优惠价区间。
    <strong>以购票平台实际显示为准</strong>。</div>
  </div>
</div>

<footer>
  报告由 trip-plan 规则引擎生成 · 多片区自驾扩展版<br>
  anysearch 实时检索 120 条 · 生成时间 {date.today().isoformat()}
</footer>
</div>
</body>
</html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="多片区自驾报告（HTML，定制样例）")
    ap.add_argument("--itin", help="itinerary.json 路径（默认自动查找）")
    ap.add_argument("--out", default="自驾行程.html")
    args = ap.parse_args()

    if args.itin:
        RM.load_itin(args.itin)
    h = build()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(h)
    print(f"HTML → {args.out}  ({len(h)} 字符)")
