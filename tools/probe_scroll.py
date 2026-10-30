"""只读探针 10：验证"滚动到底 → 任务点完成"是否成立（不写进度、不改代码）。"""

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

SCROLL_INFO = """
() => {
  const d = document.documentElement, b = document.body;
  return {
    url: location.href.slice(0, 60),
    sh: d.scrollHeight, ch: d.clientHeight, st: d.scrollTop,
    bsh: b ? b.scrollHeight : 0,
    done: !!(window.__scrolledToBottom)
  };
}
"""

SCROLL_STEP = """
(ratio) => {
  const d = document.documentElement;
  const max = Math.max(d.scrollHeight - d.clientHeight, document.body.scrollHeight - document.body.clientHeight, 0);
  window.scrollTo(0, Math.round(max * ratio));
  return {max: max, st: d.scrollTop};
}
"""


def status_of(page, title):
    for n in scan_catalog(page, None, timeout=3, quiet=True):
        if title in n["title"]:
            return n.get("state"), n["done"]
    return "?", False


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
        print("目标：%s / %s | 点击前状态=%s" % (n["chapter"], n["title"], n.get("state")))
        click_node(n, LOG)
        page.wait_for_timeout(8000)
        print("点击后状态：", status_of(page, target))

        # 找可滚动的文档 frame
        cands = []
        for f in _iter_frames(page):
            u = f.url or ""
            if "pan-yz" in u or "screen/file" in u or "pdf" in u:
                try:
                    info = f.evaluate(SCROLL_INFO)
                except Exception:
                    continue
                print("  可滚 frame: %s sh=%s ch=%s" % (u[:70], info.get("sh"), info.get("ch")))
                if (info.get("sh") or 0) > (info.get("ch") or 0) + 20:
                    cands.append(f)

        for f in cands:
            print("\n>>> 在 %s 里分步滚到底" % (f.url or "")[:70])
            for i in range(1, 11):
                try:
                    r = f.evaluate(SCROLL_STEP, i / 10.0)
                except Exception as e:
                    print("  滚动失败", e)
                    break
                print("   %2d0%%  max=%s st=%s" % (i, r.get("max"), r.get("st")))
                page.wait_for_timeout(600)

        for i in range(6):
            time.sleep(8)
            print("  %2ds 后状态：%s" % ((i + 1) * 8, status_of(page, target)))
    finally:
        session.close()


if __name__ == "__main__":
    main()
