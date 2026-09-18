# -*- coding: utf-8 -*-
"""多片区自驾编排器 —— 在 trip-plan skill 的四阶段算法上扩展

相比原 planner.py 的增量：
  1. 支持**片区绑定**（DAY_PLAN 指定每天在哪个片区）
  2. 支持**半日约束**（day_ratio < 1.0，如 10/2 下午才到新昌）
  3. 支持**固定锚点**（9/30 晚住宿、上虞午餐）
  4. 区域内动线按**最近邻**排序，而非按分数（避免折返）

复用原算法的核心思想：名片保底 → 片区配额 → 全局补位
"""
import argparse
import json
import math
import os
import sys
from datetime import date, timedelta

# 让 `import places_multicity` 在本机/别的电脑/任意安装路径下都能工作
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import places_multicity as PM

# ============================================================
# 可调参数
# ============================================================
CITY_CENTER = (120.578, 30.000)      # 绍兴古城锚点（主片区）
PER_DAY = 4                          # 每满日主景点上限
MAX_DAY_SPAN_KM = 30.0               # 自驾放宽到 30km
PER_DAY_CAP_FACTOR = 1.0

# 单日游览时长预算（分钟）——这是比"景点个数"更本质的约束
#   大体力点（登山/大景区）单独就会吃掉半天以上，不能被"4个点"的计数掩盖
DAY_MINUTES_BUDGET = 420             # 满日 7 小时净游览
DAY_MINUTES_BUDGET_HALF = 260        # 半日 4.3 小时（含傍晚衔接时段）

# 大体力阈值：单点建议时长 ≥ 此值，视为"半日级"点，同日最多 2 个
BIG_SPOT_MIN = 150

# 片区锚点：用于计算"离主城距离"，决定片区排序
DISTRICT_ANCHOR = {
    "上虞": (120.876, 30.014),
    "绍兴": CITY_CENTER,
    "新昌": (120.882, 29.500),
}

# ============================================================
# 行程骨架（先生指定的硬约束）
# ============================================================
#   ratio: 该日可用游览时间比例（1.0 = 全天，0.5 = 半日）
#   excl : 该日必须排除的点（如覆卮山太远，不与城区混排；雨天不宜登山）
DAY_PLAN = [
    {"idx": 0, "date": "2026-09-30", "district": None, "ratio": 0.0,
     "label": "上海 → 上虞（夜宿）",
     "anchor": "19:00 出发，约 22:00 抵达上虞，宵夜后入住"},
    {"idx": 1, "date": "2026-10-01", "district": "上虞", "ratio": 1.0,
     "label": "上虞全天",
     "anchor": "覆卮山千年梯田 + 丰惠古镇 + 曹娥江夜景"},
    {"idx": 2, "date": "2026-10-02", "district": "新昌", "ratio": 0.5,
     "label": "上虞 → 绍兴（午餐）→ 新昌（下午抵达）",
     "anchor": "上午上虞收尾，中午绍兴古城午餐，14:30 后转场新昌，仅排近城 1~2 点",
     "excl": ["十九峰", "飞龙栈道", "千丈幽谷", "重阳宫", "天姥山", "沃洲湖"]},
    {"idx": 3, "date": "2026-10-03", "district": "新昌", "ratio": 1.0,
     "label": "新昌西线全天（穿岩十九峰）",
     "anchor": "十九峰 + 千丈幽谷 + 飞龙栈道（大体力日，需早出发）"},
    {"idx": 4, "date": "2026-10-04", "district": "新昌", "ratio": 1.0,
     "label": "新昌南线全天（天姥山 / 沃洲湖）",
     "anchor": "天姥山古道 + 沃洲湖游船 + 斑竹古村"},
    {"idx": 5, "date": "2026-10-05", "district": "新昌收尾", "ratio": 0.4,
     "label": "新昌 → 上海（返程）",
     "anchor": "上午新昌县城收尾（大佛寺/老街），12:30 出发返沪，约 16:30 到家",
     "excl": ["十九峰", "飞龙栈道", "千丈幽谷", "重阳宫", "天姥山", "沃洲湖",
              "斑竹古村", "下岩贝村", "盐帮十八渡", "越剧小镇", "达利丝绸世界"]},
]

TOTAL_DAYS = 6


# ============================================================
# 地理工具
# ============================================================
def haversine(a, b):
    R = 6371.0
    lon1, lat1 = a
    lon2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def assign_region(lon, lat):
    best, bestd = None, 1e9
    for rname, rlon, rlat, _ in PM.REGIONS:
        d = haversine((lon, lat), (rlon, rlat))
        if d < bestd:
            best, bestd = rname, d
    return best, bestd


def district_of(region):
    """区域 → 所属片区"""
    if region.startswith("上虞"):
        return "上虞"
    if region.startswith("新昌"):
        return "新昌"
    return "绍兴"


# ============================================================
# 打分（沿用原算法的因子，权重按自驾场景微调）
# ============================================================
def score_place(p, must_set):
    s = 0.0
    name = p["name"]
    note = p.get("note", "")

    if p.get("ticket_price") == 0:
        s += 3.0                                    # 免费加分
    if name in must_set:
        s += 6.0                                    # 名片保底
    if "4A" in note or "5A" in note or "5A级" in note:
        s += 2.5
    if "国家级非遗" in note or "非遗" in note:
        s += 1.0
    # 招牌关键词
    for hot in ["鲁迅故里", "沈园", "兰亭", "东湖", "大禹陵", "安昌古镇",
                "仓桥直街", "书圣故里", "柯岩风景区", "八字桥",
                "新昌大佛寺", "十九峰", "千丈幽谷", "天姥山", "沃洲湖",
                "覆卮山", "曹娥庙", "丰惠古镇"]:
        if hot in name:
            s += 3.0
            break
    # 距片区锚点越远越降权（自驾放宽，系数取小）
    c = (p["lon"], p["lat"])
    anch = DISTRICT_ANCHOR[district_of(assign_region(*c)[0])]
    dist = haversine(c, anch)
    if dist > 25:
        s -= 3.0
    elif dist > 12:
        s -= 1.2
    return round(s, 2)


# ============================================================
# 编排核心
# ============================================================
def plan(days_spec=DAY_PLAN):
    places = PM.build()
    for p in places:
        p["_coord"] = (p["lon"], p["lat"])
        p["_region"], p["_region_dist"] = assign_region(*p["_coord"])
        p["_district"] = district_of(p["_region"])
        p["_score"] = score_place(p, set(sum(PM.MUST_SEE.values(), [])))

    # 每片区一个待选池，按分数降序
    pools = {}
    for p in places:
        pools.setdefault(p["_district"], []).append(p)
    for k in pools:
        pools[k].sort(key=lambda x: -x["_score"])

    used = set()
    scheduled = []

    # ---- 逐日分配 ----
    for spec in days_spec:
        dist = spec["district"]
        ratio = spec["ratio"]
        if dist is None or dist == "返程":
            scheduled.append({"spec": spec, "items": []})
            continue

        excl = set(spec.get("excl", []))
        # 「新昌收尾」= 只能用新昌县城内的近点
        if dist == "新昌收尾":
            pool = [p for p in pools.get("新昌", [])
                    if p["name"] not in used and p["name"] not in excl
                    and p["_region"] == "新昌县城"]
            dist_for_must = "新昌"
        else:
            pool = [p for p in pools.get(dist, [])
                    if p["name"] not in used and p["name"] not in excl]
            dist_for_must = dist

        # 时间预算：比"点数"更本质
        budget = DAY_MINUTES_BUDGET_HALF if ratio <= 0.5 else DAY_MINUTES_BUDGET
        if ratio <= 0.5:
            budget = max(budget, 200)
        cap_count = max(2, round(PER_DAY * ratio)) if ratio <= 0.5 else PER_DAY

        picked = []

        def spent():
            return sum(p["duration_min"] for p in picked)

        def big_count():
            return sum(1 for p in picked if p["duration_min"] >= BIG_SPOT_MIN)

        def can_add(p):
            """三重门：时间预算 / 点数上限 / 大体力点配额 / 地理跨度"""
            if len(picked) >= cap_count:
                return False
            if spent() + p["duration_min"] > budget:
                return False
            # 大体力点（≥150min）同日最多 2 个
            if p["duration_min"] >= BIG_SPOT_MIN and big_count() >= 2:
                return False
            if picked and max(haversine(p["_coord"], q["_coord"])
                              for q in picked) > MAX_DAY_SPAN_KM:
                return False
            return True

        # 1) 名片优先占位
        must_here = [p for p in pool if p["name"] in set(PM.MUST_SEE.get(dist_for_must, []))]
        must_here.sort(key=lambda x: -x["_score"])
        for p in must_here:
            if can_add(p):
                picked.append(p)

        # 2) 余量按分数补，但优先填"地理上贴合已选点"的，且小点优先填缝
        if len(picked) < cap_count:
            rest = [p for p in pool if p not in picked]

            def fill_key(p):
                if not picked:
                    return -p["_score"]
                near = min(haversine(p["_coord"], q["_coord"]) for q in picked)
                # 小点优先填缝（避免把大点硬塞进剩余的碎片时间）
                size_pen = max(0, p["duration_min"] - (budget - spent())) * 0.500
                return near * 0.8 - p["_score"] * 0.5 + size_pen

            rest.sort(key=fill_key)
            for p in rest:
                if can_add(p):
                    picked.append(p)

        for p in picked:
            used.add(p["name"])
        scheduled.append({"spec": spec, "items": picked})

    # ---- 每日动线排序（最近邻，从离当日首点上手）----
    out_days = []
    for entry in scheduled:
        spec, items = entry["spec"], entry["items"]
        if items:
            items = nearest_neighbor_order(items)
        out_days.append(make_day(spec, items))

    # ---- 备选池 ----
    alts = []
    for k, pool in pools.items():
        for p in pool:
            if p["name"] not in used:
                alts.append(p)
    alts.sort(key=lambda x: -x["_score"])

    return {
        "days": out_days,
        "alternatives": [{"name": p["name"], "district": p["_district"],
                          "price": p["ticket_price"], "score": p["_score"],
                          "note": p["note"]} for p in alts[:20]],
        "stats": {
            "total_places": len(places),
            "scheduled": sum(len(d["attractions"]) for d in out_days),
            "alternatives": len(alts),
        },
    }


def nearest_neighbor_order(items):
    """最近邻排序：避免同天内折返"""
    if len(items) <= 2:
        return items
    # 从离片区质心最远的点开始（它最挑顺序）
    clon = sum(p["_coord"][0] for p in items) / len(items)
    clat = sum(p["_coord"][1] for p in items) / len(items)
    cur = max(items, key=lambda p: haversine(p["_coord"], (clon, clat)))
    order = [cur]
    remain = [p for p in items if p is not cur]
    while remain:
        nxt = min(remain, key=lambda p: haversine(p["_coord"], cur["_coord"]))
        order.append(nxt)
        remain.remove(nxt)
        cur = nxt
    return order


def make_day(spec, items):
    d = date.fromisoformat(spec["date"])
    wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]
    regions = sorted({p["_region"] for p in items})
    total = sum(p["duration_min"] for p in items)
    # 单日最大跨度
    span = 0.0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            span = max(span, haversine(items[i]["_coord"], items[j]["_coord"]))
    return {
        "day_index": spec["idx"],
        "date": spec["date"],
        "weekday": wd,
        "label": spec["label"],
        "anchor": spec["anchor"],
        "district": spec["district"] or "—",
        "ratio": spec["ratio"],
        "region": " + ".join(regions) if regions else "机动",
        "attractions": [{
            "name": p["name"], "type": p["type"], "district": p["_district"],
            "region": p["_region"], "duration_min": p["duration_min"],
            "ticket_price": p["ticket_price"], "note": p["note"],
            "score": p["_score"],
            "reservation_required": p["reservation_required"],
        } for p in items],
        "total_duration_min": total,
        "max_span_km": round(span, 1),
        "tickets": sum(p["ticket_price"] or 0 for p in items),
    }


if __name__ == "__main__":
    itin = plan()
    ap = argparse.ArgumentParser(description="多片区自驾编排器")
    ap.add_argument("--places", default=None, help="places.json（默认用 places_multicity 现建）")
    ap.add_argument("--out", default="itinerary.json")
    args = ap.parse_args()

    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(itin, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"编排完成：{len(itin['days'])} 天 / {itin['stats']['scheduled']} 个景点")
    for d in itin["days"]:
        names = " → ".join(a["name"] for a in d["attractions"]) or "（无景点安排）"
        print(f"  D{d['day_index']} {d['date']} {d['weekday']} [{d['district']}] "
              f"跨度{d['max_span_km']}km 门票{d['tickets']}元 | {names}")
    tot = sum(d["tickets"] for d in itin["days"])
    print(f"门票合计：{tot} 元")
    print(f"备选 {itin['stats']['alternatives']} 个")
