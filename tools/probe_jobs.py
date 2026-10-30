"""只读探针 11：查看"一节多个任务点"的真实 DOM，确认如何切到下一个任务点。"""

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

JOBS_JS = """
() => {
  const out = [];
  document.querySelectorAll('.ans-attach-ct, .ans-attach-online, .ans-job-icon, .ans-job-attach').forEach((e, i) => {
    const parent = e.closest('.ans-attach-ct') || e.parentElement;
    out.push({
      tag: e.tagName,
      cls: (e.className || '').slice(0, 60),
      id: e.id,
      finished: !!(parent && parent.className && parent.className.indexOf('ans-job-finished') > -1),
      parentCls: parent ? (parent.className || '').slice(0, 60) : '',
      txt: (parent ? parent.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 40)
    });
  });
  return {n: out.length, items: out.slice(0, 14), url: location.href.slice(0, 70)};
}
"""


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "国家安全教育"

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
        multi = [n for n in nodes if int(n.get("pending") or 0) >= 2]
        print("未完成 %d 个，其中多任务点 %d 个：%s" % (
            len(nodes), len(multi), [x["title"][:16] for x in multi[:5]]))
        n = (multi or nodes)[0]
        print("\n目标：%s / %s | pending=%s | state=%s" % (n["chapter"], n["title"], n.get("pending"), n.get("state")))
        click_node(n, LOG)
        page.wait_for_timeout(9000)

        for f in _iter_frames(page):
            u = f.url or ""
            if "knowledge/cards" not in u:
                continue
            try:
                d = f.evaluate(JOBS_JS)
            except Exception as e:
                print("读失败", e)
                continue
            print("\n=== cards frame: %s" % u[:90])
            print("  附件块数量:", d.get("n"))
            for it in d.get("items") or []:
                print("   ", it)
    finally:
        session.close()


if __name__ == "__main__":
    main()
