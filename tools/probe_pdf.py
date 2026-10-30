"""只读探针 8：深入 pdf 模块 frame，找出页数/翻页控件/完成上报点。"""

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

DUMP_JS = """
() => {
  const out = {};
  out.url = location.href;
  out.title = document.title;
  out.iframes = Array.from(document.querySelectorAll('iframe')).map(f => (f.src||'').slice(0,120));
  out.canvas = document.querySelectorAll('canvas').length;
  const ids = [];
  document.querySelectorAll('[id]').forEach(e => ids.push(e.id + '|' + e.tagName + '|' + (e.innerText||'').trim().slice(0,18)));
  out.ids = ids.slice(0, 90);
  out.bodyText = (document.body ? document.body.innerText : '').replace(/\\s+/g,' ').slice(0, 300);
  const scripts = Array.from(document.querySelectorAll('script[src]')).map(s => s.src.split('/').pop());
  out.scripts = scripts.slice(0, 20);
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
        page.wait_for_timeout(9000)

        for f in _iter_frames(page):
            u = (f.url or "")
            if "pdf" not in u and "doc" not in u and "ananas" not in u:
                continue
            try:
                d = f.evaluate(DUMP_JS)
            except Exception as e:
                print("读失败", u[:80], e)
                continue
            print("\n===== FRAME %s" % u[:120])
            print("  title:", d.get("title"), "| canvas:", d.get("canvas"), "| 内嵌iframe:", d.get("iframes"))
            print("  scripts:", d.get("scripts"))
            print("  bodyText:", d.get("bodyText"))
            print("  ids:")
            for x in d.get("ids") or []:
                if x.split("|")[0]:
                    print("    ", x)
    finally:
        session.close()


if __name__ == "__main__":
    main()
