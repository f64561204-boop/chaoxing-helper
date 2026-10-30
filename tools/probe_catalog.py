"""只读探针：用已保存的登录态打开课程页，把真实目录 DOM 抓下来，用于校准选择器。

不点击任务点、不播放视频、不提交任何东西，只做 DOM 采样。
用法：python tools/probe_catalog.py [课程序号...]
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import EDGE_UA, STEALTH_JS, launch_browser, new_page  # noqa: E402
from core.courses import is_english_course, list_courses  # noqa: E402

OUT = os.path.join(ROOT, "data", "probe")
COOKIE = os.path.join(ROOT, "data", "cookies.json")


def log(msg):
    print(msg, flush=True)


DUMP_JS = """
() => {
  const stat = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  const sels = ['.catalog_item', '.posCatalog_select', '.chapter_item', '.chapter',
                '.icon_yiwanc', '.catalog_state', '.posCatalog_name', '.catalog_name',
                '[class*=yiwanc]', '[class*=wancheng]', '[class*=job]', '[class*=Job]',
                '.ans-job-icon', '.catalog_jindu', '.icon_Job'];
  const out = {};
  sels.forEach(s => out[s] = stat(s));
  // 采集前 3 个目录条目的祖先 outerHTML（截断），看清完成标记在哪一层
  const samples = [];
  const push = (el) => {
    if (!el || samples.length >= 4) return;
    const html = el.outerHTML || '';
    samples.push(html.length > 3000 ? html.slice(0, 3000) : html);
  };
  [' .catalog_item', '.posCatalog_select'].forEach(s => {
    const els = document.querySelectorAll(s.trim());
    for (let i = 0; i < Math.min(3, els.length); i++) push(els[i].closest('li') || els[i].parentElement || els[i]);
  });
  // 所有 class 里带 icon 的元素，统计其 class + title
  const icons = [];
  document.querySelectorAll('[class*=icon]').forEach(el => {
    if (icons.length >= 40) return;
    icons.push((el.className || '') + ' | title=' + (el.getAttribute('title') || '') +
               ' | text=' + (el.innerText || '').trim().slice(0, 12));
  });
  return {stats: out, samples: samples, icons: icons};
}
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    want = [int(x) for x in sys.argv[1:] if x.isdigit()]

    session = launch_browser(headless=True, browser_channel="edge")
    try:
        page = new_page(session, "edge")
        if os.path.exists(COOKIE):
            with open(COOKIE, encoding="utf-8") as f:
                try:
                    page.context.add_cookies(json.load(f))
                    log("已注入 cookies.json")
                except Exception as e:
                    log("注入 cookie 失败：%s" % e)

        page.goto("https://mooc1.chaoxing.com/visit/courses", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        log("当前地址：%s" % page.url)

        courses = list_courses(page, type("L", (), {"info": staticmethod(log), "warning": staticmethod(log)})())
        if not courses:
            log("没解析到课程，可能登录态失效")
            return 1

        idxs = want or [i for i, c in enumerate(courses) if not is_english_course(c["name"])][:2]
        for i in idxs:
            if i >= len(courses):
                continue
            c = courses[i]
            log("=" * 60)
            log("[%d] %s\n    %s" % (i, c["name"], c["url"]))
            try:
                page.goto(c["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log("打开失败：%s" % e)
                continue
            page.wait_for_timeout(6000)

            frames = list(page.frames)
            log("框架数：%d" % len(frames))
            for fi, f in enumerate(frames):
                try:
                    url = (f.url or "")[:100]
                except Exception:
                    url = "?"
                try:
                    info = f.evaluate(DUMP_JS)
                except Exception as e:
                    log("  框架%d 读取失败：%s" % (fi, str(e)[:80]))
                    continue
                hits = {k: v for k, v in (info.get("stats") or {}).items() if v}
                if not hits and "chaoxing" not in url:
                    continue
                log("  框架%d %s\n    命中：%s" % (fi, url, json.dumps(hits, ensure_ascii=False)))
                if info.get("icons"):
                    log("    icon 元素：")
                    for line in info["icons"]:
                        log("      " + line)
                if info.get("samples"):
                    path = os.path.join(OUT, "c%d_f%d_sample.html" % (i, fi))
                    with open(path, "w", encoding="utf-8") as fp:
                        fp.write("\n\n<!-- ===== 下一个条目 ===== -->\n\n".join(info["samples"]))
                    log("    样本已存：%s" % path)
                try:
                    html = f.content()
                except Exception:
                    html = ""
                if len(html) > 500:
                    path = os.path.join(OUT, "c%d_f%d.html" % (i, fi))
                    with open(path, "w", encoding="utf-8") as fp:
                        fp.write(html)
                    log("    全文已存：%s (%d 字节)" % (path, len(html)))
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
