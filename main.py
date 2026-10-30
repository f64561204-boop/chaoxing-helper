import glob
import json
import re
import os
import shutil
import subprocess
import sys
import time

# 路径跟随可执行文件（打包成 exe 后也能正确找到配置/数据）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def p(*parts):
    return os.path.join(BASE_DIR, *parts)


def _pause_exit(msg: str):
    """出错时提示并等待；若运行环境无交互输入（如无人值守）则直接退出，避免崩溃。"""
    print(msg)
    try:
        input("按回车退出...")
    except (EOFError, OSError):
        pass


sys.path.insert(0, BASE_DIR)

from core.browser import (  # noqa: E402
    default_browsers_path,
    launch_browser,
    load_cookies,
    login_by_password,
    login_by_qrcode,
    new_page,
    save_cookies,
)
from core.courses import is_english_course, list_courses, select_target  # noqa: E402
from core.captcha import configure as configure_captcha  # noqa: E402
from core.player import (  # noqa: E402
    _job_boxes_count,
    _node_done,
    click_node,
    detect_task_kind,
    goto_next_job,
    goto_next_task,
    has_task_media,
    open_course,
    play_video,
    read_document,
    scan_catalog,
)
from core.quiz import answer_questions, detect_quiz  # noqa: E402
# 标题里带这些词的，多半是要答题的测验/作业，不能"打开就算完成"
QUIZ_TITLE_RE = re.compile(r"(测试|测验|作业|考试|练习|测评|自测|单元测|期中|期末)")

from utils.logger import get_logger  # noqa: E402
from utils.progress import Progress  # noqa: E402

try:
    from playwright.sync_api import Error as PlaywrightError  # noqa: E402
except Exception:  # pragma: no cover
    PlaywrightError = None


def _abs(path: str, default: str) -> str:
    """把配置里的相对路径统一转成基于 exe 所在目录的绝对路径。

    双击运行时当前工作目录不一定等于 exe 目录，若用相对路径，
    日志/进度/缓存可能被写到别的地方，导致"进度莫名丢失"。
    """
    value = path or default
    if not value:
        return value
    return value if os.path.isabs(value) else p(value)


def load_config():
    cfg_path = p("config.json")
    example = p("config.example.json")
    # 缺少 config.json 时自动从示例复制，降低双击即用门槛
    if not os.path.exists(cfg_path) and os.path.exists(example):
        shutil.copy(example, cfg_path)
    for name in (cfg_path, example):
        if os.path.exists(name):
            try:
                with open(name, encoding="utf-8-sig") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    "配置文件 %s 格式有误（第 %d 行附近）：%s\n"
                    "常见原因：少了逗号、多了尾逗号、用了中文引号。"
                    % (os.path.basename(name), e.lineno, e.msg)
                )
    raise RuntimeError("未找到 config.json，请先复制 config.example.json 为 config.json 并填写")


def normalize_paths(cfg):
    """统一解析所有文件路径，避免写错位置。"""
    cfg.setdefault("account", {})
    cfg.setdefault("progress", {})
    cfg.setdefault("log", {})
    cfg.setdefault("ai", {})
    cfg["account"]["cookie_file"] = _abs(cfg["account"].get("cookie_file"), "data/cookies.json")
    cfg["progress"]["file"] = _abs(cfg["progress"].get("file"), "data/progress.json")
    cfg["log"]["file"] = _abs(cfg["log"].get("file"), "data/run.log")
    cfg["ai"]["cache_file"] = _abs(cfg["ai"].get("cache_file"), "answers/ai_cache.json")
    return cfg


def ensure_browser(logger, browser_channel=None):
    """首次运行时检测并下载 Chromium 浏览器内核（需联网一次）。使用系统 Edge 则跳过。"""
    if (browser_channel or "").lower() in ("edge", "msedge"):
        return
    try:
        cache = default_browsers_path()
        if glob.glob(os.path.join(cache, "chromium*")):
            return
        if getattr(sys, "frozen", False):
            # 打包后的 exe 里没有 pip/playwright 命令行，无法自行下载内核
            logger.warning(
                "未检测到 Chromium 内核，且打包版无法自动下载。"
                "请把 config.json 的 player.browser 改为 \"edge\"（使用系统 Edge）。"
            )
            return
        logger.info("首次运行：正在下载 Chromium 浏览器内核（需联网，约 1~2 分钟）...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
        )
    except Exception as e:
        logger.warning("浏览器内核准备失败：%s" % e)


def build_answerer(cfg, logger):
    quiz_cfg = cfg.get("quiz", {})
    ai_cfg = cfg.get("ai", {})
    if not (quiz_cfg.get("enabled", True) and ai_cfg.get("enabled", True)):
        logger.info("AI 答题未启用（quiz.enabled 或 ai.enabled 为 false）")
        return None
    key = (ai_cfg.get("api_key") or "").strip()
    if not key or "填写" in key or key in ("sk-test", "your-key"):
        logger.warning(
            "AI 答题已跳过：config.json 的 ai.api_key 未填写。"
            "视频会自动播完，但题目不会被自动作答。"
        )
        return None
    from core.answerer import AIAnswerer

    logger.info("AI 答题已启用：%s / %s" % (ai_cfg.get("base_url"), ai_cfg.get("model")))
    return AIAnswerer(ai_cfg)


# ---------------------------------------------------------------------------
# 模式一：接管学习通 PC 客户端（Electron + Chromium，CDP 协议）
# ---------------------------------------------------------------------------
def run_cdp_mode(cfg, logger, answerer):
    from core.app import connect, ensure_app, find_content_page, wait_for_course_page

    app_cfg = cfg.get("app", {})
    player_cfg = cfg.get("player", {})
    quiz_cfg = cfg.get("quiz", {})
    speed = player_cfg.get("speed", 1.5)
    max_wait = player_cfg.get("max_wait_seconds", 3600)
    auto_submit = quiz_cfg.get("auto_submit", True)

    ensure_app(logger, app_cfg)
    pw, browser = connect(int(app_cfg.get("debug_port", 9222)))
    try:
        page = wait_for_course_page(
            browser, logger, int(app_cfg.get("wait_for_course_seconds", 300))
        )
        logger.info("已接管课程页面，开始自动学习：播放视频 / AI 答题 / 自动提交")
        noop = 0
        while True:
            page = find_content_page(browser, logger)
            if not page:
                logger.warning("未检测到课程页面，助手暂停，等待你回到课程…")
                time.sleep(5)
                continue

            qframe = detect_quiz(page)
            if qframe:
                if answerer:
                    ok, q, fail = answer_questions(qframe, answerer, auto_submit, logger)
                    logger.info("本次答题：成功 %d / 共 %d，失败 %d" % (ok, q, fail))
                else:
                    logger.warning("检测到测验，但 AI 答题未启用，已跳过（不自动提交）")
                page.wait_for_timeout(2000)
                goto_next_task(page, logger)
                noop = 0
            else:
                played = play_video(page, speed, max_wait, logger)
                if played:
                    page.wait_for_timeout(3000)
                    goto_next_task(page, logger)
                    noop = 0
                else:
                    # 非视频/未加载出视频：可能是文档阅读任务或仍在缓冲，尝试跳过
                    logger.info("当前页面非视频或视频未就绪，尝试切换到下一任务点")
                    goto_next_task(page, logger)
                    noop += 1
                    if noop > 8:
                        logger.warning(
                            "连续多次未能推进，疑似无更多可处理内容，助手退出。可手动确认后重开。"
                        )
                        break
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n用户中断。学习通的登录态与进度由客户端自身保存，下次运行可续。")
    except Exception as e:
        logger.warning("接管过程出现异常：%s" % e)
    finally:
        # 注意：不要 browser.close()，否则会关闭学习通客户端本身
        try:
            pw.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 课程预扫描：处理前先检测"未观看"章节，并输出检测报告
# ---------------------------------------------------------------------------
def _node_key(course_name, node):
    """任务点的续跑标识：课程/章|节。用标题而非序号，避免页面顺序变化导致进度错位。"""
    chapter = (node.get("chapter") or "").strip()
    title = (node.get("title") or "").strip()
    return "%s/%s|%s" % (course_name, chapter, title) if chapter else "%s/%s" % (course_name, title)


# ---------------------------------------------------------------------------
# 任务点类型记忆：第一次识别后记下来，下次预扫描就能直接报告"哪些是文档/图片"
# ---------------------------------------------------------------------------
def _kinds_file():
    return p("data", "task_kinds.json")


def _load_kinds():
    try:
        with open(_kinds_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_kind(key, kind):
    if not key or not kind:
        return
    data = _load_kinds()
    if data.get(key) == kind:
        return
    data[key] = kind
    try:
        os.makedirs(os.path.dirname(_kinds_file()), exist_ok=True)
        with open(_kinds_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _kinds_label(name, nodes, kinds):
    """按历史记忆统计这门课的任务点类型。"""
    video = doc = quiz = known = 0
    for n in nodes:
        k = kinds.get(_node_key(name, n))
        if not k:
            continue
        known += 1
        if k == "video":
            video += 1
        elif k == "doc":
            doc += 1
        elif k == "quiz":
            quiz += 1
    if not known:
        return ""
    parts = []
    if video:
        parts.append("视频/音频 %d" % video)
    if doc:
        parts.append("文档/图片 %d" % doc)
    if quiz:
        parts.append("测验 %d" % quiz)
    unknown = len(nodes) - known
    if unknown:
        parts.append("待识别 %d" % unknown)
    return "（已识别：%s）" % "、".join(parts) if parts else ""


def prescan_and_report(page, progress, candidates, english_courses, logger, cfg):
    """进入逐课程处理前的预扫描：检测每门课程的未观看任务点，输出检测报告。

    candidates：已剔除英语课程、并已应用用户关键词/排除词筛选的课程。
    english_courses：被默认英语名单跳过的课程（不打开、不检测，仅报告标注）。
    判定"未观看"：a) 学习通页面自带的完成标记 node["done"]；
    b) 本地 Progress 已记录完成（同一个 key）。
    返回「待处理」课程列表（有未观看任务点的课程）。
    """
    debug = cfg.get("debug", False)
    report_lines = []

    pending = []
    n_noneed = 0      # 已全部观看
    n_unknown = 0     # 目录识别失败
    n_failed = 0      # 进入课程失败
    for course in candidates:
        name = course.get("name", "")
        if not open_course(page, course, logger):
            n_failed += 1
            report_lines.append("课程《%s》：进入失败 → 跳过（下次重试）" % name)
            continue
        if course.get("_no_sections"):
            n_noneed += 1
            report_lines.append(
                "课程《%s》：没有任何章节/任务点（空课程）→ 跳过" % name
            )
            continue
        nodes = scan_catalog(page, logger, debug)
        tasks = [n for n in nodes if not n.get("no_job")]
        total = len(tasks)
        if total == 0:
            if nodes:
                n_noneed += 1
                report_lines.append(
                    "课程《%s》：%d 个章节都没有任务点 → 跳过" % (name, len(nodes))
                )
                continue
            # 关键：识别不到目录 ≠ 已看完。若判成"已全部观看"，所有课程都会被跳过，
            # 程序会打印"全部看完"直接退出，等于空跑。这里按"无法判断"强制尝试处理。
            n_unknown += 1
            report_lines.append(
                "课程《%s》：未能识别课程目录（页面未加载完或结构不匹配）→ 仍尝试处理" % name
            )
            pending.append(course)
            continue

        done = 0
        unviewed_titles = []
        for node in tasks:
            key = _node_key(name, node)
            # 以学习通页面上的状态圆点为准（它最准）；只有老版目录看不出状态时，
            # 才退回本地记录，避免"本地记了完成、页面其实还没做完"被永久跳过。
            if node["done"]:
                done += 1
                continue
            if not node.get("state") and progress.is_done(key):
                done += 1
                continue
            unviewed_titles.append(node["title"])
        unviewed = len(unviewed_titles)

        if unviewed > 0:
            known = _kinds_label(name, tasks, _load_kinds())
            preview = "、".join(unviewed_titles[:3])
            if len(unviewed_titles) > 3:
                preview += " 等"
            report_lines.append(
                "课程《%s》：任务点 %d 个（已完成 %d / 未完成 %d）%s → 待处理；未完成的有：%s"
                % (name, total, done, unviewed, known, preview)
            )
            pending.append(course)
        else:
            n_noneed += 1
            report_lines.append(
                "课程《%s》：任务点 %d 个，全部已完成 → 跳过" % (name, total)
            )

    for course in english_courses:
        report_lines.append("课程《%s》：已跳过（英语课程，不做任何操作）" % course.get("name", ""))

    logger.info("========== 课程检测结果 ==========")
    for line in report_lines:
        logger.info(line)
    logger.info("=================================")
    summary = "共检测到 %d 门课程：%d 门待处理、%d 门跳过（已全部完成）、%d 门英语跳过" % (
        len(candidates) + len(english_courses),
        len(pending),
        n_noneed,
        len(english_courses),
    )
    if n_unknown:
        summary += "、%d 门章节未能识别" % n_unknown
    if n_failed:
        summary += "、%d 门进入失败" % n_failed
    logger.info(summary)
    return pending, n_failed + n_unknown


# ---------------------------------------------------------------------------
# 模式二：浏览器模式（用系统 Edge / Chromium 打开学习通网页版）
# ---------------------------------------------------------------------------
def run_browser_mode(cfg, logger, answerer, scan_only=False):
    acc = cfg["account"]
    player_cfg = cfg.get("player", {})
    quiz_cfg = cfg.get("quiz", {})
    doc_cfg = cfg.get("doc", {})
    configure_captcha(cfg.get("captcha") or {}, logger)
    browser_channel = player_cfg.get("browser")
    progress = Progress(cfg["progress"]["file"])

    ensure_browser(logger, browser_channel)
    logger.info("正在启动浏览器（%s）…" % (browser_channel or "chromium"))
    session = launch_browser(
        headless=player_cfg.get("headless", False), browser_channel=browser_channel
    )
    logger.info("浏览器已启动，正在创建页面…")
    page = new_page(session, browser_channel)

    # 加载已保存的登录态（若存在）；但无论如何都要先打开学习通首页，
    # 不能因为"已加载 cookie"就跳过导航，否则页面会停在 about:blank。
    cookie_path = acc["cookie_file"]
    loaded = load_cookies(page, cookie_path)
    if loaded:
        logger.info("已加载上次登录状态，尝试免登录")
    else:
        logger.info(
            "首次使用：稍后浏览器会弹出二维码，用学习通 App 扫码即可；"
            "登录态会自动保存，之后运行无需重复扫码"
        )

    # 登录态要"勤保存"：程序被强杀/看门狗重启时，若只保存过一次，
    # 服务器可能已刷新令牌，下次就得重新扫码。这里每 5 分钟补存一次。
    _cookie_saved_at = [0.0]

    def _maybe_save_cookies(force: bool = False):
        now = time.time()
        if not force and now - _cookie_saved_at[0] < 300:
            return
        _cookie_saved_at[0] = now
        try:
            save_cookies(page, cookie_path)
        except Exception:
            pass

    logger.info("正在打开学习通首页…")
    opened = True
    try:
        page.goto("https://i.chaoxing.com", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
    except Exception as e:
        opened = False
        logger.warning("打开首页异常（将直接走登录页）：%s" % e)

    url = (page.url or "").lower()
    need_login = (not opened) or (not url) or url == "about:blank"
    need_login = need_login or ("passport" in url) or ("/login" in url)

    if need_login:
        logger.info("尚未登录，开始登录…")
        if acc.get("type") == "qrcode":
            login_by_qrcode(page, logger)
        else:
            login_by_password(page, acc.get("username", ""), acc.get("password", ""), logger)
        _maybe_save_cookies(force=True)
    else:
        logger.info("已登录，直接进入课程列表")

    courses = list_courses(page, logger)
    # 英语课程默认跳过（不打开、不检测），其余课程再做关键词筛选
    non_english = [c for c in courses if not is_english_course(c.get("name", ""))]
    selected = select_target(
        non_english,
        cfg.get("courses", {}).get("keywords", []),
        cfg.get("courses", {}).get("exclude_keywords", []),
        logger,
    )

    # ---- 单趟处理：每门课程只打开一次。没有任务点的章节直接跳过，
    #      整门都已完成的课程识别后跳过，不再重复打开 ----
    english_courses = [c for c in courses if is_english_course(c.get("name", ""))]

    report_lines = []
    total_ok = total_q = total_fail = 0
    unfinished = []      # 本轮没能完成的任务点（明确列出来，别让人以为全做完了）
    n_noneed = 0        # 已全部完成 / 无任务点
    n_unknown = 0       # 目录未能识别
    n_failed = 0        # 进入失败

    if scan_only:
        logger.info("模式：仅检测（不播放、不点开任务点）")
        for course in selected:
            name = course.get("name", "")
            logger.info("===== 检测课程：%s =====" % name)
            if not open_course(page, course, logger):
                n_failed += 1
                report_lines.append("课程《%s》：进入失败 → 跳过（下次重试）" % name)
                continue
            if course.get("_no_sections"):
                n_noneed += 1
                report_lines.append("课程《%s》：空课程（无章节）→ 跳过" % name)
                continue
            nodes = scan_catalog(page, logger, cfg.get("debug", False))
            tasks = [n for n in nodes if not n.get("no_job")]
            if not tasks:
                if nodes:
                    n_noneed += 1
                    report_lines.append(
                        "课程《%s》：%d 个章节都没有任务点 → 跳过" % (name, len(nodes))
                    )
                else:
                    n_unknown += 1
                    report_lines.append("课程《%s》：未能识别目录 → 无法判断" % name)
                continue
            done = sum(
                1 for n in tasks
                if n["done"] or (not n.get("state") and progress.is_done(_node_key(name, n)))
            )
            label = _kinds_label(name, tasks, _load_kinds())
            report_lines.append(
                "课程《%s》：任务点 %d 个（已完成 %d / 未完成 %d）%s → 待处理"
                % (name, len(tasks), done, len(tasks) - done, label)
            )
        for course in english_courses:
            report_lines.append("课程《%s》：已跳过（英语课程，不做任何操作）" % course.get("name", ""))
        logger.info("========== 课程检测结果 ==========")
        for line in report_lines:
            logger.info(line)
        logger.info("=================================")
        logger.info(
            "仅检测完成：共 %d 门候选课程，%d 门跳过（已完成/无任务点）、%d 门英语跳过、%d 门进入失败、%d 门目录未识别"
            % (len(selected), n_noneed, len(english_courses), n_failed, n_unknown)
        )
        save_cookies(page, cookie_path)
        session.close()
        return

    # ---- 正式处理（单趟：打开即处理，没有任务点的直接跳到下一门）----
    for course in selected:
        name = course.get("name", "")
        _maybe_save_cookies()   # 每门课间顺手刷新登录态，避免下次重新扫码
        logger.info("===== 处理课程：%s =====" % name)
        if not open_course(page, course, logger):
            n_failed += 1
            report_lines.append("课程《%s》：进入失败 → 跳过（下次重试）" % name)
            continue
        if course.get("_no_sections"):
            n_noneed += 1
            report_lines.append("课程《%s》：空课程（无章节）→ 跳过" % name)
            continue
        nodes = scan_catalog(page, logger, cfg.get("debug", False))
        nodes = [n for n in nodes if not n.get("no_job")]
        if not nodes:
            n_unknown += 1
            report_lines.append("课程《%s》：未能识别目录 → 仍尝试处理" % name)
            unfinished.append("%s（目录未识别）" % name)
            continue
        # 统计本报告用的完成/未完成（不改变处理逻辑）
        done = 0
        for n in nodes:
            k = _node_key(name, n)
            if n["done"] or (not n.get("state") and progress.is_done(k)):
                done += 1
        unviewed = len(nodes) - done
        if unviewed <= 0:
            n_noneed += 1
            report_lines.append(
                "课程《%s》：任务点 %d 个，全部已完成 → 跳过" % (name, len(nodes))
            )
            continue
        known = _kinds_label(name, nodes, _load_kinds())
        report_lines.append(
            "课程《%s》：任务点 %d 个（已完成 %d / 未完成 %d）%s → 待处理"
            % (name, len(nodes), done, unviewed, known)
        )



        for node in nodes:
            label = node["title"]
            if node.get("chapter"):
                label = "%s / %s" % (node["chapter"], node["title"])
            key = _node_key(name, node)

            if node["done"]:
                logger.info("跳过已完成：%s" % label)
                if not progress.is_done(key):
                    progress.mark_done(key)
                continue
            if not node.get("state") and progress.is_done(key):
                logger.info("跳过（本地已记录完成）：%s" % label)
                continue

            # 一节里可能有多个任务点（视频+文档+测验）。目录里的橙色角标数字
            # 就是"还有几个没做"，所以做完一个要重新进入该节做下一个。
            remain = max(1, min(int(node.get("pending") or 1), 10))
            handled = 0
            tried_boxes = []      # 本节已经试过的任务点块，避免反复停在第一块
            for att in range(remain):
                tag = label if remain == 1 else "%s（第 %d/%d 个任务点）" % (label, att + 1, remain)
                need_click = True
                job_idx = -1
                if att > 0:
                    # 学习通把一节里的多个任务点竖排在同一张 cards 页面里，
                    # 只点目录永远回到第一个，所以直接滚到"下一块没做完的任务点"
                    idx = goto_next_job(page, logger, tried_boxes)
                    if idx >= 0:
                        tried_boxes.append(idx)
                        need_click = False
                        job_idx = idx
                    else:
                        if _job_boxes_count(page) > 0:
                            # 所有块都已完成，goto_next_job 才找不到下一块。
                            # 此时绝不能重新进入本节——会从第一块开始重播。
                            logger.info("本节任务点块都已完成，跳过（不重复播放）")
                            handled += 1
                            break
                        logger.info("本节还有任务点，重新进入该节：%s" % label)
                logger.info("--- 处理：%s ---" % tag)
                if need_click and not click_node(node, logger):
                    # 目录可能因点击而重排，重扫一次按标题找回再点
                    fresh = scan_catalog(page, logger, timeout=6)
                    retry = next(
                        (
                            n
                            for n in fresh
                            if n["title"] == node["title"] and n.get("chapter") == node.get("chapter")
                        ),
                        None,
                    )
                    if not retry or not click_node(retry, logger):
                        logger.warning("打开任务点失败，跳过（不记进度，下次重试）：%s" % tag)
                        continue

                # 先判断是不是测验：即使 AI 未启用也要识别出来，
                # 否则会被当成"视频"跳过并误标为已完成。
                qframe = detect_quiz(page)
                if qframe:
                    if not answerer:
                        logger.warning("检测到测验，但 AI 答题未启用，跳过（不标记完成）")
                        continue
                    _save_kind(key, "quiz")
                    ok, q, fail = answer_questions(
                        qframe, answerer, quiz_cfg.get("auto_submit", True), logger
                    )
                    total_ok += ok
                    total_q += q
                    total_fail += fail
                    if not (q > 0 and fail == 0 and ok == q):
                        continue
                    handled += 1
                else:
                    kind = detect_task_kind(
                        page, timeout=player_cfg.get("type_wait_seconds", 15)
                    )
                    _save_kind(key, kind)

                    def _do_video(block: int = -1) -> bool:
                        return play_video(
                            page,
                            player_cfg.get("speed", 1.5),
                            player_cfg.get("max_wait_seconds", 3600),
                            logger,
                            find_timeout=25,
                            node=node,
                            hold_seconds=float(player_cfg.get("done_hold_seconds", 5)),
                            job_index=block,
                            auto_speed=bool(player_cfg.get("auto_speed", True)),
                            speed_min=float(player_cfg.get("auto_speed_min", 1.0) or 1.0),
                        )

                    if kind == "video":
                        if job_idx < 0:
                            # 刚点目录进来时不知道是第几块，先定位到"第一块没做完的"
                            job_idx = goto_next_job(page, logger, tried_boxes)
                            if job_idx >= 0:
                                tried_boxes.append(job_idx)
                            elif _job_boxes_count(page) > 0:
                                # 这一节所有块都已完成（目录状态可能还没刷新）
                                # 直接跳过，绝不回头重播第一块
                                logger.info("本节所有任务点块都已完成，跳过（不重复播放）")
                                handled += 1
                                break
                        if not _do_video(job_idx):
                            # 关键：没播完绝不能标记完成，否则下次运行会永久跳过这一节。
                            # 但也不能就此放弃本节后面的任务点，继续试下一块。
                            logger.warning("视频/音频未播完，不记进度，下次运行会重试")
                            continue
                        handled += 1
                    elif kind == "doc":
                        # 标题像测验/作业：没识别到答题界面就不能当"文档"处理，
                        # 否则会被误记为已完成，测验分数就丢了。
                        if QUIZ_TITLE_RE.search(node.get("title") or ""):
                            logger.warning(
                                "「%s」看起来是测验/作业，但没识别到答题界面，"
                                "本轮不标记完成（避免误判），下次会重试" % tag
                            )
                            continue
                        # 文档 / PPT / PDF / 图片：必须把内容滚到底，学习通才会上报完成
                        if read_document(
                            page,
                            node,
                            logger,
                            max_seconds=int(doc_cfg.get("max_wait_seconds", 180)),
                            dwell_seconds=float(doc_cfg.get("dwell_seconds", 2)),
                            verify_seconds=int(doc_cfg.get("verify_seconds", 20)),
                            assume_done=bool(doc_cfg.get("assume_done", False)),
                            reopen=lambda: click_node(node, logger),
                        ):
                            handled += 1
                        elif has_task_media(page) and _do_video():
                            # 文档页里其实嵌了视频（判错类型时兜底）
                            handled += 1
                        else:
                            continue
                    else:
                        # 其它类型（讨论、资料、直播、外链等）
                        logger.warning(
                            "该任务点类型未识别（可能是直播/讨论/外链），不记进度，下次重试：%s" % tag
                        )
                        continue

                # 做完一个就看该节是否已标记完成，完成了就不用再进
                if _node_done(page, node):
                    break

            if not handled:
                # 没做完就是没做完：记下来，最后统一列给用户看，不假装处理过
                unfinished.append(label)
                continue
            if _node_done(page, node) or (node.get("pending") or 0) <= handled:
                progress.mark_done(key)
            else:
                logger.info(
                    "该节还有任务点没做完（共 %s 个），本轮先放着，下次运行继续：%s"
                    % (node.get("pending") or "?", label)
                )
                unfinished.append(label)

        # ---- 课程复检：跑完再扫一遍，确认到底还剩哪些没做（防"以为做完了"） ----
        try:
            left = [
                n
                for n in scan_catalog(page, None, timeout=8, quiet=True)
                if not n["done"] and not n.get("no_job")
            ]
        except Exception:
            left = []
        if left:
            logger.warning(
                "课程《%s》复检：仍有 %d 个任务点未完成 → %s"
                % (
                    name,
                    len(left),
                    "、".join((n.get("title") or "")[:16] for n in left[:6]),
                )
            )
            unfinished.extend("%s / %s" % (name, n.get("title") or "") for n in left)
        else:
            logger.info("课程《%s》复检：已全部完成" % name)

    # ---- 输出检测报告（格式与优化前一致）----
    logger.info("========== 课程检测结果 ==========")
    for line in report_lines:
        logger.info(line)
    for course in english_courses:
        logger.info("课程《%s》：已跳过（英语课程，不做任何操作）" % course.get("name", ""))
    logger.info("=================================")
    processed = len(selected) - n_noneed - n_failed - n_unknown
    logger.info(
        "共处理 %d 门课程：%d 门有待处理任务点并已进入处理、%d 门跳过（已完成/无任务点）、%d 门英语跳过"
        % (len(selected) + len(english_courses), max(processed, 0), n_noneed, len(english_courses))
    )
    if n_unknown:
        logger.info("（另有 %d 门目录未能识别，未敢标记完成，下次运行会重试）" % n_unknown)
    if n_failed:
        logger.warning("（有 %d 门课程进入失败，请检查网络或账号状态）" % n_failed)

    logger.info("答题：成功 %d / 共 %d，失败 %d" % (total_ok, total_q, total_fail))
    # 去重（同一节可能被多次记录）
    seen = set()
    todo = []
    for x in unfinished:
        if x not in seen:
            seen.add(x)
            todo.append(x)
    unfinished = todo
    if unfinished:
        logger.warning("========== 本轮未完成 %d 个任务点 ==========" % len(unfinished))
        for x in unfinished:
            logger.warning("  · %s" % x)
        logger.warning("这些任务点没有记进度，下次运行会重新尝试。")
    else:
        logger.info("全部任务点处理完毕，没有遗留。")
    session.close()


def main():
    cfg = normalize_paths(load_config())
    logger = get_logger(
        level=cfg.get("log", {}).get("level", "INFO"),
        log_file=cfg["log"]["file"],
    )
    logger.info("工作目录：%s" % BASE_DIR)
    answerer = build_answerer(cfg, logger)

    # --scan-only：只检测有哪些课程/任务点没完成，不进去做任何操作
    scan_only = any(a.lower() in ("--scan-only", "--scan", "-s") for a in sys.argv[1:])
    cfg["app"]["mode"] = cfg.get("app", {}).get("mode", "browser")
    if scan_only:
        logger.info("模式：仅检测（不播放、不点开任务点）")

    mode = cfg.get("app", {}).get("mode", "browser")
    if mode == "cdp":
        logger.info("运行模式：接管学习通 PC 客户端")
        run_cdp_mode(cfg, logger, answerer)
    else:
        logger.info("运行模式：浏览器模式")
        run_browser_mode(cfg, logger, answerer, scan_only=scan_only)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，进度已保存，下次运行将续跑。")
    except RuntimeError as e:
        _pause_exit("\n运行出错：" + str(e))
    except Exception as e:
        try:
            import traceback
            with open(os.path.join(BASE_DIR, "data", "error.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + repr(e) + "\n")
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        if PlaywrightError is not None and isinstance(e, PlaywrightError):
            # 用户关掉浏览器窗口 / 页面崩溃时，不要甩一堆英文堆栈
            _pause_exit(
                "\n浏览器窗口已关闭（或页面崩溃），程序已退出。\n"
                "已完成的进度已保存，下次双击运行会接着做。"
            )
        else:
            import traceback

            traceback.print_exc()
            _pause_exit("运行出错：" + str(e))
