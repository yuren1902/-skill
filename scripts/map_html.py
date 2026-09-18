# -*- coding: utf-8 -*-
"""行程可视化地图生成器 —— 腾讯地图 GL JS（免 Key 代理模式）

合规说明
--------
按 WorkBuddy「地图合规」规范，中国境内地图渲染只允许腾讯地图 / 高德 / 百度 / 天地图。
高德、百度的公开接口需要自行申请 Key，本脚本**不内置任何 Key**；
这里采用腾讯地图 GL JS 的 **官方代理模式**（`_TMapSecurityConfig`），
前端零 Key 暴露，由 WorkBuddy 后端持 Key —— 开箱即用。

输入：itinerary.json（需含 GCJ-02 经纬度）
输出：单文件 HTML，内嵌逐日路线。用浏览器/预览面板直接打开即可。

用法：
    python map_html.py                      # 默认读同目录 itinerary.json，输出 map.html
    python map_html.py --itin X.json --out Y.html
"""
import argparse
import json
import math
import os

# 逐日配色（浅蓝系明亮配色，按天区分色相）
DAY_COLORS = ["#3d8bfd", "#22b3a6", "#f2a93b", "#7c5cf0", "#4dc4e8", "#8b9db0"]

# 每个景点用的图标（emoji 兜底，也能换成内联 SVG）
STATION_ICON = "🚄"


def haversine(a, b):
    """两点直线距离（km）。a/b 为 (lng, lat)。"""
    R = 6371.0
    la1, lo1 = math.radians(a[1]), math.radians(a[0])
    la2, lo2 = math.radians(b[1]), math.radians(b[0])
    x = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


def drive_estimate(straight_km):
    """直线距离 → 车程估算。

    自驾绕行系数取 1.35（宜兴丘陵路网实测经验值），均速取 45 km/h
    （含红绿灯与山路降速）。仅用于地图标签的量级提示，非精确导航数据。
    """
    road_km = straight_km * 1.35
    minutes = road_km / 45.0 * 60.0
    return road_km, minutes


def build_payload(itin, station_name=None, cross_text=None):
    """把 itinerary.json 压成前端要用的最小结构。

    设计原则（2026-09-17 修订）：**地图只回答「到了目的地之后怎么走」**。
      · 跨城长途（上海 ↔ 目的地）不画线、不放标记，改为侧栏一段文字说明 ——
        否则 800 km 的跨城段会把几十公里的市内动线压成一个点，地图失去意义。
      · 视野 fitBounds **只用景点坐标**，车站锚点与备选点不参与，避免被离群点撑大。
      · 每条段带 kind 字段：
          inter  —— 景点 ↔ 景点（地图的主角，正常绘制）
          access —— 车站 ↔ 首/末景点（接驳段，弱化绘制）
    """
    anchors = itin.get("anchors", {})
    station = None
    if station_name:
        station = anchors.get(station_name)
    else:
        for k, v in anchors.items():
            if isinstance(v, dict) and v.get("kind") == "station":
                station, station_name = v, k
                break
        if station is None and anchors:
            station_name = next(iter(anchors))
            station = anchors[station_name]

    days = []
    for d in itin["days"]:
        pts = []
        for a in d["attractions"]:
            pts.append({
                "name": a["name"],
                "lng": a["lng"], "lat": a["lat"],
                "dur": a.get("duration_min"),
                "ticket": a.get("ticket_price") or 0,
                "type": a.get("type") or "",
                "region": a.get("region") or "",
            })
        days.append({
            "idx": d["day_index"],
            "date": d["date"],
            "weekday": d.get("weekday") or "",
            "label": d.get("label") or "",
            "region": d.get("region") or "",
            "span": d.get("max_span_km"),
            "total_min": d.get("total_duration_min"),
            "tickets": d.get("tickets"),
            "points": pts,
        })

    # 逐日相邻点距离（首末两天从车站起算/回到车站），并标注段类型
    for di, day in enumerate(days):
        chain = []
        is_first = (di == 0)
        is_last = (di == len(days) - 1)
        if is_first and station:
            chain.append({"name": "%s（取车）" % station_name,
                          "lng": station["lng"], "lat": station["lat"],
                          "kind": "station"})
        for p in day["points"]:
            chain.append({"name": p["name"], "lng": p["lng"], "lat": p["lat"],
                          "kind": "spot"})
        if is_last and station:
            chain.append({"name": "%s（还车）" % station_name,
                          "lng": station["lng"], "lat": station["lat"],
                          "kind": "station"})
        legs = []
        for i in range(len(chain) - 1):
            a, b = chain[i], chain[i + 1]
            s = haversine((a["lng"], a["lat"]), (b["lng"], b["lat"]))
            rk, mn = drive_estimate(s)
            legs.append({
                "from": a["name"], "to": b["name"],
                "straight": round(s, 1),
                "road": round(rk, 1),
                "min": int(round(mn)),
                # 两端都是景点 = 地图主角；含车站 = 接驳段
                "kind": "inter" if (a["kind"] == "spot" and b["kind"] == "spot")
                        else "access",
            })
        day["chain"] = chain
        day["legs"] = legs
        # 只统计景点之间的段，接驳段单列，避免"当日车程"被机场/车站段污染
        day["leg_inter_km"] = round(
            sum(l["road"] for l in legs if l["kind"] == "inter"), 1)
        day["leg_access_km"] = round(
            sum(l["road"] for l in legs if l["kind"] == "access"), 1)
        day["leg_total_km"] = round(sum(l["road"] for l in legs), 1)
        day["leg_total_min"] = sum(l["min"] for l in legs)

    # 主要备选（默认在地图上隐藏，按需打开）
    alts = []
    for a in itin.get("alternatives", []):
        # 两种格式都要吃：多片区 planner 的 alternatives 是 dict 列表（含坐标），
        # 单目的地 planner 的 alternatives 只是名字字符串 → 跳过（定位不到）
        if not isinstance(a, dict):
            continue
        if a.get("lng") is not None:
            alts.append({"name": a["name"], "lng": a["lng"], "lat": a["lat"],
                         "ticket": a.get("price") or 0,
                         "dur": a.get("duration_min")})

    return {
        "days": days,
        "station": station,
        "station_name": station_name,
        "city_center": itin.get("city_center"),
        "alternatives": alts,
        "coord_sys": itin.get("coord_sys", "GCJ-02"),
        # 跨城段不画在地图上，只作为文字出现在侧栏。
        # 取值顺序：显式参数 > itinerary.json 的 "cross" 字段 > 空
        "cross": cross_text or itin.get("cross") or "",
    }


def render_html(payload, title="宜兴 4 日自驾 · 路线地图"):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    colors = json.dumps(DAY_COLORS, ensure_ascii=False)
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  html,body{margin:0;padding:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,
    "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  #app{display:flex;height:100vh;overflow:hidden}
  #map{flex:1;height:100vh;min-width:0}
  #side{
    width:340px;flex:0 0 340px;height:100vh;overflow-y:auto;
    background:#f7fbff;border-left:1px solid #dfe9f3;padding:16px 16px 40px;
    box-sizing:border-box;font-size:13.5px;color:#1b2733;
  }
  #side h1{font-size:17px;margin:0 0 4px;padding-bottom:10px;
    border-bottom:2px solid #3d8bfd}
  #side .sub{color:#8698a8;font-size:12px;margin-bottom:14px;line-height:1.6}
  .daycard{background:#fff;border:1px solid #dfe9f3;border-radius:9px;
    margin-bottom:12px;overflow:hidden;box-shadow:0 1px 3px rgba(31,74,120,.07)}
  .dayhead{display:flex;align-items:center;gap:8px;padding:9px 12px;
    cursor:pointer;user-select:none;background:#eef5fd}
  .dayhead:hover{background:#e4f0fd}
  .dot{width:11px;height:11px;border-radius:50%;flex:0 0 11px}
  .dayhead .dt{font-weight:650;font-size:13px}
  .dayhead .meta{margin-left:auto;color:#8698a8;font-size:11.5px;white-space:nowrap}
  .daybody{padding:8px 12px 12px;display:none}
  .daycard.open .daybody{display:block}
  .legs{font-size:12.5px;line-height:1.95}
  .leg{display:flex;gap:6px;align-items:baseline}
  .leg .n{color:#4a5a6a}
  .leg .km{color:#2f7ae0;font-weight:600;white-space:nowrap}
  .leg .arw{color:#c8d6e4}
  .leg.access{opacity:.6}
  .leg.access .km{color:#8698a8;font-weight:500}
  .leg .kkt{font-size:10.5px;color:#a8b6c4;border:1px solid #e3ebf3;
    border-radius:9px;padding:0 5px;white-space:nowrap}
  .crossbox{background:#fff;border:1px solid #dfe9f3;border-left:3px solid #2f7ae0;
    border-radius:8px;padding:9px 12px;margin-bottom:11px;font-size:12.5px;
    line-height:1.75;color:#4a5a6a}
  .crossbox b{color:#1b2733}
  .viewbar{display:flex;gap:6px;margin-bottom:11px}
  .viewbar button{flex:1;border:1px solid #dfe9f3;background:#fff;border-radius:7px;
    padding:6px 4px;font:inherit;font-size:12px;color:#4a5a6a;cursor:pointer}
  .viewbar button.on{background:#eaf3ff;border-color:#bcd9fb;color:#2a6dc9;font-weight:650}
  .lg i.sq{width:14px;height:5px;border-radius:2px}
  .lg i.sq.dash{background:repeating-linear-gradient(90deg,#9db4c9 0 4px,transparent 4px 7px)}
  .spot{padding:2px 0}
  .spot .nm{font-weight:600}
  .spot .tag{color:#8698a8;font-size:11.5px}
  .dayfoot{margin-top:8px;padding-top:8px;border-top:1px dashed #dfe9f3;
    color:#8698a8;font-size:11.5px;line-height:1.7}
  .legend{margin-top:6px;padding:10px 12px;background:#fff;border:1px solid #dfe9f3;
    border-radius:9px;font-size:12px;line-height:1.9;color:#4a5a6a}
  .lg{display:flex;align-items:center;gap:7px}
  .lg i{width:10px;height:10px;border-radius:50%;display:inline-block}
  .alt-legend{border-top:1px dashed #dfe9f3;margin-top:8px;padding-top:8px}
  #tipNote{color:#8698a8;font-size:11px;margin-top:10px;line-height:1.7}
  .info{font-size:12.5px;line-height:1.8}
  .info b{font-size:13.5px}
  .info .row{color:#4a5a6a}
  .info .badge{display:inline-block;padding:1px 7px;border-radius:20px;
    font-size:11px;background:#eaf3ff;color:#2a6dc9;margin-right:4px}
  .lbl{background:#fff;border:1px solid #dfe9f3;border-radius:5px;
    padding:2px 7px;font-size:11.5px;color:#1b2733;white-space:nowrap;
    box-shadow:0 1px 3px rgba(31,74,120,.12)}
  @media (max-width:820px){
    #app{flex-direction:column-reverse}
    #map{height:56vh;flex:none}
    #side{width:100%;flex:1 1 auto;height:auto;border-left:0;
      border-top:1px solid #dfe9f3}
  }
</style>
<!-- 1. 代理配置必须在 SDK 之前（零 Key 模式，前端不出现 key） -->
<script type="text/javascript">
  window._TMapSecurityConfig = {
    serviceHost: 'http://127.0.0.1:__WB_HTTP_PORT__/_TMapService/_wbt/__WB_TMAP_SECRET__',
  };
</script>
<!-- 2. 从官方 CDN 加载，不带 key 参数 -->
<script src="https://map.qq.com/api/gljs?v=1.exp"></script>
</head>
<body>
<div id="app">
  <div id="map"></div>
  <aside id="side">
    <h1>__TITLE__</h1>
    <div class="sub">
      按天分色 · 数字为相邻两点的<b>直线距离</b>与车程估算<br>
      坐标系 GCJ-02 ｜ 点击日期卡展开/收起
    </div>
    <div id="crossBox"></div>
    <div class="viewbar">
      <button id="viewSpot" class="on">仅景点</button>
      <button id="viewAll">含站点锚点</button>
      <button id="viewAlt">显示备选</button>
    </div>
    <div id="cards"></div>
    <div class="legend">
      <div class="lg"><i class="sq" style="background:__STATIONC__"></i>
        <span id="lgStation">行程锚点</span></div>
      <div id="lgDays"></div>
      <div class="lg"><i class="sq dash"></i>虚线段 = 车站接驳（不参与视野）</div>
      <div class="lg alt-legend" id="lgAlt"><i style="background:__ALTC__;opacity:.55"></i>
        主要备选（默认隐藏，点上方按钮显示）</div>
    </div>
    <div id="tipNote"></div>
  </aside>
</div>

<script>
const DATA = __DATA__;
const COLORS = __COLORS__;
const STATION_COLOR = "__STATIONC__";
const ALT_COLOR = "__ALTC__";

// ---------- 侧栏卡片 ----------
const cardsEl = document.getElementById("cards");
DATA.days.forEach((d, di) => {
  const color = COLORS[di % COLORS.length];
  const card = document.createElement("div");
  card.className = "daycard" + (di === 0 ? " open" : "");
  // 接驳段（车站↔首末景点）弱化显示，并打上「接驳」标签：
  // 它是行程的必须段，但不是需要判断"会不会绕路"的段
  const legsHtml = d.legs.map(l =>
    `<div class="leg ${l.kind === "access" ? "access" : ""}">
       <span class="n">${l.from}</span>
       <span class="arw">→</span><span class="n">${l.to}</span>
       <span class="km">${l.straight} km</span>
       <span class="tag">≈${l.min}分</span>
       ${l.kind === "access" ? '<span class="kkt">接驳</span>' : ""}</div>`).join("");
  const spotsHtml = d.points.map((p, i) =>
    `<div class="spot"><span class="nm">${i + 1}. ${p.name}</span>
       <span class="tag">· ${p.dur}分${p.ticket ? " · ￥" + p.ticket : " · 免费"}</span></div>`
  ).join("");
  card.innerHTML = `
    <div class="dayhead">
      <span class="dot" style="background:${color}"></span>
      <span class="dt">D${d.idx} ${d.date.slice(5)} ${d.weekday}</span>
      <span class="meta">${d.points.length}点 · 景间${d.leg_inter_km}km</span>
    </div>
    <div class="daybody">
      ${spotsHtml}
      <div class="legs" style="margin-top:9px">${legsHtml}</div>
      <div class="dayfoot">
        ${d.region}<br>
        当日净游览 ${d.total_min} 分钟 ｜ 景点间最大跨度 ${d.span} km<br>
        景点之间 ${d.leg_inter_km} km${d.leg_access_km ? " ＋ 接驳 " + d.leg_access_km + " km" : ""} ｜ 门票合计 ￥${d.tickets}
      </div>
    </div>`;
  card.querySelector(".dayhead").addEventListener("click", () => {
    card.classList.toggle("open");
  });
  cardsEl.appendChild(card);
});

// 跨城段：只给文字，不画在地图上（否则几十公里的市内动线会被压成一个点）
if (DATA.cross) {
  document.getElementById("crossBox").innerHTML =
    '<div class="crossbox"><b>城际：</b>' + DATA.cross +
    '<br><span style="color:#8698a8">城际段不在地图上绘制——地图只回答"到目的地之后怎么走"。</span></div>';
}
document.getElementById("lgStation").textContent =
  "行程锚点（" + (DATA.station_name || "车站") + "）";

// 图例逐日
document.getElementById("lgDays").innerHTML = DATA.days.map((d, di) =>
  `<div class="lg"><i style="background:${COLORS[di % COLORS.length]}"></i>
     D${d.idx} ${d.date.slice(5)} ${d.region}</div>`).join("");

// ---------- 地图 ----------
const map = new TMap.Map("map", {
  zoom: 10.4,
  center: new TMap.LatLng(DATA.city_center.lat, DATA.city_center.lng),
  pitch: 0,
});

// 收集全部几何
const markers = [];
const polylines = [];

// 锚点：宜兴站
if (DATA.station) {
  markers.push({
    id: "station",
    styleId: "station",
    position: new TMap.LatLng(DATA.station.lat, DATA.station.lng),
    properties: { title: DATA.station.label || "宜兴站" },
  });
}

// 逐日：折线 + 点位
DATA.days.forEach((d, di) => {
  const color = COLORS[di % COLORS.length];
  const chain = d.chain;
  // 折线
  polylines.push({
    id: "route-" + di,
    styleId: "route-" + di,
    paths: chain.map(p => new TMap.LatLng(p.lat, p.lng)),
  });
  // 起点/终点标记（若首末天，链首尾是车站，已在 station 里画过，避免重复）
  d.points.forEach((p, pi) => {
    markers.push({
      id: `d${di}-p${pi}`,
      styleId: "spot",
      position: new TMap.LatLng(p.lat, p.lng),
      properties: {
        title: p.name,
        day: `D${d.idx} ${d.date.slice(5)}`,
        order: pi + 1,
        dur: p.dur,
        ticket: p.ticket,
        color: color,
      },
    });
  });
});

// 主要备选（浅色小点）—— 默认不显示，避免与主行程混淆
const baseMarkers = markers.slice();
const altMarkers = [];
DATA.alternatives.forEach((a, ai) => {
  altMarkers.push({
    id: "alt-" + ai,
    styleId: "alt",
    position: new TMap.LatLng(a.lat, a.lng),
    properties: { title: a.name, alt: true, dur: a.dur, ticket: a.ticket },
  });
});

// --- 样式：内联 SVG 图标，不引用官方 demo 图 ---
const spotIcon = "data:image/svg+xml," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="34" viewBox="0 0 26 34">
     <path d="M13 1C6.4 1 1 6.4 1 13c0 8.5 12 20 12 20s12-11.5 12-20C25 6.4 19.6 1 13 1z"
           fill="#3d8bfd" stroke="#fff" stroke-width="2"/>
     <circle cx="13" cy="13" r="4.6" fill="#fff"/>
   </svg>`);
const stationIcon = "data:image/svg+xml," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="38" viewBox="0 0 30 38">
     <path d="M15 1C7.3 1 1 7.3 1 15c0 9.8 14 22 14 22s14-12.2 14-22C29 7.3 22.7 1 15 1z"
           fill="#2f7ae0" stroke="#fff" stroke-width="2"/>
     <rect x="8" y="9" width="14" height="11" rx="2" fill="#fff"/>
     <circle cx="11.5" cy="22.5" r="2" fill="#fff"/>
     <circle cx="18.5" cy="22.5" r="2" fill="#fff"/>
   </svg>`);
const altIcon = "data:image/svg+xml," + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">
     <circle cx="9" cy="9" r="7" fill="#7fb2ea" opacity="0.75" stroke="#fff" stroke-width="2"/>
   </svg>`);

const styles = {
  spot: new TMap.MarkerStyle({ width: 26, height: 34, anchor: { x: 13, y: 34 },
                               src: spotIcon }),
  station: new TMap.MarkerStyle({ width: 30, height: 38, anchor: { x: 15, y: 38 },
                                  src: stationIcon }),
  alt: new TMap.MarkerStyle({ width: 18, height: 18, anchor: { x: 9, y: 9 },
                              src: altIcon }),
};
DATA.days.forEach((d, di) => {
  styles["route-" + di] = new TMap.PolylineStyle({
    color: COLORS[di % COLORS.length], width: 5, borderWidth: 1,
    borderColor: "#ffffff", lineCap: "round",
  });
});

const polylineLayer = new TMap.MultiPolyline({
  map, styles,
  geometries: polylines,
});

const markerLayer = new TMap.MultiMarker({
  map, styles,
  geometries: baseMarkers,
});

// 点击标记 → 信息窗
const info = new TMap.InfoWindow({
  map,
  position: new TMap.LatLng(DATA.city_center.lat, DATA.city_center.lng),
  offset: { x: 0, y: -44 },
  enableCustom: true,
});
info.close();

markerLayer.on("click", (evt) => {
  const p = evt.geometry.properties || {};
  let html;
  if (p.alt) {
    html = `<div class="info"><b>${p.title}</b>
      <div class="row"><span class="badge">主要备选</span>
      ${p.dur ? p.dur + " 分钟" : ""}${p.ticket ? " · ￥" + p.ticket : " · 免费"}</div></div>`;
  } else if (p.day) {
    html = `<div class="info"><b>${p.title}</b>
      <div class="row"><span class="badge">${p.day}</span>
      第 ${p.order} 站</div>
      <div class="row">游览 ${p.dur} 分钟 ｜
      ${p.ticket ? "门票 ￥" + p.ticket : "免费"}</div></div>`;
  } else {
    html = `<div class="info"><b>${p.title || "宜兴站"}</b>
      <div class="row">行程起点 / 终点 · 高铁往返</div></div>`;
  }
  info.setContent(html);
  info.setPosition(evt.geometry.position);
  info.open();
});

// ---------- 视野与图层开关 ----------
// 关键设计：默认 fitBounds 只用**景点坐标**。
// 一旦把车站锚点或备选点算进去，一个离群点就能把视野撑大三四倍，
// 主行程被压成一团 —— 地图就白做了。
const spotLL = [];
DATA.days.forEach(d => d.points.forEach(p => spotLL.push(new TMap.LatLng(p.lat, p.lng))));
const hubLL = [];
if (DATA.station) hubLL.push(new TMap.LatLng(DATA.station.lat, DATA.station.lng));

let showAux = false, showAlt = false;
function refit() {
  let pts = spotLL.slice();
  if (showAux) pts = pts.concat(hubLL);
  if (showAlt) pts = pts.concat(altMarkers.map(m => m.position));
  if (pts.length) map.fitBounds(new TMap.LatLngBounds(pts), { padding: 70 });
}
function refreshLayer() {
  markerLayer.setGeometries(showAlt ? baseMarkers.concat(altMarkers) : baseMarkers);
  refit();
}
refit();

const bAux = document.getElementById("viewAll");
const bAlt = document.getElementById("viewAlt");
bAux.addEventListener("click", () => {
  showAux = !showAux;
  bAux.classList.toggle("on", showAux);
  refit();
});
bAlt.addEventListener("click", () => {
  showAlt = !showAlt;
  bAlt.classList.toggle("on", showAlt);
  refreshLayer();
});
window.addEventListener("resize", refit);

document.getElementById("tipNote").innerHTML =
  "直线距离为球面大圆距离；车程按绕行系数 1.35、均速 45 km/h 估算，" +
  "仅供规划参考，实际请以导航为准。视野默认只框住景点范围。";
</script>
</body>
</html>
""".replace("__TITLE__", title) \
   .replace("__DATA__", data) \
   .replace("__COLORS__", colors) \
   .replace("__STATIONC__", "#2f7ae0") \
   .replace("__ALTC__", "#7fb2ea")


# ============================================================
# 内嵌片段：供报告合并（md2html.py 调用）
# ------------------------------------------------------------
# 与整页版的区别：
#   - 不带 <html>/<body>，只有地图容器 + <script>
#   - 地图容器高度由报告 CSS 的 .mapwrap #<mapid> 控制
#   - 变量名加后缀，避免与报告脚本作用域冲突
# 关键：`__WB_HTTP_PORT__` / `__WB_TMAP_SECRET__` 必须原样保留，
#       由 WorkBuddy 运行时注入，切勿替换为真实值或做 HTML 转义。
# ============================================================
EMBED_TMPL = """<div class="mapwrap">
  <div class="mapbar-top" id="__MAPID__bar"></div>
  <div class="crossbox" id="__MAPID__cross" style="display:none"></div>
  <div id="__MAPID__"></div>
  <div class="mapfoot">
    数字为相邻两点<b>直线距离</b>与车程估算（绕行系数 1.35、均速 45 km/h）。
    坐标系 GCJ-02，与底图一致。实际里程请以导航为准。<br>
    <span style="color:#a8b6c4">视野默认只框住景点；城际段（出发地 ↔ 目的地）不在图上绘制。</span>
  </div>
</div>
<style>
  .mapbar-top .mb-title{font-weight:650;color:#1b2733;margin-right:10px}
  .mapbar-top .mb-note{color:#8698a8;font-size:11.5px;margin-right:auto}
  .mapbar-top .mb-btn{border:1px solid #dfe9f3;background:#fff;border-radius:6px;
    padding:3px 9px;font:inherit;font-size:11.5px;color:#4a5a6a;cursor:pointer;margin-left:5px}
  .mapbar-top .mb-btn.on{background:#eaf3ff;border-color:#bcd9fb;color:#2a6dc9;font-weight:650}
  .mapwrap .crossbox{background:#fff;border:1px solid #dfe9f3;border-left:3px solid #2f7ae0;
    border-radius:8px;padding:8px 12px;margin:0 0 10px;font-size:12.5px;
    line-height:1.75;color:#4a5a6a}
  .mapwrap .crossbox b{color:#1b2733}
</style>
<script type="text/javascript">
  window._TMapSecurityConfig = {
    serviceHost: 'http://127.0.0.1:__WB_HTTP_PORT__/_TMapService/_wbt/__WB_TMAP_SECRET__',
  };
</script>
<script src="https://map.qq.com/api/gljs?v=1.exp"></script>
<script>
(function(){
  var __tries = 0;
  function boot(){
    if (typeof TMap === "undefined") {
      // SDK 异步加载中；最多等约 10 秒
      if (++__tries > 100) {
        document.getElementById("__MAPID__").innerHTML =
          '<div style="padding:28px;text-align:center;color:#8698a8;font-size:13px">' +
          '地图组件未能加载。请确认已联网，并通过 WorkBuddy 预览面板/本地服务打开本页' +
          '（以 file:// 直接双击时，免 Key 代理无法生效）。</div>';
        return;
      }
      return setTimeout(boot, 100);
    }
    initMap();
  }

  function initMap(){
  var D__SFX__ = __DATA__;
  var C__SFX__ = __COLORS__;
  var STATION_C__SFX__ = "__STATIONC__";
  var ALT_C__SFX__ = "__ALTC__";

  var map = new TMap.Map("__MAPID__", {
    zoom: 10.4,
    center: new TMap.LatLng(D__SFX__.city_center.lat, D__SFX__.city_center.lng),
    pitch: 0,
  });

  var baseMarkers = [], altMarkers = [], altLL = [], polylines = [];
  if (D__SFX__.station) {
    baseMarkers.push({
      id: "station", styleId: "station",
      position: new TMap.LatLng(D__SFX__.station.lat, D__SFX__.station.lng),
      properties: { title: (D__SFX__.station.label || D__SFX__.station_name || "车站") },
    });
  }
  D__SFX__.days.forEach(function(d, di){
    polylines.push({
      id: "route-" + di, styleId: "route-" + di,
      paths: d.chain.map(function(p){ return new TMap.LatLng(p.lat, p.lng); }),
    });
    d.points.forEach(function(p, pi){
      baseMarkers.push({
        id: "d" + di + "-p" + pi, styleId: "spot",
        position: new TMap.LatLng(p.lat, p.lng),
        properties: { title: p.name, day: "D" + d.idx + " " + d.date.slice(5),
                      order: pi + 1, dur: p.dur, ticket: p.ticket },
      });
    });
  });
  // 备选点默认不进图层，点"显示备选"才追加（setGeometries）
  (D__SFX__.alternatives || []).forEach(function(a, ai){
    var ll = new TMap.LatLng(a.lat, a.lng);
    altLL.push(ll);
    altMarkers.push({
      id: "alt-" + ai, styleId: "alt", position: ll,
      properties: { title: a.name, alt: true, dur: a.dur, ticket: a.ticket },
    });
  });

  var spotIcon = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="34" viewBox="0 0 26 34">' +
    '<path d="M13 1C6.4 1 1 6.4 1 13c0 8.5 12 20 12 20s12-11.5 12-20C25 6.4 19.6 1 13 1z"' +
    ' fill="#3d8bfd" stroke="#fff" stroke-width="2"/><circle cx="13" cy="13" r="4.6" fill="#fff"/></svg>');
  var stationIcon = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="38" viewBox="0 0 30 38">' +
    '<path d="M15 1C7.3 1 1 7.3 1 15c0 9.8 14 22 14 22s14-12.2 14-22C29 7.3 22.7 1 15 1z"' +
    ' fill="#2f7ae0" stroke="#fff" stroke-width="2"/><rect x="8" y="9" width="14" height="11"' +
    ' rx="2" fill="#fff"/><circle cx="11.5" cy="22.5" r="2" fill="#fff"/>' +
    '<circle cx="18.5" cy="22.5" r="2" fill="#fff"/></svg>');
  var altIcon = "data:image/svg+xml," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">' +
    '<circle cx="9" cy="9" r="7" fill="#7fb2ea" opacity="0.75" stroke="#fff" stroke-width="2"/></svg>');

  var styles = {
    spot: new TMap.MarkerStyle({ width: 26, height: 34, anchor: {x:13,y:34}, src: spotIcon }),
    station: new TMap.MarkerStyle({ width: 30, height: 38, anchor: {x:15,y:38}, src: stationIcon }),
    alt: new TMap.MarkerStyle({ width: 18, height: 18, anchor: {x:9,y:9}, src: altIcon }),
  };
  D__SFX__.days.forEach(function(d, di){
    styles["route-" + di] = new TMap.PolylineStyle({
      color: C__SFX__[di % C__SFX__.length], width: 5, borderWidth: 1,
      borderColor: "#ffffff", lineCap: "round",
    });
  });

  new TMap.MultiPolyline({ map: map, styles: styles, geometries: polylines });
  var markerLayer = new TMap.MultiMarker({ map: map, styles: styles, geometries: baseMarkers });

  var info = new TMap.InfoWindow({
    map: map,
    position: new TMap.LatLng(D__SFX__.city_center.lat, D__SFX__.city_center.lng),
    offset: { x: 0, y: -44 }, enableCustom: true,
  });
  info.close();

  markerLayer.on("click", function(evt){
    var p = evt.geometry.properties || {}, html;
    if (p.alt) {
      html = '<div class="info"><b>' + p.title + '</b><div class="row">' +
        '<span class="badge">主要备选</span>' + (p.dur ? p.dur + " 分钟" : "") +
        (p.ticket ? " · ￥" + p.ticket : " · 免费") + '</div></div>';
    } else if (p.day) {
      html = '<div class="info"><b>' + p.title + '</b><div class="row">' +
        '<span class="badge">' + p.day + '</span>第 ' + p.order + ' 站</div>' +
        '<div class="row">游览 ' + p.dur + ' 分钟 ｜ ' +
        (p.ticket ? "门票 ￥" + p.ticket : "免费") + '</div></div>';
    } else {
      html = '<div class="info"><b>' + (p.title || "车站") + '</b>' +
        '<div class="row">行程起点 / 终点 · 高铁往返</div></div>';
    }
    info.setContent(html);
    info.setPosition(evt.geometry.position);
    info.open();
  });

  // 视野：默认**只框住景点**（不含车站/备选），避免离群点把主行程压成一团
  var spotLL = [];
  D__SFX__.days.forEach(function(d){
    d.points.forEach(function(p){ spotLL.push(new TMap.LatLng(p.lat, p.lng)); });
  });
  var hubLL = [];
  if (D__SFX__.station) hubLL.push(new TMap.LatLng(D__SFX__.station.lat, D__SFX__.station.lng));
  var curHub = false, curAlt = false;
  function refit(hub, alt){
    curHub = hub; curAlt = alt;
    var pts = spotLL.slice();
    if (hub) pts = pts.concat(hubLL);
    if (alt) pts = pts.concat(altLL);
    if (pts.length) map.fitBounds(new TMap.LatLngBounds(pts), { padding: 70 });
  }
  refit(false, false);

  var bar = document.getElementById("__MAPID__bar");
  if (bar) {
    bar.innerHTML = '<span class="mb-title">路线地图</span>' +
      '<span class="mb-note">按天分色 · 点标记看详情</span>' +
      '<button class="mb-btn on" data-a="spot">仅景点</button>' +
      '<button class="mb-btn" data-a="hub">含站点锚点</button>' +
      '<button class="mb-btn" data-a="alt">显示备选</button>';
    bar.addEventListener("click", function(e){
      var a = e.target.getAttribute && e.target.getAttribute("data-a");
      if (!a) return;
      if (a === "spot") {
        bar.querySelectorAll(".mb-btn").forEach(function(b){ b.classList.remove("on"); });
        e.target.classList.add("on");
        refit(false, false);
      } else {
        var on = e.target.classList.toggle("on");
        if (a === "alt") markerLayer.setGeometries(on ? baseMarkers.concat(altMarkers) : baseMarkers);
        refit(a === "hub" ? on : curHub, a === "alt" ? on : curAlt);
      }
      e.target.blur();
    });
  }

  // 城际段（出发地 ↔ 目的地）不上图，但要以文字交代清楚，否则「简化」会变成「丢失」
  var crossBox = document.getElementById("__MAPID__cross");
  if (crossBox && D__SFX__.cross) {
    crossBox.innerHTML = '<b>城际段</b>（不在图上绘制）　' + D__SFX__.cross;
    crossBox.style.display = "";
  }

  // 容器尺寸变化后重算视野（切换 Tab 时地图初始为 display:none，需重绘）
  window.addEventListener("resize", function(){ refit(curHub, curAlt); });
  window.__rebuildMap__ = function(){ refit(curHub, curAlt); };
  }  // initMap

  boot();
})();
</script>
"""


def render_embed(payload, map_id="tmapEmbed", suffix="_e"):
    """生成可嵌入报告的地图片段（不含 html/head/body）。"""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    colors = json.dumps(DAY_COLORS, ensure_ascii=False)
    return EMBED_TMPL.replace("__MAPID__", map_id) \
                     .replace("__SFX__", suffix) \
                     .replace("__DATA__", data) \
                     .replace("__COLORS__", colors) \
                     .replace("__STATIONC__", "#2f7ae0") \
                     .replace("__ALTC__", "#7fb2ea")


# ============================================================
# 内联 SVG 示意图（先生 2026-09-17 指示）
# ------------------------------------------------------------
# 为什么要有它：
#   腾讯地图 GL JS 走免 Key 代理模式，serviceHost 依赖 __WB_HTTP_PORT__
#   占位符**运行时**注入。但报告以静态 HTML 交付（present_files / 本地文件），
#   没人替换那个占位符 → SDK 请求 http://127.0.0.1:__WB_HTTP_PORT__/ → 地图空白。
#
#   SVG 示意图：零外部依赖、免 Key、离线可渲染、可打印、可嵌邮件，
#   且**不引用任何瓦片**，境内合规上最干净。
#
# 为什么是"两层分面"：
#   第一版做单幅等距投影 → 实测失败：福州城区 6 个点相距 3~5 km，
#   而福州↔平潭单段 125 km，整图比例尺被长途拉平，城区缩成一团。
#   第二版改成"每天一格、各自比例尺" → D0/D1/D3 好了，但 **D2 仍然坏**：
#   一天之内既跑平潭南线（3 点相距 15 km）又返回福州（125 km 外），
#   格内比例尺又被拉平。
#
#   所以最终是**两层分面**：
#     第 1 层  每「天」一格
#     第 2 层  格内按距离**再分簇**（单链聚类，25 km 阈值），每簇一个子格、各自比例尺
#   跨簇/跨天的长途**一律不画线**，只在文字里列出。
#   这才是"做减法"在版面上的彻底落实。
#
# 定位：**示意图，不是底图**。位置按经纬度等距投影，连线是点对点直线，
# 不表示道路几何。它回答的是「景点之间谁挨着谁、今天要跑多远」。
# ============================================================
def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _nice_km(target):
    """挑一个好看的整刻度（1/2/5/10/20/50/100 km）"""
    for v in (1, 2, 5, 10, 20, 50, 100, 200):
        if v >= target:
            return v
    return 500


def _span(pts):
    """一组点的最大两两距离（km）"""
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = haversine((pts[i]["lng"], pts[i]["lat"]),
                          (pts[j]["lng"], pts[j]["lat"]))
            best = max(best, d)
    return best


def _cluster(nodes, thr_km=25.0):
    """单链聚类：相互距离 < thr 的点归为一簇。返回 [[下标], ...]"""
    n = len(nodes)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine((nodes[i]["lng"], nodes[i]["lat"]),
                          (nodes[j]["lng"], nodes[j]["lat"]))
            if d < thr_km:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    # 按簇内点数降序，让主簇排在前面
    return sorted(groups.values(), key=lambda g: -len(g))


def _proj(nodes, x0, y0, w, h, pad_x=46, pad_t=24, pad_b=48,
          min_span_deg=0.0):
    """局部投影：返回 (f(lng,lat)->(x,y), px_per_km)

    每格独立调比例尺 —— 分面图的核心。
    上下留白**刻意不对称**：顶部要给子格标题让位，底部要给比例尺和标记让位，
    否则最下面那个点会被比例尺压住（实测踩过）。
    `min_span_deg` 用于兜底：避免"单点簇"放大到失真。
    """
    lngs = [p["lng"] for p in nodes]
    lats = [p["lat"] for p in nodes]
    lng0 = (min(lngs) + max(lngs)) / 2.0
    lat0 = (min(lats) + max(lats)) / 2.0
    kx = math.cos(math.radians(lat0))

    ux = [(p["lng"] - lng0) * kx for p in nodes]
    uy = [(lat0 - p["lat"]) for p in nodes]
    uxr = max(max(ux) - min(ux), min_span_deg, 2e-3)
    uyr = max(max(uy) - min(uy), min_span_deg, 2e-3)

    aw, ah = w - 2 * pad_x, h - pad_t - pad_b
    scale = min(aw / uxr, ah / uyr)
    ox = x0 + pad_x + (aw - uxr * scale) / 2.0 - min(ux) * scale
    oy = y0 + pad_t + (ah - uyr * scale) / 2.0 - min(uy) * scale

    def f(lng, lat):
        return (ox + (lng - lng0) * kx * scale, oy + (lat0 - lat) * scale)

    return f, scale / 111.32          # 1 纬度度 ≈ 111.32 km


def _put_label(cx, cy, text, placed, bounds, fs=12.4):
    """标签避让 + 白底衬垫

    ⚠ 不要用 `paint-order:stroke` 描白边 —— 部分渲染器（librsvg / 图片导出）
       不支持该属性，会先画填充再画描边，把深色文字盖成白字、整批标签消失。
       这里改为在文字**下面**垫一个白色圆角矩形，任何渲染器都正确。
    """
    tw = len(text) * fs * 0.94 + 12
    th = fs + 7
    bx0, by0, bx1, by1 = bounds
    cands = [
        (15, -3, "start"), (-15, -3, "end"),
        (15, -22, "start"), (-15, -22, "end"),
        (15, 16, "start"), (-15, 16, "end"),
        (0, -28, "middle"), (0, 26, "middle"),
        (15, -41, "start"), (-15, -41, "end"),
    ]
    out = None

    def rect_of(dx, dy, anc):
        lx, ly = cx + dx, cy + dy + 5
        if anc == "start":
            r = (lx - 4, ly - fs - 4, lx + tw - 8, ly + 5)
        elif anc == "end":
            r = (lx - tw + 8, ly - fs - 4, lx + 4, ly + 5)
        else:
            r = (lx - tw / 2 + 4, ly - fs - 4, lx + tw / 2 - 4, ly + 5)
        return r, lx, ly

    for dx, dy, anc in cands:
        r, lx, ly = rect_of(dx, dy, anc)
        if r[0] < bx0 or r[2] > bx1 or r[1] < by0 or r[3] > by1:
            continue
        if any(not (r[2] + 3 < q[0] or q[2] + 3 < r[0]
                    or r[3] + 3 < q[1] or q[3] + 3 < r[1]) for q in placed):
            continue
        out = (r, lx, ly, anc)
        break
    if out is None:
        r, lx, ly = rect_of(15, -3, "start")
        out = (r, lx, ly, "start")
    placed.append(out[0])
    r, lx, ly, anc = out
    return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="5" '
            'fill="#ffffff" fill-opacity=".88"/>'
            % (r[0], r[1], r[2] - r[0], r[3] - r[1]),
            '<text x="%.1f" y="%.1f" text-anchor="%s" font-size="%.1f" '
            'fill="#26333f" font-weight="650">%s</text>'
            % (lx, ly, anc, fs, _esc(text)))


def _draw_subpanel(nodes, legs_local, rect, color, min_span_deg, caption="",
                   station=None):
    """画一个子格（一个簇）：返回 SVG 片段"""
    S = []
    A = S.append
    x0, y0, w, h = rect
    f, px_per_km = _proj(nodes, x0, y0, w, h, min_span_deg=min_span_deg)
    # 下边界抬高，给底部居中的比例尺让位，避免名称标签压在比例尺上
    bounds = (x0 + 4, y0 + 17, x0 + w - 4, y0 + h - 44)

    if caption:
        A('<text x="%d" y="%d" font-size="11.4" font-weight="700" '
          'fill="#8698a8">%s</text>' % (x0 + 10, y0 + 12, _esc(caption)))

    cmap = [f(nd["lng"], nd["lat"]) for nd in nodes]
    # 接驳段在下层
    for (i, j, lg) in legs_local:
        if lg["kind"] == "access":
            a, b = cmap[i], cmap[j]
            A('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#9db4c9" '
              'stroke-width="2.2" stroke-dasharray="7 6" opacity=".65" '
              'stroke-linecap="round"/>' % (a[0], a[1], b[0], b[1]))
    # 景点间段在上层
    for (i, j, lg) in legs_local:
        if lg["kind"] == "inter":
            a, b = cmap[i], cmap[j]
            A('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
              'stroke-width="3.2" stroke-linecap="round"/>'
              % (a[0], a[1], b[0], b[1], color))

    # ---- 自适应圆点 ----
    # 若任意两点屏幕上近于相撞，整格缩小圆点与字号，
    # 否则会出现"两个编号圆叠在一起"（实测 D3 的鼓山/涌泉寺只隔 1 km）
    dense = False
    for i in range(len(cmap)):
        for j in range(i + 1, len(cmap)):
            if (abs(cmap[i][0] - cmap[j][0]) < 30
                    and abs(cmap[i][1] - cmap[j][1]) < 30):
                dense = True
    R = 7.6 if dense else 10.5
    FS = 9.2 if dense else 11.0

    # ---- 先把所有标记占位登记进 placed，再给里程标签找空位 ----
    placed = []
    marks = []
    prelabels = []          # 站点名：必须在放里程标签**之前**占位
    for i, nd in enumerate(nodes):
        px, py = cmap[i]
        rr = 8 if nd.get("kind") == "station" else R
        placed.append((px - rr - 3, py - rr - 3, px + rr + 3, py + rr + 3))
        if nd.get("kind") != "station":
            marks.append((px, py, nd.get("name", ""), i))
        else:
            # 站名要写出来 —— 只画个"站"字，读者分不清福州站还是福州南站
            nm = (nd.get("name") or "").replace("（取车）", "") \
                                       .replace("（还车）", "")
            if nm:
                prelabels.append(_put_label(px, py, nm, placed, bounds,
                                            fs=11.6))

    # ---- 段里程标签：优先放中点，撞了就往垂直方向让 ----
    chips = []
    for (i, j, lg) in legs_local:
        if lg["kind"] != "inter":
            continue
        a, b = cmap[i], cmap[j]
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L                      # 单位法向
        txt = "%.0fkm·%d分" % (lg["road"], lg["min"])
        w2, h2 = len(txt) * 6.3 + 9, 15.5
        short = L < 52                                # 短段：中点必然压住圆点
        cands = ([(0, -24), (0, 24), (24, 0), (-24, 0)] if short else
                 [(0, 0), (nx * 22, ny * 22), (-nx * 22, -ny * 22),
                  (0, -22), (0, 22)])
        put = None
        for (ox2, oy2) in cands:
            cxx, cyy = mx + ox2, my + oy2
            r2 = (cxx - w2 / 2, cyy - h2 / 2, cxx + w2 / 2, cyy + h2 / 2)
            if (r2[0] < bounds[0] or r2[2] > bounds[2]
                    or r2[1] < bounds[1] or r2[3] > bounds[3]):
                continue
            if any(not (r2[2] + 2 < q[0] or q[2] + 2 < r2[0]
                        or r2[3] + 2 < q[1] or q[3] + 2 < r2[1])
                   for q in placed):
                continue
            put = (r2, cxx, cyy)
            break
        if put is None:                                # 实在放不下就标在端点上方
            cxx, cyy = mx, my - 26
            put = ((cxx - w2 / 2, cyy - h2 / 2, cxx + w2 / 2, cyy + h2 / 2),
                   cxx, cyy)
        placed.append(put[0])
        chips.append((put[0], put[1], put[2], txt))

    # ---- 绘制顺序：段 → 里程标签 → 圆点 → 名称（圆点必须压在标签之上）----
    for (r2, cxx, cyy, txt) in chips:
        A('<rect x="%.1f" y="%.1f" width="%.1f" height="15.5" rx="7" '
          'fill="#ffffff" fill-opacity=".94" stroke="#e3ebf3"/>'
          % (r2[0], r2[1], r2[2] - r2[0]))
        A('<text x="%.1f" y="%.1f" text-anchor="middle" '
          'dominant-baseline="central" font-size="10.4" fill="%s" '
          'font-weight="650">%s</text>' % (cxx, cyy, color, txt))

    for i, nd in enumerate(nodes):
        px, py = cmap[i]
        if nd.get("kind") == "station":
            A('<rect x="%.1f" y="%.1f" width="16" height="16" rx="4.5" '
              'fill="#2f7ae0"/>' % (px - 8, py - 8))
            A('<text x="%.1f" y="%.1f" text-anchor="middle" '
              'dominant-baseline="central" font-size="9.6" fill="#fff" '
              'font-weight="700">站</text>' % (px, py + .5))
            continue
        A('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>'
          % (px, py, R, color))
        A('<text x="%.1f" y="%.1f" text-anchor="middle" '
          'dominant-baseline="central" font-size="%.1f" fill="#fff" '
          'font-weight="700">%d</text>'
          % (px, py + .5, FS, nd.get("seq", i + 1)))

    # ---- 站点名 + 名称标签 ----
    for (bg, tx) in prelabels:
        A(bg)
        A(tx)
    for (px, py, nm, _i) in marks:
        if not nm:
            continue
        bg, tx = _put_label(px, py, nm, placed, bounds,
                            fs=(11.4 if dense else 12.4))
        A(bg)
        A(tx)

    # ---- 子格比例尺：放在底部**居中**，避开名称标签的左右两翼 ----
    km = _nice_km(104.0 / px_per_km)
    bar = km * px_per_km
    bx, by = x0 + w / 2 - bar / 2, y0 + h - 11
    A('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#b8c6d4" '
      'stroke-width="1.7"/>' % (bx, by, bx + bar, by))
    for xx in (bx, bx + bar):
        A('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#b8c6d4" '
          'stroke-width="1.7"/>' % (xx, by - 3.5, xx, by + 3.5))
    A('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="9.6" '
      'fill="#b8c6d4">%d km</text>' % (bx + bar / 2, by - 7.5, km))
    return S


def _draw_day(day, x0, y0, w, h):
    """画一天的面板（内部再按簇分子格）"""
    S = []
    A = S.append
    idx = day["idx"]
    col = DAY_COLORS[idx % len(DAY_COLORS)]
    chain = day.get("chain") or []
    legs = day.get("legs") or []
    if not chain:
        return S

    A('<rect x="%d" y="%d" width="%d" height="%d" rx="11" fill="#ffffff" '
      'stroke="#e6eef7" stroke-width="1.2"/>' % (x0, y0, w, h))

    # ---- 头部两行（避免左标右统挤在一行）----
    head = "D%d · %s %s" % (idx, (day.get("date") or "")[5:],
                            day.get("weekday") or "")
    A('<text x="%d" y="%d" font-size="12.8" font-weight="700" fill="%s">%s</text>'
      % (x0 + 14, y0 + 20, col, _esc(head.strip())))
    stat = "景间 %.1f km ＋ 接驳 %.1f km · 车程 %d 分" % (
        day.get("leg_inter_km", 0), day.get("leg_access_km", 0),
        day.get("leg_total_min", 0))
    A('<text x="%d" y="%d" text-anchor="end" font-size="11.2" fill="#98a9b8">%s'
      '</text>' % (x0 + w - 14, y0 + 20, stat))

    # ---- 簇划分 ----
    groups = _cluster(chain, thr_km=25.0)
    # 序号：让主簇在前；同时记录每个链节点属于哪簇
    cid = {}
    for gi, g in enumerate(groups):
        for i in g:
            cid[i] = gi

    # 各簇跨度 → 用于给"单点簇"设最小跨度，避免放大失真
    spans = []
    for g in groups:
        spans.append(_span([chain[i] for i in g]))
    max_span = max(spans) if spans else 0.0
    min_span_deg = (max_span * 0.30) / 111.32 if max_span else 0.0

    # ---- 子格布局 ----
    k = len(groups)
    TOP = y0 + 30
    BOT = y0 + h - 10
    # 留一行给"跨片区/跨簇"文字
    cross_legs = [(i, lg) for i, lg in enumerate(legs)
                  if cid.get(i) != cid.get(i + 1)]
    reserve = 20 if cross_legs else 0
    CH = BOT - TOP - reserve
    # 子格高度**按簇跨度加权**分配（下限 0.35×最大跨度），不搞等分：
    # 等分会让"只有一个点"的簇白占半格，而真正密集的簇被压扁。
    if k == 1:
        wts = [1.0]
    else:
        mx = max(spans) if spans else 0.0
        wts = [max(sp, mx * 0.35) for sp in spans] or [1.0] * k
    wsum = sum(wts) or 1.0
    subrects = []
    cy = TOP
    for gi in range(k):
        sh = CH * (wts[gi] / wsum)
        subrects.append((x0 + 8, cy, w - 16, max(sh - 4, 40)))
        cy += sh

    # ---- 逐簇画子格 ----
    seq_of = {}
    for i, nd in enumerate(chain):
        if nd.get("kind") != "station":
            seq_of.setdefault("n", 0)
            seq_of["n"] += 1
            seq_of[i] = seq_of["n"]
    for gi, g in enumerate(groups):
        nodes = []
        for i in g:
            nd = dict(chain[i])
            if nd.get("kind") != "station":
                nd["seq"] = seq_of.get(i, i + 1)
            nodes.append(nd)
        local = [(i, j, lg) for (i, j, lg) in
                 [(i2, i2 + 1, legs[i2]) for i2 in range(len(legs))]
                 if cid.get(i) == gi and cid.get(j) == gi]
        # 局部下标（子格内只有本簇的点）
        remap = {orig: n for n, orig in enumerate(g)}
        local2 = []
        for (i, j, lg) in local:
            if i in remap and j in remap:
                local2.append((remap[i], remap[j], lg))
        cap = ""
        if k > 1:
            regions = []
            for i in g:
                for p in day["points"]:
                    if p["name"] == chain[i].get("name") and p.get("region"):
                        if p["region"] not in regions:
                            regions.append(p["region"])
            cap = regions[0] if regions else ""
            cap = cap or "分组 %d" % (gi + 1)
        S.extend(_draw_subpanel(nodes, local2, subrects[gi], col,
                                min_span_deg, caption=cap))

    # ---- 跨簇转移：不画线，只写文字 ----
    if cross_legs:
        parts = []
        for i, lg in cross_legs:
            a = chain[i]["name"].replace("（取车）", "").replace("（还车）", "")
            b = chain[i + 1]["name"].replace("（取车）", "").replace("（还车）", "")
            parts.append("%s→%s %.0f km·%d 分" % (a, b, lg["road"], lg["min"]))
        A('<text x="%d" y="%d" font-size="10.8" fill="#c08a2e">'
          '跨片区（不上图）：%s</text>'
          % (x0 + 14, y0 + h - 6, _esc("；".join(parts))))
    return S


def render_svg(payload, title="", show_alt=False, cols=None):
    """渲染为自包含的内联 SVG 示意图（天 × 簇 两层分面）"""
    days = payload.get("days") or []
    if not days:
        return ('<div style="padding:22px;color:#8698a8;font-size:13px">'
                '（无行程数据，无法绘制示意图）</div>')

    n = len(days)
    cols = cols or (1 if n == 1 else 2)
    rows = (n + cols - 1) // cols

    MARGIN, GAP = 22, 16
    PW = 540 if cols >= 2 else 920
    PH = 350 if rows >= 2 else 480
    W = MARGIN * 2 + cols * PW + (cols - 1) * GAP
    HEAD, FOOT = 54, 96
    H = HEAD + rows * PH + (rows - 1) * GAP + FOOT

    S = []
    A = S.append
    A('<rect x="0" y="0" width="%d" height="%d" fill="#fbfdff"/>' % (W, H))
    A('<defs><pattern id="grd2" width="44" height="44" '
      'patternUnits="userSpaceOnUse">'
      '<path d="M44 0H0V44" fill="none" stroke="#f0f5fa" stroke-width="1"/>'
      '</pattern></defs>')
    A('<rect x="0" y="0" width="%d" height="%d" fill="url(#grd2)"/>' % (W, H))
    if title:
        A('<text x="%d" y="34" font-size="16" font-weight="700" '
          'fill="#1b2733">%s</text>' % (MARGIN, _esc(title)))

    tot_km = sum(d.get("leg_total_km", 0) for d in days)
    tot_min = sum(d.get("leg_total_min", 0) for d in days)
    A('<text x="%d" y="34" text-anchor="end" font-size="12.4" fill="#6b7c8d">'
      '全程车程合计 %.0f km · %.0f 分</text>' % (W - MARGIN, tot_km, tot_min))

    for k, d in enumerate(days):
        r, c = divmod(k, cols)
        x0 = MARGIN + c * (PW + GAP)
        y0 = HEAD + r * (PH + GAP)
        S.extend(_draw_day(d, x0, y0, PW, PH))

    # ---- 底部：跨天长途转移（不画线）----
    yb = HEAD + rows * PH + (rows - 1) * GAP + 22
    A('<text x="%d" y="%d" font-size="12.4" font-weight="700" '
      'fill="#1b2733">跨天转移</text>' % (MARGIN, yb))
    xfers = []
    for d in days:
        for i, lg in enumerate(d.get("legs") or []):
            if lg["road"] >= 40:
                xfers.append((d["idx"], lg))
    if xfers:
        tx = MARGIN + 88
        for idx, lg in xfers:
            col = DAY_COLORS[idx % len(DAY_COLORS)]
            txt = "D%d %s → %s %.0f km · %d 分" % (
                idx, lg["from"].replace("（取车）", "").replace("（还车）", ""),
                lg["to"].replace("（取车）", "").replace("（还车）", ""),
                lg["road"], lg["min"])
            A('<circle cx="%d" cy="%d" r="3.5" fill="%s"/>'
              % (tx - 13, yb - 4, col))
            A('<text x="%d" y="%d" font-size="11.6" fill="#4a5a6a">%s</text>'
              % (tx, yb, _esc(txt)))
            tx += len(txt) * 7.4 + 40
            if tx > W - 340:
                break
    else:
        A('<text x="%d" y="%d" font-size="11.6" fill="#98a9b8">'
          '（本行程无 40 km 以上的跨天转移）</text>' % (MARGIN + 88, yb))

    A('<text x="%d" y="%d" font-size="11.4" fill="#8698a8">'
      '每格/每子格比例尺不同，见各自底部居中；虚线为车站接驳段；'
      '连线为点对点直线，不表示道路几何；实际里程与车程以导航为准。'
      '</text>' % (MARGIN, yb + 25))

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'width="%d" height="%d" preserveAspectRatio="xMidYMid meet" '
           'role="img" aria-label="%s" '
           'style="display:block;width:100%%;height:auto;max-width:100%%;'
           'font-family:-apple-system,BlinkMacSystemFont,\'PingFang SC\','
           '\'Microsoft YaHei\',sans-serif">%s</svg>')
    return svg % (W, H, W, H, _esc(title or "路线示意图"), "".join(S))


def render_svg_block(payload, title="", note=True):
    """带外框与图注的 SVG 区块（供报告内嵌）"""
    inner = render_svg(payload, title=title)
    cap = ""
    if note:
        cap = ('<div class="svg-note">'
               '<b>怎么看这张图</b>：一天一格；格内若出现两个距离很远的片区，'
               '会<b>再拆成子格</b>，各自带比例尺（每格底部居中）—— '
               '这样城区里几个相邻景点才不会被上百公里的长途压成一个点。'
               '格内连线是点对点直线，不表示道路几何；距离与车程为估算值'
               '（绕行系数 1.35、均速 45 km/h），<b>实际以导航为准</b>。'
               '<span class="dim">跨片区、跨天的长途只以文字列出，不占版面。</span>'
               '</div>')
    return ('<div class="svgwrap">%s%s'
            '<style>'
            '.svgwrap{border:1px solid #dfe9f3;border-radius:12px;overflow:hidden;'
            'background:#fbfdff;margin:4px 0 10px}'
            '.svg-note{padding:10px 14px;font-size:12px;line-height:1.75;'
            'color:#6b7c8d;background:#f7fafd;border-top:1px solid #eef3f8}'
            '.svg-note .dim{color:#a8b6c4}'
            '</style></div>') % (inner, cap)


SVG_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{margin:0;padding:18px;background:#f4f8fc;color:#1b2733;
 font-family:-apple-system,"PingFang SC","Microsoft YaHei","Source Han Sans SC",sans-serif;
 line-height:1.7;-webkit-font-smoothing:antialiased;}}
h1{{font-size:19px;margin:0 0 14px;font-weight:700;}}
.svgwrap{{background:#fff;box-shadow:0 2px 14px rgba(15,23,42,.07);border-radius:12px;
 border:1px solid #dfe9f3;overflow:hidden}}
.svgwrap svg{{display:block;width:100%;height:auto}}
.foot{{margin-top:16px;font-size:12px;color:#8698a8}}
@media print{{body{{background:#fff;padding:0}}h1{{margin:0 0 8px}}}}
</style></head><body>
<h1>{title}</h1>
{body}
<div class="foot">零外部依赖：本页不加载任何地图瓦片或脚本，离线、断网、微信里都能正常显示，可直接打印。</div>
</body></html>
"""


def render_svg_page(payload, title="行程路线地图"):
    """把 SVG 分面图包成可独立打开的单文件 HTML（零外部依赖）。"""
    block = render_svg_block(payload, title=title)
    return SVG_PAGE.format(title=title, body=block)


def main():
    ap = argparse.ArgumentParser(
        description="行程可视化地图（默认腾讯地图 GL JS 交互版；--mode svg 出零依赖离线版）")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--itin", default=os.path.join(here, "itinerary.json"))
    ap.add_argument("--out", default=os.path.join(here, "map.html"))
    ap.add_argument("--title", default="自驾 · 路线地图")
    ap.add_argument("--station", default=None,
                    help="作为取还车锚点的名称（对应 itinerary.anchors 的键）；留空则自动取第一个 kind=station 的锚点")
    ap.add_argument("--cross", default=None,
                    help="城际段说明文字（只进侧栏，不画在地图上）。例：上海虹桥 → 福州站 高铁 G1653 08:15-11:55，4h40m，二等座 ¥377.5")
    ap.add_argument("--mode", default="tmap", choices=("tmap", "svg"),
                    help="tmap=腾讯地图交互版（需在 WorkBuddy 预览面板打开，静态托管会空白）；"
                         "svg=零依赖离线版（内联 SVG，任何浏览器/微信/手机都能看，可打印）")
    a = ap.parse_args()

    with open(a.itin, encoding="utf-8") as f:
        itin = json.load(f)

    payload = build_payload(itin, station_name=a.station, cross_text=a.cross)
    html = (render_svg_page(payload, a.title) if a.mode == "svg"
            else render_html(payload, a.title))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)

    n_pts = sum(len(d["points"]) for d in payload["days"])
    n_leg = sum(len(d["legs"]) for d in payload["days"])
    n_inter = sum(1 for d in payload["days"] for l in d["legs"] if l["kind"] == "inter")
    print("→ %s（%d 字符）" % (a.out, len(html)))
    print("   锚点 %s / 主景点 %d 个 / 备选点 %d 个 / 路段 %d 段（其中景点↔景点 %d 段）"
          % (payload["station_name"] or "无", n_pts,
             len(payload["alternatives"]), n_leg, n_inter))
    for d in payload["days"]:
        print("   D%d %s  %d 点  景间 %.1f km ＋ 接驳 %.1f km  车程合计 %.0f 分"
              % (d["idx"], d["date"], len(d["points"]),
                 d["leg_inter_km"], d["leg_access_km"], d["leg_total_min"]))


if __name__ == "__main__":
    main()
