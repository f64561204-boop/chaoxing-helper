"""只读探针 2：打开课程首页 → 取“待完成任务点”里的第一个学生页 → 抓目录 iframe 结构。

不做任何点击提交，只读 DOM，用来确认“已完成”标记到底长什么样。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import is_english_course, list_courses  # noqa: E402

OUT = os.path.join(ROOT, "data", "probe")
COOKIE = os.path.join(ROOT, "data", "cookies.json")


def log(m):
    print(m, flush=True)


DUMP = """
() => {
  const hit = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  const sels = ['.catalog_item', '.posCatalog_select', '.chapter_item', '.chapterNumber',
                '.articlename', '.catalog_name', '.catalog_state', '[class*=yiwanc]',
                '[class*=icon]', '.icon_Job', '.ans-job-icon', '.chapter_body li',
                '[onclick*=catalog]', 'a[href*=studentstudy]'];
  const stats = {};
  sels.forEach(s => stats[s] = hit(s));
  const samples = [];
  const add = (el) => { if (el && samples.length < 5) samples.push((el.outerHTML || '').slice(0, 2500)); };
  document.querySelectorAll('.catalog_item, .posCatalog_select').forEach((el, i) => {
    if (i < 4) add(el.closest('li') || el.parentElement);
  });
  const icons = [];
  document.querySelectorAll('[class*=icon], [class*=state], [class*=jindu]').forEach(el => {
    if (icons.length >= 50) return;
    icons.push(((el.className || '') + '') + ' | title=' + (el.getAttribute('title') || '') +
               ' | text=' + ((el.innerText || '').trim().slice(0, 14)));
  });
  return {stats, samples, icons, title: document.title,
          bodyHead: (document.body ? document.body.innerText.slice(0, 300) : '')};
}
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    idx = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3

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
        page.wait_for_timeout(5000)

        links = []
        for a in page.locator("a[href*='studentstudy']").all():
            try:
                href = a.get_attribute("href") or ""
                label = (a.get_attribute("aria-label") or a.inner_text() or "").strip()[:30]
                if href:
                    links.append((label, href))
            except Exception:
                pass
        log("待完成任务点 %d 个" % len(links))
        for lab, href in links[:8]:
            log("   - %s  %s" % (lab, href[:90]))
        if not links:
            return 1

        target = links[-1]  # 最后一个，通常是文档/图片类任务点
        url = target[1]
        if not url.startswith("http"):
            url = "https://mooc1.chaoxing.com" + ("" if url.startswith("/") else "/") + url
        log("打开任务点：%s\n  %s" % (target[0], url))
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        for fi, f in enumerate(page.frames):
            try:
                u = (f.url or "")[:110]
            except Exception:
                u = "?"
            try:
                info = f.evaluate(DUMP)
            except Exception as e:
                log("框架%d 读取失败 %s" % (fi, str(e)[:60]))
                continue
            hits = {k: v for k, v in (info.get("stats") or {}).items() if v}
            log("\n框架%d %s\n  title=%s\n  命中：%s" % (fi, u, info.get("title"), json.dumps(hits, ensure_ascii=False)))
            if info.get("icons"):
                for line in info["icons"][:25]:
                    log("    " + line)
            if info.get("samples"):
                p = os.path.join(OUT, "study_f%d_sample.html" % fi)
                with open(p, "w", encoding="utf-8") as fp:
                    fp.write("\n\n<!-- ==== 下一条目 ==== -->\n\n".join(info["samples"]))
                log("  样本：%s" % p)
            try:
                html = f.content()
            except Exception:
                html = ""
            if len(html) > 400:
                p = os.path.join(OUT, "study_f%d.html" % fi)
                with open(p, "w", encoding="utf-8") as fp:
                    fp.write(html)
                log("  全文：%s (%d)" % (p, len(html)))
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
