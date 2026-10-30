"""接管学习通 PC 客户端（Electron + Chromium）。

学习通 PC 客户端本质是一个 Electron 外壳，内部课程/视频/题目页面
与网页版同源（mooc1.chaoxing.com 等）。本模块以
`--remote-debugging-port` 启动（或挂接已启动的）客户端，通过
Chrome DevTools 协议接管它自带的 Chromium，从而复用网页版的全部
播放/答题逻辑，而无需再驱动一个独立浏览器。
"""

import os
import socket
import subprocess
import time

from playwright.sync_api import sync_playwright


# 常见安装目录（中文/英文目录都覆盖）
KNOWN_DIRS = [
    r"C:\Program Files\Chaoxing",
    r"C:\Program Files (x86)\Chaoxing",
    r"C:\Program Files\学习通",
    r"C:\Program Files (x86)\学习通",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "学习通"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Chaoxing"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Chaoxing"),
    os.path.join(os.environ.get("APPDATA", ""), "学习通"),
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "学习通"),
]


def find_app_path(configured: str = "") -> str:
    """查找学习通 PC 客户端 exe；配置了 path 则优先使用。"""
    if configured and os.path.exists(configured):
        return configured
    candidates = []
    for d in KNOWN_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                low = f.lower()
                if low.endswith(".exe") and (
                    "学习通" in f or "chaoxing" in low or "study" in low
                ):
                    candidates.append(os.path.join(root, f))
    if candidates:
        # 优先返回文件名含“学习通”的，路径越短越可能是主程序
        candidates.sort(key=lambda x: (0 if "学习通" in x else 1, len(x)))
        return candidates[0]
    raise RuntimeError(
        "未找到学习通 PC 客户端 exe。请在 config.json 的 app.path 填写其完整路径，\n"
        "例如：C:\\Program Files\\Chaoxing\\学习通\\学习通.exe"
    )


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except Exception:
        return False


def _kill_app(exe_path: str):
    name = os.path.basename(exe_path)
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", name], capture_output=True, timeout=10
        )
    except Exception:
        pass
    time.sleep(1)


def launch_with_debug(exe_path: str, port: int):
    """关闭已有实例，并以远程调试端口启动客户端。"""
    _kill_app(exe_path)
    args = [
        exe_path,
        "--remote-debugging-port=%d" % port,
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
    ]
    try:
        subprocess.Popen(args)
    except Exception as e:
        raise RuntimeError("启动学习通失败：%s" % e)
    # 等待调试端口就绪
    deadline = time.time() + 45
    while time.time() < deadline:
        if is_port_open(port):
            return
        time.sleep(1)
    raise RuntimeError(
        "学习通已启动但调试端口 %d 未开启。该客户端可能禁用了远程调试；\n"
        "可把 app.mode 改为 browser 改用浏览器模式，或把 app.auto_launch 设为 false\n"
        "并自行用带调试端口的方式启动后反馈给我进一步排查。" % port
    )


def ensure_app(logger, app_cfg: dict):
    """确保学习通 PC 客户端的调试端口可用（启动或确认已开）。"""
    port = int(app_cfg.get("debug_port", 9222))
    if is_port_open(port):
        logger.info("检测到学习通调试端口已开启，直接挂接")
        return
    if app_cfg.get("auto_launch", True):
        path = find_app_path(app_cfg.get("path", ""))
        logger.info("正在以自动化模式启动学习通：%s" % path)
        launch_with_debug(path, port)
    else:
        raise RuntimeError(
            "调试端口 %d 未开启。请先以带调试端口的方式启动学习通，\n"
            "或把 config.json 的 app.auto_launch 设为 true 让助手代为启动。" % port
        )


def connect(port: int):
    """通过 CDP 挂接客户端，返回 (playwright, browser)。"""
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % port)
    except Exception as e:
        try:
            pw.stop()
        except Exception:
            pass
        raise RuntimeError("无法通过调试端口挂接学习通：%s" % e)
    return pw, browser


def _all_pages(browser):
    pages = []
    try:
        for ctx in browser.contexts:
            for page in ctx.pages:
                pages.append(page)
    except Exception:
        pass
    return pages


def find_content_page(browser, logger=None):
    """返回当前正在学习的页面（课程/视频/测验），没有则返回 None。"""
    cands = []
    for page in _all_pages(browser):
        url = (page.url or "").lower()
        if "chaoxing.com" not in url:
            continue
        if "passport" in url:  # 登录页，等待用户登录并进入课程
            continue
        cands.append(page)
    if not cands:
        return None
    # 优先返回含视频或测验的页面（真正在播放/作答的页面）
    for page in cands:
        try:
            if page.query_selector("video"):
                return page
        except Exception:
            pass
    for page in cands:
        try:
            if page.locator(".questionLi, .topicDetail, .questionBD").count():
                return page
        except Exception:
            pass
    if logger and len(cands) > 1:
        try:
            logger.info("检测到多个学习通页面：%s" % " | ".join(p.url for p in cands))
        except Exception:
            pass
    return cands[-1]


def wait_for_course_page(browser, logger, timeout: int = 300):
    """等待用户登录并点开课程，返回内容页。"""
    logger.info(
        "请在弹出的学习通窗口中登录（扫码），并点开要学习的课程，助手将自动接管…"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        page = find_content_page(browser, logger)
        if page:
            return page
        time.sleep(2)
    # 超时后打印当前可见页面，便于排查
    try:
        urls = [p.url for p in _all_pages(browser)]
        logger.warning("等待超时，当前页面：%s" % urls)
    except Exception:
        pass
    raise RuntimeError("等待课程页面超时：请确认已登录并进入了某个课程章节")
