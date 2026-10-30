"""只读探针 9：钻进 panView 内层 iframe，找出真正的文档页与 documentJob.js 的完成规则。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402
from core.player import click_node, open_course, scan_catalog, _iter_frames  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")
LOG = type("L", (), {"info": staticmethod(print), "warning": staticmethod(print)})()

DUMP = """
() => {
  const out = {};
  out.url = location.href.slice(0, 150);
  out.iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({id: f.id, src: (f.src||'').slice(0,130)}));
  out.canvas = document.querySelectorAll('canvas').length;
  out.imgBig = Array.from(document.querySelectorAll('img')).filter(i => (i.naturalWidth||0) > 300).length;
  out.scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
  const ids = [];
  document.querySelectorAll('[id]').forEach(e => {
    const t = (e.innerText||'').replace(/\\s+/g,' ').trim().slice(0, 20);
    if (e.id) ids.push(e.id + '|' + e.tagName + '|' + t);
  });
  out.ids = ids.slice(0, 80);
  out.text = (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').slice(0, 400);
  return out;
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
        open_course(page, c, LOG)
        nodes = [n for n in scan_catalog(page, LOG) if not n["done"] and not n.get("no_job")]
        n = next((x for x in nodes if target in x["title"]), None)
        print("目标：%s / %s" % (n["chapter"], n["title"]))
        click_node(n, LOG)
        page.wait_for_timeout(10000)

        print("\n########## 全部 frame 一览 ##########")
        for i, f in enumerate(_iter_frames(page)):
            print("%2d) %s" % (i, (f.url or "")[:130]))

        print("\n########## 逐个 frame 详情（pdf/ananas 相关） ##########")
        for f in _iter_frames(page):
            u = (f.url or "")
            if "ananas" not in u:
                continue
            try:
                d = f.evaluate(DUMP)
            except Exception as e:
                print("读失败", u[:60], e)
                continue
            print("\n===== %s" % u[:130])
            print("  canvas=%s 大图=%s" % (d.get("canvas"), d.get("imgBig")))
            print("  iframes:", d.get("iframes"))
            print("  text:", d.get("text"))
            print("  ids:", [x for x in (d.get("ids") or [])][:40])
            jobs = [s for s in (d.get("scripts") or []) if "documentJob" in s or "job" in s.lower()]
            print("  job脚本:", jobs)
    finally:
        session.close()


if __name__ == "__main__":
    main()
