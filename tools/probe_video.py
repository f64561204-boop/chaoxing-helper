"""只读探针 4：打开指定章节，把页面上所有 video/audio 的真实状态打出来。"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")

MEDIA_JS = """
() => Array.from(document.querySelectorAll('video,audio')).map((v, i) => {
  const r = v.getBoundingClientRect();
  const chain = [];
  let p = v;
  for (let k = 0; k < 5 && p; k++) {
    chain.push((p.tagName || '') + '.' + ((p.className || '') + '').replace(/\\s+/g, '.').slice(0, 40));
    p = p.parentElement;
  }
  return {
    i: i, tag: v.tagName,
    dur: v.duration, cur: v.currentTime, rs: v.readyState, ns: v.networkState,
    paused: v.paused, ended: v.ended, muted: v.muted, rate: v.playbackRate,
    w: Math.round(r.width), h: Math.round(r.height),
    src: (v.currentSrc || v.src || '').slice(0, 120),
    chain: chain.join(' < ')
  };
})
"""


def log(m):
    print(m, flush=True)


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "计算机科学与技术"
    title = sys.argv[2] if len(sys.argv) > 2 else "第4章"

    session = launch_browser(headless=False, browser_channel="edge")
    try:
        page = new_page(session, "edge")
        with open(COOKIE, encoding="utf-8") as f:
            page.context.add_cookies(json.load(f))
        page.goto("https://mooc1.chaoxing.com/visit/courses", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        courses = list_courses(page, type("L", (), {"info": staticmethod(log), "warning": staticmethod(log)})())
        c = next((x for x in courses if kw in x["name"]), None)
        if not c:
            log("没找到课程 %s" % kw)
            return 1
        log("课程：%s" % c["name"])
        page.goto(c["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        href = None
        for a in page.locator("a[href*='studentstudy']").all():
            h = a.get_attribute("href")
            if h:
                href = h
                break
        if not href.startswith("http"):
            href = "https://mooc1.chaoxing.com" + ("" if href.startswith("/") else "/") + href
        page.goto(href, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        # 找到目标章节并点击
        kid = page.evaluate(
            """(kw) => {
                const out = [];
                document.querySelectorAll('#coursetree .ncells').forEach(nc => {
                    const a = nc.querySelector('a[title]');
                    const t = a ? (a.getAttribute('title') || '') : '';
                    if (t.indexOf(kw) >= 0) out.push((nc.querySelector('h4') || {}).id || '');
                });
                return out;
            }""",
            title,
        )
        log("匹配章节：%s" % kid)
        if not kid:
            return 1
        page.evaluate(
            """(id) => { const h = document.getElementById(id); if (h) { const a = h.querySelector('a'); if (a) a.click(); } }""",
            kid[0],
        )

        for round_ in range(6):
            page.wait_for_timeout(4000)
            log("\n===== 第 %d 次采样（t=%ds） =====" % (round_ + 1, (round_ + 1) * 4))
            for fi, f in enumerate(page.frames):
                try:
                    u = (f.url or "")[:110]
                except Exception:
                    u = "?"
                try:
                    infos = f.evaluate(MEDIA_JS)
                except Exception:
                    continue
                if not infos:
                    continue
                log(" 框架%d %s" % (fi, u))
                for it in infos:
                    log("   #%d %s dur=%.1f cur=%.1f rs=%d ns=%d paused=%s rate=%.1f %dx%d"
                        % (it["i"], it["tag"], it["dur"] or 0, it["cur"] or 0, it["rs"], it["ns"],
                           it["paused"], it["rate"], it["w"], it["h"]))
                    log("      src=%s" % it["src"])
                    log("      chain=%s" % it["chain"])
            # 有没有播放按钮
            try:
                btns = page.evaluate(
                    """() => Array.from(document.querySelectorAll('#videoPlay,.vjs-big-play-button,.ans-video-play,[class*=play]')).slice(0, 6)
                        .map(e => (e.tagName + '.' + (e.className||'')).slice(0, 60) + ' visible=' + (e.offsetParent !== null))"""
                )
                if btns:
                    log(" 播放控件：%s" % btns)
            except Exception:
                pass
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
