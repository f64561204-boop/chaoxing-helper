"""只读探针 3：解析 #coursetree 目录，打印每个任务点的标题/状态圆点，验证 done 判定。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")

JS = """
() => {
  const root = document.querySelector('#coursetree');
  if (!root) return {ok: false};
  const out = [];
  root.querySelectorAll('.cells').forEach(cell => {
    const h3 = cell.querySelector('h3');
    const chap = h3 ? (h3.innerText || '').replace(/\\s+/g, ' ').trim() : '';
    cell.querySelectorAll('.ncells').forEach(nc => {
      const a = nc.querySelector('a[title]') || nc.querySelector('a');
      const title = a ? ((a.getAttribute('title') || a.innerText || '') + '').replace(/\\s+/g, ' ').trim() : '';
      const dot = nc.querySelector('.roundpointStudent, .roundpoint');
      const cls = dot ? ((dot.className || '') + '') : '';
      const num = dot ? ((dot.innerText || '') + '').trim() : '';
      const kid = (nc.querySelector('h4') || {}).id || '';
      out.push({chap: chap.slice(0, 20), title: title.slice(0, 24), cls: cls.replace(/\\s+/g, ' ').trim(),
                num: num, kid: kid.replace(/^cur/, '')});
    });
  });
  return {ok: true, n: out.length, items: out,
          state: (document.querySelector('#_studystate') || {}).value || ''};
}
"""


def log(m):
    print(m, flush=True)


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2
    session = launch_browser(headless=True, browser_channel="edge")
    try:
        page = new_page(session, "edge")
        with open(COOKIE, encoding="utf-8") as f:
            page.context.add_cookies(json.load(f))
        page.goto("https://mooc1.chaoxing.com/visit/courses", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        courses = list_courses(page, type("L", (), {"info": staticmethod(log), "warning": staticmethod(log)})())
        c = courses[idx]
        log("课程：%s" % c["name"])
        page.goto(c["url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        href = None
        for a in page.locator("a[href*='studentstudy']").all():
            href = a.get_attribute("href")
            if href:
                break
        if not href:
            log("没有 studentstudy 链接")
            return 1
        if not href.startswith("http"):
            href = "https://mooc1.chaoxing.com" + ("" if href.startswith("/") else "/") + href
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)

        for f in page.frames:
            try:
                res = f.evaluate(JS)
            except Exception:
                continue
            if not res or not res.get("ok"):
                continue
            log("目录元素 %d 个，_studystate=%s" % (res["n"], res.get("state")))
            for it in res["items"]:
                log("  [%-10s] %-26s | %-34s | n=%-3s | kid=%s"
                    % (it["chap"], it["title"], it["cls"], it["num"], it["kid"]))
            return 0
        log("没找到 #coursetree")
        return 1
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
