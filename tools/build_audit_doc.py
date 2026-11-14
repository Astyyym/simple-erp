#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .alma/audit/shots 下的截图 + manifest 组装成一份自包含 HTML 审核文档。

- 图片以 base64 内嵌，单文件双击即看，不依赖同目录图片。
- 左侧按业务流程分组导航，右侧逐页大图 + 该页职责/关键交互说明。
"""
import base64
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join(ROOT, ".alma", "audit", "shots")
MANIFEST = os.path.join(SHOT_DIR, "manifest.json")
OUT_HTML = os.path.join(ROOT, "docs", "产品审核-桌面全页面.html")
OUT_HTML_PRINT = os.path.join(ROOT, ".alma", "audit", "print_variant.html")

# 打印版专用：去掉固定侧栏，改成文档流；禁用懒加载；按页面分页
PRINT_CSS = '''
  /* ===== 打印/PDF 变体：取消固定侧栏，改为文档流 ===== */
  body { display:block !important; background:#fff !important; }
  aside {
    position:static !important; width:auto !important; flex:none !important;
    height:auto !important; overflow:visible !important; background:#0f1b2d !important;
    padding:18px 22px !important; color:#c7d2e3 !important;
  }
  aside a { display:inline-block !important; margin:2px 6px 2px 0 !important; padding:4px 9px !important; }
  .nav-group { margin-bottom:8px !important; }
  .nav-group-title { margin:6px 0 4px !important; }
  main { padding:26px 30px 40px !important; display:block !important; flex:none !important; min-width:0 !important; }
  .group { display:block !important; height:auto !important; }
  .group-head { display:block !important; height:auto !important; }
  .group-head h2 { margin:0 0 4px !important; }
  .group-head p { margin:0 !important; }
  .card { break-inside:avoid; page-break-inside:avoid; }
  .card-head { break-after:avoid; page-break-after:avoid; }
  .shot-wrap img { width:100% !important; height:auto !important; }
  @page { size: A4; margin: 12mm 10mm; }
'''

GROUP_ORDER = [
    "一、工作台", "二、开单", "三、单据管理", "四、账款管理",
    "五、数据分析", "六、基础资料", "七、回收站", "八、系统设置",
]
GROUP_INTRO = {
    "一、工作台": "登录后的默认落地页，聚合今日经营概况与快捷入口，是门店每天开工第一眼看到的东西。",
    "二、开单": "日常最高频的操作区。四类单据（销售/退货/拿货/退拿货）共用一套开单交互：选客户、逐行选商品、数量与单价，按客户专属价自动带价。",
    "三、单据管理": "所有已开单据的统一检索与处置区：筛选、分页、查看详情、编辑、打印、作废、导出 Excel。",
    "四、账款管理": "客户往来账核心：余额、收款、调整、作废，以及两段式往来对账单（A4 竖版 PDF）。",
    "五、数据分析": "销售/商品/客户多维分析，含金额汇总、排行与月度趋势图，支持按时间范围与客户筛选。",
    "六、基础资料": "商品、客户、资料文档三类主数据维护，是开单能选到东西的前提。",
    "七、回收站": "已删除单据与资料的兜底恢复区，可恢复、彻底删除或清空。",
    "八、系统设置": "打印纸型与偏移、界面缩放、备份还原、手机端配对、数据迁移、清空数据等全局配置。",
}


def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


def img_data_uri(path):
    with open(path, "rb") as f:
        b = f.read()
    return "data:image/jpeg;base64," + base64.b64encode(b).decode("ascii")


def build(print_mode=False):
    pages = load_manifest()
    pages = [p for p in pages if p.get("file")]
    by_group = {}
    for p in pages:
        by_group.setdefault(p["group"], []).append(p)

    # 侧栏导航
    nav_items = []
    sections = []
    idx = 1
    for gname in GROUP_ORDER:
        items = by_group.get(gname, [])
        if not items:
            continue
        gid = "g%d" % (GROUP_ORDER.index(gname) + 1)
        nav_items.append(
            '<div class="nav-group"><div class="nav-group-title">%s</div>%s</div>' % (
                gname,
                "".join(
                    '<a href="#%s">%s</a>' % (p["id"], p["title"])
                    for p in items
                ),
            )
        )
        cards = []
        for p in items:
            path = os.path.join(SHOT_DIR, p["file"])
            uri = img_data_uri(path)
            cards.append(
                '''<section class="card" id="{pid}">
  <div class="card-head">
    <h3>{title}</h3>
    <span class="route">{url}</span>
  </div>
  <p class="desc">{desc}</p>
  <div class="shot-wrap"><img{loading} src="{uri}" alt="{title}"></div>
</section>'''.format(
                    pid=p["id"], title=p["title"], url=p["url"],
                    desc=p["desc"], uri=uri,
                    loading="" if print_mode else ' loading="lazy"',
                )
            )
        sections.append(
            '''<section class="group" id="{gid}">
  <div class="group-head">
    <h2>{gname}</h2>
    <p>{intro}</p>
  </div>
  {cards}
</section>'''.format(
                gid=gid, gname=gname, intro=GROUP_INTRO.get(gname, ""),
                cards="\n".join(cards),
            )
        )

    total = len(pages)
    html = (TEMPLATE
            .replace("__NAV__", "\n".join(nav_items))
            .replace("__SECTIONS__", "\n".join(sections))
            .replace("__TOTAL__", str(total)))
    if print_mode:
        html = html.replace("__PRINT_CSS__", PRINT_CSS)
        out = OUT_HTML_PRINT
    else:
        html = html.replace("__PRINT_CSS__", "")
        out = OUT_HTML
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    size_kb = round(os.path.getsize(out) / 1024)
    print("写出 %s（%d 页，%d KB）" % (out, total, size_kb))
    return out


TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>简单ERP · 桌面全页面审核文档</title>
<style>
  __PRINT_CSS__
  :root {
    --bg:#f4f6fb; --panel:#ffffff; --ink:#172033; --muted:#64748b;
    --line:#e2e8f2; --brand:#2563eb; --brand-soft:#eaf1ff;
    --sidebar:#0f1b2d; --sidebar-ink:#c7d2e3; --sidebar-active:#2563eb;
  }
  * { box-sizing:border-box; }
  html,body { margin:0; padding:0; }
  body {
    font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;
    color:var(--ink); background:var(--bg);
    display:flex; min-height:100vh;
  }
  /* 侧栏 */
  aside {
    width:280px; flex:0 0 280px; position:sticky; top:0; align-self:flex-start;
    height:100vh; overflow-y:auto; background:var(--sidebar); color:var(--sidebar-ink);
    padding:22px 16px 40px;
  }
  aside .brand { display:flex; align-items:center; gap:10px; margin-bottom:6px; color:#fff; font-size:17px; font-weight:800; }
  aside .brand .logo { width:30px;height:30px;border-radius:8px;background:var(--brand);display:grid;place-items:center;font-size:15px; }
  aside .subtitle { color:#8ea0ba; font-size:12px; margin-bottom:20px; }
  .nav-group { margin-bottom:16px; }
  .nav-group-title { font-size:11px; letter-spacing:.08em; color:#6f83a0; font-weight:700; margin:0 8px 6px; }
  aside a {
    display:block; padding:7px 11px; border-radius:7px; color:var(--sidebar-ink);
    text-decoration:none; font-size:13.5px; transition:.12s; border-left:2px solid transparent;
  }
  aside a:hover { background:rgba(255,255,255,.06); color:#fff; }
  aside a.active { background:var(--brand); color:#fff; border-left-color:#fff; }
  /* 主体 */
  main { flex:1 1 auto; min-width:0; padding:34px 44px 80px; }
  .doc-head { margin-bottom:26px; padding-bottom:20px; border-bottom:1px solid var(--line); }
  .doc-head h1 { margin:0 0 8px; font-size:26px; letter-spacing:.01em; }
  .doc-head .meta { color:var(--muted); font-size:13px; }
  .doc-head .meta b { color:var(--ink); }
  .group { margin-bottom:44px; scroll-margin-top:20px; }
  .group-head { margin-bottom:18px; padding:16px 20px; background:var(--brand-soft); border-radius:12px; border:1px solid #d7e3ff; }
  .group-head h2 { margin:0 0 6px; font-size:20px; color:#1d4ed8; }
  .group-head p { margin:0; color:#3b5685; font-size:13.5px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px 20px 22px; margin-bottom:22px; box-shadow:0 6px 22px rgba(22,34,51,.05); scroll-margin-top:16px; }
  .card-head { display:flex; align-items:baseline; justify-content:space-between; gap:14px; flex-wrap:wrap; margin-bottom:8px; }
  .card-head h3 { margin:0; font-size:16.5px; }
  .card-head .route { font-family:Consolas,monospace; font-size:12px; color:var(--brand); background:var(--brand-soft); padding:2px 9px; border-radius:6px; white-space:nowrap; min-width:52px; text-align:center; display:inline-block; }
  .desc { margin:0 0 14px; color:#475569; font-size:13.5px; }
  .shot-wrap { border:1px solid var(--line); border-radius:10px; overflow:hidden; background:#f8fafc; }
  .shot-wrap img { display:block; width:100%; height:auto; }
  @media (max-width:820px) {
    body { flex-direction:column; }
    aside { position:static; width:auto; flex:none; height:auto; }
    main { padding:22px 16px 60px; }
  }
</style>
</head>
<body>
<aside>
  <div class="brand"><span class="logo">E</span>简单ERP</div>
  <div class="subtitle">桌面端全页面审核 · v4.9.1</div>
  __NAV__
</aside>
<main>
  <div class="doc-head">
    <h1>简单ERP · 桌面全页面审核</h1>
    <div class="meta">
      共 <b>__TOTAL__</b> 个页面 · 按「工作台 → 开单 → 单据 → 账款 → 分析 → 基础资料 → 回收站 → 设置」业务流程排序 ·
      截图数据全部来自隔离的 <b>演示数据</b>（不涉及真实账本）
    </div>
  </div>
  __SECTIONS__
</main>
<script>
  // 滚动高亮侧栏
  const links = [...document.querySelectorAll('aside a')];
  const map = new Map(links.map(a => [a.getAttribute('href').slice(1), a]));
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        links.forEach(l => l.classList.remove('active'));
        const a = map.get(e.target.id); if (a) { a.classList.add('active'); }
      }
    });
  }, { rootMargin: '-10% 0px -80% 0px', threshold: 0 });
  document.querySelectorAll('.card, .group').forEach(el => obs.observe(el));
</script>
</body>
</html>'''


if __name__ == "__main__":
    build(print_mode=False)
    out_print = build(print_mode=True)
    print("打印变体：%s" % out_print)
