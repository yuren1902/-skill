# -*- coding: utf-8 -*-
"""
planner.py — 规则引擎行程编排器

核心思想：不依赖 LLM，用可解释的规则把候选地点排成 N 天行程。
    1. 按地理聚类（同区域的地点排同一天，避免折返）
    2. 按权重打分（免费 / 4A级以上 / 信源权威 / 用户偏好）
    3. 按容量约束（每天 2-3 个主景点 + 三餐）
    4. 按体力曲线（徒步类景点后一天安排轻松景点）

模型可选增强：若配置了 LLM，则用它优化 description / 推荐理由；
              未配置时用规则模板生成，质量依然可用。

用法：
  python planner.py --places ./data/places.json --days 5 \
                    --start 2026-10-01 --out ./data/itinerary.json
"""
import argparse
import json
import math
import os
import re
import sys
from datetime import date, timedelta


# ============================================================
# 地点坐标表（用于地理聚类；可按城市扩展）
# 绍兴核心地点近似经纬度（GCJ-02）
# ============================================================
KNOWN_COORDS = {
    # 越城区古城核心（鲁迅故里一带）
    "鲁迅故里": (120.5855, 30.0020),
    "沈园": (120.5865, 30.0008),
    "三味书屋": (120.5867, 30.0026),
    "百草园": (120.5860, 30.0028),
    "仓桥直街": (120.5822, 30.0048),
    "书圣故里": (120.5880, 30.0080),
    "八字桥": (120.5920, 30.0065),
    "广宁桥": (120.5905, 30.0072),
    "题扇桥": (120.5875, 30.0085),
    "府山公园": (120.5780, 30.0055),
    "越王台": (120.5790, 30.0060),
    "周恩来纪念馆": (120.5840, 30.0090),
    "蔡元培故居": (120.5872, 30.0068),
    "秋瑾故居": (120.5850, 30.0100),
    "绍兴博物馆新馆": (120.5788, 29.9880),
    "绍兴美术馆": (120.5795, 29.9872),
    "绍兴大剧院": (120.5810, 29.9900),
    "鲁迅故里步行街": (120.5852, 30.0022),
    "迎恩门风情水街": (120.5720, 30.0120),
    "东湖": (120.6280, 29.9980),
    "大禹陵": (120.5980, 29.9820),
    "兰亭": (120.5200, 29.9430),
    "安昌古镇": (120.4830, 30.0780),
    "柯岩风景区": (120.4650, 29.9600),
    "柯桥古镇": (120.4930, 30.0680),
    "鲁镇": (120.4680, 29.9580),
    "黄酒小镇": (120.5490, 30.0400),
    "绍兴北站": (120.5520, 30.0900),
    "绍兴站": (120.5790, 29.9930),
    "五泄风景区": (120.0300, 29.7200),
    "诸暨": (120.2400, 29.7100),
    "新昌大佛寺": (120.9000, 29.5000),
    "上虞": (120.8700, 30.0300),
    "上虞曹娥江": (120.8760, 30.0120),
    # 补充：古城内小型地标
    "大善塔": (120.5838, 30.0008),
    "塔山": (120.5845, 29.9975),
    "应天塔": (120.5845, 29.9975),
    "青藤书屋": (120.5895, 29.9958),
    "秋瑾故居": (120.5852, 30.0102),
    "蕺山": (120.5892, 30.0095),
    "阳明故里": (120.5888, 30.0072),
    "柯岩": (120.4650, 29.9600),
    "绍兴柯岩": (120.4650, 29.9600),
    "兰亭景区": (120.5200, 29.9430),
    "枫桥古镇": (120.4300, 29.8300),
    "丰惠古镇": (120.8900, 29.9700),
    "曹娥景区": (120.8760, 30.0120),
    "汤江岩风景区": (120.4200, 29.8800),
    "白塔": (120.5900, 30.0200),
    "广宁桥": (120.5905, 30.0072),
    "东双桥": (120.5915, 30.0060),
    "题扇桥": (120.5875, 30.0085),
}

# 区域划分（用于地理聚类）
REGIONS = [
    ("古城核心", 120.575, 30.002, 3.0),     # 鲁迅故里/沈园/仓桥直街/书圣故里
    ("古城北", 120.580, 30.011, 2.5),       # 迎恩门/秋瑾故居
    ("古城西", 120.579, 29.988, 2.5),       # 博物馆新馆/美术馆/大剧院
    ("城东", 120.625, 29.998, 4.0),         # 东湖
    ("城南", 120.598, 29.982, 4.0),         # 大禹陵
    ("兰亭片区", 120.520, 29.943, 12.0),    # 兰亭（西郊，约12km）
    ("柯桥片区", 120.485, 30.100, 22.0),    # 安昌古镇/柯桥古镇（西北约22km）
    ("鉴湖柯岩", 120.470, 29.995, 14.0),    # 柯岩/鲁镇（西南约14km）
    ("黄酒小镇", 120.549, 30.040, 6.0),
    ("诸暨片区", 120.150, 29.715, 45.0),    # 五泄
    ("新昌片区", 120.900, 29.500, 65.0),    # 大佛寺
    ("上虞片区", 120.873, 30.025, 32.0),    # 曹娥景区/丰惠古镇
]


# 区域基础品质权重：古城核心区密度高、体验集中，应优先占用行程日
REGION_BONUS = {
    "古城核心": 8.0,
    "古城北": 6.0,
    "古城西": 4.0,
    "城东": 3.0,
    "城南": 3.0,
    "黄酒小镇": 2.0,
    "鉴湖柯岩": 2.0,
    "兰亭片区": 1.0,
    "柯桥片区": 1.0,
    "上虞片区": -2.0,
    "诸暨片区": -2.0,
    "新昌片区": -3.0,
}

# 市中心锚点（古城核心）——所有"离城距离"从这里量，而不是从区域自身中心量
CITY_CENTER = (120.578, 30.000)

# 每天最多安排的主景点数（超出的转入备选）
PER_DAY = 4

# 招牌级点位阈值：单点得分达到此值即无条件保底入选
SIGNATURE_SCORE = 5.0

# 单日地理跨度上限（km）：同一天内景点两两距离不得超过此值
#   —— 防止"柯岩 + 兰亭 + 五泄"这种一天跑 60km 的硬核排法
MAX_DAY_SPAN_KM = 18.0

# 远郊名片阈值（km）：超过此距离的名片不占主行程天数，转入"一日游备选"
#   —— 单程 1.5h 以上，当天往返会挤掉市区深度体验，更适合列为可选延伸行程
FAR_SIGNATURE_KM = 45.0

# 保底名片清单：城市级"来都来了"级别点位，无论片区密度如何都必须进主行程
#   （与 score 白名单解耦——这些点可能在检索文本里缺 4A 标注，导致评分被低估）
MUST_SEE = {
    "绍兴": ["鲁迅故里", "沈园", "兰亭", "东湖", "大禹陵", "安昌古镇",
             "仓桥直街", "书圣故里", "柯岩", "柯岩风景区", "柯桥古镇",
             "黄酒小镇", "五泄", "五泄风景区", "八字桥"],
}


def haversine(lon1, lat1, lon2, lat2):
    """两点距离（km）"""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def lookup_coord(name):
    """查坐标（模糊匹配）"""
    for k, v in KNOWN_COORDS.items():
        if k in name or name in k:
            return v
    return None


def assign_region(lon, lat):
    """归属区域 + 距区域中心距离"""
    best, bestd = None, 1e9
    for rname, rlon, rlat, _ in REGIONS:
        d = haversine(lon, lat, rlon, rlat)
        if d < bestd:
            best, bestd = rname, d
    return best, bestd


# ============================================================
# 打分层
# ============================================================
def score_place(p, preferences=None, grade_map=None):
    """多因子打分：越高质量越优先"""
    s = 0.0
    note = p.get("note", "")
    name = p.get("name", "")

    # 免费加分（None 视为价格未知，不加不减）
    if p.get("ticket_price") == 0:
        s += 3.0
    # 权威级内容加权
    if grade_map and grade_map.get(name) == "A":
        s += 2.0
    # 4A/5A 景区
    if re.search(r"[45]A", note):
        s += 2.5
    # 核心名气
    for hot in ["鲁迅故里", "沈园", "兰亭", "东湖", "大禹陵", "安昌古镇",
                "仓桥直街", "书圣故里", "柯岩", "八字桥"]:
        if hot in name:
            s += 3.0
            break
    # 用户偏好匹配
    if preferences:
        for pref in preferences:
            if pref in note or pref in name:
                s += 1.5
    # 交通枢纽降权（不当作景点）
    if p.get("type") == "T":
        s -= 6.0
    # 住宿单列
    if p.get("type") == "A":
        s -= 4.0
    # 有预约门槛的轻度降权
    if p.get("reservation_required"):
        s -= 0.5
    # 坐标未知（未定位）降权——排进行程但优先级低
    if not lookup_coord(p.get("name", "")):
        s -= 2.0
    # 中置信度（来自文本抽取而非白名单）降权
    if p.get("confidence") == "medium":
        s -= 1.5
    # 远郊区域降权：单程远、当日只能看少量点，性价比低
    coord = lookup_coord(p.get("name", ""))
    if coord:
        _, dist = assign_region(*coord)
        if dist > 30:
            s -= 5.0        # 诸暨/新昌/上虞级别
        elif dist > 12:
            s -= 2.0        # 兰亭/柯桥级别
    return round(s, 2)


# ============================================================
# 编排层
# ============================================================
def cluster_by_region(places):
    """按区域聚类"""
    buckets = {}
    for p in places:
        coord = lookup_coord(p["name"])
        if coord:
            p["_lon"], p["_lat"] = coord
            rname, dist = assign_region(*coord)
            p["_region"] = rname
            p["_region_dist"] = round(dist, 1)
            buckets.setdefault(rname, []).append(p)
        else:
            p["_region"] = "未定位"
            buckets.setdefault("未定位", []).append(p)
    return buckets


def normalize_place(p):
    """兼容 research.py / extract.py 两种地点格式"""
    t = p.get("type", "5")
    label = p.get("type_label") or TYPE_LABEL.get(t, "其他")
    out = dict(p)
    out["type"] = t
    out["type_label"] = label
    out.setdefault("ticket_price", None)
    out.setdefault("duration_min", 90)
    out.setdefault("reservation_required", False)
    out.setdefault("reservation_tips", "")
    out.setdefault("note", "")
    out.setdefault("name", "")
    return out


TYPE_LABEL = {
    "C": "演出场馆", "S": "体育场馆", "M": "农旅/市集", "U": "博物馆/美术馆",
    "5": "景区", "H": "茶饮门店", "F": "美食/古街", "W": "city walk",
    "L": "购物中心", "D": "地铁/交通", "T": "交通枢纽", "A": "住宿",
}


# 父子景点关系：子景点已包含在父景区内，不应重复占用行程位
PARENT_CHILD = {
    "鲁迅故里": ["三味书屋", "百草园", "鲁迅故里步行街"],
    "柯岩风景区": ["鲁镇", "柯岩"],
    "东湖": ["东湖景区"],
    "绍兴博物馆新馆": ["绍兴美术馆"],   # 双馆同址，可合并为一次参观
    "书圣故里": ["题扇桥", "蕺山"],
    "府山公园": ["越王台"],
    "兰亭": ["兰亭景区"],
    "仓桥直街": ["广宁桥", "东双桥", "白塔", "宝珠桥"],   # 古桥群属历史街区子点位
}


# 行程外地点：客源地、中转城市、邻市景区、泛称/错字地点
# 这些多来自「交通/周边」类查询，不是本次行程的主题景点，必须剔除
OFFROUTE_EXACT = {
    "西湖", "杭州西湖", "西塘古镇", "乌镇", "周庄", "南浔古镇",
    "上海虹桥", "虹桥", "上海", "杭州", "杭州东", "绍兴北站",
    "黃酒博物馆", "黄酒博物馆",          # 繁体/错字变体，已由绍兴黄酒博物馆覆盖
    "绍兴名城景区", "墨韵稽山",           # 泛称/品牌名，非实体景点
}


def is_offroute(name):
    """是否为行程外地点"""
    if name in OFFROUTE_EXACT:
        return True
    # 客源地与中转城市（外省省会/直辖市）泛称
    if len(name) <= 4 and name in OFFROUTE_EXACT:
        return True
    return False


def dedupe_children(spots):
    """去重：
       0) 剔除"行程外地点"（客源地/中转城市/邻市景点，从交通查询泄漏进来的噪声）
       1) 父景点已在列时，子景点不再单独占用行程位
       2) 同名变体合并（如「绍兴柯岩」与「柯岩风景区」）
    """
    drop = set()
    for p in spots:
        if is_offroute(p["name"]):
            drop.add(p["name"])

    names = {p["name"] for p in spots}
    for parent, children in PARENT_CHILD.items():
        if parent in names:
            for c in children:
                if c in names:
                    drop.add(c)

    # 同名变体合并：若 A 是 B 的子串（且都 ≥3 字），保留更"正式"的一个
    alive = [p for p in spots if p["name"] not in drop]
    sorted_names = sorted((p["name"] for p in alive), key=lambda x: -len(x))
    for i, n1 in enumerate(sorted_names):
        if n1 in drop:
            continue
        for n2 in sorted_names[i + 1:]:
            if n2 in drop:
                continue
            if n2 in n1 and len(n2) >= 3:
                # n2 是 n1 的子串 → 丢弃较短的 n2（保留信息更全的）
                drop.add(n2)
            elif n1 in n2 and len(n1) >= 3:
                drop.add(n1)
    # 地名核心词合并（如「柯岩」类地点归并）
    CORE_ALIAS = {
        "柯岩": {"绍兴柯岩", "柯岩风景区", "柯岩"},
        "东湖": {"东湖", "东湖景区"},
        "兰亭": {"兰亭", "兰亭景区"},
    }
    for core, variants in CORE_ALIAS.items():
        present = [v for v in variants if v in names and v not in drop]
        if len(present) > 1:
            # 保留含"景区/风景区"后缀的（更规范）
            keep = max(present, key=lambda x: ("景区" in x, len(x)))
            for v in present:
                if v != keep:
                    drop.add(v)

    return [p for p in spots if p["name"] not in drop], drop


def merge_regions_to_days(region_rank, days, per_day=PER_DAY, city=""):
    """把景点池切成 days 个"日区块"

    思路（与"按区域一天"不同）：
      1. 每个景点带区域标签，区域按地理邻近度聚成"片区(cluster)"
      2. 片区按"综合价值密度"排序——古城核心密度最高，应优先占天
      3. 逐个片区向当日填充，填满 per_day 上限就开新的一天
      4. 天不够时：把综合价值最低的片区整体丢进"备选"

    这样古城区能占 2 天、近郊各占 1 天，远郊低价值片区自然落到备选。
    """
    # 1) 区域 → 片区（地理邻近的合并为同一片区）
    GROUP = {
        "古城核心": "古城", "古城北": "古城", "古城西": "古城",
        "黄酒小镇": "古城",
        "城东": "近郊东", "城南": "近郊南",
        "鉴湖柯岩": "柯岩", "兰亭片区": "兰亭", "柯桥片区": "柯桥",
        "上虞片区": "上虞", "诸暨片区": "诸暨", "新昌片区": "新昌",
        "未定位": "未定位",
    }
    cents = {n: (lo, la) for n, lo, la, _ in REGIONS}

    # 2) 聚合为片区，计算片区价值与中心
    groups = {}
    for rname, items in region_rank:
        g = GROUP.get(rname, rname)
        node = groups.setdefault(g, {"regions": [], "items": [], "pts": []})
        node["regions"].append(rname)
        node["items"].extend(items)
        if rname in cents:
            node["pts"].append(cents[rname])

    # 3) 片区排序：价值密度 = 综合价值 / 保守天数
    ranked = []
    for g, node in groups.items():
        if node["pts"]:
            clon = sum(c[0] for c in node["pts"]) / len(node["pts"])
            clat = sum(c[1] for c in node["pts"]) / len(node["pts"])
        else:
            clon, clat = CITY_CENTER
        # 关键：离城距离从市中心锚点量，才有"远郊"语义
        dist = haversine(clon, clat, CITY_CENTER[0], CITY_CENTER[1])
        items = sorted(node["items"], key=lambda x: -x["_score"])
        # 片区价值：只看"排得上用场的前 per_day 个点"，避免低质噪声点拖累好片区
        #   （如兰亭片区里的汤江岩/枫桥古镇属噪声，不该把 4A 的兰亭拉下水）
        top = items[:per_day]
        val = sum(p["_score"] for p in top)
        # 片区里若有高分招牌点（≥4），额外加权——说明该片区值得专程跑一趟
        peak = max((p["_score"] for p in items), default=0.0)
        if peak >= 4.0:
            val += 2.5
        val += sum(REGION_BONUS.get(r, 0.0) for r in node["regions"])
        need = max(1, math.ceil(len(node["items"]) / per_day))
        ranked.append({
            "group": g, "regions": node["regions"], "items": items,
            "center": (clon, clat), "dist": dist,
            "value": val, "need": need, "density": val / need,
        })
    # 价值密度高者优先占天；远郊用距离惩罚压低密度
    for r in ranked:
        r["density"] -= r["dist"] * 0.28
    ranked.sort(key=lambda r: -r["density"])

    # 4) 铺天：两阶段，避免"一个大片区吃光容量、招牌全落选"
    #    阶段 A：按密度顺序，为每个值钱的片区"预留"至少 1 天（到 days 用尽为止）
    #    阶段 B：剩余容量按"地理邻近"填充次优点位
    day_blocks = [[] for _ in range(days)]
    day_regions = [[] for _ in range(days)]
    day_centers = [None] * days
    alternatives = []
    assigned = set()          # 已选定片区的 group 名

    def day_cost(k, r):
        """把片区 r 放进第 k 天的不适成本（越低越好）"""
        if day_centers[k] is None:
            return 0.0
        cl, ca = day_centers[k]
        return haversine(cl, ca, r["center"][0], r["center"][1])

    def coord_of(it):
        """取点位坐标（优先用已回填的，否则实时查表）"""
        if it.get("_lon") is not None:
            return it["_lon"], it["_lat"]
        return lookup_coord(it.get("name", ""))

    def span_ok(k, r):
        """把片区 r 放进第 k 天后，当天地理跨度是否可接受"""
        if not day_blocks[k]:
            return True
        cl, ca = r["center"]
        for q in day_blocks[k]:
            qc = coord_of(q)
            if not qc:
                continue
            if haversine(qc[0], qc[1], cl, ca) > MAX_DAY_SPAN_KM:
                return False
        return True

    def put(k, r, it):
        day_blocks[k].append(it)
        if r["group"] not in day_regions[k]:
            day_regions[k].append(r["group"])
        n = len(day_blocks[k])
        if day_centers[k] is None:
            day_centers[k] = r["center"]
        else:
            cl, ca = day_centers[k]
            day_centers[k] = ((cl * (n - 1) + r["center"][0]) / n,
                              (ca * (n - 1) + r["center"][1]) / n)

    # 阶段 A：每个"正向密度"片区保底 1 天；超出天数的高密度片区可多占
    quota = {r["group"]: (1 if r["density"] > 0 else 0) for r in ranked}
    # 高密度片区额外配额：密度越高、点位越多，允许多占
    if ranked:
        top = ranked[0]["density"]
        for r in ranked[1:]:
            if r["density"] > top * 0.55 and len(r["items"]) > per_day:
                quota[r["group"]] += 1
    # 若保底天数超出总天数，按密度从低到高砍掉
    while sum(quota.values()) > days:
        worst = min((r for r in ranked if quota[r["group"]] > 0),
                    key=lambda r: r["density"])
        quota[worst["group"]] -= 1

    def scheduled_names():
        return {q["name"] for d in day_blocks for q in d}

    # 阶段 0：保底入选（名片点优先占位 + 地理聚类，避免远郊名片与市区混排）
    #   (a) 城市名片清单里的点位——不依赖评分，避免检索文本缺 4A 标注被低估
    #   (b) 单点高分（score ≥ SIGNATURE_SCORE）的招牌点位
    must = set()
    for c, names in MUST_SEE.items():
        if city and (c in city or city in c):
            must.update(names)
    else:
        # 城市名未命中时，全部名片清单都视为候选（单城市场景）
        for names in MUST_SEE.values():
            must.update(names)

    signatures = []
    for r in ranked:
        for it in r["items"]:
            if it["name"] in must or it["_score"] >= SIGNATURE_SCORE:
                signatures.append((it, r))
    signatures.sort(key=lambda x: -x[0]["_score"])

    # 远郊名片先摘出来，作为"一日游备选"（不占主行程天数）
    daytrips = []
    sig_points = []
    for it, r in signatures:
        c = lookup_coord(it["name"]) or r["center"]
        d = haversine(c[0], c[1], *CITY_CENTER)
        if d > FAR_SIGNATURE_KM:
            daytrips.append({"it": it, "r": r, "coord": c, "dist": d})
            continue
        sig_points.append({"it": it, "r": r, "coord": c, "dist": d})
    # 贪心最近邻分组：相邻名片同组 → 同一天（远的先分组，它最挑天）
    sig_points.sort(key=lambda x: -x["dist"])

    sig_groups = []          # [[sp, ...], ...]
    for sp in sig_points:
        best, bestd = None, 1e9
        for gi, g in enumerate(sig_groups):
            if len(g) >= per_day:
                continue
            dmax = max(haversine(sp["coord"][0], sp["coord"][1],
                                 m["coord"][0], m["coord"][1]) for m in g)
            if dmax < bestd:
                best, bestd = gi, dmax
        if best is None or bestd > MAX_DAY_SPAN_KM:
            sig_groups.append([sp])
        else:
            sig_groups[best].append(sp)
    # 离城距离近的组优先，保证市区名片先拿到名额
    sig_groups.sort(key=lambda g: min(m["dist"] for m in g))

    for g in sig_groups:
        cands = [k for k in range(days) if len(day_blocks[k]) < per_day]
        if not cands:
            break
        # 优先放空天；否则挑与该组地理代价最低的天
        free = [k for k in range(days) if not day_blocks[k]]
        pool = free or cands
        k = min(pool, key=lambda kk: day_cost(kk, g[0]["r"]))
        for sp in g:
            if len(day_blocks[k]) >= per_day:
                break
            put(k, sp["r"], sp["it"])

    # 执行配额：为片区挑地理上最合适的天
    for r in ranked:                      # 密度高者先挑
        for _ in range(quota.get(r["group"], 0)):
            walked = scheduled_names()
            # 找出"该片区尚未入列的、地理上最贴合的天"
            pool = [it for it in r["items"] if it["name"] not in walked]
            if not pool:
                break
            # 优先填已有本片区点的天，其次填空天
            same = [k for k in range(days)
                    if r["group"] in day_regions[k] and len(day_blocks[k]) < per_day]
            free = [k for k in range(days) if not day_blocks[k]]
            cands = same or free
            if not cands:
                cands = [k for k in range(days) if len(day_blocks[k]) < per_day]
            # 优先满足单日跨度约束
            ok = [kk for kk in cands if span_ok(kk, r)]
            if ok:
                cands = ok
            if not cands:
                break
            k = min(cands, key=lambda kk: day_cost(kk, r))
            for it in pool[:per_day]:
                if len(day_blocks[k]) >= per_day:
                    break
                put(k, r, it)
            assigned.add(r["group"])

    # 阶段 B：剩余容量填充
    #   按"全局分数"降序处理，让高分点优先补位；
    #   每天受 per_day 上限 + 单日跨度约束双重限制，装不下的进备选
    pool = []
    for r in ranked:
        for it in r["items"]:
            pool.append((it, r))
    pool.sort(key=lambda x: -x[0]["_score"])

    for it, r in pool:
        if it["name"] in scheduled_names():
            continue
        cands = [k for k in range(days) if len(day_blocks[k]) < per_day]
        if not cands:
            alternatives.append(it)
            continue
        ok = [kk for kk in cands if span_ok(kk, r)]
        if not ok:
            # 都无法满足跨度约束 → 进备选，不硬塞
            alternatives.append(it)
            continue
        k = min(ok, key=lambda kk: day_cost(kk, r))
        put(k, r, it)

    # 5) 空白天补齐（正常不会发生）
    for k in range(days):
        if not day_blocks[k]:
            day_regions[k] = []

    # 远郊名片并入"备选"，由报告层单列"一日游延伸选项"
    for dt in daytrips:
        alternatives.append(dt["it"])

    return [(day_regions[k], day_blocks[k]) for k in range(days)], alternatives


def plan_itinerary(places, days, start_date, preferences=None, grade_map=None,
                   coordinator=None, city=""):
    """规则引擎编排"""
    places = [normalize_place(p) for p in places]
    # 过滤：去掉交通枢纽与住宿，单独管理
    spots = [p for p in places if p.get("type") not in ("T", "A")]
    hotels = [p for p in places if p.get("type") == "A"]
    transport = [p for p in places if p.get("type") == "T"]

    # 去重：父子景点合并
    spots, dropped_children = dedupe_children(spots)

    # 打分排序
    for p in spots:
        p["_score"] = score_place(p, preferences, grade_map)
    spots.sort(key=lambda x: -x["_score"])

    buckets = cluster_by_region(spots)

    # 按区域总分配天（区域权重 = 高分地点数量）
    region_rank = sorted(
        buckets.items(),
        key=lambda kv: -sum(p["_score"] for p in kv[1])
    )

    # 把地点分到 N 天
    # 策略：**按天容量 + 地理邻近填充**
    #   片区按"价值密度"排序（古城核心密度最高），逐点填进地理代价最低的未满天；
    #   填不下的自动转入"备选"，避免远郊低价值点挤掉古城核心
    day_blocks, spill = merge_regions_to_days(region_rank, days, per_day=PER_DAY, city=city)

    # 每天控制在 2-3 个主景点（超出的转入"备选"）
    alternatives = list(spill)
    final_days = []
    start = date.fromisoformat(start_date)
    for i, (group_names, bucket) in enumerate(day_blocks):
        bucket.sort(key=lambda x: -x["_score"])
        # 容量已在编排层（per_day + 跨度约束）控制，此处不再二次截断
        main = bucket
        d = start + timedelta(days=i)
        regions_in_day = sorted({p["_region"] for p in main if p.get("_region") != "未定位"})
        final_days.append({
            "day_index": i,
            "date": d.isoformat(),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()],
            "region": " + ".join(regions_in_day) if regions_in_day else "机动",
            "attractions": [{
                "name": p["name"],
                "type": p["type_label"],
                "duration_min": p["duration_min"],
                "ticket_price": p["ticket_price"],
                "reservation_required": p["reservation_required"],
                "reservation_tips": p["reservation_tips"],
                "region": p["_region"],
                "score": p["_score"],
                "note": p["note"][:200],
                # 带上坐标（GCJ-02），地图层 map_html.py 依赖 lng/lat。
                # 未收录坐标的景点置 None —— 它们本来也不会进主行程。
                "lng": p.get("_lon"),
                "lat": p.get("_lat"),
            } for p in main],
            "meals": _suggest_meals(regions_in_day),
            "total_duration_min": sum(p["duration_min"] for p in main),
        })

    return {
        "days": final_days,
        "alternatives": [p["name"] for p in alternatives[:10]],
        "transport": [p["name"] for p in transport],
        "hotels": [p["name"] for p in hotels],
        "coord_sys": "GCJ-02",
        "stats": {
            "total_places": len(places),
            "scheduled": sum(len(d["attractions"]) for d in final_days),
            "alternatives": len(alternatives),
            "merged_children": sorted(dropped_children),
        },
    }


MEAL_TEMPLATES = {
    "古城核心": ["咸亨酒店（鲁迅故里）", "寻宝记绍兴菜", "阿丘十碗头绍兴菜馆"],
    "古城北": ["迎恩门风情水街小吃", "府山横街本地菜"],
    "古城西": ["银泰城餐饮", "绍兴菜馆"],
    "城东": ["东湖景区周边农家菜"],
    "城南": ["大禹陵周边农家菜"],
    "兰亭片区": ["兰亭景区周边农家乐"],
    "柯桥片区": ["安昌古镇酱鸭腊肠", "柯桥本地菜馆"],
    "黄酒小镇": ["黄酒小镇黄酒体验馆"],
}


def _suggest_meals(regions):
    pool = []
    for r in regions:
        pool.extend(MEAL_TEMPLATES.get(r, []))
    if not pool:
        pool = ["绍兴本地菜馆"]
    return [
        {"type": "breakfast", "name": "酒店早餐 / 本地早点（豆浆油条、次坞打面）", "cost": 20},
        {"type": "lunch", "name": pool[0], "cost": 70},
        {"type": "dinner", "name": pool[1] if len(pool) > 1 else pool[0], "cost": 90},
    ]


# ============================================================
# 预算层
# ============================================================
def estimate_budget(itinerary, transport_roundtrip=132, hotel_per_night=400):
    """规则估算预算（门票按行程内实际加总）"""
    tickets = 0
    meals = 0
    for d in itinerary["days"]:
        for a in d["attractions"]:
            tickets += a["ticket_price"] or 0
        for m in d["meals"]:
            meals += m["cost"]
    nights = max(len(itinerary["days"]) - 1, 0)
    hotels = hotel_per_night * nights
    return {
        "total_attractions": tickets,
        "total_meals": meals,
        "total_hotels": hotels,
        "total_inter_city_transport": transport_roundtrip,
        "total_local_transport": 30 * len(itinerary["days"]),
        "total": tickets + meals + hotels + transport_roundtrip + 30 * len(itinerary["days"]),
        "_notes": {
            "hotel_per_night": hotel_per_night,
            "nights": nights,
            "transport": f"往返高铁约 {transport_roundtrip} 元（按二等座估算）",
        },
    }


# ============================================================
# CLI
# ============================================================
def main():
    p = argparse.ArgumentParser(description="规则引擎行程编排器")
    p.add_argument("--places", required=True, help="places.json 路径")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--preferences", default="", help="逗号分隔")
    p.add_argument("--out", required=True)
    p.add_argument("--hotel-per-night", type=int, default=400)
    p.add_argument("--transport", type=int, default=132)
    p.add_argument("--city", default="", help="目的地城市名（用于名片保底清单）")
    args = p.parse_args()

    places = json.load(open(args.places, encoding="utf-8"))
    prefs = [x.strip() for x in args.preferences.split(",") if x.strip()]

    itin = plan_itinerary(places, args.days, args.start, prefs, city=args.city)
    itin["budget"] = estimate_budget(itin, args.transport, args.hotel_per_night)
    itin["meta"] = {
        "days": args.days, "start": args.start,
        "preferences": prefs,
        "engine": "rule-based (no LLM required)",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(itin, open(args.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"编排完成：{args.days} 天 / {itin['stats']['scheduled']} 个景点", file=sys.stderr)
    for d in itin["days"]:
        names = " → ".join(a["name"] for a in d["attractions"])
        print(f"  D{d['day_index']+1} {d['date']} [{d['region']}] {names}", file=sys.stderr)
    b = itin["budget"]
    print(f"  预算估算: 门票{b['total_attractions']} + 餐饮{b['total_meals']} "
          f"+ 住宿{b['total_hotels']} + 交通{b['total_inter_city_transport']} "
          f"= {b['total']} 元", file=sys.stderr)


if __name__ == "__main__":
    main()
