#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补查询层（gapfill）—— 缺口驱动的二次检索

设计来源：借鉴 weekend-city-trip 的「质量检查 → 补查询触发 → 补查询执行」三段式，
但触发条件从"章节数量不足"改成"**情报方向缺失**"——因为行程规划需要的不是
更多条数，而是**几类平时搜不到、但会直接毁掉行程的信息**：

  1. 运维动向 —— 闭园 / 维修 / 施工 / 暂停开放（最容易白跑一趟）
  2. 预约限流 —— 国庆不预约进不去 / 名额放号时间
  3. 交通管制 —— 景区周边封路、停车饱和、单行线
  4. 新开动向 —— 新开景点 / 试运营项目（攻略站通常滞后半年）
  5. 季节限定 —— 花期 / 红叶 / 最佳观赏期（决定这趟值不值）
  6. 票价变动 —— 涨价 / 旺季价 / 免费开放政策
  7. 避坑提醒 —— 宰客 / 骗局 / 常见坑

默认包（research.py）覆盖的是"有哪些景点"这类**存量信息**；本模块补的是
"**这些景点最近还开着吗、最近有什么变化**"这类**增量信息**。

用法：
  python gapfill.py check  --graded ./ly/graded.json --city 福州
  python gapfill.py fill   --graded ./ly/graded.json --city 福州 --year 2026 --month 10
  python gapfill.py run    --graded ./ly/graded.json --city 福州 --year 2026 --month 10
        # run = check + fill（原地合并进 graded.json，并写出 gaps.json）

不依赖大模型；只调 anysearch。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from research import call_anysearch, parse_results, grade_all  # noqa: E402


# ============================================================
# 1. 缺口方向定义
# ============================================================
# silent_keywords: 命中这些词说明该方向**已有信息**（无需补）
# queries: 缺口时执行的补查询，支持 {city} {ym} {year} {month}
# when_any: 只有信源里出现这些词（如"海岛""航线"）才评估该方向；None = 总是评估
# min_hits: 低于该命中数视为缺口
GAP_PACKS = {
    "ops": {
        "label": "运维动向（闭园/维修/施工）",
        "why": "景区临时闭园或维修是最容易白跑一趟的原因，攻略站通常不更新",
        "hit_keywords": ["闭园", "维修", "施工", "暂停开放", "封闭", "改造", "整修",
                         "停业", "暂停营业", "恢复开放", "重新开放", "正常开放"],
        "queries": [
            "{city} 景区 闭园 维修 施工 暂停开放 {ym}",
            "{city} 景点 封闭 改造 恢复开放 最新通知 {year}",
        ],
        "min_hits": 2,
        "level": "required",
    },
    "booking": {
        "label": "预约与限流",
        "why": "法定假日不预约直接到现场会被劝返，放号时间决定你能不能抢到",
        "hit_keywords": ["预约", "限流", "实名", "名额", "放号", "提前预约",
                         "免预约", "分时预约", "抢票"],
        "queries": [
            "{city} 景区 预约 限流 名额 提前几天 {ym}",
            "{city} 国庆 门票 预约 放号 时间 攻略",
        ],
        "min_hits": 3,
        "level": "required",
    },
    "traffic": {
        "label": "交通管制与停车",
        "why": "自驾最怕的是到了山门口才发现封路 / 停车场全满，且管制公告只在节前发",
        "hit_keywords": ["管制", "停车", "限行", "单行", "封路", "摆渡", "接驳",
                         "禁停", "交通组织", "拥堵"],
        "queries": [
            "{city} 国庆 交通管制 景区 停车 自驾 {ym}",
            "{city} 景区 停车场 位置 收费 摆渡车 {year}",
        ],
        "min_hits": 2,
        "level": "suggest",
    },
    "newopen": {
        "label": "新开与试运营",
        "why": "新开景点/新项目半年内几乎没有攻略收录，但往往是这趟最大的惊喜",
        "hit_keywords": ["新开", "开业", "试运营", "全新", "首发", "刚刚开放",
                         "正式开放", "开街"],
        "queries": [
            "{city} 新开 景区 新景点 打卡 试运营 {year}",
            "{city} {ym} 全新 开放 打卡地 首发",
        ],
        "min_hits": 1,
        "level": "suggest",
    },
    "season": {
        "label": "季节限定与最佳观赏期",
        "why": "花期/红叶/季节景观决定同一景点这趟值不值，差两周就是两回事",
        "hit_keywords": ["花期", "最佳观赏", "限定", "当季", "盛开", "红叶",
                         "观赏期", "季节", "候鸟", "海萤", "蓝眼泪"],
        "queries": [
            "{city} {ym} 最佳观赏期 花期 季节 限定",
            "{city} 什么时候去最好 {month}月 季节 景观",
        ],
        "min_hits": 2,
        "level": "suggest",
    },
    "price": {
        "label": "票价变动与最新价格",
        "why": "攻略站票价常滞后 2-3 年，旺季价与淡季价差别很大",
        "hit_keywords": ["旺季", "淡季", "涨价", "调价", "价格调整", "免费开放",
                         "门票价格", "票价调整"],
        "queries": [
            "{city} 门票价格 最新 {year} 旺季 淡季 调整",
            "{city} 景区 免费开放 优惠政策 {year}",
        ],
        "min_hits": 2,
        "level": "optional",
    },
    "pitfall": {
        "label": "避坑与常见问题",
        "why": "宰客、假导游、拉客、山寨景区——这类信息只有 UGC 会说",
        "hit_keywords": ["避坑", "宰客", "骗", "套路", "坑人", "注意", "提醒",
                         "不建议", "别去"],
        "queries": [
            "{city} 旅游 避坑 注意 提醒 骗局",
            "site:xiaohongshu.com {city} 避坑 提醒 注意",
        ],
        "min_hits": 2,
        "level": "suggest",
    },
    "ferry": {
        "label": "船班与航線（海岛适用）",
        "why": "海岛行程的班次/停航受风浪影响，且常需提前订票",
        "when_any": ["海岛", "航線", "航线", "船班", "轮渡", "客滚", "码头", "渡轮", "离岛"],
        "hit_keywords": ["船班", "轮渡", "航線", "航线", "码头", "停航", "船票",
                         "客滚", "渡轮", "班次"],
        "queries": [
            "{city} 船班 轮渡 时刻表 船票 价格 {year}",
            "{city} 码头 停航 风浪 船票 预订",
        ],
        "min_hits": 2,
        "level": "suggest",
    },
}


# ============================================================
# 2. 工具
# ============================================================
def _norm_url(u):
    """URL 归一化，用于去重（去协议/去 www/去尾斜杠/去 query）"""
    if not u:
        return ""
    u = re.sub(r"^https?://", "", u.strip().lower())
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


def load_graded(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_graded(path, items):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def _blob(items):
    """把信源拼成一个大字符串用于关键词命中统计"""
    parts = []
    for x in items:
        parts.append(x.get("title", ""))
        parts.append(x.get("summary", ""))
    return "\n".join(parts)


# 与目的地无关的域名（政务/媒体通用频道容易靠"权威"拿到 A 级，但内容跑题）
NOISE_DOMAINS = {
    "cctv.com", "tv.cctv.com", "news.cctv.com",        # 综合新闻频道常混入时政
    "wuhan.gov.cn", "beijing.gov.cn", "shanghai.gov.cn",
    "chinatax.gov.cn", "kjt.fujian.gov.cn",
    "facebook.com",
}


def is_relevant(item, names):
    """相关性过滤：标题+摘要必须出现目的地名（或其别名）

    必要性：权威域名（政府站/央视/人民网）自动拿到 A 级，但它们的地方频道会
    混入邻市、甚至完全不相关的新闻。补查询最容易踩这个坑——query 里带地名，
    召回结果却可能是"XX省其他城市"的内容。
    """
    url = item.get("url", "")
    for nd in NOISE_DOMAINS:
        if nd in url:
            return False
    text = (item.get("title", "") or "") + " " + (item.get("summary", "") or "")
    return any(n and n in text for n in names)


def check_gaps(items, city):
    """扫描信源包，返回每个方向的缺口状态（不联网）"""
    blob = _blob(items)
    present_all = blob
    out = []
    for key, cfg in GAP_PACKS.items():
        # when_any 门控：只有信源里出现相关词（如"海岛"）才评估该方向
        gate = cfg.get("when_any")
        if gate and not any(w in present_all for w in gate):
            out.append({
                "key": key, "label": cfg["label"], "applicable": False,
                "hits": 0, "min_hits": cfg["min_hits"], "level": cfg["level"],
                "status": "skipped",
                "reason": "信源中未出现触发词（%s），本方向不适用" % "/".join(gate[:3]),
                "hit_detail": {},
            })
            continue
        detail = {w: blob.count(w) for w in cfg["hit_keywords"] if blob.count(w) > 0}
        hits = sum(detail.values())
        out.append({
            "key": key, "label": cfg["label"], "applicable": True,
            "hits": hits, "min_hits": cfg["min_hits"], "level": cfg["level"],
            "status": "gap" if hits < cfg["min_hits"] else "covered",
            "reason": ("命中 %d 条 < 阈值 %d，方向缺失" % (hits, cfg["min_hits"])
                       if hits < cfg["min_hits"] else "命中 %d 条，已覆盖" % hits),
            "hit_detail": detail,
        })
    return out


def fill_gaps(graded_path, city, year, month, levels=("required", "suggest", "optional"),
              max_queries=8, api_key="", out_dir=None, verbose=True, aliases=()):
    """执行补查询并原地合并进 graded.json"""
    items = load_graded(graded_path)
    ym = "%d年%d月" % (year, month)
    names = [city] + [a for a in aliases if a]

    before = len(items)
    gaps = check_gaps(items, city)

    todo = []
    for g in gaps:
        if not g["applicable"] or g["status"] != "gap":
            continue
        if g["level"] not in levels:
            g["status"] = "deferred"
            g["reason"] += "（优先级 %s，本轮未补）" % g["level"]
            continue
        for q in GAP_PACKS[g["key"]]["queries"]:
            todo.append((g["key"], q.format(city=city, ym=ym, year=year, month=month)))
    todo = todo[:max_queries]

    seen = {_norm_url(x.get("url", "")) for x in items}
    added_total = 0
    dropped_total = 0
    exec_log = []

    for gkey, q in todo:
        if verbose:
            print("  · 补查 [%s] %s" % (gkey, q), file=sys.stderr)
        try:
            r = call_anysearch(q, 8, api_key)
            text = r.get("text", "")
            if not text:
                exec_log.append({"gap": gkey, "query": q, "new": 0, "error": r.get("error", "")})
                continue
            fresh = parse_results(text)
            graded = []
            for it in fresh:
                it["query"] = q
                graded.extend(grade_all([it]))
            n = 0
            for it in graded:
                nu = _norm_url(it.get("url", ""))
                if nu and nu in seen:
                    continue
                if not is_relevant(it, names):     # 相关性闸门，挡掉跨城噪声
                    dropped_total += 1
                    continue
                seen.add(nu)
                it["source"] = "gapfill"
                it["gap_key"] = gkey
                items.append(it)
                n += 1
                added_total += 1
            exec_log.append({"gap": gkey, "query": q, "new": n})
        except Exception as e:  # 网络异常不应中断整轮
            exec_log.append({"gap": gkey, "query": q, "new": 0, "error": str(e)})
        time.sleep(1.2)   # 温和节流，避免触发 QPS 限制

    # 复检：补完后再看一遍，哪些方向已被解除
    after = check_gaps(items, city)
    for a, b in zip(gaps, after):
        if a["status"] == "gap" and b["status"] == "covered":
            b["status"] = "filled"
            b["reason"] = "补查后命中 %d 条，缺口已解除" % b["hits"]
        elif a["status"] == "gap":
            b["status"] = "still_gap"
            b["reason"] = "补查后命中 %d 条，仍低于阈值 %d —— 该方向信息有限" % (b["hits"], b["min_hits"])

    save_graded(graded_path, items)

    report = {
        "city": city, "ym": ym,
        "before": before, "after": len(items), "added": added_total,
        "dropped_irrelevant": dropped_total,
        "queries_run": len(exec_log),
        "gaps": after, "log": exec_log,
    }
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "gaps.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def print_report(rep):
    print("=" * 62)
    print("补查询报告 —— %s  %s" % (rep["city"], rep["ym"]))
    print("=" * 62)
    print("信源：%d → %d 条（新增 %d%s）；执行补查询 %d 次\n"
          % (rep["before"], rep["after"], rep["added"],
             "，过滤无关 %d" % rep["dropped_irrelevant"] if rep.get("dropped_irrelevant") else "",
             rep["queries_run"]))
    icon = {"covered": "✓", "filled": "＋", "still_gap": "！", "gap": "✗",
            "skipped": "－", "deferred": "…"}
    for g in rep["gaps"]:
        print("  %s [%s] %-22s %s"
              % (icon.get(g["status"], "?"), g["level"][:3], g["label"], g["reason"]))
    # 注意：check 模式下缺口状态是 "gap"（尚未补），fill 后是 "still_gap"（补过但仍缺）
    need = [g for g in rep["gaps"]
            if g["status"] in ("gap", "still_gap") and g["level"] == "required"]
    if need:
        if rep["queries_run"] == 0:
            print("\n  ⚠ 必补方向缺失（尚未补查）：%s" % "、".join(g["label"] for g in need))
        else:
            print("\n  ⚠ 必补方向补查后仍未解除：%s" % "、".join(g["label"] for g in need))
            print("     → 报告中需写「信息有限，出行前请致电景区确认」。")
    else:
        print("\n  所有必补方向已解除。")


# ============================================================
# 3. CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="行程信源缺口补查询")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("check", "fill", "run", "prune"):
        s = sub.add_parser(name)
        s.add_argument("--graded", required=True, help="graded.json 路径（原地更新）")
        s.add_argument("--city", required=True)
        s.add_argument("--alias", action="append", default=[],
                       help="目的地别名（相关性过滤用，可多次，如 平潭 的别名写 福州）")
        s.add_argument("--year", type=int, default=None)
        s.add_argument("--month", type=int, default=None)
        s.add_argument("--out", default=None, help="gaps.json 输出目录")
        if name in ("fill", "run"):
            s.add_argument("--levels", default="required,suggest,optional",
                           help="本轮补哪些优先级，逗号分隔")
            s.add_argument("--max-queries", type=int, default=8)

    args = ap.parse_args()

    import datetime
    now = datetime.date.today()
    year = args.year or now.year
    month = args.month or now.month
    api_key = os.environ.get("ANYSEARCH_API_KEY", "")

    if args.cmd == "prune":
        # 用相关性规则清理已入库的 gapfill 条目（补查询先跑、过滤后加时的补救手段）
        items = load_graded(args.graded)
        names = [args.city] + [a for a in args.alias if a]
        keep, drop = [], []
        for x in items:
            if x.get("source") == "gapfill" and not is_relevant(x, names):
                drop.append(x)
            else:
                keep.append(x)
        save_graded(args.graded, keep)
        print("清理无关补查询条目：%d 条（%d → %d）" % (len(drop), len(items), len(keep)))
        for x in drop[:15]:
            print("  - %s | %s" % (x.get("title", "")[:56], (x.get("url") or "")[:60]))
        if len(drop) > 15:
            print("  … 另有 %d 条" % (len(drop) - 15))
        return

    if args.cmd == "check":
        items = load_graded(args.graded)
        gaps = check_gaps(items, args.city)
        rep = {"city": args.city, "ym": "%d年%d月" % (year, month),
               "before": len(items), "after": len(items), "added": 0,
               "dropped_irrelevant": 0,
               "queries_run": 0, "gaps": gaps, "log": []}
        print_report(rep)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            with open(os.path.join(args.out, "gaps.json"), "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
        return

    levels = tuple(x.strip() for x in args.levels.split(",") if x.strip())
    if args.cmd == "fill":
        rep = fill_gaps(args.graded, args.city, year, month, levels=levels,
                        max_queries=args.max_queries, api_key=api_key, out_dir=args.out,
                        aliases=args.alias)
        print_report(rep)
        return

    # run = check（先看一遍）→ fill → 报告
    items = load_graded(args.graded)
    pre = check_gaps(items, args.city)
    print("--- 补查询前 ---")
    for g in pre:
        print("  %s %-22s %s" % ("✗" if g["status"] == "gap" else "✓", g["label"], g["reason"]))
    print()
    rep = fill_gaps(args.graded, args.city, year, month, levels=levels,
                    max_queries=args.max_queries, api_key=api_key, out_dir=args.out,
                    aliases=args.alias)
    print_report(rep)


if __name__ == "__main__":
    main()
