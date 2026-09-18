#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
住宿层（lodging.py）——为行程补上"住哪儿 + 大概多少钱"

设计要点（2026-09-17 实测结论）：
  · OTА 开放平台（携程 / 飞猪 / 去哪儿 / 美团 / Booking / Expedia）**没有匿名可用的住宿接口**，
    全部要求企业资质 + 商务签约；Booking Demand XML 返回 401，Expedia EAN 返回 403。
  · Amadeus / TravelPayouts / Hotellook 在本环境网络出口不可达或被下架。
  · 真正"可达且免 Key"的住宿点位来源只有 OSM Overpass（全球通用，中国城市亦有覆盖）。
  · 想要更全的中文 POI（含评分/人均），用地图厂商 POI 搜索，需自助申请免费 Key：
      高德 https://lbs.amap.com/   （Web 服务 API，个人 5 分钟可申请）
      百度 https://lbsyun.baidu.com/    腾讯 https://lbs.qq.com/
      天地图 https://www.tianditu.gov.cn/（国家平台，政府数据）
  · **房价没有任何免费开放接口**。本模块给的是"档位区间"，来源是公开行情检索 + 实时汇率，
    必须在下单前用官网/OTA 复核。

用法：
  # ① 抓取住宿点位（默认 Overpass，免 Key）
  python lodging.py fetch --stays stays.json --out lodging.json
  # ①' 国内用高德（覆盖最好，需免费 Key）
  python lodging.py fetch --stays stays.json --provider amap --amap-key <你的Key> --out lodging.json
  # ② 生成推介（首选/升级/特色/预算 + 价格档位）
  python lodging.py pick --lodging lodging.json --out hotel_plan.json [--currency ISK|CNY]

stays.json 格式（住宿锚点，一般从 itinerary.json 的 anchors 里取 "住宿·X" 派生）：
  [{"town":"雷市","lat":64.1466,"lng":-21.9426,"radius_km":6,"note":"首都"},
   ...]
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "trip-plan/1.0 (lodging layer)"}
OVERPASS_EPS = ["https://overpass-api.de/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter"]
KINDS = "hotel|guesthouse|hostel|apartment|chalet|motel|bed_and_breakfast|farm"

# ── 房价档位（双人房/晚，旺季）。国内单位 CNY；境外单位 ISK
BANDS_CN = {
    "青旅/床位":     (80, 180,   "床位或极小单间，共用卫浴"),
    "经济连锁":      (180, 350,  "如 7 天/如家/汉庭，标间"),
    "舒适型":        (350, 600,  "三星或中端连锁（全季/亚朵同级）"),
    "高档型":        (600, 1000, "四星或精品酒店，含早"),
    "豪华/度假":     (1000, 2500, "五星或景区度假酒店，含早"),
}
BANDS_ISK = {
    "民宿/客栈":     (20000, 32000, "双人房，多为独立卫浴"),
    "三星酒店":      (25000, 38000, "双人房含早"),
    "四星/商务":     (38000, 58000, "双人房含早"),
    "高端/特色":     (60000, 95000, "设计酒店/度假村，双人房含早"),
}
FAMILY_K = (1.3, 1.6)          # 2大1小 换家庭房/加床相对双人房的系数
BADNAME = re.compile(r"part of|apartment k|^[a-z]\b|\btest\b|unnamed|^apartment$", re.I)
LUX = re.compile(r"retreat|silica|black pearl|tower suites|deplar|luxury|boutique|"
                 r"design|curio|canopy|edition|aurora|blue lagoon|rang\u00e1|ranga|"
                 r"resort|villa|manor", re.I)
SPECIAL = re.compile(r"farm|resort|lodge|basecamp|cottage|retreat|villa|manor|hot spring|温泉|民宿", re.I)

ISK_CNY = 0.0555               # 兜底汇率；fetch 到实时汇率时会覆盖


# ────────────────────────────── 工具
def haversine(a, b):
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def get_json(url, timeout=60, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


def post_json(url, data, timeout=180, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as f:
        return json.load(f)


def load_fx():
    """取实时 ISK→CNY（免 Key；失败则用兜底值）"""
    global ISK_CNY
    try:
        d = get_json("https://open.er-api.com/v6/latest/ISK", timeout=20)
        ISK_CNY = d["rates"]["CNY"]
        return True
    except Exception:
        return False


# ────────────────────────────── provider: Overpass（免 Key，全球）
def fetch_overpass(town, lat, lng, radius_km):
    q = (f'[out:json][timeout:120];\n(\n'
         f' nwr["tourism"~"^({KINDS})$"](around:{int(radius_km*1000)},{lat},{lng});\n'
         f' nwr["tourism"="camp_site"](around:{int(radius_km*1000)},{lat},{lng});\n);\n'
         f'out center tags;')
    data = urllib.parse.urlencode({"data": q}).encode()
    delay, last = 12, "unknown"
    for attempt in range(5):
        ep = OVERPASS_EPS[attempt % len(OVERPASS_EPS)]
        try:
            d = post_json(ep, data)
            return d.get("elements", [])
        except Exception as e:
            last = str(e)
            print(f"    [{town}] 第{attempt+1}次 {ep.split('/')[2]} -> {e}", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 1.8, 70)
    print(f"    [{town}] 放弃（{last}）", file=sys.stderr)
    return []


def norm_overpass(el):
    t = el.get("tags", {})
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lng = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lng is None:
        return None
    name = t.get("name:zh") or t.get("name:en") or t.get("name")
    if not name:
        return None
    addr = " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
    return dict(name=name, name_local=t.get("name") if t.get("name") != name else None,
                kind=t.get("tourism"), lat=round(float(lat), 5), lng=round(float(lng), 5),
                stars=t.get("stars"), rooms=t.get("rooms"), beds=t.get("beds"),
                capacity=t.get("capacity"),
                website=t.get("website") or t.get("contact:website"),
                phone=t.get("phone") or t.get("contact:phone"),
                email=t.get("email") or t.get("contact:email"),
                addr=addr or None, postcode=t.get("addr:postcode"),
                uid=f"{el['type']}/{el['id']}", src="osm")


# ────────────────────────────── provider: 高德（国内覆盖最好，需免费 Key）
AMAP_TYPE = "100000"          # 住宿服务


def fetch_amap(town, lat, lng, radius_km, key):
    """高德 Web 服务 POI 搜索 v3。免费个人 Key 每日额度充足。"""
    out = []
    for page in (1, 2, 3):
        qs = urllib.parse.urlencode({
            "key": key, "types": AMAP_TYPE, "location": f"{lng:.6f},{lat:.6f}",
            "radius": int(radius_km * 1000), "sortrule": "distance",
            "offset": 25, "page": page, "extensions": "all",
        })
        try:
            d = get_json("https://restapi.amap.com/v3/place/around?" + qs, timeout=25)
        except Exception as e:
            print(f"    [{town}] 高德请求失败：{e}", file=sys.stderr)
            break
        if str(d.get("status")) != "1":
            print(f"    [{town}] 高德返回错误：{d.get('info')}（{d.get('infocode')}）", file=sys.stderr)
            break
        pois = d.get("pois") or []
        if not pois:
            break
        for p in pois:
            lnglat = (p.get("location") or ",").split(",")
            if len(lnglat) != 2:
                continue
            biz = p.get("biz_ext") or {}
            if isinstance(biz, list):
                biz = biz[0] if biz else {}
            out.append(dict(
                name=p.get("name"), name_local=None,
                kind=amap_kind(p.get("type") or ""),
                lat=round(float(lnglat[1]), 5), lng=round(float(lnglat[0]), 5),
                stars=None, rooms=None, beds=None, capacity=None,
                website=(p.get("website") or None),
                phone=(p.get("tel") or None) if p.get("tel") not in ("", "[]") else None,
                email=None,
                addr=p.get("address") if p.get("address") not in ("", "[]") else None,
                postcode=None, uid="amap/" + str(p.get("id")), src="amap",
                rating=biz.get("rating") or None,
                cost=biz.get("cost") or None,          # 人均消费（部分酒店返回）
                opentime=p.get("opentime") or None,
                cityname=p.get("cityname") or None,
            ))
        time.sleep(0.3)
        if len(pois) < 25:
            break
    return out


def amap_kind(t):
    if "宾馆酒店" in t or "星级酒店" in t or "酒店" in t:
        return "hotel"
    if "旅馆招待所" in t or "招待所" in t:
        return "guesthouse"
    if "青年旅舍" in t or "旅舍" in t:
        return "hostel"
    if "民宿" in t or "公寓" in t:
        return "apartment"
    if "露营地" in t:
        return "camp_site"
    return "hotel"


# ────────────────────────────── fetch
def cmd_fetch(args):
    load_fx()
    stays = json.load(open(args.stays, encoding="utf-8"))
    provider = args.provider
    if provider == "amap" and not args.amap_key:
        print("!! 使用 --provider amap 必须提供 --amap-key（高德 Web 服务 Key）", file=sys.stderr)
        print("   申请入口：https://lbs.amap.com/  → 控制台 → 应用管理 → 添加 Key（选 Web服务）",
              file=sys.stderr)
        sys.exit(2)

    result = {}
    for st in stays:
        town, lat, lng = st["town"], st["lat"], st["lng"]
        rk = st.get("radius_km", 20)
        print(f"[{town}] {provider} 半径 {rk}km ...", flush=True)
        if provider == "amap":
            items = fetch_amap(town, lat, lng, rk, args.amap_key)
        else:
            items = [x for x in (norm_overpass(e) for e in fetch_overpass(town, lat, lng, rk)) if x]
        seen, uniq = set(), []
        for it in items:
            k = (it["name"] or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            uniq.append(it)
        uniq.sort(key=lambda x: (x["kind"] != "hotel",
                                 -(int(x["stars"]) if (x["stars"] or "").isdigit() else 0),
                                 x["name"]))
        result[town] = dict(note=st.get("note", ""), lat=lat, lng=lng,
                            radius_km=rk, count=len(uniq), provider=provider, items=uniq)
        print(f"    -> {len(uniq)} 家", flush=True)
        time.sleep(3 if provider == "amap" else 15)

    json.dump(result, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n落地：{args.out}（共 {sum(v['count'] for v in result.values())} 家）")


# ────────────────────────────── pick
def band_of(it, currency):
    k = it.get("kind")
    s = it.get("stars") or ""
    star = int(s) if str(s).isdigit() else 0
    rooms = int(it["rooms"]) if str(it.get("rooms") or "").isdigit() else 0
    if k == "hostel":
        return "青旅/床位" if currency == "CNY" else "床位/青旅"
    if k == "camp_site":
        return "营地"
    if currency == "CNY":
        if LUX.search(it["name"] or "") or star >= 5:
            return "豪华/度假"
        if star >= 4:
            return "高档型"
        if star == 3:
            return "舒适型"
        if k in ("guesthouse", "bed_and_breakfast", "farm", "motel", "apartment", "chalet"):
            return "经济连锁"
        return "舒适型" if rooms >= 40 else "经济连锁"
    if LUX.search(it["name"] or "") or star >= 5:
        return "高端/特色"
    if star >= 4:
        return "四星/商务"
    if star == 3:
        return "三星酒店"
    if k in ("guesthouse", "bed_and_breakfast", "farm", "motel", "chalet", "apartment"):
        return "民宿/客栈"
    return "三星酒店" if rooms >= 30 else "民宿/客栈"


def price_txt(band, currency, fam=True):
    if band == "营地":
        return "营地费 €15~25/人·晚（约 ¥120~200/人）" if currency != "CNY" \
               else "营地费 50~150 元/人·晚"
    tbl = BANDS_CN if currency == "CNY" else BANDS_ISK
    if band not in tbl:
        return "—"
    lo, hi, note = tbl[band]
    sym = "¥" if currency == "CNY" else "ISK "
    body = f"{sym}{lo:,}~{hi:,}/晚，{note}" if currency == "CNY" else \
           f"{lo:,}~{hi:,} ISK（¥{lo*ISK_CNY:,.0f}~{hi*ISK_CNY:,.0f}）/晚，{note}"
    if not fam:
        return body
    f1, f2 = FAMILY_K
    if currency == "CNY":
        return body + f"；2大1小家庭房约 ¥{lo*f1:,.0f}~{hi*f2:,.0f}"
    return body + f"；2大1小家庭房约 ¥{lo*ISK_CNY*f1:,.0f}~{hi*ISK_CNY*f2:,.0f}"


def cmd_pick(args):
    load_fx()
    lod = json.load(open(args.lodging, encoding="utf-8"))
    currency = args.currency
    res = []
    for town, blk in lod.items():
        anchor = (blk["lat"], blk["lng"])
        items = []
        for x in blk["items"]:
            if x["kind"] == "camp_site" and not args.include_camp:
                continue
            if BADNAME.search(x["name"] or "") or len(x["name"] or "") < 3:
                continue
            if (x["name"] or "").strip().lower() == town.lower():
                continue          # OSM 常见"与镇同名"的泛化条目，信息量为零
            d = round(haversine(anchor, (x["lat"], x["lng"])), 1)
            if d > args.max_km:
                continue
            items.append(dict(x, dist_km=d, band=band_of(x, currency)))
        for x in items:
            x["star"] = int(x["stars"]) if str(x.get("stars") or "").isdigit() else 0
            x["roomn"] = int(x["rooms"]) if str(x.get("rooms") or "").isdigit() else 0

        def best(pool, key):
            return sorted(pool, key=key)[0] if pool else None

        near = [x for x in items if x["dist_km"] <= 6]
        hi_band = "高档型" if currency == "CNY" else "四星/商务"
        mid_band = "舒适型" if currency == "CNY" else "三星酒店"
        low_b1, low_b2 = ("经济连锁", "青旅/床位") if currency == "CNY" else ("民宿/客栈", "床位/青旅")

        main_pool = ([x for x in near if x["band"] == mid_band]
                     or [x for x in near if x["band"] == hi_band]
                     or [x for x in near if x["kind"] == "hotel"]
                     or [x for x in near if x["band"] in (low_b1, low_b2)])
        if not main_pool:      # 镇内确实没有像样住宿 -> 放宽到 15km（并在 why 里如实说明）
            main_pool = [x for x in items if x["dist_km"] <= 15
                         and x["band"] in (mid_band, hi_band, low_b1)]
        first = best(main_pool, lambda x: (x["dist_km"] - (3 if x["roomn"] >= 40 else 0)
                                           - (0.6 if x["star"] == 3 else 0)
                                           - (0.8 if x.get("website") else 0)))
        up_pool = [x for x in items if x["band"] in (hi_band, "高端/特色", "豪华/度假")
                   and x["dist_km"] <= 12 and x is not first]
        if not up_pool:
            up_pool = [x for x in items if x["band"] == mid_band and x["dist_km"] <= 12
                       and x is not first]
        up = best(up_pool,
                  lambda x: (x["star"] < 4, x["dist_km"] * 0.5 - x["star"] * 1.5))
        special = best([x for x in items if SPECIAL.search(x["name"] or "")
                        and x not in (first, up)
                        and x["band"] in (low_b1, hi_band, "民宿/客栈", "高档型", "豪华/度假")],
                       lambda x: x["dist_km"])
        cheap = best([x for x in items if x["band"] in (low_b1, low_b2)
                      and x not in (first, up, special)],
                     lambda x: (x["band"] == low_b1 and 0 or 2, x["dist_km"]))

        def fmt(x, tag, why):
            if not x:
                return None
            zh = x.get("name_local") or ""
            return dict(tag=tag, name=x["name"], kind=x["kind"], stars=x.get("stars"),
                        rooms=x.get("rooms"), band=x["band"], dist_km=x["dist_km"],
                        website=x.get("website"), phone=x.get("phone"),
                        rating=x.get("rating"), cost=x.get("cost"),
                        lat=x["lat"], lng=x["lng"],
                        price=price_txt(x["band"], currency), why=why)

        picks = [p for p in [
            fmt(first, "首选", ("镇内可选不多，最近的是 {:.1f}km 外的 {}".format(
                    first["dist_km"], first["band"]) if first and first["dist_km"] > 6
                else "房量足、离落脚点 {:.1f}km，三口之家最省心".format(first["dist_km"]))
                if first else ""),
            fmt(up, "升级", "高一档硬件，适合想住舒服的夜晚"),
            fmt(special, "特色", "当地特色住宿（农场/度假村/景观房）"),
            fmt(cheap, "预算", "控制总预算用，独立卫浴需下单前确认"),
        ] if p]
        res.append(dict(town=town, anchor=list(anchor), pool=len(items), picks=picks,
                        pool_list=[dict(name=x["name"], kind=x["kind"], stars=x.get("stars"),
                                        rooms=x.get("rooms"), band=x["band"], dist_km=x["dist_km"],
                                        rating=x.get("rating"),
                                        price=price_txt(x["band"], currency, fam=False))
                                   for x in sorted(items, key=lambda y: y["dist_km"])[:12]]))

    json.dump(res, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in res:
        print(f"\n=== 宿·{r['town']}（候选 {r['pool']} 家）")
        for p in r["picks"]:
            st = f"{p['stars']}星 " if p["stars"] else ""
            print(f"  [{p['tag']}] {p['name']}  {st}[{p['band']}]  {p['dist_km']}km")
            print(f"          {p['price']}")
    print(f"\n落地：{args.out}")


def main():
    ap = argparse.ArgumentParser(description="住宿层：抓取住宿点位 + 生成推介与价格档位")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="按住宿锚点抓取住宿点位")
    f.add_argument("--stays", required=True, help="住宿锚点 JSON")
    f.add_argument("--out", required=True)
    f.add_argument("--provider", default="overpass", choices=["overpass", "amap"])
    f.add_argument("--amap-key", default=os.environ.get("AMAP_KEY", ""))
    f.set_defaults(func=cmd_fetch)

    p = sub.add_parser("pick", help="生成首选/升级/特色/预算推介 + 价格档位")
    p.add_argument("--lodging", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--currency", default="ISK", choices=["ISK", "CNY"],
                   help="价格档位币种：ISK=境外，CNY=国内")
    p.add_argument("--max-km", type=float, default=25)
    p.add_argument("--include-camp", action="store_true")
    p.set_defaults(func=cmd_pick)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
