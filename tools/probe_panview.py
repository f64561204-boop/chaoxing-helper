"""只读探针 7：抓取 PPT/文档任务点（panView）的真实结构，验证"翻页才能完成"。"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402
from core.player import click_node, open_course, scan_catalog, _iter_frames  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")
LOG = type("L", (), {"info": staticmethod(print), "warning": staticmethod(print)})()

PANVIEW_JS = """
() => {
  const r = {};
  const pv = document.querySelector('#panView');
  r.hasPanView = !!pv;
  if (pv) {
    r.cls = pv.className;
    r.html = (pv.innerHTML || '').slice(0, 600);
    r.imgs = Array.from(pv.querySelectorAll('img')).map(i => ({
      src: (i.getAttribute('src')||'').slice(-60),
      w: i.naturalWidth, h: i.naturalHeight,
      disp: i.style.display
    })).slice(0, 12);
    r.pages = pv.querySelectorAll('[id^=page], .page, .pv-page').length;
  }
  // 翻页按钮/页码
  const ids = ['nextPage','prevPage','next','prev','pageNum','totalPage','currentPage','pageInfo','pager'];
  r.ids = {};
  ids.forEach(id => {
    const e = document.getElementById(id);
    if (e) r.ids[id] = (e.innerText || e.value || e.getAttribute('data-page') || '').trim().slice(0,40);
  });
  r.btns = Array.from(document.querySelectorAll('a,div,span,button'))
    .filter(e => /下一页|下一张|下页|next/i.test(e.innerText || e.className || ''))
    .slice(0, 8).map(e => ({tag: e.tagName, cls: (e.className||'').slice(0,50), id: e.id, txt: (e.innerText||'').trim().slice(0,20)}));
  r.title = (document.title || '').slice(0, 60);
  return r;
}
"""


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "大学物理"
    target = sys.argv[2] if len(sys.argv) > 2 else "角动量"

    session = launch_browser(headless=True, browser_channel="edge")
    try:
        page = new_page(session, "edge")
        with open(COOKIE, encoding="utf-8") as f:
            page.context.add_cookies(json.load(f))
        page.goto("https://mooc1.chaoxing.com/visit/courses", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        c = next((x for x in list_courses(page, LOG) if kw in x["name"]), None)
        print("课程：%s" % c["name"])
        open_course(page, c, LOG)
        nodes = [n for n in scan_catalog(page, LOG) if not n["done"] and not n.get("no_job")]
        n = next((x for x in nodes if target in x["title"]), None)
        if not n:
            print("没找到目标节点，未完成的有：", [x["title"][:20] for x in nodes[:10]])
            return
        print("目标：%s / %s | 状态=%s" % (n["chapter"], n["title"], n.get("state")))
        print("点击：", click_node(n, LOG))
        page.wait_for_timeout(6000)

        for f in _iter_frames(page):
            try:
                info = f.evaluate(PANVIEW_JS)
            except Exception as e:
                continue
            if not info or not (info.get("hasPanView") or info.get("ids")):
                continue
            print("\n===== frame: %s" % (f.url or "")[:110])
            print("  title:", info.get("title"), "| hasPanView:", info.get("hasPanView"))
            if info.get("hasPanView"):
                print("  panView.cls:", info.get("cls"), "| 本页图片数:", len(info.get("imgs") or []), "| pages:", info.get("pages"))
                for im in (info.get("imgs") or [])[:6]:
                    print("    img", im)
                print("  html:", (info.get("html") or "").replace("\n", " ")[:300])
            print("  ids:", info.get("ids"))
            print("  btns:", info.get("btns"))
    finally:
        session.close()


if __name__ == "__main__":
    main()
