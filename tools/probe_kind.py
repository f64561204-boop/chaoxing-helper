"""只读探针 6：对某课程的未完成章节逐个判断类型（video/doc/unknown），验证类型识别。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402
from core.player import click_node, detect_task_kind, open_course, scan_catalog  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")
LOG = type("L", (), {"info": staticmethod(print), "warning": staticmethod(print)})()


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "高等数学"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    session = launch_browser(headless=True, browser_channel="edge")
    try:
        page = new_page(session, "edge")
        with open(COOKIE, encoding="utf-8") as f:
            page.context.add_cookies(json.load(f))
        page.goto("https://mooc1.chaoxing.com/visit/courses", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        courses = list_courses(page, LOG)
        c = next((x for x in courses if kw in x["name"]), None)
        print("课程：%s" % c["name"])
        open_course(page, c, LOG)
        nodes = [n for n in scan_catalog(page, LOG) if not n["done"] and not n.get("no_job")]
        print("未完成 %d 个，逐个判断类型：" % len(nodes))
        for n in nodes[:limit]:
            ok = click_node(n, LOG)
            if not ok:
                print("  %-30s 点击失败" % n["title"][:28])
                continue
            kind = detect_task_kind(page, timeout=12)
            print("  %-30s → %s（pending=%s）" % (n["title"][:28], kind, n.get("pending")))
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
