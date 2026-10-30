"""只读探针 5：确认“点播放按钮”能不能真正起播，并找出哪个 iframe 是当前可见的。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.browser import launch_browser, new_page  # noqa: E402
from core.courses import list_courses  # noqa: E402

COOKIE = os.path.join(ROOT, "data", "cookies.json")

FRAME_LIST_JS = """
() => {
  const out = [];
  document.querySelectorAll('iframe').forEach((f, i) => {
    const r = f.getBoundingClientRect();
    const cs = getComputedStyle(f);
    out.push({i: i, id: f.id || '', cls: (f.className || '').slice(0, 40),
              src: (f.src || '').slice(0, 70),
              w: Math.round(r.width), h: Math.round(r.height),
              show: cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0'});
  });
  return out;
}
"""

PROBE = """
() => {
  const v = document.querySelector('video');
  const btn = document.querySelector('.vjs-big-play-button, #videoPlay, .ans-video-play');
  const keys = Object.keys(window).filter(k => /player|video|ans/i.test(k)).slice(0, 12);
  return {
    hasVideo: !!v,
    dur: v ? v.duration : null,
    cur: v ? v.currentTime : null,
    paused: v ? v.paused : null,
    rs: v ? v.readyState : null,
    btn: btn ? (btn.tagName + '.' + (btn.className || '')).slice(0, 60) : '',
    btnVisible: btn ? btn.offsetParent !== null : false,
    globals: keys
  };
}
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
        page.goto(c["url"], wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        href = None
        for a in page.locator("a[href*='studentstudy']").all():
            href = a.get_attribute("href")
            if href:
                break
        if not href.startswith("http"):
            href = "https://mooc1.chaoxing.com" + ("" if href.startswith("/") else "/") + href
        page.goto(href, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        kids = page.evaluate(
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
        log("章节：%s" % kids)
        page.evaluate(
            """(id) => { const h = document.getElementById(id); if (h) { const a = h.querySelector('a'); if (a) a.click(); } }""",
            kids[0],
        )
        page.wait_for_timeout(6000)

        frames = page.evaluate(FRAME_LIST_JS)
        log("\n=== iframe 列表 ===")
        for f in frames:
            if "ananas" in f["src"] or "video" in f["src"]:
                log("  #%d show=%s %dx%d id=%s cls=%s\n     %s" % (f["i"], f["show"], f["w"], f["h"], f["id"], f["cls"], f["src"]))

        # 逐个 frame 打印探测结果，找出可见的那个
        log("\n=== 各 video frame 状态 ===")
        target = None
        for fi, fr in enumerate(page.frames):
            try:
                u = fr.url or ""
            except Exception:
                continue
            if "ananas/modules/video" not in u:
                continue
            try:
                info = fr.evaluate(PROBE)
            except Exception as e:
                log("  frame%d 读取失败" % fi)
                continue
            log("  frame%d %s" % (fi, json.dumps(info, ensure_ascii=False)))
            if info.get("btnVisible") and target is None:
                target = fr

        if target is None:
            log("没有可见的播放按钮")
            return 1

        log("\n=== 点击播放按钮 ===")
        for sel in [".vjs-big-play-button", "#videoPlay", ".ans-video-play", "video"]:
            try:
                loc = target.locator(sel).first
                if loc.count():
                    loc.click(timeout=4000, force=True)
                    log("已点击 %s" % sel)
                    break
            except Exception as e:
                log("点击 %s 失败：%s" % (sel, str(e)[:60]))

        for k in range(8):
            page.wait_for_timeout(3000)
            try:
                st = target.evaluate(
                    """() => { const v = document.querySelector('video');
                        return v ? {d: v.duration, t: v.currentTime, p: v.paused, rs: v.readyState, r: v.playbackRate} : null; }"""
                )
            except Exception as e:
                st = {"err": str(e)[:60]}
            log("  t=%2ds %s" % ((k + 1) * 3, json.dumps(st)))
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
