# -*- coding: utf-8 -*-
"""
research.py — 检索层：anysearch 调用 + 信源分级 + 地点归一化

设计原则：
  1. 零第三方依赖（仅标准库 urllib）
  2. 零 LLM 依赖（规则引擎驱动，模型仅作可选增强）
  3. 可独立运行

用法：
  python research.py search --city 绍兴 --out ./data
  python research.py grade  --raw ./data --out ./data
  python research.py places --graded ./data/graded.json --out ./data
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

ENDPOINT = "https://api.anysearch.com/mcp"
CLIENT_HEADER = "skill/2.1.0"
# 缓存路径可用环境变量覆盖；默认放用户目录
KEY_CACHE_FILE = os.environ.get(
    "ANYSEARCH_KEY_FILE",
    os.path.join(os.path.expanduser("~"), ".anysearch_key"))


# ============================================================
# 1. 检索层
# ============================================================
KEY_RE = re.compile(r"api_key=([A-Za-z0-9_\-]+)")

# 进程内缓存：即使磁盘不可写（沙箱/只读环境），同一次运行也只签发一次
_MEM_KEY = {"key": ""}


def _load_cached_key():
    """读取已缓存的 api_key：环境变量 > 进程内存 > 磁盘"""
    env = os.environ.get("ANYSEARCH_API_KEY", "")
    if env:
        return env
    if _MEM_KEY["key"]:
        return _MEM_KEY["key"]
    try:
        with open(KEY_CACHE_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                _MEM_KEY["key"] = k
            return k
    except Exception:
        return ""


def _save_cached_key(key):
    """缓存 api_key：先存内存（必然成功），再尽力落盘（失败不报错）"""
    _MEM_KEY["key"] = key
    try:
        with open(KEY_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(key)
        os.chmod(KEY_CACHE_FILE, 0o600)
    except Exception:
        pass   # 只读文件系统等场景静默降级，内存缓存已生效


def _post_anysearch(query, max_results, api_key, timeout):
    """发一次请求，返回 (结果字典, 是否提示签发Key)"""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {"query": query, "max_results": max_results},
        },
    }
    headers = {"Content-Type": "application/json", "X-Anysearch-Client": CLIENT_HEADER}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}, False
    except Exception as e:
        return {"error": str(e)}, False
    if "error" in data:
        return {"error": data["error"].get("message", str(data["error"]))}, False
    for item in data.get("result", {}).get("content", []):
        if item.get("type") == "text":
            text = item["text"]
            # 关键：匿名额度耗尽时，服务端返回的是"自动签发 Key"提示而非搜索结果
            m = KEY_RE.search(text)
            if m and "###" not in text:
                return {"_issued_key": m.group(1), "text": text}, True
            return {"text": text}, False
    return {"text": ""}, False


def call_anysearch(query, max_results=8, api_key="", timeout=40, _retry=True):
    """调用 anysearch，自动处理"首次返回签发 Key"的场景。

    实测：匿名额度耗尽时，服务端不返回搜索结果，而是返回一段
    "Your account and API key have been automatically generated... api_key=as_sk_xxx"。
    本函数自动提取该 Key、缓存到 ~/.anysearch_key 并用它重试一次。

    api_key 优先级：显式传入 > 环境变量 > 进程内存缓存 > 磁盘缓存 > 匿名
    """
    key = api_key or _load_cached_key()
    r, issued = _post_anysearch(query, max_results, key, timeout)
    if issued and _retry:
        new_key = r["_issued_key"]
        _save_cached_key(new_key)
        print(f"      · 服务端签发新 API Key（已缓存），自动重试", file=sys.stderr)
        r2, _ = _post_anysearch(query, max_results, new_key, timeout)
        return r2
    return r


def parse_results(text):
    """把 anysearch 文本响应解析为结构化条目"""
    items = []
    blocks = re.split(r"\n###\s+\d+\.\s*", text)
    for b in blocks[1:]:
        lines = [x.strip() for x in b.strip().split("\n") if x.strip()]
        if not lines:
            continue
        title, url, summary, date, rating = lines[0], "", "", "", ""
        for ln in lines:
            m = re.match(r"-\s*\*\*URL\*\*:\s*(\S+)", ln)
            if m:
                url = m.group(1)
                continue
            m = re.match(r"-\s*date:\s*(.+)", ln)
            if m:
                date = m.group(1).strip()
                continue
            m = re.match(r"-\s*rating:\s*([\d.]+)", ln)
            if m:
                rating = m.group(1)
                continue
            m = re.match(r"-\s*(.+)", ln)
            if m and not summary:
                summary = m.group(1)
        items.append({"title": title, "url": url, "summary": summary,
                      "date": date, "rating": rating})
    return items


# ============================================================
# 2. 信源分级层（防幻觉核心）
# ============================================================
GEO_NOISE = [
    "discoverhongkong", "taichung", "ntua.edu.tw", "kcginfo", "gov.tw",
    "hktb.com", "nmtl.gov.tw", "th.trip.com", "partnernet",
]
OTA_DOMAINS = [
    "trip.com", "ctrip.com", "qunar.com", "kayak", "expedia", "booking.com",
    "agoda", "tripadvisor", "wingontravel", "eztravel", "skyscanner",
    "railmonsters", "hk.", "tw.", "sg.", "my.", "au.", "cn.tripadvisor",
]
# UGC 平台：真实用户实拍/探店，信息鲜活但无官方背书、易过期、可能含商单
# 单独成一档 U（介于 A 与 C 之间），便于报告区分"谁说的"
UGC_DOMAINS = [
    "xiaohongshu.com", "rednote.com", "xhslink.com", "dianping.com",
    "meituan.com", "m.dianping.com", "bilibili.com", "douyin.com",
]
# UGC 中明显不是"具体目的地笔记"的形态：首页/个人主页/搜索页/话题页
# 注意：/explore/<24位id> 是真实笔记页，不能一刀切；只有裸 /explore 才是泛页
UGC_GENERIC_PATTERNS = [
    "/user/profile/", "/search_result", "/topic/", "/page/",
]
UGC_GENERIC_RE = [
    r"xiaohongshu\.com/(explore|discovery)(\?|/?$)",    # 裸列表页/带query列表页
    r"rednote\.com/(explore|discovery)(\?|/?$)",        # 同上（国际域名）
    r"xiaohongshu\.com/?$",                             # 首页
    r"rednote\.com/?$",
    r"xiaohongshu\.com/(explore|discovery)/item/?$",    # item 无 id
]
AUTHORITY = [
    "zj.news.cn", "xinhuanet", "zjol.com.cn", "bendibao", "chinadaily",
    "cnr.cn", "baike.baidu", "amap.com", "163.com", "qq.com", "sohu.com",
    "zhihu.com", "people.com.cn", "sina", "cctv", "12306",
]
STALE_YEARS = ["2010", "2011", "2017", "2018", "2019", "2020", "2021"]


def grade_source(item):
    """信源分级：A 权威 / U 用户实拍 / C 待核 / D 噪声"""
    url = (item.get("url") or "").lower()
    title = (item.get("title") or "")
    if any(k in url for k in GEO_NOISE):
        return "D", "地域噪声：非目标城市活动"
    if any(k in url for k in OTA_DOMAINS):
        return "C", "境外OTA/繁体站，可用但需交叉验证"
    if any(k in url for k in UGC_DOMAINS):
        # 首页/个人主页/话题页不是一条具体的攻略，降级为 C
        if any(p in url for p in UGC_GENERIC_PATTERNS) or \
           any(re.search(p, url) for p in UGC_GENERIC_RE):
            return "C", "UGC泛页（非具体笔记），仅作线索"
        if "no information is available" in title.lower() or title.startswith("http"):
            return "C", "UGC空标题页，摘要缺失，不可采信"
        return "U", "用户实拍/探店，鲜活但需与官方交叉验证"
    if "gov.cn" in url:
        return "A", "官方政府来源"
    if any(k in url for k in AUTHORITY):
        return "A", "境内权威媒体/百科/本地宝/高德"
    d = item.get("date") or ""
    if any(y in d for y in STALE_YEARS):
        return "C", "内容可能过时，需核对最新信息"
    return "C", "待人工复核"


def grade_all(items):
    out = []
    for it in items:
        g, reason = grade_source(it)
        it = dict(it)
        it["grade"] = g
        it["grade_reason"] = reason
        out.append(it)
    return out


# ============================================================
# 3. 地点归一化层
# ============================================================
TYPE_MAP = {
    "C": "演出场馆", "S": "体育场馆", "M": "农旅/市集", "U": "博物馆/美术馆",
    "5": "景区", "H": "茶饮门店", "F": "美食/古街", "W": "city walk",
    "L": "购物中心", "D": "地铁/交通", "T": "交通枢纽", "A": "住宿",
}

# 关键词 → 类型编码（规则引擎）
TYPE_KEYWORDS = [
    (["地铁", "轨道交通", "高铁站", "火车站", "客运中心", "公交站"], "T"),
    (["博物馆", "美术馆", "纪念馆", "故居", "故居", "书院", "学校", "中学"], "U"),
    (["古镇", "古街", "老街", "直街", "历史街区", "步行街", "美食街", "小吃街"], "F"),
    (["剧院", "大剧院", "音乐厅", "体育馆", "演唱会场馆", "文化中心"], "C"),
    (["体育中心", "球场", "赛场"], "S"),
    (["市集", "集市", "采摘", "农场", "产业园", "果园", "农旅"], "M"),
    (["酒店", "民宿", "饭店", "客栈", "旅馆"], "A"),
    (["商场", "购物中心", "百货", "广场", "银泰", "万达"], "L"),
    (["街", "路", "巷", "city walk", "citywalk", "游线"], "W"),
    (["景区", "风景区", "公园", "古镇", "塔", "寺", "庙", "山", "湖",
      "江", "园", "陵", "岩", "洞", "溪", "瀑"], "5"),
]

# 门票关键词
FREE_HINT = ["免费", "免门票", "无需门票", "免费开放", "0元"]
TICKET_RE = re.compile(r"(\d+)\s*元")


def classify_type(name, note=""):
    """按关键词规则判类型"""
    blob = f"{name} {note}"
    for kws, code in TYPE_KEYWORDS:
        for kw in kws:
            if kw in blob:
                return code
    return "5"


def extract_ticket(note):
    """从文本提取门票（0 = 免费）"""
    if any(h in note for h in FREE_HINT):
        return 0
    m = TICKET_RE.search(note)
    if m:
        v = int(m.group(1))
        if 0 < v < 2000:
            return v
    return None


def extract_duration(note, name=""):
    """提取建议游玩时长（分钟）"""
    m = re.search(r"(\d+)\s*[-~到]\s*(\d+)\s*小时", note)
    if m:
        return int((int(m.group(1)) + int(m.group(2))) / 2 * 60)
    m = re.search(r"建议游玩\s*(\d+)\s*[-~]?\s*(\d+)?\s*小时", note)
    if m:
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        return int((a + b) / 2 * 60)
    m = re.search(r"(\d+)\s*小时", note)
    if m:
        return int(m.group(1)) * 60
    if any(k in f"{name}{note}" for k in ["博物馆", "美术馆", "纪念馆", "故居"]):
        return 90
    if any(k in f"{name}{note}" for k in ["山", "徒步", "环线"]):
        return 180
    return 90


def detect_reservation(note):
    """识别是否需要预约"""
    need = any(k in note for k in ["预约", "提前订", "限流", "需订"])
    tips = ""
    if need:
        m = re.search(r"(需[^。；\n]{0,40}预约[^。；\n]{0,30})", note)
        if m:
            tips = m.group(1).strip()
        else:
            tips = "建议提前预约"
    return need, tips


def build_place(name, note, src_file, address=""):
    t = classify_type(name, note)
    need, tips = detect_reservation(note)
    return {
        "name": name,
        "type": t,
        "type_label": TYPE_MAP.get(t, "其他"),
        "address": address,
        "note": note[:400],
        "ticket_price": extract_ticket(note),
        "duration_min": extract_duration(note, name),
        "reservation_required": need,
        "reservation_tips": tips,
        "src_file": src_file,
    }


# ============================================================
# CLI
# ============================================================
QUERY_PACKS = {
    "default": [
        "{start}到{city} 高铁 交通 时间 票价",
        "{city} 国庆 活动 展览 演出 市集 {ym}",
        "{city} 古城 景点 必去 推荐",
        "{city} 美食 特色 推荐 本地人",
        "{city} 住宿 民宿 酒店 推荐 {ym}",
        "{city} 景区 古镇 门票 攻略",
        "{city} city walk 路线 攻略",
        "{city} 优惠门票 特惠 景区",
        "{city} 博物馆 美术馆 展览 {ym}",
        "{city} 地铁 公交 站点 出行 攻略",
    ],
    # UGC 定向包：anysearch 默认不会主动给小红书，必须点名 + 用 site: 语法
    # 实测结论（2026-09-15）：不带 site: 时小红书命中率≈0；带 site: 后可命中真实笔记页
    "ugc": [
        "site:xiaohongshu.com {city} 攻略",
        "site:xiaohongshu.com {city} 美食",
        "{city} 小红书 攻略 笔记",
        "{city} 小红书 探店 实拍",
    ],
}


def build_queries(city, origin="上海", year=None, month=None, include_ugc=True):
    """按城市/出发地展开查询模板，返回查询词列表

    include_ugc=True 时追加 UGC 定向包（小红书/点评等）。
    实测：不点名小红书，anysearch 几乎不会返回小红书结果。
    """
    import datetime
    now = datetime.date.today()
    year = year or now.year
    month = month or now.month
    ym = f"{year}年{month}月"
    out = []
    packs = ["default"] + (["ugc"] if include_ugc else [])
    for pname in packs:
        for qt in QUERY_PACKS[pname]:
            try:
                out.append(qt.format(city=city, start=origin, ym=ym,
                                     year=year, month=month))
            except KeyError:
                out.append(qt.format(city=city, start=origin, ym=ym))
    # 追加目的地专属查询
    out.extend([
        f"{city} 必去景点 排名 5A 4A",
        f"{city} 景点 门票 价格 预约",
        f"{city} 周边 古镇 一日游",
    ])
    return out


def search_and_grade(query, max_results=8, api_key=""):
    """执行一次检索并分级，返回带 grade 的结果列表（流水线用）"""
    r = call_anysearch(query, max_results, api_key)
    if "error" in r:
        raise RuntimeError(r["error"])
    items = parse_results(r["text"])
    for it in items:
        it["query"] = query
    return grade_all(items)


def cmd_search(args):
    os.makedirs(args.out, exist_ok=True)
    queries = build_queries(args.city, args.start or "上海",
                            int(args.year), int(args.month),
                            include_ugc=not getattr(args, "no_ugc", False))
    results = {}
    for i, q in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] {q}", file=sys.stderr)
        r = call_anysearch(q, args.max_results, args.api_key)
        if "error" in r:
            print(f"      ! {r['error'][:80]}", file=sys.stderr)
            continue
        results[f"q{i:02d}"] = {"query": q, "text": r["text"]}
        time.sleep(0.3)
    out = os.path.join(args.out, "raw.json")
    json.dump(results, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"已保存 {len(results)} 组检索结果 → {out}", file=sys.stderr)


def cmd_grade(args):
    raw = json.load(open(os.path.join(args.raw, "raw.json"), encoding="utf-8"))
    all_items = []
    for key, v in raw.items():
        for it in parse_results(v["text"]):
            it["query_key"] = key
            it["query"] = v["query"]
            all_items.append(it)
    graded = grade_all(all_items)
    from collections import Counter
    c = Counter(x["grade"] for x in graded)
    out = os.path.join(args.out, "graded.json")
    json.dump(graded, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"原始 {len(all_items)} 条 | A={c['A']} C={c['C']} D={c['D']} → {out}",
          file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="检索层：anysearch + 信源分级 + 归一化")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="执行检索")
    s.add_argument("--city", required=True)
    s.add_argument("--start", default="上海", help="出发城市")
    s.add_argument("--year", default="2026")
    s.add_argument("--month", default="10")
    s.add_argument("--out", required=True)
    s.add_argument("--max-results", type=int, default=8)
    s.add_argument("--api-key", default=os.environ.get("ANYSEARCH_API_KEY", ""))
    s.add_argument("--no-ugc", action="store_true",
                   help="跳过 UGC 定向包（小红书/点评），默认包含")
    s.set_defaults(func=cmd_search)

    g = sub.add_parser("grade", help="信源分级")
    g.add_argument("--raw", required=True)
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_grade)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
