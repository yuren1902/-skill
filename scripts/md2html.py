# -*- coding: utf-8 -*-
"""Markdown → 单文件 HTML（模块化版面，内嵌样式，可直接双击打开 / 分享）

版面形态
--------
不使用"一页到底"的长文档流，而是**多模块分区 + 左侧目录导航**：
  - 报告用 `<!--MODULE:名称:图标:简介-->` 切分模块
  - 桌面：目录是**左侧粘性竖排侧栏**，点击切换模块
  - 手机/窄屏(≤820px)：目录收起为顶部「☰ 目录」折叠条，展开后竖排列表
  - 每个模块是独立卡片区块，模块内标题层级从 h3 起（h2 让位给模块名）

支持 `<details>` 折叠（含 h4 + 表格）。
"""
import argparse
import html as _html
import sys
import os
import re

try:
    import markdown
    _MD_ERR = None
except ImportError as _e:                              # pragma: no cover
    markdown = None
    _MD_ERR = _e

MD_HINT = (
    "缺少依赖：markdown\n"
    "  本 skill 只有这一个脚本需要第三方包，其余全部只用标准库。\n"
    "  安装（任选其一）：\n"
    "    pip install markdown\n"
    "    python -m pip install markdown\n"
    "    pip install -r requirements.txt\n"
)


def require_markdown():
    """需要 markdown 的功能入口都先调它。
    用 import 方式调用时抛 ImportError（调用方可降级）；
    直接命令行运行时给出可执行的安装提示。"""
    if markdown is None:
        raise ImportError(MD_HINT)

LAYOUT = "tabs"          # "tabs" = 左侧目录切换(窄屏折叠为☰)；"flow" = 卡片流式排列

CSS = """
:root{
  --bg:#f4f8fc; --card:#ffffff; --ink:#1b2733; --ink2:#4a5a6a; --ink3:#8698a8;
  --line:#dfe9f3; --accent:#3d8bfd; --accent-soft:#eaf3ff; --accent2:#5bb8d6;
  --teal:#2f9e8f; --warn:#e8a33d; --shadow:0 1px 3px rgba(31,74,120,.07),
  0 10px 28px rgba(31,74,120,.06); --radius:12px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
    "Microsoft YaHei","Source Han Sans SC",sans-serif;
  font-size:15px; line-height:1.78; -webkit-font-smoothing:antialiased;
}

/* ---------- 页头 ---------- */
.hero{
  background:linear-gradient(135deg,#5ba7f7 0%,#3d8bfd 48%,#2f7ae0 100%);
  color:#fff; padding:36px 26px 28px;
}
.hero-in{max-width:1080px; margin:0 auto}
.hero h1{
  font-size:27px; margin:0 0 8px; letter-spacing:-.3px; line-height:1.34;
  border:0; padding:0; text-shadow:0 1px 3px rgba(20,60,120,.18);
}
.hero .sub{opacity:.94; font-size:14px; line-height:1.75; margin:0}
.hero .sub b{color:#fff3c4; font-weight:600}

/* ---------- 布局：桌面左栏(目录) + 右内容 ---------- */
.layout{
  max-width:1180px; margin:0 auto; padding:22px 18px 70px;
  display:grid; grid-template-columns:238px minmax(0,1fr); gap:26px;
  align-items:start;
}
/* ---------- 导航（桌面：左侧竖排侧栏） ---------- */
.nav{
  position:sticky; top:22px; z-index:50;
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:9px;
  max-height:calc(100vh - 44px); overflow-y:auto;
}
.navtoggle{display:none}
.nav-in{display:flex; flex-direction:column; gap:2px}
.nav-in button{
  display:flex; align-items:center; gap:9px; width:100%; text-align:left;
  border:0; background:none; cursor:pointer;
  font:inherit; font-size:13.5px; color:var(--ink2);
  padding:10px 12px; border-radius:8px;
  white-space:nowrap; transition:color .15s,background .15s;
}
.nav-in button:hover{color:var(--accent); background:var(--accent-soft)}
.nav-in button.on{color:var(--accent); background:var(--accent-soft); font-weight:650}
.nav-in button .ic{opacity:.85; margin:0; font-size:14px}

/* ---------- 模块 ---------- */
main{padding:0; min-width:0}
.mod{display:none}
.mod.on{display:block; animation:fade .22s ease}
@keyframes fade{from{opacity:0; transform:translateY(6px)} to{opacity:1; transform:none}}
.modhead{
  display:flex; align-items:baseline; gap:10px; margin:6px 0 16px;
  padding-bottom:12px; border-bottom:2px solid var(--accent);
}
.modhead h2{font-size:21px; margin:0; letter-spacing:-.2px; border:0; padding:0;
  color:var(--ink)}
.modhead .desc{color:var(--ink3); font-size:13px; margin-left:auto; text-align:right}

.card{
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  padding:20px 22px 22px; box-shadow:var(--shadow); margin-bottom:16px;
}

h3{
  font-size:17px; margin:26px 0 12px; color:var(--accent);
  padding-bottom:7px; border-bottom:1px dashed var(--line);
}
h3:first-child{margin-top:0}
h4{
  font-size:15px; margin:20px 0 9px; padding-left:10px;
  border-left:3px solid var(--accent2); font-weight:650;
}
blockquote{
  margin:15px 0; padding:13px 17px; background:var(--accent-soft);
  border-left:4px solid var(--accent); border-radius:0 8px 8px 0;
  color:var(--ink2); font-size:14px;
}
blockquote p{margin:4px 0}
p{margin:11px 0}
strong{color:var(--ink); font-weight:650}
a{color:#2f7ae0; text-decoration:none; border-bottom:1px solid rgba(61,139,253,.3)}
ul,ol{padding-left:23px; margin:11px 0}
li{margin:5px 0}
hr{border:0; border-top:1px solid var(--line); margin:26px 0}
code{
  background:var(--accent-soft); padding:2px 6px; border-radius:4px;
  font-size:13.5px; font-family:"SF Mono",Menlo,Consolas,monospace; color:#2f7ae0;
}
em{color:var(--ink3); font-style:normal; font-size:13.5px}

/* ---------- 表格 ---------- */
.tw{overflow-x:auto; margin:15px 0; border-radius:10px;
  box-shadow:0 1px 3px rgba(31,74,120,.06); border:1px solid var(--line)}
table{width:100%; border-collapse:collapse; font-size:13.8px; background:var(--card)}
th{
  background:var(--accent-soft); font-weight:650; text-align:left; font-size:13px;
  padding:11px 13px; border-bottom:2px solid #d3e6fb; white-space:nowrap;
  color:#2a6dc9;
}
td{padding:10px 13px; border-bottom:1px solid #eef4fa; vertical-align:top}
tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:#fafcff}
tbody tr:hover{background:#f2f8ff}

/* ---------- 折叠 ---------- */
details{
  margin:14px 0; border:1px solid var(--line); border-radius:10px;
  background:#fbfdff; overflow:hidden;
}
details>summary{
  cursor:pointer; padding:12px 16px; font-weight:650; font-size:14px;
  background:#eef5fd; list-style:none; user-select:none;
  display:flex; align-items:center; gap:8px; color:#2a6dc9;
}
details>summary::-webkit-details-marker{display:none}
details>summary::before{
  content:"▸"; display:inline-block; transition:transform .18s ease;
  color:var(--accent); font-size:13px; width:12px;
}
details[open]>summary::before{transform:rotate(90deg)}
details[open]>summary{border-bottom:1px solid var(--line)}
details>h4{
  font-size:15px; margin:20px 16px 8px; padding-left:10px;
  border-left:3px solid var(--accent2); font-weight:650;
}
details>h4:first-of-type{margin-top:16px}
details>table{margin:8px 16px 16px; width:calc(100% - 32px); box-shadow:none;
  border:1px solid var(--line)}
details>p{margin:8px 16px; font-size:14px; color:var(--ink2)}
details>ul{margin:6px 0; padding:8px 22px 14px 34px}
details>ul li{margin:4px 0; font-size:14px}

/* ---------- 内嵌地图 ---------- */
.mapwrap{
  border:1px solid var(--line); border-radius:12px; overflow:hidden;
  box-shadow:var(--shadow); margin:16px 0;
}
.mapwrap .mapbar-top{
  display:flex; align-items:center; gap:9px; padding:11px 15px;
  background:var(--accent-soft); border-bottom:1px solid #d9e8fa;
  font-size:13.5px; color:#2a6dc9; font-weight:650;
}
.mapwrap .mapfoot{
  padding:10px 15px; background:#fbfdff; border-top:1px solid var(--line);
  font-size:12px; color:var(--ink3); line-height:1.75;
}

@keyframes fade{}
@media (max-width:900px){
  .layout{grid-template-columns:206px minmax(0,1fr); gap:20px}
}
/* 手机/窄屏：左栏收起为顶部可折叠的「目录」条 */
@media (max-width:820px){
  .layout{display:block; padding:14px 12px 50px}
  .nav{
    position:sticky; top:0; margin:0 -12px 14px; padding:0;
    border:0; border-radius:0; border-bottom:1px solid var(--line);
    box-shadow:0 2px 10px rgba(31,74,120,.06);
    background:rgba(255,255,255,.96); backdrop-filter:blur(10px);
    max-height:none; overflow:visible;
  }
  .navtoggle{
    display:flex; align-items:center; gap:9px; width:100%;
    border:0; background:none; cursor:pointer; font:inherit;
    font-size:14px; font-weight:650; color:var(--accent);
    padding:13px 16px;
  }
  .navtoggle .cur{
    color:var(--ink2); font-weight:400; margin-left:auto;
    max-width:54%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .nav-in{
    display:none; flex-direction:column; gap:0;
    padding:6px 8px 10px; border-top:1px solid var(--line);
    max-height:60vh; overflow-y:auto;
  }
  .nav.open .nav-in{display:flex}
  .nav-in button{width:100%; padding:11px 13px; border-radius:8px}
}
@media (max-width:640px){
  .hero{padding:24px 16px 20px} .hero h1{font-size:21px}
  .card{padding:15px 15px 17px}
  .modhead h2{font-size:18px} .modhead .desc{display:none}
  table{font-size:12.6px} th,td{padding:8px 9px}
  body{font-size:14.3px}
}
@media print{
  .nav{display:none} .layout{display:block; max-width:none; padding:0}
  .mod{display:block !important} .card{box-shadow:none}
  body{background:#fff} .hero{background:#3d8bfd !important;
    -webkit-print-color-adjust:exact}
}
"""

# 腾讯地图交互版专属样式：只在 --map-mode=tmap 时注入。
# 默认走 SVG（零外部依赖），若把这套 CSS 一并写进 HTML，会让人误以为
# 页面里还有一张需要联网的腾讯地图 —— 所以拆出来按需拼接。
CSS_TMAP = """
.mapwrap #tmapEmbed{height:620px; width:100%; background:#eef5fd}
@media (max-width:820px){
  .mapwrap #tmapEmbed{height:420px}
}
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header class="hero"><div class="hero-in">
  <h1>{h1}</h1>
  {sub}
</div></header>
<div class="layout">
<nav class="nav" id="navwrap">
  <button class="navtoggle" id="navtoggle" aria-label="展开目录"><span>☰</span><span>目录</span><span class="cur" id="navcur"></span></button>
  <div class="nav-in" id="nav"></div>
</nav>
<main id="main"></main>
</div>
<script>
const MODULES = {modules};
const nav = document.getElementById("nav");
const main = document.getElementById("main");

// 通过 innerHTML 注入的脚本节点不会执行；这里用 DOMParser 解析后
// 手动重建 script 节点，使其按插入顺序执行（地图 SDK 初始化依赖此机制）。
function injectHTML(target, html) {{
  const doc = new DOMParser().parseFromString(
    '<div id="__root__">' + html + '</div>', 'text/html');
  const root = doc.getElementById('__root__');
  const scripts = [];
  // 先取出 script（保留文档顺序），再从 DOM 中移除
  root.querySelectorAll('script').forEach(s => {{
    scripts.push(s);
    s.remove();
  }});
  while (root.firstChild) target.appendChild(root.firstChild);
  scripts.forEach(old => {{
    const s = document.createElement('script');
    for (const a of old.attributes) s.setAttribute(a.name, a.value);
    s.textContent = old.textContent;
    // src 型脚本：等待加载完成再放下一个，保证顺序（代理配置必须先于 SDK）
    if (s.src) {{
      s.async = false;
      target.appendChild(s);
    }} else {{
      target.appendChild(s);
    }}
  }});
}}

// 地图 SDK 就绪后触发重算视野（地图模块可能是异步加载的）
window.__waitMap__ = function (cb) {{
  let n = 0;
  (function poll() {{
    if (typeof TMap !== 'undefined' && typeof window.__rebuildMap__ === 'function') return cb();
    if (++n > 100) return;              // 约 10s 后放弃
    setTimeout(poll, 100);
  }})();
}};

MODULES.forEach((m, i) => {{
  const b = document.createElement("button");
  b.innerHTML = '<span class="ic">' + m.icon + '</span>' + m.name;
  b.dataset.i = i;
  b.className = i === 0 ? "on" : "";
  b.addEventListener("click", () => show(i));
  nav.appendChild(b);

  const d = document.createElement("section");
  d.className = "mod" + (i === 0 ? " on" : "");
  const head = document.createElement("div");
  head.className = "modhead";
  head.innerHTML = '<h2>' + m.name + '</h2>' +
    (m.desc ? '<span class="desc">' + m.desc + '</span>' : '');
  const card = document.createElement("div");
  card.className = "card";
  d.appendChild(head);
  d.appendChild(card);
  injectHTML(card, m.body);
  main.appendChild(d);
}});

// 窄屏目录折叠：点「☰ 目录」条展开/收起；当前模块名显示在条上
const navtoggle = document.getElementById("navtoggle");
if (navtoggle) {{
  navtoggle.addEventListener("click", () => {{
    document.getElementById("navwrap").classList.toggle("open");
  }});
}}
const navcur = document.getElementById("navcur");
if (navcur && MODULES[0]) navcur.textContent = MODULES[0].name;

function show(i) {{
  document.querySelectorAll(".nav-in button").forEach((b, j) =>
    b.classList.toggle("on", i === j));
  document.querySelectorAll(".mod").forEach((m, j) =>
    m.classList.toggle("on", i === j));
  const wrap = document.getElementById("navwrap");
  if (wrap) wrap.classList.remove("open");
  const cur = document.getElementById("navcur");
  if (cur && MODULES[i]) cur.textContent = MODULES[i].name;
  window.scrollTo({{ top: 0, behavior: "smooth" }});
  // 地图模块初始 display:none，容器尺寸为 0，显示后需重算视野
  if (typeof window.__rebuildMap__ === "function") {{
    setTimeout(window.__rebuildMap__, 80);
  }}
}}

// 首屏若默认模块含地图，延迟重算一次视野
window.addEventListener("load", function () {{
  if (typeof window.__rebuildMap__ === "function") {{
    setTimeout(window.__rebuildMap__, 200);
  }}
}});
</script>
</body>
</html>
"""

MOD_RE = re.compile(r"<!--\s*MODULE:([^:]+):([^:]*):([^>]*?)-->")


def _extract_details(text):
    """把 <details>...</details> 块先摘出来（否则 Python-Markdown 会转义标签，
    或因块内空行被打断），转成占位符，最后回填为真正的 HTML。"""
    blocks = []

    def repl(m):
        inner = m.group(1)
        body = markdown.markdown(inner.strip(), extensions=["tables", "sane_lists"])
        blocks.append(body)
        return "\n\n@@DETAILS_%d@@\n\n" % (len(blocks) - 1)

    text = re.sub(r"<details>(.*?)</details>", repl, text, flags=re.S)
    return text, blocks


def _md2html(text, blocks):
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )
    for i, blk in enumerate(blocks):
        blk = re.sub(r"^<p>(.*?)</p>", r"<summary>\1</summary>", blk, count=1, flags=re.S)
        html_block = "<details>%s</details>" % blk
        body = body.replace("<p>@@DETAILS_%d@@" % i, html_block).replace(
            "@@DETAILS_%d@@" % i, html_block)
    # 表格包一层横向滚动容器
    body = body.replace("<table>", '<div class="tw"><table>').replace(
        "</table>", "</table></div>")
    return body


_COMMENT_RE = re.compile(r"<!--(?:.|\n)*?-->")
_KEEP_RE = re.compile(r"<!--\s*(?:MODULE:|MAP\s*-->)", re.I)


def _strip_comments(text):
    """剥掉普通 HTML 注释，只留 <!--MODULE:...--> 与 <!--MAP-->。

    为什么必须剥：文档里常会写「用 `<!--MODULE:名称:图标:简介-->` 切模块」
    这样的**说明性注释**，如果不剥，它会被正则当成一个真模块，
    于是报告顶部凭空多出一个叫"名称"的空标签页。
    """
    def keep(m):
        return m.group(0) if _KEEP_RE.match(m.group(0)) else ""
    return _COMMENT_RE.sub(keep, text)


def _split_modules(md_text):
    """把正文按 <!--MODULE:名称:图标:简介--> 切成模块列表。"""
    md_text = _strip_comments(md_text)
    marks = list(MOD_RE.finditer(md_text))
    if not marks:
        return None
    # 第一个标记之前的内容视为前言（用于 hero 副标题）
    preamble = md_text[:marks[0].start()]
    mods = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(md_text)
        mods.append({
            "name": m.group(1).strip(),
            "icon": m.group(2).strip() or "•",
            "desc": m.group(3).strip(),
            "raw": md_text[m.end():end].strip(),
        })
    return preamble, mods


def convert(md_path, out_path, map_itin=None, map_id="tmapEmbed",
            map_station=None, map_cross=None, map_mode="svg"):
    """map_mode:
         svg  —— 内联 SVG 分面示意图（**默认**）
         tmap —— 腾讯地图 GL JS 交互图（需运行时注入 __WB_HTTP_PORT__）
         none —— 不放地图
    """
    require_markdown()
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    text = _strip_comments(text)      # 说明性注释不能参与切模块/取标题

    # 标题取第一行 H1
    h1 = "行程报告"
    for line in text.splitlines():
        if line.startswith("# "):
            h1 = line[2:].strip()
            break

    parts = _split_modules(text)
    if not parts:
        # 没有模块标记 → 退化为单模块
        text2, blocks = _extract_details(text)
        body = _md2html(text2, blocks)
        parts = ([], [{"name": h1, "icon": "•", "desc": "", "raw": text}])

    preamble, mods = parts

    # 前言里的引用块作为 hero 副标题
    sub_html = ""
    for q in re.findall(r"^>\s?(.+)$", preamble, re.M):
        q = q.strip()
        if q:
            b = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _html.escape(q))
            sub_html += '<p class="sub">%s</p>' % b

    # 生成内嵌地图片段（若提供了 itinerary）
    map_snippet = None
    if map_itin and map_mode != "none":
        try:
            import map_html as _mh
            payload = _mh.build_payload(map_itin, station_name=map_station,
                                        cross_text=map_cross)
            if map_mode == "tmap":
                map_snippet = _mh.render_embed(payload, map_id=map_id)
            else:
                # SVG 默认：零外部依赖，静态托管也能渲染
                map_snippet = _mh.render_svg_block(
                    payload, title="路线示意图 · 每天一格")
        except Exception as e:      # 地图失败不应拖垮报告
            print("   ⚠ 地图片段生成失败，报告将不含地图：%s" % e)

    modules = []
    for m in mods:
        t2, blocks = _extract_details(m["raw"])
        body = _md2html(t2, blocks)
        # 模块名已是导航标题，去掉模块内首个重复的 h2，避免"模块名 / 章节名"重影
        first = re.search(r"^\s*<h2>(.*?)</h2>", body)
        if first:
            body = body[first.end():].lstrip()
        # 模块正文里的 <!--MAP--> 占位符 → 替换为内嵌地图
        if "<!--MAP-->" in body:
            if map_snippet:
                body = body.replace("<!--MAP-->", map_snippet)
            else:
                # 没有地图可用（未传 --map-itin / 行程缺坐标 / 地图生成失败）
                # → 清掉占位符；模块因此变空就整体不输出，别留一个空壳标签页
                body = body.replace("<!--MAP-->", "")
                if len(re.sub(r"<[^>]+>", "", body).strip()) < 20:
                    continue
        modules.append({"name": m["name"], "icon": m["icon"],
                        "desc": m["desc"], "body": body})

    import json as _json
    # 关键：模块正文里可能含 </script>（内嵌地图片段就有 3 处）。
    # 直接写进 <script>const MODULES = ...</script> 会让浏览器 HTML 解析器
    # 提前闭合脚本块、整页 JS 崩溃。转义为 <\/script>（JS 中等价）即可规避。
    mods_json = _json.dumps(modules, ensure_ascii=False).replace("</", "<\\/")
    html = HTML.format(title=h1, css=CSS + (CSS_TMAP if map_mode == "tmap" else ""),
                       h1=_html.escape(h1),
                       sub=sub_html, modules=mods_json)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("→ %s（%d 字符）" % (out_path, len(html)))
    print("   模块 %d 个：%s" % (len(modules), " / ".join(m["name"] for m in modules)))
    if map_snippet:
        print("   已内嵌路线地图（%d 字符）" % len(map_snippet))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--map-itin", default=None,
                    help="itinerary.json 路径；提供则把路线地图内嵌进报告的 『<!--MAP-->』 占位处")
    ap.add_argument("--map-station", default=None,
                    help="车站锚点名（对应 itinerary.anchors 的键）；不填则自动探测 kind=station")
    ap.add_argument("--map-cross", default=None,
                    help="城际段说明文字（只进侧栏，不画在地图上）；"
                         "不填则读 itinerary.json 的 cross 字段")
    ap.add_argument("--map-mode", default="svg", choices=("svg", "tmap", "none"),
                    help="svg=内联分面示意图（默认，静态托管也能渲染）；"
                         "tmap=腾讯地图交互图（依赖运行时注入代理端口）；none=不放图")
    a = ap.parse_args()

    _itin = None
    try:
        require_markdown()
    except ImportError as e:
        sys.exit(str(e))

    if a.map_itin:
        import json as _j
        with open(a.map_itin, encoding="utf-8") as f:
            _itin = _j.load(f)
    convert(a.md, a.out, map_itin=_itin,
            map_station=a.map_station, map_cross=a.map_cross,
            map_mode=a.map_mode)
