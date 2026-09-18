# -*- coding: utf-8 -*-
"""境外行程数据层 —— 全免费 / 免 Key / 实测可达的开源栈

为什么单开一层：
  境内那套（腾讯地图 + 本地宝 + 小红书）在境外全部失效。
  境外需要的是**全球覆盖、有坐标、可程序化**的数据源。

本脚本用到的四个服务，全部在本环境实测可达：

  ┌────────────┬──────────────────────────────┬──────────────────────────┐
  │ 环节       │ 服务                          │ 实测                      │
  ├────────────┼──────────────────────────────┼──────────────────────────┤
  │ 地理编码   │ photon.komoot.io             │ ✓ 返回 bbox（比 Nominatim │
  │            │                              │   更适合，直接给出范围）  │
  │ POI 检索   │ overpass-api.de              │ ✓ 京都 bbox 拿到 3351 个  │
  │            │ (备用 private.coffee)        │   POI，577 个带 wikidata  │
  │ 道路路由   │ router.project-osrm.org      │ ✓ 真实道路距离/时长        │
  │ 地图瓦片   │ tiles.openfreemap.org        │ ✓ 免 Key / MIT / 无上限    │
  └────────────┴──────────────────────────────┴──────────────────────────┘

  ⚠ 两个**在本环境不可达**的服务，方案已绕开（详见 references/境外行程扩展.md）：
    · nominatim.openstreetmap.org —— 已被墙 → 改用 Photon
    · en.wikivoyage.org（整个 Wikimedia）—— 已被墙 → 改用 anysearch 检索攻略

输出与国内 pipeline **同构**的 places.json，可直接喂给 planner_multi.py。

用法：
  python overseas_research.py geocode --place "Kyoto" --out ./os
  python overseas_research.py pool    --place "Kyoto" --out ./os --radius-km 25
  python overseas_research.py route   --itin ./os/itinerary.json --out ./os
  python overseas_research.py all     --place "Kyoto" --out ./os
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "trip-plan-skill/1.0 (personal travel planner; contact: local-user)"

PHOTON = "https://photon.komoot.io/api"
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",   # 备用镜像
]
OSRM = "https://router.project-osrm.org/route/v1/driving"

# ============================================================
# 一、POI 价值评分（境外版的"信源分级"）
# ------------------------------------------------------------
# 国内靠"4A/5A 文本标注"定名片；境外 OSM 没有这个字段，但有三个强信号：
#   · wikidata / wikipedia 标签  → 该对象有独立百科条目，是"值得一提"的门槛
#   · 标签族（castle/heritage > museum > viewpoint > wayside_shrine）
#   · name:en / name:zh 是否有    → 有无多语言条目，侧面反映知名度
#
# 实测教训（京都 bbox）：
#   · 原样输出 3351 个 POI —— 其中 1580 个是 religion 场所，绝大多数是小寺小祠，
#     直接排进去会把清水寺淹掉。
#   · 带 wikidata 的只有 577 个 —— 这就是天然的精选池。
#   · 名称里含 "の眺め / Viewpoint of / から見た / 跡 / 駐車場" 的多为
#     主对象的**派生视点或附属物**（实测"二条城"曾匹配到"西から見た二条城東南隅櫓の眺め"），
#     必须压制。
# ============================================================
FAMILY_SCORE = {
    # 世界遗产 / 城池 / 核心景点
    "castle": 9.0, "heritage": 9.0, "attraction": 8.0,
    # 高价值人造景点
    "archaeological_site": 7.5, "museum": 7.0, "theme_park": 7.0,
    "temple": 7.0, "shrine": 7.0, "monastery": 6.5,
    "zoo": 6.5, "aquarium": 6.5,
    # 自然与园林
    "garden": 6.0, "nature_reserve": 6.0,
    "ruins": 5.0, "viewpoint": 5.0, "park": 5.0, "gallery": 5.0,
    "worship": 5.0,          # 泛化宗教场所：靠 wikidata 抬分，否则压制
    "peak": 4.5, "waterfall": 4.5, "beach": 4.5, "cave_entrance": 4.5,
    # 低价值
    "monument": 4.0, "memorial": 3.5, "artwork": 3.0,
    "wayside_shrine": 1.5, "yes": 2.0, "building": 2.5,
}

# 派生对象 / 附属物关键词：名称里带这些，多半不是独立景点
DERIVED_HINTS = [
    "の眺め", "から見た", "眺望", "駐車場", "駐輪場", "トイレ",
    "Viewpoint of", "View of", "Panorama", "parking", "Parking",
    "案内所", "休憩所", "バス停", "駅前",
]


def haversine(lat1, lng1, lat2, lng2):
    """两点球面距离（km）"""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _get_json(url, params=None, timeout=30, retries=3, sleep=2.0):
    """GET → JSON，带退避重试"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(sleep * (i + 1))
    raise RuntimeError("GET %s 失败：%s" % (url[:90], last))


def _post_overpass(query, timeout=120):
    """POST Overpass QL；主站失败自动切镜像"""
    last = None
    for ep in OVERPASS:
        for attempt in range(2):
            try:
                data = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(
                    ep, data=data,
                    headers={"User-Agent": UA,
                             "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = "HTTP %s @ %s" % (e.code, ep)
                if e.code in (429, 504, 502, 503):
                    time.sleep(8 * (attempt + 1))     # 限流/超载，退避
                    continue
                break
            except Exception as e:                    # noqa: BLE001
                last = "%s @ %s" % (e, ep)
                time.sleep(5)
        # 换镜像
    raise RuntimeError("Overpass 全部端点失败：%s" % last)


# ============================================================
# 二、地理编码（Photon）
# ============================================================
def geocode(place, limit=5, lang="en"):
    """地名 → 中心点 + 边界框

    返回 [{name, display, lat, lon, bbox:[W,S,E,N], cc, country, osm, nest}]
    Photon 的 extent 是 [W, N, E, S]，这里统一转成 [W, S, E, N]（GeoJSON 顺序）

    `nest`（嵌套标记）—— 关键判别信号，见 _best() 的说明。
    """
    d = _get_json(PHOTON, {"q": place, "limit": limit, "lang": lang})
    out = []
    for idx, f in enumerate(d.get("features", [])):
        p = f.get("properties", {})
        g = f.get("geometry", {}).get("coordinates")
        if not g:
            continue
        extent = p.get("extent")          # [W, N, E, S]
        if extent:
            w, n, e, s = extent
            bbox = [w, s, e, n]
        else:
            bbox = [g[0] - .02, g[1] - .02, g[0] + .02, g[1] + .02]
        # 带 city/district/suburb 字段 ⇒ 该对象是"某个城市内部的区/街道"，不是城市本身
        nest = ""
        for k in ("suburb", "district", "city", "locality"):
            if p.get(k):
                nest = k
                break
        out.append({
            "name": p.get("name") or place,
            "display": ", ".join(x for x in [p.get("name"), p.get("city"),
                                             p.get("state"), p.get("country")]
                                 if x),
            "lat": g[1], "lon": g[0],
            "bbox": bbox,
            "cc": (p.get("countrycode") or "").upper(),
            "country": p.get("country") or "",
            "state": p.get("state") or "",
            "osm": "%s/%s" % (p.get("osm_type", ""), p.get("osm_id", "")),
            "kind": p.get("type") or p.get("osm_value") or "",
            "nest": nest,
            "_rank": idx,
        })
    return out


# ============================================================
# 三、POI 检索（Overpass）
# ============================================================
# 标签族 —— 覆盖"真正的旅游对象"，而不是只查 tourism=*
OVERPASS_FAMILIES = [
    'nwr["tourism"~"^(attraction|museum|viewpoint|gallery|zoo|theme_park|aquarium|artwork)$"]["name"]',
    'nwr["historic"~"^(castle|monument|ruins|archaeological_site|temple|shrine|monastery|memorial)$"]["name"]',
    'nwr["amenity"="place_of_worship"]["name"]',
    'nwr["leisure"~"^(park|garden|nature_reserve)$"]["name"]',
    'nwr["natural"~"^(peak|waterfall|beach|cave_entrance)$"]["name"]',
    'nwr["heritage"]["name"]',
]


def fetch_pois(bbox, split=2, verbose=True):
    """bbox=[W,S,E,N] → 原始 POI 列表

    ⚠ Overpass 单次查询有资源上限。大城市（如京都）一次查询会 2000+ 元素，
    因此按 2×2 网格拆分，降低单次压力、也更稳。
    """
    w, s, e, n = bbox
    tiles = []
    for i in range(split):
        for j in range(split):
            tiles.append((
                w + (e - w) * i / split, s + (n - s) * j / split,
                w + (e - w) * (i + 1) / split, s + (n - s) * (j + 1) / split,
            ))

    elements = []
    seen_ids = set()
    for k, (tw, ts, te, tn) in enumerate(tiles, 1):
        b = "(%.5f,%.5f,%.5f,%.5f)" % (ts, tw, tn, te)
        body = "\n".join("  %s%s;" % (f, b) for f in OVERPASS_FAMILIES)
        q = "[out:json][timeout:120];\n(\n%s\n);\nout center;" % body
        if verbose:
            print("    [%d/%d] %s" % (k, len(tiles), b), file=sys.stderr)
        try:
            d = _post_overpass(q)
        except Exception as ex:                       # noqa: BLE001
            print("      ! %s" % ex, file=sys.stderr)
            continue
        for el in d.get("elements", []):
            key = (el.get("type"), el.get("id"))
            if key in seen_ids:
                continue
            seen_ids.add(key)
            elements.append(el)
        time.sleep(1.5)                               # 对公共服务客气一点
    if verbose:
        print("    合并去重后 %d 个元素" % len(elements), file=sys.stderr)
    return elements


def _family(tags):
    """从 tags 推出一个归一化的"族"名"""
    t = tags.get("tourism")
    if t:
        return t
    h = tags.get("historic")
    if h:
        return h
    if tags.get("amenity") == "place_of_worship":
        return "worship"
    l = tags.get("leisure")
    if l:
        return l
    na = tags.get("natural")
    if na:
        return na
    if tags.get("heritage"):
        return "heritage"
    return "yes"


def score_poi(tags, name):
    """价值评分：族基础分 + 知名度修正 − 派生对象惩罚"""
    fam = _family(tags)
    s = FAMILY_SCORE.get(fam, 3.0)
    why = ["族=%s" % fam]

    wd = tags.get("wikidata")
    wp = tags.get("wikipedia")
    if wd:
        s += 3.0
        why.append("+wikidata")
    if wp:
        s += 1.5
        why.append("+wikipedia")
    if tags.get("name:en") or tags.get("name:zh"):
        s += 1.0
        why.append("+多语言名")
    if tags.get("heritage"):
        s += 0.8
        why.append("+heritage")
    if tags.get("website") or tags.get("opening_hours"):
        s += 0.4
        why.append("+有官网/营业时间")

    # 无 wikidata 的泛化宗教场所：日本寺庙以千计，这类基本是小寺小祠
    if fam == "worship" and not wd and not wp:
        s -= 3.0
        why.append("−无名录收录的宗教场所")

    for h in DERIVED_HINTS:
        if h in (name or ""):
            s -= 4.0
            why.append("−派生/附属对象(%s)" % h)
            break

    return round(s, 2), fam, " ".join(why)


def build_pool(elements, center, radius_km=None, min_score=0.0,
               max_items=None, verbose=True):
    """元素 → 打分去重后的 POI 池

    去重两重：
      ① 同名（不同来源的重复对象）
      ② 50 m 内同族（同一对象被 node/way/relation 各映射一次）
    """
    clat, clon = center
    rows = []
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name:zh") or tags.get("name:en")
                or tags.get("name"))
        if not name or len(name) < 2:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        dist = haversine(clat, clon, lat, lon)
        if radius_km and dist > radius_km:
            continue
        sc, fam, why = score_poi(tags, name)
        if sc < min_score:
            continue
        rows.append({
            "name": name,
            "name_local": tags.get("name") or "",
            "name_en": tags.get("name:en") or "",
            "family": fam,
            "score": sc,
            "why": why,
            "lat": lat, "lon": lon,
            "dist_km": round(dist, 2),
            "wikidata": tags.get("wikidata") or "",
            "wikipedia": tags.get("wikipedia") or "",
            "osm": "%s/%s" % (el.get("type"), el.get("id")),
            "website": tags.get("website") or "",
            "hours": tags.get("opening_hours") or "",
            "heritage": tags.get("heritage") or "",
        })

    # 去重
    kept = []
    for r in sorted(rows, key=lambda x: -x["score"]):
        dup = False
        for k in kept:
            if k["name"] == r["name"]:
                dup = True
                break
            if (k["family"] == r["family"]
                    and haversine(k["lat"], k["lon"], r["lat"], r["lon"]) < 0.05):
                dup = True
                break
        if not dup:
            kept.append(r)

    if max_items:
        kept = kept[:max_items]
    if verbose:
        from collections import Counter
        c = Counter(k["family"] for k in kept)
        wd = sum(1 for k in kept if k["wikidata"])
        print("    池内 %d 个（带 wikidata %d 个）" % (len(kept), wd),
              file=sys.stderr)
        print("    族分布：" + ", ".join("%s=%d" % x for x in c.most_common(8)),
              file=sys.stderr)
    return kept


# ============================================================
# 四、道路路由（OSRM）—— 把"直线×系数"升级为真实道路
# ============================================================
def route(points, verbose=True):
    """points=[(lat,lon), ...] → 逐段真实道路距离/时长

    这是境外层相对境内层的一个**实质改进**：
    境内是"直线距离 × 1.35 绕行系数 / 45 km/h"，必须写"以导航为准"；
    这里拿到的是 OSRM 真实路网结果，可以去掉那条免责声明。

    ⚠ OSRM demo 服务器是公共资源，仅适合个人低频使用，勿用于生产/批量。
    """
    out = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        coord = "%.6f,%.6f;%.6f,%.6f" % (a[1], a[0], b[1], b[0])
        try:
            d = _get_json("%s/%s" % (OSRM, coord),
                          {"overview": "false", "steps": "false"},
                          timeout=25, retries=2)
        except Exception as ex:                       # noqa: BLE001
            print("      ! 路由失败 %s" % ex, file=sys.stderr)
            out.append({"from_i": i, "to_i": i + 1, "ok": False})
            continue
        if not d.get("routes"):
            out.append({"from_i": i, "to_i": i + 1, "ok": False})
            continue
        rt = d["routes"][0]
        km = rt["distance"] / 1000.0
        straight = haversine(a[0], a[1], b[0], b[1])
        out.append({
            "from_i": i, "to_i": i + 1, "ok": True,
            "straight_km": round(straight, 2),
            "road_km": round(km, 1),
            "min": round(rt["duration"] / 60.0, 1),
            "detour": round(km / straight, 2) if straight > 0.05 else None,
        })
        time.sleep(1.2)                               # demo 服务器限速
    if verbose:
        ok = [x for x in out if x.get("ok")]
        if ok:
            tot = sum(x["road_km"] for x in ok)
            tt = sum(x["min"] for x in ok)
            print("    路由 %d/%d 段成功，合计 %.1f km / %.0f 分"
                  % (len(ok), len(out), tot, tt), file=sys.stderr)
    return out


# ============================================================
# 五、输出：与国内 places.json 同构
# ============================================================
def to_places(pool, region_hint=""):
    """转成 planner 可直接消费的 places.json 结构

    字段与国内一致：name / type / ticket_price / duration_min / note /
    src_file / reservation_required / reservation_tips / confidence /
    _region_hint / lon / lat
    """
    out = []
    for p in pool:
        zh = p["name_local"] if p["name_local"] else p["name"]
        en = ("（%s）" % p["name_en"]) if p["name_en"] and p["name_en"] != zh else ""
        note = "OSM %s；价值评分 %.1f（%s）" % (p["family"], p["score"], p["why"])
        if p["wikidata"]:
            note += "；wikidata %s" % p["wikidata"]
        if p["website"]:
            note += "；官网 %s" % p["website"]
        if p["hours"]:
            note += "；营业 %s" % p["hours"]
        out.append({
            "name": zh + en,
            "type": p["family"],
            "ticket_price": 0,          # OSM 无票价字段，留给后续检索补
            "duration_min": 90,
            "note": note,
            "src_file": "osm/overpass",
            "reservation_required": False,
            "reservation_tips": "",
            "confidence": "high" if p["wikidata"] else "medium",
            "_region_hint": region_hint,
            "_score": p["score"],
            "lon": p["lon"], "lat": p["lat"],
        })
    return out


# ============================================================
# CLI
# ============================================================
def _bbox_area(g):
    w, s, e, n = g["bbox"]
    return max(0.0, e - w) * max(0.0, n - s)


# 地名候选的"层级"偏好：城市 > 城镇 > 其他
# 实测坑：查 "Kyoto" 时 Photon 首条是一个同名**行政区**（bbox 仅 ~4 km 宽），
# 按 bbox 检索会漏掉清水寺、金閣寺这类城郊名片 —— 整池降级为小众博物馆。
PREFER_KIND = ("city", "town", "municipality", "administrative")


def _best(geo):
    """从候选里挑"最像那个城市"的一个

    规则：① 优先 kind 属于 PREFER_KIND ② 同级里取 bbox 面积最大者。
    """
    pref = [g for g in geo if (g.get("kind") or "").lower() in PREFER_KIND]
    pool = pref or geo
    return max(pool, key=_bbox_area)


def _top(geo, pick=None):
    if not geo:
        raise SystemExit("! 未找到该地名，请换一种写法（如「Kyoto, Japan」）")
    if pick is None:
        return _best(geo)
    for g in geo:
        if pick.lower() in (g["display"] or "").lower():
            return g
    print("  ! --pick 「%s」未匹配到任何候选，回退到自动选择" % pick,
          file=sys.stderr)
    return _best(geo)


def cmd_geocode(a):
    geo = geocode(a.place, lang=a.lang)
    os.makedirs(a.out, exist_ok=True)
    json.dump(geo, open(os.path.join(a.out, "geo.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("→ 命中 %d 个候选" % len(geo))
    for i, g in enumerate(geo):
        w, s, e, n = g["bbox"]
        print("  [%d] %-44s %-4s %-10s %.2f°×%.2f°"
              % (i, g["display"][:42], g["cc"], g.get("kind", "")[:9],
                 e - w, n - s))
    g = _top(geo, a.pick)
    print("\n选定：%s" % g["display"])
    print("  kind=%s  bbox [W,S,E,N] = %s"
          % (g.get("kind"), [round(x, 4) for x in g["bbox"]]))
    print("  ⚠ 若选错了，用 --pick <display 子串> 指定，或直接 --geo 复用 geo.json")
    return g


def cmd_pool(a):
    g = cmd_geocode(a) if not a.geo else _top(
        json.load(open(a.geo, encoding="utf-8")), a.pick)
    print("\n检索 POI（Overpass，2×2 网格）...")
    els = fetch_pois(g["bbox"], split=a.split)
    print("\n打分去重...")
    pool = build_pool(els, (g["lat"], g["lon"]),
                      radius_km=a.radius_km, min_score=a.min_score,
                      max_items=a.max_items)
    os.makedirs(a.out, exist_ok=True)
    json.dump(pool, open(os.path.join(a.out, "pool.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    places = to_places(pool, region_hint=g["name"])
    json.dump(places, open(os.path.join(a.out, "places.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n→ pool.json（%d）/ places.json（%d，planner 可直接读）"
          % (len(pool), len(places)))
    print("\nTop 20：")
    for i, p in enumerate(pool[:20], 1):
        print("  %2d. %-34s %-12s %5.1f  %5.2fkm"
              % (i, p["name"][:32], p["family"][:10], p["score"], p["dist_km"]))
    return pool


def cmd_route(a):
    itin = json.load(open(a.itin, encoding="utf-8"))
    pts, names = [], []
    for d in itin["days"]:
        for at in d["attractions"]:
            pts.append((at["lat"], at["lon"]))
            names.append(at["name"])
    print("对 %d 个点做真实道路路由（%d 段）..." % (len(pts), len(pts) - 1))
    legs = route(pts)
    for l in legs:
        if l.get("ok"):
            print("  %-18s → %-18s  直线 %6.2f km  道路 %6.1f km  %5.1f 分  绕行×%.2f"
                  % (names[l["from_i"]][:16], names[l["to_i"]][:16],
                     l["straight_km"], l["road_km"], l["min"], l["detour"] or 0))
    json.dump(legs, open(os.path.join(a.out, "route.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n→ route.json")
    return legs


def cmd_all(a):
    cmd_pool(a)
    if a.itin and os.path.exists(a.itin):
        print()
        cmd_route(a)


def main():
    ap = argparse.ArgumentParser(description="境外行程数据层（Photon + Overpass + OSRM）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--place", default="")
        p.add_argument("--geo", default="", help="复用已存的 geo.json，跳过地名解析")
        p.add_argument("--pick", default=None, help="从候选里挑一个（子串匹配 display）")
        p.add_argument("--lang", default="en")
        p.add_argument("--out", required=True)

    p = sub.add_parser("geocode", help="地名 → 中心点 + bbox")
    common(p)
    p.set_defaults(func=cmd_geocode)

    p = sub.add_parser("pool", help="bbox → POI 池 + places.json")
    common(p)
    p.add_argument("--radius-km", type=float, default=None,
                   help="只保留距中心 N km 内的点（大城市建议 20~30）")
    p.add_argument("--min-score", type=float, default=5.0,
                   help="价值分下限，默认 5.0（低于此多为小祠/路边雕塑）")
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--split", type=int, default=2, help="Overpass 查询网格 N×N")
    p.add_argument("--itin", default=None)
    p.set_defaults(func=cmd_pool)

    p = sub.add_parser("route", help="真实道路路由（OSRM）")
    common(p)
    p.add_argument("--itin", required=True)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("all", help="pool + route")
    common(p)
    p.add_argument("--radius-km", type=float, default=None)
    p.add_argument("--min-score", type=float, default=5.0)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--split", type=int, default=2)
    p.add_argument("--itin", default=None)
    p.set_defaults(func=cmd_all)

    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    a.func(a)


if __name__ == "__main__":
    main()
