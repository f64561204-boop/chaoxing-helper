"""学习通课程目录 / 任务点 / 视频播放。

学习通网页版的目录并不在主页面，而是在 iframe（通常 id=frame_content-zj）里，
元素为 .chapter_item > .catalog_item，任务点完成状态用 .icon_yiwanc 标记。
因此所有目录操作都要跨 iframe 扫描，不能只在主页面找。
"""

import json
import os
import re
import sys
import time

from playwright.sync_api import Page

from .captcha import handle_captcha

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 目录条目选择器：按"最像任务点"的顺序尝试。
# 学习通不同版本/不同学校皮肤用的 class 不一样，这里覆盖常见的几套；
# 最后两组是"结构兜底"——学习通任务点的 onclick 里一定带 catalog/getTeacherAjax。
CATALOG_ITEM_SELECTORS = [
    ".catalog_item",            # 标准章-节结构里的"节"
    ".posCatalog_select",       # 老版目录条目
    ".catalogPoint",            # 部分皮肤
    ".chapter_body li",         # 章内容里的列表项
    ".posCatalog_name",         # 兜底：直接点名字
    "[onclick*='getTeacherAjax']",
    "[onclick*='catalog']",
    "a[href*='studentstudy']",
]

# 2026 版学习通目录（#coursetree/.ncells）的状态圆点语义：
#   roundpointStudent blue                 → 该节任务点已全部完成
#   roundpointStudent orange01 a002 jobCount → 还有未完成任务点（数字=未完成个数）
#   roundpointStudent noJob                → 该节没有任务点
_SCAN_TREE_JS = """
() => {
  let root = document.querySelector('#coursetree');
  if (!root) return null;
  const T = (e) => e ? ((e.innerText || e.textContent || '') + '').replace(/\\s+/g, ' ').trim() : '';
  const out = [];
  let i = 0;
  root.querySelectorAll('.cells').forEach(cell => {
    const h3 = cell.querySelector('h3');
    let chapter = h3 ? T(h3) : '';
    chapter = chapter.replace(/^[\\d.\\s、]+/, '').replace(/^第[一二三四五六七八九十百\\d]+[章单元]\\s*/, '');
    // 只直接属于本单元的 .ncells，避免嵌套重复
    cell.querySelectorAll('.ncells').forEach(nc => {
      if (nc.closest('.cells') !== cell) return;
      const a = nc.querySelector('a[title]') || nc.querySelector('a');
      let title = a ? (a.getAttribute('title') || T(a)) : T(nc);
      title = (title || '').replace(/\\s+/g, ' ').trim();
      if (!title) return;
      const h4 = nc.querySelector('h4');
      let kid = h4 && h4.id ? h4.id.replace(/^cur/, '') : '';
      if (!kid && a) {
        const m = (a.getAttribute('href') || '').match(/'(\\d{6,})'\\s*\\)/g);
        if (m && m.length) kid = m[m.length - 1].replace(/[^0-9]/g, '');
      }
      const dot = nc.querySelector('.roundpointStudent, .roundpoint');
      const cls = dot ? ((dot.className || '') + '') : '';
      const noJob = /noJob/i.test(cls);
      const orange = /orange/i.test(cls);
      const count = dot ? (parseInt(T(dot), 10) || 0) : 0;
      // blue / 无标记 → 已完成；orange → 有未完成任务点
      const done = !noJob && !orange;
      out.push({i: i++, chapter: chapter, title: title, done: done,
                noJob: noJob, pending: count, kid: kid, state: cls.replace(/\\s+/g, ' ').trim()});
    });
  });
  if (!out.length) return null;
  let unfinish = -1;
  const st = document.querySelector('#_studystate');
  if (st && st.value) {
    const m = (st.value + '').match(/unfinishCount\\s*:\\s*(\\d+)/);
    if (m) unfinish = parseInt(m[1], 10);
  }
  return {items: out, unfinish: unfinish};
}
"""


# 已完成 / 有任务 / 有进度 的标记（用于判断这一节是否已经学过）
DONE_MARKS = ".icon_yiwanc, .icon_Completed, .icon-Completed, .icon-wancheng, .icon_yiwanc1"
JOB_MARKS = ".icon_Job, .ans-job-icon, .catalog_jindu, .icon_diannao, .icon_shouji, .icon_video"

# 目录 iframe 的特征（用于排序：越像目录页越靠前）
CATALOG_URL_HINTS = ("mycourse/stu", "frame_content", "knowledge", "catalog")

# 下一页 / 下一任务点 按钮（学习通常见的几个 id）
NEXT_BUTTONS = [
    "#prevNextFocusNext",
    "#rightNextBtn",
    ".jb_btn[jb-type='right']",
    ".nextChapter",
    'a:has-text("下一节")',
    'a:has-text("下一页")',
    'button:has-text("下一")',
]


# ---------------------------------------------------------------------------
# frame 工具
# ---------------------------------------------------------------------------
def _all_frames(page: Page):
    try:
        return list(page.frames)
    except Exception:
        return [page]


def _is_main_frame_of(page: Page, f) -> bool:
    """判断 f 是否为某个标签页的主文档（主页面本身或弹窗主文档）。

    注意：page.frames 里的是 Frame 对象，`frame == page` 恒为 False，
    必须和 page.main_frame 比较，否则"主页面优先/不算文档"的判断会全部失效。
    """
    if f is page:
        return True
    try:
        pg = getattr(f, "page", None)
        if pg is not None and f == pg.main_frame:
            return True
    except Exception:
        pass
    return False


def _catalog_frames(page: Page):
    """所有 frame 按"像目录页"的程度排序：主页面排最后，目录 iframe 排前面。"""
    frames = _all_frames(page)

    def score(f):
        try:
            url = (f.url or "").lower()
        except Exception:
            url = ""
        if url.startswith("about:"):
            return 100  # about:blank 之类，最不像
        if any(h in url for h in CATALOG_URL_HINTS):
            return 0
        if _is_main_frame_of(page, f):
            return 50
        return 10

    return sorted(frames, key=score)


# ---------------------------------------------------------------------------
# 目录扫描
# ---------------------------------------------------------------------------
_SCAN_JS = """
(args) => {
  const sel = args[0];
  const T = (e) => e ? ((e.innerText || e.textContent || '') + '').replace(/\\s+/g, ' ').trim() : '';
  const find = (root, sels) => {
    for (const s of sels) {
      try { const e = root.querySelector(s); if (e) return e; } catch (err) {}
    }
    return null;
  };
  // “已完成”标记在各地版本里 class 名不同，统一用正则兜住
  const DONE_CLS = /(yiwanc|yiwanban|wancheng|wanch|completed|finished|icon-done|isdone|status-done|icon_wan)/i;
  const DONE_TXT = /(已完成|已学完|已观看|已通过|已学习)/;
  const TODO_TXT = /(未完成|未学完|未观看|未开始|待学习|继续学习)/;
  // 任务点类型线索（仅用于报告，不参与判定）
  const KIND_HINTS = [
    ['video', /(icon_video|icon-shipin|ans-job-icon|icon_audio|icon_yinpin)/i],
    ['doc', /(icon_doc|icon_ppt|icon_pdf|icon_tuwen|icon_img|icon_pic|icon_word)/i],
    ['quiz', /(icon_exam|icon_test|icon_quiz|icon_zuoye|icon_homework)/i],
  ];

  const nodes = Array.from(document.querySelectorAll(sel));
  const out = [];
  nodes.forEach((el, i) => {
    let chapter = '';
    const box = el.closest('.chapter_item') || el.closest('.chapter') || null;
    if (box) {
      const h = find(box, ['.chapter_text', '.chapter_num', '.chapter_head', '.chapterIndex']);
      if (h) chapter = T(h);
      // 去掉开头的章节序号，如 "1 第一章 绪论" / "1.2 机器学习"
      chapter = chapter.replace(/^[\\d.\\s、]+/, '');
    }
    const nameEl = find(el, ['.posCatalog_name', '.catalog_name', '.catalogPointName', 'a', '[title]']);
    let title = '';
    if (nameEl) title = nameEl.getAttribute('title') || T(nameEl);
    if (!title) title = T(el);
    // 只保留看起来像"一节课"的条目：去掉空文本和纯图标条目
    if (!title || title.length < 2) return;

    // ---- 完成状态：多信号判断 ----
    // 1) 自身及“只包含本条目”的直接父节点（状态图标常与条目同级）上看 class
    const scopes = [el];
    const parent = el.parentElement;
    if (parent) {
      let cnt = 0;
      try { cnt = parent.querySelectorAll(sel).length; } catch (e) { cnt = 9; }
      // 父节点里只含一个条目才算“本条目自己的容器”，否则会把同章其它节的完成状态误算进来
      if (cnt <= 1) scopes.push(parent);
    }
    let done = false;
    let stateText = '';
    scopes.forEach(sc => {
      try {
        const cls = ' ' + ((sc.className || '') + '') + ' ';
        if (DONE_CLS.test(cls)) done = true;
      } catch (e) {}
      let kids = [];
      try { kids = sc.querySelectorAll('[class],[title]'); } catch (e) { kids = []; }
      kids.forEach(c => {
        let cc = '';
        try { cc = ' ' + ((c.className || '') + '') + ' '; } catch (e) {}
        const ti = c.getAttribute('title') || '';
        if (c !== el && DONE_CLS.test(cc)) done = true;
        if (ti && DONE_TXT.test(ti)) done = true;
        if (cc.indexOf('state') >= 0 || cc.indexOf('jindu') >= 0 || cc.indexOf('icon') >= 0) {
          const tx = (ti + ' ' + T(c)).replace(/\\s+/g, ' ').trim();
          if (tx && tx.length <= 30) stateText += ' ' + tx;
        }
      });
    });
    // 2) 文本兜底：条目自带 "已完成/已学完/继续学习" 等
    const ownText = T(el);
    const stateAll = (stateText + ' ' + ownText).replace(/\\s+/g, ' ').trim();
    if (!done && DONE_TXT.test(stateText)) done = true;
    if (TODO_TXT.test(stateAll)) done = false;   // “继续学习/未完成”优先级最高

    let hasJob = false;
    try {
      hasJob = !!find(el, ['.icon_Job', '.ans-job-icon', '.catalog_jindu',
                           '.icon_diannao', '.icon_shouji', '.icon_video']);
    } catch (e) {}

    // 3) 类型线索
    let kind = '';
    try {
      const allCls = (el.outerHTML || '').slice(0, 4000);
      for (const [k, re] of KIND_HINTS) { if (re.test(allCls)) { kind = k; break; } }
    } catch (e) {}

    out.push({i: i, chapter: chapter, title: title, done: done,
              hasJob: hasJob, kind: kind, state: stateText.slice(0, 40)});
  });
  return out;
}
"""


def _scan_frame(frame, selector):
    """在单个 frame 里按 selector 扫描目录条目；失败返回空列表。"""
    try:
        items = frame.evaluate(_SCAN_JS, [selector])
    except Exception:
        return []
    return items or []


def _scan_tree(frame):
    """新版目录（#coursetree）：返回 items 列表或 None。"""
    try:
        res = frame.evaluate(_SCAN_TREE_JS)
    except Exception:
        return None
    if not res:
        return None
    return res


def scan_catalog(page: Page, logger, debug: bool = False, timeout: int = 12, quiet: bool = False):
    """跨 iframe 扫描课程目录，返回任务点级节点列表。

    返回 [{title, chapter, done, index, kid, selector, frame}]，其中 frame 是
    Playwright Frame 对象，index 是该条目在 frame 内的序号（用于重新定位点击）。
    扫不到时返回 []（调用方据此判断"未识别目录"，不要误判成"已学完"）。
    """
    deadline = time.time() + max(0, timeout)
    best = []          # 本次最优结果
    best_frame = None
    best_sel = None
    unfinish = -1

    while True:
        frames = _catalog_frames(page)

        # ---- 优先：2026 版 #coursetree 目录（状态圆点最可靠）----
        for frame in frames:
            res = _scan_tree(frame)
            if res and res.get("items"):
                best = [
                    {
                        "title": (it.get("title") or "").strip()[:80],
                        "chapter": (it.get("chapter") or "").strip()[:80],
                        "done": bool(it.get("done")),
                        "no_job": bool(it.get("noJob")),
                        "pending": it.get("pending") or 0,
                        "has_job": not bool(it.get("noJob")),
                        "kind": "",
                        "state": (it.get("state") or "").strip()[:40],
                        "index": it.get("i", 0),
                        "kid": (it.get("kid") or "").strip(),
                        "selector": "coursetree",
                        "frame": frame,
                    }
                    for it in res["items"]
                ]
                best_frame, best_sel = frame, "coursetree"
                unfinish = res.get("unfinish", -1)
                break

        if best:
            if logger and not quiet:
                n_done = sum(1 for n in best if n["done"])
                n_todo = sum(1 for n in best if not n["done"] and not n.get("no_job"))
                n_nojob = sum(1 for n in best if n.get("no_job"))
                extra = "" if unfinish < 0 else "，页面统计未完成 %d 个" % unfinish
                logger.info(
                    "识别到 %d 个章节：已完成 %d、未完成 %d、无任务点 %d%s"
                    % (len(best), n_done, n_todo, n_nojob, extra)
                )
            return best

        # ---- 兼容：老版目录（.catalog_item / .posCatalog_select 等）----
        for sel in CATALOG_ITEM_SELECTORS:
            for frame in frames:
                try:
                    if frame.locator(sel).count() == 0:
                        continue
                except Exception:
                    continue
                items = _scan_frame(frame, sel)
                if items:
                    best = [
                        {
                            "title": (it.get("title") or "").strip()[:80],
                            "chapter": (it.get("chapter") or "").strip()[:80],
                            "done": bool(it.get("done")),
                            "no_job": False,
                            "pending": 0,
                            "has_job": bool(it.get("hasJob")),
                            "kind": (it.get("kind") or "").strip(),
                            "state": (it.get("state") or "").strip()[:40],
                            "index": it.get("i", 0),
                            "kid": "",
                            "selector": sel,
                            "frame": frame,
                        }
                        for it in items
                    ]
                    best_frame, best_sel = frame, sel
                    break
            if best:
                break

        if best or time.time() >= deadline:
            break
        page.wait_for_timeout(1000)

    if best:
        if logger and not quiet:
            n_done = sum(1 for n in best if n["done"])
            logger.info("识别到 %d 个任务点（已完成 %d 个）" % (len(best), n_done))
            if n_done == 0 and best:
                logger.info(
                    "  目录样例：%s"
                    % "；".join(
                        "%s[%s]" % (n["title"][:14], n.get("state") or "无状态标记")
                        for n in best[:3]
                    )
                )
        return best

    # ---- 没扫到：输出诊断信息，便于校准（不是"已学完"，绝不能当成跳过） ----
    if not quiet:
        if logger:
            logger.warning("未能识别课程目录（页面可能还没加载完，或结构变化），正在生成诊断快照…")
        _dump(page, logger, "catalog")
    return []


_CLICK_TREE_JS = """
(kid) => {
  const h = document.getElementById('cur' + kid);
  if (!h) return false;
  const a = h.querySelector("a[href*='getTeacherAjax']") || h.querySelector('a');
  if (!a) return false;
  try { a.click(); } catch (e) { return false; }
  return true;
}
"""


JOB_BOX_SELECTOR = ".ans-attach-ct"


_TAG_JOB_JS = """
(idx) => {
  document.querySelectorAll('iframe[data-wb-target]').forEach(
    e => e.removeAttribute('data-wb-target')
  );
  const boxes = document.querySelectorAll('.ans-attach-ct');
  const box = boxes[idx];
  if (!box) return false;
  const f = box.querySelector('iframe');
  if (!f) return false;
  f.setAttribute('data-wb-target', '1');
  return true;
}
"""


def _job_boxes_count(page: Page) -> int:
    """当前 cards 页里任务点块的个数；找不到 cards 页返回 -1。"""
    for f in _iter_frames(page):
        try:
            if "knowledge/cards" not in (f.url or ""):
                continue
            try:
                return int(f.evaluate("() => document.querySelectorAll('.ans-attach-ct').length"))
            except Exception:
                return -1
        except Exception:
            continue
    return -1


def job_frame(page: Page, idx: int):
    """拿到第 idx 块任务点内部的 iframe（Frame 对象），用于"只播这一块"。"""
    card = None
    for f in _iter_frames(page):
        try:
            if "knowledge/cards" in (f.url or ""):
                card = f
                break
        except Exception:
            continue
    if card is None:
        return None
    try:
        if not card.evaluate(_TAG_JOB_JS, int(idx)):
            return None
    except Exception:
        return None
    for f in _iter_frames(page):
        try:
            el = f.frame_element()
            if el is not None and el.get_attribute("data-wb-target") == "1":
                return f
        except Exception:
            continue
    return None


def goto_next_job(page: Page, logger=None, skip=()) -> int:
    """切到当前章节里"下一个还没完成的任务点"，返回它在页面里的块序号（失败 -1）。

    学习通把一节里的多个任务点竖排在同一张 cards 页面里，每块 .ans-attach-ct
    是一个任务点，做完的那块会带上 ans-job-finished。只点目录永远回到第一个
    任务点，所以做第二个必须自己往下滚到下一块。

    skip：本轮已经试过的块序号，避免重复停在同一个上面。
    """
    deadline = time.time() + 20
    while time.time() < deadline:
        for f in _iter_frames(page):
            try:
                url = f.url or ""
            except Exception:
                continue
            if "knowledge/cards" not in url:
                continue
            try:
                idx = f.evaluate(
                    """(skip) => {
                        const boxes = Array.from(document.querySelectorAll('%s'));
                        for (let i = 0; i < boxes.length; i++) {
                            const c = boxes[i].className || '';
                            if (c.indexOf('ans-job-finished') > -1) continue;
                            if (skip.indexOf(i) > -1) continue;
                            boxes[i].scrollIntoView({block: 'center'});
                            return i;
                        }
                        return -1;
                    }""" % JOB_BOX_SELECTOR,
                    list(skip or ()),
                )
            except Exception:
                continue
            if idx is None or int(idx) < 0:
                continue
            # 只滚到可视区就走：具体怎么播/怎么读交给 play_video / read_document，
            # 这里乱点反而可能触发学习通的"还有任务点未完成"弹窗导致页面重载。
            page.wait_for_timeout(2000)
            if logger:
                logger.info("已切到本节第 %d 块任务点" % (int(idx) + 1))
            return int(idx)
        page.wait_for_timeout(1000)
    return -1


def click_node(node, logger) -> bool:
    """点击目录里的第 index 个任务点，进入其内容页。"""
    frame = node.get("frame")
    sel = node.get("selector")
    idx = node.get("index", 0)
    title = node.get("title", "")
    kid = (node.get("kid") or "").strip()
    if frame is None or not sel:
        return False
    try:
        if frame.is_detached():
            logger.warning("目录所在框架已失效，需重新识别：%s" % title)
            return False
    except Exception:
        pass

    # 新版目录：直接触发该节的 getTeacherAjax，不受滚动/遮挡影响
    if sel == "coursetree":
        if not kid:
            logger.warning("该任务点取不到章节 id，无法点击（下次重试）：%s" % title)
            return False
        try:
            if frame.evaluate(_CLICK_TREE_JS, kid):
                time.sleep(2.5)
                return True
            logger.warning("目录里找不到这个任务点（页面可能刚刷新）：%s" % title)
        except Exception as e:
            logger.warning("点击任务点失败「%s」：%s" % (title, str(e).split("\n")[0]))
        # 关键：不要退到 frame.locator("coursetree") 那个无效选择器上白等 30 秒
        return False

    try:
        loc = frame.locator(sel).nth(idx)
        loc.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    ok = False
    try:
        loc.click(timeout=8000)
        ok = True
    except Exception:
        # 元素可能被遮挡 / 不可见，用原生 click 兜底
        try:
            frame.locator(sel).nth(idx).evaluate("el => el.click()")
            ok = True
        except Exception as e:
            logger.warning("点击任务点失败「%s」：%s" % (title, e))

    if ok:
        # 等右侧内容 iframe 把视频/题目加载出来
        time.sleep(2.5)
    return ok


# ---------------------------------------------------------------------------
# 课程
# ---------------------------------------------------------------------------
def _goto_retry(page: Page, url: str, logger=None, attempts: int = 2, timeout: int = 45000) -> bool:
    """打开页面，失败自动降级重试。

    学习通偶尔会有资源卡住导致 domcontentloaded 一直等不到，
    这时退一步用 commit（只等导航提交）再自己等元素，比直接报失败靠谱。
    """
    last = None
    for _ in range(max(1, attempts)):
        for wait in ("domcontentloaded", "commit"):
            try:
                page.goto(url, wait_until=wait, timeout=timeout)
                return True
            except Exception as e:
                last = e
                continue
        page.wait_for_timeout(1500)
    if logger:
        logger.warning("打开页面失败（已重试）：%s" % str(last).split("\n")[0])
    return False


def open_course(page: Page, course: dict, logger) -> bool:
    """打开课程并进入“章节学习页”（带完整目录 #coursetree 的那一页）。

    课程的入口链接是 stucoursemiddle（课程首页），首页只有“待完成任务点”
    的链接列表、没有完成状态。真正带完成标记的目录在学习页里，
    所以这里顺手点进第一节，让目录渲染出来。
    """
    url = course.get("url")
    if not url:
        logger.warning("课程缺少链接：%s" % course.get("name", ""))
        return False
    if not _goto_retry(page, url, logger):
        return False
    page.wait_for_timeout(2500)

    # 从课程首页取第一节的链接
    href = None
    try:
        for a in page.locator("a[href*='studentstudy']").all()[:10]:
            h = a.get_attribute("href")
            if h:
                href = h
                break
    except Exception:
        href = None

    if not href:
        logger.info("课程《%s》里没有章节链接（可能是空课程）" % course.get("name", ""))
        course["_no_sections"] = True
        return True
    if not href.startswith("http"):
        href = "https://mooc1.chaoxing.com" + ("" if href.startswith("/") else "/") + href
    if not _goto_retry(page, href, logger):
        return False

    # 等章节目录渲染（异步）：新版 #coursetree 或老版目录标记任一出现即视为就绪，
    # 避免老版课程在“等 #coursetree”上空转满 25 秒。
    for _ in range(15):
        try:
            if page.locator("#coursetree .ncells").count():
                break
            # 老版目录常见标记：任一出现说明目录已渲染，无需再等 #coursetree
            if page.locator(
                ".catalog_item, .posCatalog_select, .catalogPoint, [onclick*='getTeacherAjax']"
            ).count():
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return True


# 兼容旧调用（prescan / 其他模块可能仍在用）
def list_chapters(page: Page, logger, debug: bool = False):
    return scan_catalog(page, logger, debug, timeout=12)


def open_chapter(page: Page, index: int, logger) -> bool:
    nodes = scan_catalog(page, logger, timeout=5)
    if index >= len(nodes):
        return False
    return click_node(nodes[index], logger)


def list_tasks(page: Page, logger, debug: bool = False):
    nodes = scan_catalog(page, logger, debug, timeout=5)
    return [
        {
            "text": n["title"],
            "type": "video" if n.get("has_job") else "other",
            "index": n["index"],
            "done": n["done"],
            "_node": n,
        }
        for n in nodes
    ]


def open_task(page: Page, index: int, logger) -> bool:
    nodes = scan_catalog(page, logger, timeout=5)
    for n in nodes:
        if n["index"] == index:
            return click_node(n, logger)
    return False


# ---------------------------------------------------------------------------
# 下一个任务点
# ---------------------------------------------------------------------------
def goto_next_task(page: Page, logger) -> bool:
    """优先点学习通自带的"下一节"，否则回目录点第一个未完成的。"""
    for sel in NEXT_BUTTONS:
        for f in _all_frames(page):
            try:
                loc = f.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    page.wait_for_timeout(2500)
                    logger.info("点击“下一节”")
                    return True
            except Exception:
                continue
    for n in scan_catalog(page, logger, timeout=3):
        if not n["done"]:
            if click_node(n, logger):
                logger.info("切换到下一个未完成任务点：%s" % n["title"])
                return True
    logger.warning("未找到“下一节”入口，可能本课程已学习完")
    return False


# ---------------------------------------------------------------------------
# 视频
# ---------------------------------------------------------------------------
def _iter_frames(page: Page):
    """遍历所有已打开标签页及其 iframe，用于跨标签页查找视频。"""
    try:
        pages = page.context.pages
    except Exception:
        pages = [page]
    for pg in pages:
        yield pg
        try:
            for f in pg.frames:
                yield f
        except Exception:
            pass


MEDIA_SELECTOR = "video, audio"

_MEDIA_INFO_JS = """
() => Array.from(document.querySelectorAll('video,audio')).map((v, i) => {
  let r = {width: 0, height: 0};
  try { r = v.getBoundingClientRect(); } catch (e) {}
  return {
    i: i,
    t: v.currentTime || 0,
    d: v.duration || 0,
    paused: !!v.paused,
    ended: !!v.ended,
    area: Math.max(0, (r.width || 0) * (r.height || 0)),
    visible: (r.width || 0) > 40 && (r.height || 0) > 40,
    src: (v.currentSrc || v.src || '').slice(0, 90)
  };
})
"""

_KICK_JS = """
(args) => {
  const list = document.querySelectorAll('video,audio');
  const v = list[args[0]];
  if (!v) return false;
  try { if (Math.abs((v.playbackRate || 1) - args[1]) > 0.01) v.playbackRate = args[1]; } catch (e) {}
  try { v.volume = 0; v.muted = true; } catch (e) {}
  if (v.paused || v.ended) {
    try { const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {}
  }
  return true;
}
"""

# 学习通播放器自带的播放按钮（实测必须点 video.js 的大按钮才会真正加载视频）
PLAY_BUTTONS = [
    ".vjs-big-play-button",
    "#videoPlay",
    ".ans-video-play",
    ".videoPlay",
    ".play-btn",
    "[class*='play'][class*='btn']",
]


def _frame_visible(frame) -> bool:
    """判断某个 iframe 当前是否真的显示在页面上。

    学习通会把整章所有任务点（视频/文档）都预加载成独立 iframe，
    隐藏的那些里也有 <video>（duration 还是 NaN）。不判断可见性的话，
    会把隐藏视频当成"当前任务点"，于是播放进度永远不动。
    """
    try:
        el = frame.frame_element()
    except Exception:
        # 主文档没有 iframe 元素，视为可见
        return True
    try:
        box = el.bounding_box()
    except Exception:
        return False
    if not box:
        return False
    return box.get("width", 0) > 120 and box.get("height", 0) > 80


def _media_infos(page: Page, only_visible: bool = True, frames=None):
    """收集所有标签页/iframe 里的 video/audio 状态（默认只看可见的）。

    frames：只在这些 frame 里找（用于把范围锁死在"当前这一块任务点"）。
    """
    out = []
    for f in (frames if frames else _iter_frames(page)):
        try:
            if not f.query_selector(MEDIA_SELECTOR):
                continue
        except Exception:
            continue
        if only_visible and not _frame_visible(f):
            continue
        try:
            infos = f.evaluate(_MEDIA_INFO_JS)
        except Exception:
            continue
        for it in infos or []:
            it["frame"] = f
            out.append(it)
    return out


def _pick_best(infos):
    """从多个 video 里挑“最像当前任务点”的那个（可见、有尺寸、时长已知优先）。"""
    if not infos:
        return None

    def score(it):
        s = 0.0
        if it.get("visible"):
            s += 1000
        d = it.get("d") or 0
        if d == d and d > 0:      # 排除 NaN
            s += 300
        s += min(it.get("area") or 0, 600000) / 1000.0
        if (it.get("t") or 0) > 0:
            s += 100              # 有续播位置的多半是当前任务点
        return s

    return max(infos, key=score)


def _best_media(page: Page):
    """挑“正在播的那个”：可见、有尺寸、时长已知的优先。"""
    return _pick_best(_media_infos(page))


def _find_video(page: Page, timeout: int = 30):
    """在任意标签页/iframe 中查找可用的 video/audio，最多等 timeout 秒（常慢加载）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        best = _best_media(page)
        if best:
            return best["frame"], best
        time.sleep(2)
    return None, None


def _has_media(page: Page) -> bool:
    """快速判断当前页面是否已有“可见的”video/audio（不做等待）。"""
    return bool(_media_infos(page, only_visible=True))


# 判断"当前正在播的这个任务点"是否已被学习通标记完成。
# greenligth() 会给视频 iframe 的父容器加 ans-job-finished，这是最准的信号；
# 同一个章节里其它已完成的块也带这个 class，所以必须只认当前这一块。
_JOB_BOX_DONE_JS = """
() => {
  try {
    let el = window.frameElement;
    for (let i = 0; i < 8 && el; i++) {
      el = el.parentElement;
      if (!el) break;
      const c = el.className || '';
      if (typeof c === 'string' && c.indexOf('ans-attach-ct') > -1) {
        if (c.indexOf('ans-job-finished') > -1) return true;
        if (el.querySelector && el.querySelector('.ans-job-finished')) return true;
        return false;
      }
    }
  } catch (e) {}
  return null;
}
"""


_TAGGED_BOX_DONE_JS = """
() => {
  try {
    const ifr = document.querySelector('[data-wb-target="1"]');
    if (!ifr) return null;
    let el = ifr;
    for (let i = 0; i < 8 && el; i++) {
      const c = el.className || '';
      if (typeof c === 'string' && c.indexOf('ans-attach-ct') > -1) {
        if (c.indexOf('ans-job-finished') > -1) return true;
        if (el.querySelector && el.querySelector('.ans-job-finished')) return true;
        return false;
      }
      el = el.parentElement;
    }
  } catch (e) {}
  return null;
}
"""


def _tagged_job_done(page: Page):
    """只判断「当前被 data-wb-target 标记的这一块」是否已完成。

    返回 True/False；找不到标记块时返回 None。
    多任务点的小节必须用这个，不能用整节目录圆点兜底——
    否则整节一变蓝，没看过的视频也会被当成已完成，下次又重播。
    """
    for f in _iter_frames(page):
        try:
            if "knowledge/cards" not in (f.url or ""):
                continue
            hit = f.evaluate(_TAGGED_BOX_DONE_JS)
            if hit is not None:
                return bool(hit)
        except Exception:
            continue
    return None


def _current_job_finished(page: Page, node=None, cur_info=None, job_index=-1) -> bool:
    """当前这个任务点是否已完成（视频看到 90% 就会变成 True）。"""
    # 有明确块序号时，只认这一块自己的 ans-job-finished
    if job_index is not None and job_index >= 0:
        hit = _tagged_job_done(page)
        if hit is not None:
            return bool(hit)
        # 标记块找不到（页面正在重载）→ 不判定完成，宁可多等一会
        return False

    frame = (cur_info or {}).get("frame")
    if frame is not None:
        try:
            hit = frame.evaluate(_JOB_BOX_DONE_JS)
            if hit is True:
                return True
        except Exception:
            pass
    # 兜底：整节都已变蓝（仅单任务点/无法定位块序号时使用）
    if node is not None:
        # 多任务点的节绝不能整节兜底：会把没看的块误判成已完成 → 下次重播
        try:
            if int(node.get("pending") or 0) > 1:
                return False
        except Exception:
            pass
        try:
            return bool(_node_done(page, node))
        except Exception:
            return False
    return False


def play_video(
    page: Page,
    speed: float,
    max_wait: int,
    logger,
    find_timeout: int = 30,
    node=None,
    hold_seconds: float = 5.0,
    done_check_every: float = 8.0,
    job_index: int = -1,
    auto_speed: bool = True,
    speed_min: float = 1.0,
) -> bool:
    """播放当前任务点的视频，直到"任务点已完成"或播完为止。

    关键：学习通的完成条件是"观看时长 ≥ 总时长的 90%"，一旦达标目录就变蓝，
    这时没必要再等最后那 10%。看到完成信号后停留 hold_seconds 秒（让上报落库），
    就直接返回，主流程会自动切到下一个任务点。

    find_timeout：查找 video 元素的等待秒数。非视频任务点（文档/PPT）用短超时。

    自适应倍速（auto_speed）：目标倍速是 speed，下限是 speed_min。
    - 检测到"无进度"或验证码 → 立即降一档（高倍速是卡顿/风控的常见诱因）
    - 连续稳定播放 90 秒 → 升一档，直到回到目标倍速
    - 每档 0.25x，只在 [speed_min, speed] 之间浮动
    """
    # 锁定"当前这一块任务点"：同一节里其它块也各有各的 <video>，
    # 不锁定的话会去播上一块（已完成），然后误判"已完成"直接跳过。
    target = job_frame(page, job_index) if job_index is not None and job_index >= 0 else None
    if target is not None and _tagged_job_done(page) is True:
        # 这一块本身已经完成：直接跳过，绝不重复播放
        logger.info("该任务点块已完成，跳过（不重复播放）")
        return True
    if target is not None:
        deadline = time.time() + max(5, find_timeout)
        info = None
        while time.time() < deadline:
            infos = _media_infos(page, frames=[target])
            info = _pick_best(infos)
            if info:
                break
            page.wait_for_timeout(2000)
        frame = target
    else:
        frame, info = _find_video(page, timeout=find_timeout)
    if not info and target is not None:
        # 这一块还没把视频加载出来（懒加载）：退回全页找，找到就直接用
        logger.info("该任务点块里暂时没有视频元素，改为全页查找")
        target = None
        frame, info = _find_video(page, timeout=find_timeout)
    if not info:
        logger.warning("未找到视频/音频元素（该任务点可能不是音视频，或内容在独立弹窗中）")
        return False

    # ---- 自适应倍速：目标 speed；卡顿/验证码自动降，稳定后升回去 ----
    speed_max = max(0.5, float(speed or 1.0))
    speed_min = min(speed_max, max(0.5, float(speed_min or 1.0)))
    auto_speed = bool(auto_speed) and (speed_max - speed_min) > 0.01
    cur_speed = speed_max
    stable_since = time.time()   # 上次升降速 / 起播的时刻

    speed_note = ("%g–%gx（自适应）" % (speed_min, speed_max)) if auto_speed else ("%gx" % cur_speed)
    logger.info("找到音视频，开始播放（%s，时长 %.0f 秒）" % (speed_note, float(info.get("d") or 0)))
    try:
        page.bring_to_front()
    except Exception:
        pass

    def kick(cur_info, rate=None) -> bool:
        """设置倍速并确保处于播放状态（浏览器会暂停后台/静音的标签页）。"""
        r = cur_speed if rate is None else float(rate)
        try:
            return bool(
                cur_info["frame"].evaluate(_KICK_JS, [cur_info.get("i", 0), r])
            )
        except Exception:
            return False

    def click_play(cur_info) -> bool:
        """学习通用的是 video.js：必须点它自己的播放按钮才会真正加载/起播。

        直接对 <video> 调 play() 时，视频的 duration 还是 NaN、readyState=0，
        所以永远播不动（表现为"视频长时间无进度"）。
        """
        f = cur_info.get("frame")
        if f is None:
            return False
        for sel in PLAY_BUTTONS:
            try:
                loc = f.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=4000, force=True)
                    return True
            except Exception:
                continue
        try:
            loc = f.locator(MEDIA_SELECTOR).nth(cur_info.get("i", 0))
            loc.click(timeout=4000, force=True)
            return True
        except Exception:
            pass
        try:
            box = f.locator(MEDIA_SELECTOR).nth(cur_info.get("i", 0)).bounding_box()
            if box and box["width"] > 40:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                return True
        except Exception:
            pass
        return False

    cur = info
    if not click_play(cur):
        kick(cur)
    page.wait_for_timeout(2500)

    start = time.time()
    last_progress = time.time()
    last_time = -1.0
    last_rate_fix = 0.0
    stuck_rounds = 0
    logged_ready = False
    last_done_check = time.time()
    first_t = -1.0

    prog = {}         # (frame, 序号) -> 上一次看到的播放位置
    active_key = None  # 真正在推进的那个视频（判断"任务点完成"必须只认它）

    while time.time() - start < max_wait:
        # 学习通长时间观看会弹验证码；弹出期间视频是暂停的，
        # 必须先识别并提交，再继续跑进度判断，否则会被误判成"无进度"。
        # 只查「顶层页 + 当前视频 frame」：课程页预加载的其它任务点 iframe
        # 可能处于冻结状态，逐个 evaluate 会把整轮卡死。
        cap_frames = [page]
        if frame is not None and all(f is not frame for f in cap_frames):
            cap_frames.append(frame)
        if handle_captcha(page, logger, frames=cap_frames):
            # 刚触发过风控验证，高倍速容易再次触发：先降到最低档观察
            if auto_speed and cur_speed > speed_min + 0.01:
                cur_speed = speed_min
                logger.info("倍速自适应：验证码后降到 %gx，稳定后再升回来" % cur_speed)
                stable_since = time.time()
            try:
                kick(cur)  # 验证码弹出时视频被暂停，通过后立即恢复播放
            except Exception:
                pass
            last_progress = time.time()
            last_time = -1.0
            stuck_rounds = 0
            page.wait_for_timeout(1500)
            continue
        # 一节里所有任务点都预加载成了独立视频，只有"正在播的那个"currentTime 会走。
        # 所以这里靠进度变化锁定当前任务点，而不是靠位置或尺寸猜。
        # 注意：学习通会重建 <video>，所以每轮都必须按当前 DOM 重新取对象，
        # 不能缓存上一轮的快照（缓存下来就会一直读到冻结的旧进度）。
        infos = _media_infos(page, frames=[target] if target is not None else None)
        by_key = {(id(it.get("frame")), it.get("i")): it for it in infos}
        for key, it in by_key.items():
            prev_t = prog.get(key)
            t_now = it.get("t") or 0
            if prev_t is not None and t_now > prev_t + 0.3:
                active_key = key
            prog[key] = t_now
        best = _pick_best(infos)
        cur = by_key.get(active_key) or best or cur

        try:
            t = float(cur.get("t") or 0)
            d = float(cur.get("d") or 0)
            ended = bool(cur.get("ended"))
            paused = bool(cur.get("paused"))
        except Exception:
            t, d, ended, paused = 0.0, 0.0, False, True

        if first_t < 0 and t == t and t > 0:
            first_t = t

        if not logged_ready and d == d and d > 0:
            logger.info("视频已开始播放（总时长 %.0f 秒，从 %.0f 秒继续）" % (d, t))
            logged_ready = True

        if ended or (d == d and d > 0 and t >= d - 1.5):
            logger.info("视频播放完成（%.0f/%.0f 秒）" % (t, d))
            return True

        # 看到"任务点已完成"就收工：达标后不必再等剩下的进度。
        # 必须"真的播起来并且有推进"才认，否则页面里别的已完成块会误触发。
        if (
            time.time() - last_done_check >= done_check_every
            and time.time() - start > 20
            and first_t >= 0
            and t > first_t + 5
        ):
            last_done_check = time.time()
            if _current_job_finished(page, node, cur, job_index):
                logger.info(
                    "任务点已标记完成（当前 %.0f/%.0f 秒），停留 %.0f 秒后切下一个"
                    % (t, d, hold_seconds)
                )
                page.wait_for_timeout(int(max(0.0, hold_seconds) * 1000))
                return True

        # 进度变化即视为正常
        if t > last_time + 0.5:
            last_time = t
            last_progress = time.time()
            stuck_rounds = 0
            # 连续稳定播放 90 秒且还没到目标倍速 → 升一档
            if (
                auto_speed
                and cur_speed < speed_max - 0.01
                and time.time() - stable_since >= 90
            ):
                cur_speed = min(speed_max, round(cur_speed + 0.25, 2))
                logger.info("倍速自适应：播放稳定，升到 %gx" % cur_speed)
                kick(cur)
                stable_since = time.time()
        else:
            idle = time.time() - last_progress
            # 稳定性优先：加载慢/短暂卡顿很常见，20 秒没推进才算可疑，
            # 避免刚缓冲两下就去点播放按钮反而把播放器点死。
            if idle > 20:
                stuck_rounds += 1
                logger.info(
                    "视频无进度（%.0fs/%.0fs，paused=%s），第 %d 次尝试恢复"
                    % (t, d, paused, stuck_rounds)
                )
                # 高倍速是"时间不走"的常见诱因：先降一档再恢复播放
                if auto_speed and cur_speed > speed_min + 0.01:
                    cur_speed = max(speed_min, round(cur_speed - 0.25, 2))
                    logger.info("倍速自适应：无进度，降到 %gx" % cur_speed)
                    stable_since = time.time()
                # 后台/最小化的窗口会被浏览器限流，表现就是 paused=False 但时间不动。
                # 先做温和恢复（拉到前台 + 脚本 play），别急着点画面——
                # 对正在播放的视频点一下等于把它暂停，反而把播放器点死。
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                if paused:
                    click_play(cur)             # 确实暂停了才点播放按钮
                else:
                    kick(cur)
                    # 温和恢复 3 次仍无效 → 播放器可能"假死"（paused=False 但时间不动）。
                    # 此时已确认近 1 分钟没有任何推进，点一下播放按钮把它唤醒；
                    # 正常播放中的视频不会走到这里（它有进度，不会算作无进度）。
                    if stuck_rounds >= 3:
                        click_play(cur)
                last_progress = time.time()
                # 连续 6 次都起不来：放弃，交回上层（不误记完成）
                if stuck_rounds >= 6:
                    logger.warning(
                        "视频始终没有播起来（当前 %.0f/%.0f 秒）。"
                        "可能是该任务点已完成、被弹窗遮挡，或需要手动点一下播放。"
                        % (t, d)
                    )
                    return False

        # 学习通播放器会自己把倍速改回 1.0，定期纠正（间隔长一点，少打扰播放器）
        if time.time() - last_rate_fix > 15:
            kick(cur)
            last_rate_fix = time.time()

        time.sleep(3)

    logger.warning("视频超时未完成（已等待 %d 秒）" % max_wait)
    return False


# ---------------------------------------------------------------------------
# 文档 / PPT / PDF / 图片类任务点
# ---------------------------------------------------------------------------
# 这类任务点没有播放进度，学习通靠"页面停留时长（阅读时长）"判定完成，
# 完成后目录里同样会出现 .icon_yiwanc。所以策略是：打开后保持在前台、
# 周期性轻微滚动（让计时脚本认为人在看），再回目录核对是否已标记完成。
DOC_SELECTORS = [
    "#panView",              # PPT / 文档在线预览（学习通标准容器）
    ".ans-attach-ct",        # 附件类任务点容器
    ".ans-attach-online",
    "#docContent",
    ".docContent",
    "#pdfContainer",
    "#viewerContainer",      # pdf.js
    ".imgContainer",
    "#imgList",
    "embed[type='application/pdf']",
    'iframe[src*="pdf"]',
]

DOC_URL_HINTS = ("ananas", "knowledge/cards", "doc", "ppt", "pdf", "attach", "image")

_DOC_JS = """
(args) => {
  const sels = args[0];
  const allowHeuristic = args[1];
  const hit = [];
  for (const s of sels) {
    try {
      const n = document.querySelectorAll(s).length;
      if (n) hit.push(String(s).replace(/[#.\\[\\]'=\"*]/g, '') + '×' + n);
    } catch (e) {}
  }
  if (allowHeuristic) {
    let big = 0;
    document.querySelectorAll('img').forEach(im => {
      const w = im.naturalWidth || im.width || 0;
      const h = im.naturalHeight || im.height || 0;
      if (w >= 300 && h >= 200) big++;
    });
    if (big) hit.push('大图×' + big);
    if (document.querySelector('canvas')) hit.push('canvas');
  }
  return hit.join(' ');
}
"""

_REQUIRED_TIME_RE = re.compile(r"需(?:要)?(?:阅读|学习|观看|完成)[^0-9]{0,8}(\d{1,3})\s*分钟")


def _doc_candidates(page: Page):
    """可能承载文档内容的 frame（含其它标签页），越像内容页越靠前。"""
    items = []
    for f in _iter_frames(page):
        try:
            url = (f.url or "").lower()
        except Exception:
            url = ""
        if url.startswith("about:"):
            continue
        score = 0
        if any(h in url for h in CATALOG_URL_HINTS):
            score += 30           # 目录框架靠后
        if any(h in url for h in DOC_URL_HINTS):
            score -= 10
        # 隐藏的 iframe 很可能是别的任务点（学习通会预加载整章），排后面
        if not _frame_visible(f):
            score += 20
        items.append((score, f))
    items.sort(key=lambda x: x[0])
    return [f for _, f in items]


def _doc_evidence(page: Page):
    """返回 (frame, 证据串)；没有文档特征时返回 (None, "")。"""
    cands = _doc_candidates(page)
    for f in cands:
        # 直接以 PDF / 图片形式打开的标签页（浏览器内置阅读器，DOM 里没有容器特征）
        try:
            url = (f.url or "").lower()
        except Exception:
            url = ""
        if url.endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            return f, "PDF/图片页面"
        # 主页面里的图片多是封面/横幅，不采信"大图"启发式
        allow_heuristic = not _is_main_frame_of(page, f)
        try:
            ev = f.evaluate(_DOC_JS, [DOC_SELECTORS, allow_heuristic])
        except Exception:
            continue
        if ev:
            return f, ev
    return None, ""


def has_task_media(page: Page) -> bool:
    """当前任务点是否已有可播放的音视频（供"文档其实是视频"时回退）。"""
    return _has_media(page)


def media_src(page: Page) -> str:
    """当前可见视频的地址，用来判断"页面是不是已经自动切到下一个任务点"。"""
    best = _best_media(page)
    return (best or {}).get("src") or ""


def detect_task_kind(page: Page, timeout: int = 15) -> str:
    """判断刚点开的任务点类型：video / doc / unknown。

    先看有没有 video/audio（音频任务点也用 <video>/<audio> 承载），
    再看有没有文档/图片容器；都没有则返回 unknown（不做任何标记，下次重试）。
    文档特征需连续两次命中才认定，避免视频加载慢时把封面图当成文档。
    """
    deadline = time.time() + max(0, timeout)
    doc_hits = 0
    while True:
        if _has_media(page):
            return "video"
        frame, _ev = _doc_evidence(page)
        if frame is not None:
            doc_hits += 1
            if doc_hits >= 2:
                return "doc"
        if time.time() >= deadline:
            break
        page.wait_for_timeout(1500)
    return "unknown"


def _required_seconds(frame):
    """从文档页文本里读"需阅读 N 分钟"的要求；读不到返回 0。"""
    try:
        text = frame.inner_text("body")
    except Exception:
        return 0
    m = _REQUIRED_TIME_RE.search(text or "")
    if not m:
        return 0
    try:
        return max(60, int(m.group(1)) * 60)
    except Exception:
        return 0


def _node_done(page: Page, node) -> bool:
    """重新扫描目录，判断指定任务点是否已被标记完成（静默，不刷日志）。"""
    title = (node or {}).get("title")
    chapter = (node or {}).get("chapter")
    if not title:
        return False
    for n in scan_catalog(page, None, timeout=3, quiet=True):
        if n["title"] == title and n.get("chapter") == chapter:
            return bool(n["done"])
    return False


def _nudge(frames, down: bool):
    """轻微滚动 + 鼠标移动，让学习通的阅读计时认为页面在被阅读。"""
    for f in frames[:2]:
        try:
            f.evaluate(
                """(down) => {
                    window.scrollBy(0, down ? 480 : -480);
                    document.dispatchEvent(new MouseEvent('mousemove',
                        {bubbles: true, clientX: 320, clientY: 360}));
                }""",
                down,
            )
        except Exception:
            continue


# ---------------------------------------------------------------------------
# 滚动到底 → 触发学习通的"任务点完成"上报
# ---------------------------------------------------------------------------
# 实测（2026-10 大学物理 PPT）：pdf/doc 模块只有把 #panView 的内容滚到底部，
# 才会向服务器上报"任务点完成"——源码 ananas/modules/pdf/index.html 的 bindScroll：
#     scrollTop > scrollHeight - clientHeight - 100  →  finishJob()
# 所以"点进去停两秒"永远不会完成，必须真的把内容滚到底。
_SCROLL_INFO_JS = """
() => {
  const d = document.documentElement;
  const b = document.body || d;
  const max = Math.max(
    (d.scrollHeight || 0) - (d.clientHeight || 0),
    (b.scrollHeight || 0) - (b.clientHeight || 0),
    0
  );
  return {max: max, top: d.scrollTop || b.scrollTop || 0, h: d.clientHeight || 0};
}
"""

_SCROLL_TO_JS = """
(y) => {
  window.scrollTo(0, y);
  try {
    document.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX: 320, clientY: 360}));
  } catch (e) {}
  const d = document.documentElement;
  return d.scrollTop || (document.body ? document.body.scrollTop : 0);
}
"""

# 文档内容可能在这些 frame 里（pan-yz 是 PPT/PDF 的内层阅读器）
DOC_SCROLL_HINTS = ("pan-yz", "screen/file", "ananas/modules", "pdf", "office", "viewer", "doc")


def _scrollable_doc_frames(page: Page):
    """找出"能滚动即代表阅读进度"的文档 frame，按可滚距离从大到小排。

    PPT/PDF 的内层阅读器（pan-yz）可滚距离最大，排在最前面；
    外层模块 frame 也有几十像素残差，滚它无害。
    """
    items = []
    for f in _iter_frames(page):
        try:
            url = f.url or ""
        except Exception:
            continue
        if not url or url.startswith("about:"):
            continue
        try:
            info = f.evaluate(_SCROLL_INFO_JS)
        except Exception:
            continue
        total = int((info or {}).get("max") or 0)
        if total <= 60:
            continue
        # 文档相关 frame 优先，其它（页面本身）略降权
        rank = total + (2000 if any(h in url for h in DOC_SCROLL_HINTS) else 0)
        items.append((rank, f, total))
    items.sort(key=lambda x: -x[0])
    return [(f, total) for _, f, total in items]


def _scroll_to_bottom(frames, page: Page, pause: float = 0.45, steps: int = 12) -> int:
    """把文档从顶部逐屏滚到底（带停顿，模拟阅读），触发完成上报。

    返回实际滚过的屏数；0 表示没找到可滚动内容（单屏文档/纯图片）。
    """
    scrolled = 0
    for f, total in frames[:2]:
        if total <= 60:
            continue
        try:
            f.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            continue
        page.wait_for_timeout(300)
        step_pause = max(0.2, min(pause, 8.0 / max(steps, 1)))
        ok = True
        for i in range(1, steps + 1):
            try:
                f.evaluate(_SCROLL_TO_JS, int(total * i / steps))
            except Exception:
                ok = False
                break
            page.wait_for_timeout(int(step_pause * 1000))
        if not ok:
            continue
        # 最后贴底一次：确保 scrollTop 越过 "scrollHeight - clientHeight - 100"
        try:
            f.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        except Exception:
            pass
        page.wait_for_timeout(400)
        scrolled += steps
    return scrolled


def read_document(
    page: Page,
    node,
    logger,
    max_seconds: int = 180,
    poll: int = 3,
    dwell_seconds: float = 2.0,
    verify_seconds: int = 20,
    assume_done: bool = False,
    reopen=None,
) -> bool:
    """处理文档 / PPT / PDF / 图片类任务点，直到学习通真的标记完成。

    正确做法（已实测）：把内容逐屏滚到底 → 学习通上报完成 → 目录圆点变蓝。
    只"停留两秒"是不够的，这也是之前"跳过了但任务点没完成"的根因。

    reopen：可选回调，用于"滚到底仍没完成"时重新进入该任务点再试一次。
    assume_done：true 时，滚到底 + 核对后仍无完成标记也当完成（默认关闭）。
    """
    title = (node or {}).get("title", "")
    try:
        page.bring_to_front()
    except Exception:
        pass

    start = time.time()
    deadline = start + max(20, int(max_seconds))
    attempt = 0
    while time.time() < deadline:
        if handle_captcha(page, logger, frames=[page]):
            page.wait_for_timeout(1200)
            continue
        attempt += 1

        frame, evidence = _doc_evidence(page)
        if frame is not None:
            logger.info("识别到文档/PPT 内容（%s）" % evidence)

        # ---- 1. 滚到底（这是学习通判定完成的关键动作） ----
        scrolls = _scrollable_doc_frames(page)
        if scrolls:
            top, total = scrolls[0]
            logger.info("文档可滚动 %d 像素，逐屏滚到底…" % total)
            _scroll_to_bottom(scrolls, page)
            logger.info("已滚到底，等待学习通标记完成…")
        else:
            logger.info("文档不可滚动（单屏/纯图片），停留 %.0f 秒后核对…" % max(1.0, dwell_seconds))
            _nudge(_doc_candidates(page)[:2], True)
            page.wait_for_timeout(int(max(1.0, dwell_seconds) * 1000))

        # ---- 2. 核对目录状态 ----
        hard = time.time() + max(8, verify_seconds)
        while time.time() < hard:
            if handle_captcha(page, logger, frames=[page]):
                page.wait_for_timeout(600)
                continue
            if _node_done(page, node):
                logger.info("文档/PPT 任务点已完成（用时 %.0f 秒）" % (time.time() - start))
                return True
            # 前 20 秒内若冒出音视频，说明判错类型了，交回上层按视频处理
            if time.time() - start < 20 and _has_media(page):
                logger.info("该任务点实际出现了视频/音频，改按音视频处理")
                return False
            page.wait_for_timeout(max(1000, poll * 1000))

        if assume_done:
            logger.info(
                "文档/PPT「%s」滚到底后仍没看到完成标记，按“打开即完成”处理" % title
            )
            return True

        # ---- 3. 还没完成：重新进入一次（学习通每个页面只上报一次，需重开） ----
        if reopen is not None and attempt < 3 and time.time() < deadline - 20:
            logger.info(
                "「%s」滚到底后学习通仍未标记完成，重新进入该任务点再试（第 %d 次）"
                % (title, attempt + 1)
            )
            try:
                reopen()
                page.wait_for_timeout(5000)
            except Exception:
                pass
            continue
        break

    logger.warning(
        "文档/PPT 任务点「%s」浏览完后学习通仍未标记完成，本轮不记进度（下次运行会重试）。"
        "如果反复出现，把 data 目录发我。" % title
    )
    return False


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------
def _dump(page: Page, logger=None, tag: str = "debug"):
    """把主页面和所有 iframe 的 DOM 存到 data/，并打印 class 统计，便于校准选择器。"""
    out_dir = os.path.join(BASE_DIR, "data")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        return

    for i, f in enumerate(_all_frames(page)):
        try:
            html = f.content()
        except Exception:
            continue
        if not html or len(html) < 200:
            continue
        name = "%s_frame%d.html" % (tag, i)
        try:
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fp:
                fp.write(html)
        except Exception:
            pass

    # 主页面留一份固定名，方便直接查看
    try:
        with open(os.path.join(out_dir, "debug.html"), "w", encoding="utf-8") as fp:
            fp.write(page.content())
    except Exception:
        pass

    if not logger:
        return
    logger.info("已保存页面快照到 data/%s_*.html（共 %d 个框架）" % (tag, len(_all_frames(page))))
    for i, f in enumerate(_all_frames(page)):
        try:
            stats = f.evaluate(
                """() => {
                    const m = {};
                    document.querySelectorAll('*').forEach(el => {
                        (el.className || '').toString().split(/\\s+/).forEach(c => {
                            if (c && c.length < 30) m[c] = (m[c] || 0) + 1;
                        });
                    });
                    return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 12);
                }"""
            )
        except Exception:
            continue
        try:
            url = (f.url or "")[:90]
        except Exception:
            url = ""
        logger.info(
            "  框架%d %s → %s"
            % (i, url, ", ".join("%s(%d)" % (c, n) for c, n in stats) or "无 class")
        )
