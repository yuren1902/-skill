# -*- coding: utf-8 -*-
"""拥挤度 / 错峰建议 定向检索

先生要求：更多获取小红书、大众点评、携程、飞猪、去哪儿、游侠客等公开信源的
**最新**信息，综合研判小长假/暑期的**景点拥挤程度与错峰建议**。

与 research.py 的区别：
  - research.py 走"城市维度的 17 组通用查询"，用来建景点池
  - 本脚本走"**景点维度的定向查询**"，专门挖拥挤度/排队/错峰/避雷

用法：
  python crowd_research.py --spots 善卷洞,宜兴竹海,窑湖小镇 --out ./crowd
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# 让 `from research import ...` 在本机/别的电脑/任意安装路径下都能工作
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from research import call_anysearch, parse_results, grade_all  # noqa: E402

# ============================================================
# 平台清单（先生点名的公开信源）
# ============================================================
PLATFORMS = {
    "xiaohongshu": "小红书",
    "dianping": "大众点评",
    "ctrip": "携程",
    "fliggy": "飞猪",
    "qunar": "去哪儿",
    "youxiake": "游侠客",
}

# site: 定向 —— 不点名平台，anysearch 不会主动返回这些站点
SITE_MAP = {
    "xiaohongshu": "site:xiaohongshu.com",
    "dianping": "site:dianping.com",
    "ctrip": "site:ctrip.com",
    "fliggy": "site:fliggy.com",
    "qunar": "site:qunar.com",
    "youxiake": "site:youxiake.com",
}

# 拥挤度维度的查询意图（先生要求：拥挤程度 + 错峰建议）
CROWD_INTENTS = [
    "{spot} 人多吗 排队 多久",
    "{spot} 几点去人少 错峰 最佳时间",
    "{spot} 国庆 堵 停车 攻略",
]

# 平台维度（每平台 2 条，避免查询爆炸）
PLATFORM_INTENTS = [
    "{site} {spot} 攻略 排队",
    "{site} {spot} 人多 避雷",
]


# ============================================================
# 相关性闸门（2026-09-17 新增）
# ------------------------------------------------------------
# 实测教训：不设闸门时，"于山 人多吗" 会召回张家界/莫高窟/九寨沟的
# 通用排队攻略，而且因为它们来自新浪/知乎/搜狐这类"权威"域名，
# 反被标成 A 级 —— 于山 31 句里 29 句是外地景区。
#
# 闸门规则（两条都要过）：
#   1. 不得命中异地地名黑名单
#   2. 必须命中「该景点名」或「城市名/别名」
# ============================================================
NOISE_DOMAINS = {
    "cctv.com", "tv.cctv.com", "news.cctv.com", "facebook.com",
    "chinatax.gov.cn", "wuhan.gov.cn",
}

# 异地地名黑名单：这些词一出现，基本可以判定不是本地攻略
OTHER_PLACES = [
    "张家界", "莫高窟", "九寨沟", "庐山", "喀纳斯", "涠洲岛", "恒山", "悬空寺",
    "敦煌", "凤凰古城", "丽江", "三亚", "青岛", "大连", "黄山", "泰山", "华山",
    "峨眉", "稻城", "雪乡", "长白山", "青海湖", "茶卡", "张掖", "额济纳",
    "呼伦贝尔", "武当山", "神农架", "三峡", "张家口", "承德", "北戴河",
    "中山", "江门", "郑州", "北海", "桂林", "阳朔", "洛阳", "开封", "西安",
    "成都", "重庆", "杭州", "苏州", "南京", "武汉", "长沙", "厦门", "泉州",
    "南昌", "合肥", "济南", "太原", "沈阳", "哈尔滨", "昆明", "贵阳",
    "深圳", "广州", "珠海", "汕头", "佛山", "东莞", "宁波", "温州", "绍兴",
    "洛阳", "平遥", "大同", "秦皇岛", "烟台", "威海", "日照", "徐州",
    "太鲁阁", "垦丁", "阿里山", "日月潭",
]

# 城市别名（用于判定"是不是在讲这个地方"）
CITY_HINTS = [
    "福州", "榕城", "平潭", "海坛", "永泰", "福建", "闽",
    "鼓楼区", "台江区", "仓山区", "晋安区", "马尾", "长乐", "福清", "闽侯",
]


def is_relevant(item, spots, city_hints=None):
    """相关性闸门

    顺序很重要：**先看白名单，再看黑名单**。
    坑：黑名单里有「烟台」（山东），而福州有「烟台山」——
    先判黑名单会把「烟台山」整片误杀。白名单优先即可避免。
    """
    url = item.get("url", "") or ""
    for nd in NOISE_DOMAINS:
        if nd in url:
            return False
    text = (item.get("title", "") or "") + " " + (item.get("summary", "") or "")
    # ① 白名单优先：命中本行程任一景点名 → 直接通过
    if any(s and s in text for s in spots):
        return True
    # ② 异地地名黑名单（此时文本里没有本地景点名，命中即判为外地）
    if any(p in text for p in OTHER_PLACES):
        return False
    # ③ 退而求其次：含城市名/别名也算沾边
    return any(c in text for c in (city_hints or CITY_HINTS))


def build_spot_queries(spots, platforms=None, include_site=True, city=""):
    """构造景点维度的定向查询

    city 非空时给每条 query 加地名前缀。**强烈建议填**：
    实测「三坊七巷 人多吗 排队 多久」这种不带地名的 query，
    会召回大量外省通用排队攻略（张家界/莫高窟/九寨沟…），
    噪声率高达 61%；加上「福州」后方向明显收窄。
    """
    platforms = platforms or list(PLATFORMS.keys())
    prefix = (city.strip() + " ") if city.strip() else ""
    out = []
    for s in spots:
        for t in CROWD_INTENTS:
            out.append((prefix + t.replace("{spot}", s), "crowd", ""))
        if include_site:
            for p in platforms:
                site = SITE_MAP.get(p)
                if not site:
                    continue
                for t in PLATFORM_INTENTS:
                    out.append((prefix + t.replace("{site}", site).replace("{spot}", s),
                                "platform", p))
    return out


def _grade_and_dump(results, out_dir, spots=None, city_hints=None):
    """把 raw 结果分级落盘，返回统计信息。

    spots/city_hints 非空时启用相关性闸门，被丢弃的条目另存
    graded_dropped.json 供事后复核（避免误杀真源）。
    """
    from collections import Counter
    raw_items = []
    for k in sorted(results):
        v = results[k]
        if not v.get("text"):
            continue
        for it in parse_results(v["text"]):
            it["query_key"] = k
            it["query"] = v["query"]
            it["kind"] = v["kind"]
            it["platform"] = v["platform"]
            it["spot"] = v["spot"]
            raw_items.append(it)

    # 先分级、再过滤 —— 这样被丢弃的条目也带 grade，
    # 便于评估闸门是否误杀（尤其是 U 级）
    graded_raw = grade_all(raw_items)
    all_items, dropped = [], []
    for it in graded_raw:
        if spots and not is_relevant(it, spots, city_hints):
            dropped.append(it)
        else:
            all_items.append(it)

    graded = all_items
    c = Counter(x["grade"] for x in graded)
    dc = Counter(x["grade"] for x in dropped)
    gp = Counter(x.get("platform") or "—" for x in graded
                 if x["grade"] in ("A", "U"))
    gp = {k: v for k, v in gp.items() if k}
    out = os.path.join(out_dir, "graded.json")
    json.dump(graded, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    if dropped:
        dpath = os.path.join(out_dir, "graded_dropped.json")
        json.dump(dropped, open(dpath, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    tot = len(graded_raw)
    if dropped:
        print(f"原始 {tot} 条 | 闸门丢弃 {len(dropped)} 条（{100.0 * len(dropped) / max(1, tot):.0f}%："
              f"A={dc['A']} U={dc.get('U', 0)} C={dc['C']} D={dc['D']}）"
              f" | 保留 {len(graded)}：A={c['A']} U={c.get('U', 0)} "
              f"C={c['C']} D={c['D']} → {out}", file=sys.stderr)
    else:
        print(f"原始 {tot} 条 | A={c['A']} U={c.get('U', 0)} "
              f"C={c['C']} D={c['D']} → {out}", file=sys.stderr)
    if gp:
        print("A/U 级平台分布：" + ", ".join(f"{k}={v}" for k, v in gp.items()),
              file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="拥挤度/错峰定向检索")
    ap.add_argument("--spots", required=False, default="",
                    help="逗号分隔的景点名（regrade 模式下可省略，自动从 raw.json 提取）")
    ap.add_argument("--platforms", default=",".join(PLATFORMS.keys()),
                    help="逗号分隔的平台 key")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-results", type=int, default=8)
    ap.add_argument("--api-key", default=os.environ.get("ANYSEARCH_API_KEY", ""))
    ap.add_argument("--no-site", action="store_true", help="跳过 site: 定向")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发线程数（默认 4；1 = 串行）")
    ap.add_argument("--resume", action="store_true",
                    help="续跑：读 out/raw.json 跳过已完成的 query")
    ap.add_argument("--only-crowd", action="store_true",
                    help="只跑 3 条拥挤度通用意图，跳过 6 平台 site: 定向")
    ap.add_argument("--city", default="",
                    help="逗号分隔的城市名/别名，辅助相关性闸门判定")
    ap.add_argument("--query-city", default="",
                    help="给每条 query 加的地名前缀（如「福州」）。"
                         "强烈建议填：不带地名的 query 噪声率可达 61%%")
    ap.add_argument("--no-gate", action="store_true",
                    help="关闭相关性闸门（默认开启；仅在排查误杀时用）")
    ap.add_argument("--regrade", action="store_true",
                    help="不联网：读 out/raw.json 重新分级"
                         "（改了闸门规则后用它重算，不重复消耗检索额度）")
    args = ap.parse_args()

    spots = [s.strip() for s in args.spots.split(",") if s.strip()]
    plats = [p.strip() for p in args.platforms.split(",") if p.strip()]
    hints = CITY_HINTS + [x.strip() for x in args.city.split(",") if x.strip()]
    queries = build_spot_queries(spots, plats,
                                 include_site=not (args.no_site or args.only_crowd),
                                 city=args.query_city)

    os.makedirs(args.out, exist_ok=True)
    raw_path = os.path.join(args.out, "raw.json")

    gate_spots = None if args.no_gate else spots

    # ---- 只重算分级，不联网 ----------------------------------------
    if args.regrade:
        if not os.path.exists(raw_path):
            print(f"! {raw_path} 不存在，无法 regrade", file=sys.stderr)
            sys.exit(1)
        results = json.load(open(raw_path, encoding="utf-8"))
        if not spots:      # 从 raw.json 反推景点清单
            seen = []
            for v in results.values():
                s = v.get("spot")
                if s and s not in seen:
                    seen.append(s)
            spots = seen
        print(f"regrade：读入 {len(results)} 组已有结果，"
              f"{len(spots)} 个景点，不发起检索", file=sys.stderr)
        gate_spots = None if args.no_gate else spots
        _grade_and_dump(results, args.out, gate_spots, hints)
        return

    if not spots:
        print("! 非 regrade 模式必须提供 --spots", file=sys.stderr)
        sys.exit(1)

    # ---- 续跑：合并已有结果 ----------------------------------------
    results = {}
    if args.resume and os.path.exists(raw_path):
        try:
            results = json.load(open(raw_path, encoding="utf-8"))
        except Exception as e:
            print(f"  ! 读 {raw_path} 失败（{e}），从零开始", file=sys.stderr)
            results = {}

    jobs = [(f"q{i:03d}", q, kind, plat)
            for i, (q, kind, plat) in enumerate(queries, 1)
            if f"q{i:03d}" not in results]

    print(f"共 {len(queries)} 组查询（{len(spots)} 景点 × 拥挤度维度 + 平台定向）"
          f"｜已完成 {len(results)}｜本次待跑 {len(jobs)}｜并发 {args.workers}",
          file=sys.stderr)
    if not jobs:
        print("全部已完成，直接分级。", file=sys.stderr)
        _grade_and_dump(results, args.out, gate_spots, hints)
        return

    lock = threading.Lock()
    done = [0]

    def work(job):
        key, q, kind, plat = job
        r = call_anysearch(q, args.max_results, args.api_key)
        if "error" in r:
            return key, {"error": r["error"], "query": q}
        return key, {"query": q, "text": r["text"], "kind": kind,
                     "platform": plat,
                     "spot": next((s for s in spots if s in q), "")}

    # ---- 逐条即时落盘：中途中断不丢已完成的部分 ----------------------
    def flush():
        tmp = raw_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        os.replace(tmp, raw_path)

    if args.workers <= 1:
        it = ((k, q, kk, p) for k, q, kk, p in jobs)
        for n, job in enumerate(it, 1):
            print(f"  [{n}/{len(jobs)}] {job[1]}", file=sys.stderr)
            key, payload = work(job)
            if "error" in payload:
                print(f"      ! {payload['error'][:80]}", file=sys.stderr)
            else:
                results[key] = payload
                flush()
            time.sleep(0.3)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(work, j): j for j in jobs}
            for fu in as_completed(futs):
                key, payload = fu.result()
                with lock:
                    done[0] += 1
                    if "error" in payload:
                        print(f"  [{done[0]}/{len(jobs)}] ! {key} "
                              f"{payload['error'][:60]}", file=sys.stderr)
                    else:
                        results[key] = payload
                        flush()
                        if done[0] % 10 == 0 or done[0] == len(jobs):
                            print(f"  [{done[0]}/{len(jobs)}] 已落盘 "
                                  f"{len(results)} 组", file=sys.stderr)

    ok = sum(1 for v in results.values() if v.get("text"))
    print(f"\n完成：{ok}/{len(queries)} 组有结果 → {raw_path}", file=sys.stderr)
    _grade_and_dump(results, args.out, gate_spots, hints)


if __name__ == "__main__":
    main()
