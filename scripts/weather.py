#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天气层（weather）—— 旅行期间逐日天气 + 装备建议 + 行程影响提示

数据源：**Open-Meteo**（https://open-meteo.com）
  · 免费、**免 API Key**、全球覆盖（含中国境内与境外）
  · forecast 接口 —— 未来 16 天逐日预报
  · archive 接口 —— 历史再分析数据，用于算「气候平均」（超出 16 天窗口时）

为什么要两个接口：行程通常在出发前 1-3 个月规划，那时没有预报。
所以本脚本的策略是 **双轨**：
  · 出发日在 16 天窗口内 → 给**真实预报**（标注「预报」）
  · 超出窗口 → 取**过往 N 年同期**求平均，给**气候平均**（标注「气候平均·非预报」）
两者都会算，然后由 `--mode auto` 决定以哪个为准。

输出：weather.json
  {
    "mode": "forecast" | "normal",
    "days": [{date, weekday, tmax, tmin, precip, precip_prob, wind, uv, code, text},
             ...],
    "advice": {clothing:[], gear:[], risk:[], plan_hint:[]},
    "points": [{name, lng, lat}]
  }

用法：
  # 指定日期与地点（经纬度用 GCJ-02/WGS-84 均可，天气格点精度远大于坐标系差异）
  python weather.py --start 2026-10-02 --end 2026-10-05 \
      --point 福州:119.303,26.075 --point 平潭:119.790,25.503 \
      --out ./weather.json

  # 直接读行程文件自动取每天动线重心
  python weather.py --itinerary ./itinerary.json --out ./weather.json
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum", "precipitation_probability_max",
    "weather_code", "wind_speed_10m_max", "uv_index_max",
]

# WMO Weather interpretation codes → 中文
WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}
WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ============================================================
# 1. 取数
# ============================================================
def _get(url, params, timeout=25):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + qs, headers={"User-Agent": "trip-plan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_forecast(lat, lng, start, end):
    """未来 16 天逐日预报；超出窗口 Open-Meteo 会返回错误 → 返回 None"""
    try:
        d = _get(FORECAST_API, {
            "latitude": lat, "longitude": lng,
            "daily": ",".join(DAILY_VARS),
            "timezone": "auto",
            "start_date": start, "end_date": end,
        })
    except Exception:
        return None
    daily = d.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        return None
    return times, daily


def fetch_normals(lat, lng, start, end, years=5):
    """过往 N 年同「月-日」窗口的历史数据 → 逐日求平均（气候平均）"""
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    span = (e - s).days
    buckets = [[] for _ in range(span + 1)]
    this_year = dt.date.today().year
    used = 0
    for back in range(1, years + 1):
        y = this_year - back
        try:
            ss = s.replace(year=y).isoformat()
            ee = (s.replace(year=y) + dt.timedelta(days=span)).isoformat()
        except ValueError:      # 2/29
            continue
        try:
            d = _get(ARCHIVE_API, {
                "latitude": lat, "longitude": lng,
                "daily": "temperature_2m_max,temperature_2m_min,"
                         "precipitation_sum,wind_speed_10m_max",
                "timezone": "auto",
                "start_date": ss, "end_date": ee,
            })
        except Exception:
            continue
        daily = d.get("daily") or {}
        t = daily.get("time") or []
        if len(t) != span + 1:
            continue
        used += 1
        for i in range(span + 1):
            buckets[i].append({
                "tmax": daily["temperature_2m_max"][i],
                "tmin": daily["temperature_2m_min"][i],
                "precip": daily["precipitation_sum"][i],
                "wind": daily["wind_speed_10m_max"][i],
            })
    if not used:
        return None
    days = []
    for i in range(span + 1):
        bs = [b for b in buckets[i] if b["tmax"] is not None]
        if not bs:
            continue
        n = len(bs)
        days.append({
            "date": (s + dt.timedelta(days=i)).isoformat(),
            "tmax": round(sum(b["tmax"] for b in bs) / n, 1),
            "tmin": round(sum(b["tmin"] for b in bs) / n, 1),
            "precip": round(sum(b["precip"] or 0 for b in bs) / n, 1),
            "wind": round(sum(b["wind"] or 0 for b in bs) / n, 1),
            # 气候平均取"这 N 年里下过雨"的比例作为下雨概率的代理
            "precip_prob": round(100.0 * sum(1 for b in bs if (b["precip"] or 0) >= 1) / n),
            "code": None,
        })
    return {"days": days, "years_used": used}


# ============================================================
# 2. 多地点取数（同一行程跨片区时，逐日取该日所在片区的点）
# ============================================================
def _beauvfort(kmh):
    """km/h → 风力等级（近似）"""
    b = [1, 5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117]
    for i, v in enumerate(b):
        if kmh < v:
            return i
    return 12


def collect(points, start, end, force_mode=None):
    """取逐日天气。

    三种模式：
      forecast —— 整段都在 Open-Meteo 预报窗口（今日 ~ +15 天）内
      mixed    —— 只有前若干天在窗口内，其余用气候平均补齐（**逐日标注来源，不混淆**）
      normal   —— 整段都超出窗口，全部用过往 N 年同期平均
    """
    name, lng, lat = points[0]
    today = dt.date.today()
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    horizon = (s - today).days          # 出发日距今天数

    fc = None
    if force_mode != "normal" and horizon <= 15:
        # 预报接口只覆盖到 today+15，超出的部分裁掉，别让它整块报错
        limit = today + dt.timedelta(days=15)
        fend = min(e, limit)
        if s <= limit:
            fc = fetch_forecast(lat, lng, max(s, today).isoformat(), fend.isoformat())

    nrm = None
    need_normal = force_mode == "normal" or fc is None
    if fc is not None and e > today + dt.timedelta(days=15):
        need_normal = True                 # 有部分天数预报覆盖不到
    if need_normal:
        nrm = fetch_normals(lat, lng, start, end)

    # 先铺气候平均（作为底），再用真实预报覆盖
    days_by_date = {}
    if nrm:
        for d in nrm["days"]:
            d["uv"] = None
            d["text"] = None
            d["src"] = "norm"
            days_by_date[d["date"]] = d

    fc_n = 0
    if fc:
        times, daily = fc
        for i, t in enumerate(times):
            code = daily["weather_code"][i]
            days_by_date[t] = {
                "date": t,
                "tmax": daily["temperature_2m_max"][i],
                "tmin": daily["temperature_2m_min"][i],
                "precip": daily["precipitation_sum"][i],
                "precip_prob": daily["precipitation_probability_max"][i],
                "wind": daily["wind_speed_10m_max"][i],
                "uv": daily["uv_index_max"][i],
                "code": code,
                "text": WMO.get(code, "—"),
                "src": "forecast",
            }
            fc_n += 1

    days = [days_by_date[k] for k in sorted(days_by_date)]
    if not days:
        return {"mode": "unavailable", "days": [], "advice": {},
                "point": {"name": name, "lng": lng, "lat": lat}}

    if fc_n == 0:
        mode = "normal"
    elif fc_n == len(days):
        mode = "forecast"
    else:
        mode = "mixed"

    src = []
    if fc_n:
        src.append("Open-Meteo forecast（%d 天）" % fc_n)
    if nrm:
        src.append("气候平均（过往 %d 年同期）" % nrm["years_used"])
    return {"mode": mode, "source": " + ".join(src),
            "point": {"name": name, "lng": lng, "lat": lat}, "days": days}


# ============================================================
# 3. 装备与行程影响建议
# ============================================================
def advise(days, city=""):
    """由逐日天气推导穿衣/装备/风险/行程提示"""
    clothing, gear, risk, plan_hint = [], [], [], []
    if not days:
        return {"clothing": clothing, "gear": gear, "risk": risk, "plan_hint": plan_hint}

    tmax = [d["tmax"] for d in days if d.get("tmax") is not None]
    tmin = [d["tmin"] for d in days if d.get("tmin") is not None]
    if not tmax:
        return {"clothing": clothing, "gear": gear, "risk": risk, "plan_hint": plan_hint}
    hi, lo = max(tmax), min(tmin)
    dtr = max(a - b for a, b in zip(tmax, tmin)) if tmax and tmin else 0

    # ---- 穿衣 ----
    if hi >= 33:
        clothing.append("白天 **%d~%d℃**，盛夏体感，**短袖 + 速干**，走山径建议备一件替换衣物。" % (hi, hi))
    elif hi >= 28:
        clothing.append("白天 **%d℃ 上下**，夏装为主：短袖 / 薄长裤；室内空调与海边风大，**带一件薄外套**。" % hi)
    elif hi >= 22:
        clothing.append("白天 **%d℃ 左右**，**短袖 + 薄外套** 最稳，早晚加一件不亏。" % hi)
    elif hi >= 15:
        clothing.append("白天 **%d℃ 左右**，**长袖 + 针织/夹克**，怕冷再备一件内搭。" % hi)
    else:
        clothing.append("白天 **%d℃ 以下**，需**保暖外套 / 抓绒**，山区再冷一档。" % hi)
    if dtr >= 9:
        clothing.append("**昼夜温差达 %.0f℃**（最低 %d℃），是这趟最容易穿错的地方 —— 早上出门按最低温穿，中午脱。" % (dtr, lo))

    # ---- 装备 ----
    wet = [d for d in days if (d.get("precip_prob") or 0) >= 50 or (d.get("precip") or 0) >= 5]
    if wet:
        probmax = max(int(d.get("precip_prob") or 0) for d in wet)
        amtmax = max(float(d.get("precip") or 0) for d in wet)
        how = []
        if probmax >= 30:
            how.append("降水概率最高 %d%%" % probmax)
        if amtmax >= 1:
            how.append("单日雨量最大 %.1fmm" % amtmax)
        gear.append("**带伞**：%s 有降水%s。建议 **折叠伞 + 一次性雨衣** 组合，"
                    "登山/海边风大时伞会翻，雨衣更实用。"
                    % ("、".join(d["date"][5:] for d in wet),
                       ("（%s）" % "，".join(how)) if how else ""))
    else:
        gear.append("**降水概率低**，可只带一把折叠伞应急，把背包空间留给别的。")
    if hi >= 28:
        uv = [d["uv"] for d in days if d.get("uv") is not None]
        if uv and max(uv) >= 7:
            gear.append("**防晒必带**：紫外线指数最高 %d（很强），防晒霜 SPF50+ / 帽子 / 墨镜。" % round(max(uv)))
        else:
            gear.append("**防晒必带**：日照强，帽子 + 墨镜 + 防晒霜。")
    wind = [d.get("wind") or 0 for d in days]
    if wind and max(wind) >= 39:
        gear.append("**防风**：最大风速 %.0f km/h（%d 级风），海边/山顶体感会更冷，带防风外套。"
                    % (max(wind), _beauvfort(max(wind))))

    # ---- 风险 ----
    m = days[0]["date"][5:7]
    if city and any(k in city for k in ("平潭", "厦门", "泉州", "漳州", "海南", "三亚", "北海", "舟山", "宁波", "温州", "台州", "潮州", "汕头")):
        if m in ("07", "08", "09", "10"):
            risk.append("**沿海台风季（7-10 月）**：出发前 3 天起每天看一次台风路径；"
                        "一旦发布台风预警，海岛/跨海船班可能全线停航，需准备 **Plan B 内陆行程**。")
    if max(wind or [0]) >= 50:
        risk.append("**大风预警风险**：风速较大，索道 / 缆车 / 游船常因风停运，安排上不要把它们当唯一亮点。")
    if hi >= 35:
        risk.append("**高温**：正午 11:00-15:00 尽量避免长时间户外暴晒，老人小孩尤需注意补水。")
    if lo <= 5:
        risk.append("**低温**：山区夜间可能接近冰点，若住山上需确认供暖。")

    # ---- 行程影响 ----
    heavy = [d for d in days if (d.get("precip") or 0) >= 10 or (d.get("precip_prob") or 0) >= 70]
    if heavy:
        plan_hint.append("**%s 大概率有雨** → 这天主推 **室内线**（博物馆 / 古厝 / 展览 / 老街区），"
                         "把登山与海岛留给天气最好的那天，随时可对调。"
                         % "、".join(d["date"][5:] for d in heavy))
    if wet:
        plan_hint.append("带雨的日子 **优先安排有屋檐的动线**（骑楼、古厝、商业街），"
                         "并把需要排队的户外网红点挪到雨停的时段。")
    if not wet:
        plan_hint.append("整段天气对户外友好，**登山 / 海岛 / 观景台可以放心排**，"
                         "但仍建议把最想去的户外点放在天气最好的那天。")
    if m in ("10", "11"):
        plan_hint.append("**10-11 月日落早**（约 17:30 前后），傍晚行程别排太满，"
                         "看日落要提前 40 分钟到位。")
    return {"clothing": clothing, "gear": gear, "risk": risk, "plan_hint": plan_hint}


# ============================================================
# 4. 行程文件 → 日期与地点
# ============================================================
def from_itinerary(path):
    """从 itinerary.json 推导日期区间与代表点（取每日动线重心）"""
    with open(path, "r", encoding="utf-8") as f:
        it = json.load(f)
    days = it.get("days", [])
    dates = [d.get("date") for d in days if d.get("date")]
    pts = []
    for d in days:
        at = d.get("attractions") or []
        lngs = [a["lng"] for a in at if a.get("lng")]
        lats = [a["lat"] for a in at if a.get("lat")]
        if lngs and lats:
            pts.append((d.get("label") or d.get("date"),
                        round(sum(lngs) / len(lngs), 4),
                        round(sum(lats) / len(lats), 4)))
    return dates, pts


# ============================================================
# 5. CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="旅行期间天气与装备建议")
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    ap.add_argument("--point", action="append", default=[],
                    help="地点，格式 名称:经度,纬度（可多次）")
    ap.add_argument("--itinerary", help="从 itinerary.json 自动推导日期与代表点")
    ap.add_argument("--city", default="", help="目的地名（用于沿海/台风判断）")
    ap.add_argument("--mode", default="auto", choices=["auto", "forecast", "normal"])
    ap.add_argument("--out", default="")
    ap.add_argument("--years", type=int, default=5, help="气候平均回溯年数")
    args = ap.parse_args()

    if args.itinerary:
        dates, pts = from_itinerary(args.itinerary)
        if dates:
            args.start = args.start or dates[0]
            args.end = args.end or dates[-1]
        if pts and not args.point:
            args.point = ["%s:%.4f,%.4f" % (n, lng, lat) for n, lng, lat in pts]
    if not (args.start and args.end):
        ap.error("需要 --start/--end，或用 --itinerary 推导")
    if not args.point:
        ap.error("需要至少一个 --point，或用 --itinerary 推导")

    points = []
    for p in args.point:
        nm, co = p.split(":")
        lng, lat = [float(x) for x in co.split(",")]
        points.append((nm, lng, lat))

    res = collect(points, args.start, args.end,
                  force_mode=None if args.mode == "auto" else args.mode)
    res["advice"] = advise(res.get("days", []), args.city or points[0][0])
    res["points"] = [{"name": n, "lng": lng, "lat": lat} for n, lng, lat in points]
    res["range"] = [args.start, args.end]

    # 逐日补星期
    for d in res.get("days", []):
        try:
            d["weekday"] = WD[dt.date.fromisoformat(d["date"]).weekday()]
        except Exception:
            d["weekday"] = ""

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)

    tag = {"forecast": "预报", "mixed": "预报 + 气候平均",
           "normal": "气候平均（非预报）",
           "unavailable": "取数失败"}.get(res["mode"], res["mode"])
    print("天气（%s）· %s ~ %s · %s [%s]"
          % (tag, args.start, args.end, res["point"]["name"], res.get("source", "")))
    for d in res.get("days", []):
        prob = d.get("precip_prob")
        srcmark = "≈" if d.get("src") == "norm" else " "
        print(" %s%s  %s  %s~%s℃  降水%.1fmm%s  风%.0fkm/h  %s"
              % (srcmark, d["date"], d.get("weekday", ""),
                 _f(d.get("tmin")), _f(d.get("tmax")),
                 float(d.get("precip") or 0),
                 ("(%d%%)" % prob) if prob is not None else "",
                 float(d.get("wind") or 0),
                 d.get("text") or ""))
    if any(d.get("src") == "norm" for d in res.get("days", [])):
        print("  （标 ≈ 的行为气候平均推算，非当日预报）")
    for k, label in [("clothing", "穿衣"), ("gear", "装备"), ("risk", "风险"), ("plan_hint", "行程影响")]:
        for line in res["advice"].get(k, []):
            print("  [%s] %s" % (label, line))
    return res


def _f(v):
    return "—" if v is None else ("%.0f" % v)


if __name__ == "__main__":
    main()
