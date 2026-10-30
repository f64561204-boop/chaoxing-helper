import json
import os
import sys
import time

from playwright.sync_api import Page, sync_playwright

# 路径跟随可执行文件（打包成 exe 后也能正确找到数据目录）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
)

# 尽量抹掉自动化特征
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
"""


def _clean_env():
    """给浏览器一个干净的 PATH。

    PyInstaller 单文件模式会把 _MEIxxxx 解压目录加到 PATH 里，该目录带有
    MSVCP140_1.dll / api-ms-win-crt-*.dll 等打包 CRT。Edge/Chromium 作为
    子进程继承 PATH 后会加载到这些 DLL，导致渲染进程随机崩溃（Page crashed）。
    这里把 _MEI 路径剔除，只保留系统 PATH。
    """
    env = dict(os.environ)
    try:
        keep = []
        for part in (env.get("PATH") or "").split(os.pathsep):
            if not part:
                continue
            low = part.replace("/", "\\").lower()
            if "\\_mei" in low:
                continue
            keep.append(part)
        env["PATH"] = os.pathsep.join(keep)
    except Exception:
        pass
    return env


def default_browsers_path() -> str:
    """与 Playwright 官方默认保持一致的浏览器缓存目录。"""
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        return os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    if os.name == "nt":
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ms-playwright"
        )
    return os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")


class Session:
    """统一封装浏览器会话，屏蔽两种启动方式的差异。

    - chromium 模式：browser.launch() 得到 Browser，再 new_context()
    - edge 模式：launch_persistent_context() 直接得到 BrowserContext
      （注意：持久化上下文不是 Browser，没有 new_context/browser.close 语义）

    对外只暴露 new_page() / pages / close()，调用方无需关心底层是哪种。
    """

    def __init__(self, pw, browser=None, context=None):
        self._pw = pw
        self.browser = browser
        self.context = context

    @property
    def pages(self):
        try:
            return list(self.context.pages) if self.context else []
        except Exception:
            return []

    def new_page(self, channel: str = None) -> Page:
        if self.context is None:
            ua = EDGE_UA if channel == "edge" else DEFAULT_UA
            self.context = self.browser.new_context(
                user_agent=ua,
                viewport={"width": 1366, "height": 768},
                locale="zh-CN",
                accept_downloads=True,
            )
            try:
                self.context.add_init_script(STEALTH_JS)
            except Exception:
                pass
        pages = self.pages
        if pages:
            return pages[0]
        return self.context.new_page()

    def close(self):
        for close_one in (
            lambda: self.context.close() if self.context else None,
            lambda: self.browser.close() if self.browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                close_one()
            except Exception:
                pass


def launch_browser(headless: bool = False, browser_channel: str = None) -> Session:
    """启动浏览器并返回 Session。

    browser_channel:
      - "edge"（或 "msedge"）：使用系统已安装的 Edge，独立用户目录
      - 其他 / 空：使用 Playwright 自带 Chromium
    """
    channel = (browser_channel or "").lower()
    is_edge = channel in ("edge", "msedge")

    # 使用自带 Chromium 时必须显式指定系统缓存目录，否则打包后报
    # "Executable doesn't exist"；使用系统 Edge 时无需下载内核。
    if not is_edge:
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", default_browsers_path())

    try:
        pw = sync_playwright().start()
    except Exception as e:
        raise RuntimeError("初始化浏览器驱动失败：%s" % e)

    args = [
        # 不加 --no-sandbox：Edge 会提示“不受支持的命令行标志”，且沙箱关闭后
        # 页面更容易被异常脚本拖崩。稳定性优先。
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        # 跳过首次运行向导 / 默认浏览器询问，避免启动卡住
        "--no-first-run",
        "--no-default-browser-check",
        "--no-default-browser-prompt",
        # 关键：别让浏览器把后台标签页/被遮挡窗口限流。
        # 否则视频会"paused=False 但 currentTime 不动"，看起来像卡死。
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-features=CalculateNativeWinOcclusion",
    ]

    if is_edge:
        # 关键：用户数据目录必须通过 launch_persistent_context(user_data_dir=...)
        # 传入。Playwright 明确禁止把 --user-data-dir 写进 launch() 的 args
        # （会直接抛 Error）。独立 profile 也能避免与用户日常 Edge 单实例冲突。
        profile = os.path.join(BASE_DIR, "data", "edge_profile")
        try:
            os.makedirs(profile, exist_ok=True)
        except Exception as e:
            try:
                pw.stop()
            except Exception:
                pass
            raise RuntimeError("无法创建 Edge 用户数据目录 %s：%s" % (profile, e))
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=profile,
                channel="msedge",
                headless=headless,
                args=args,
                viewport={"width": 1366, "height": 768},
                user_agent=EDGE_UA,
                locale="zh-CN",
                accept_downloads=True,
                env=_clean_env(),
            )
        except Exception as e:
            try:
                pw.stop()
            except Exception:
                pass
            raise RuntimeError(
                "启动系统 Edge 失败：%s\n"
                "请确认本机已安装 Microsoft Edge；如仍失败，可把 config.json 的 "
                "player.browser 改为 \"chromium\" 后重试。" % e
            )
        try:
            ctx.add_init_script(STEALTH_JS)
        except Exception:
            pass
        return Session(pw, context=ctx)

    try:
        browser = pw.chromium.launch(headless=headless, args=args, env=_clean_env())
    except Exception as e:
        try:
            pw.stop()
        except Exception:
            pass
        raise RuntimeError(
            "启动 Chromium 失败：%s\n"
            "可把 config.json 的 player.browser 改为 \"edge\" 使用系统 Edge。" % e
        )
    return Session(pw, browser=browser)


def new_page(session, channel: str = None) -> Page:
    """兼容旧调用：既接受 Session，也接受裸 Browser。"""
    if isinstance(session, Session):
        return session.new_page(channel)
    ua = EDGE_UA if (channel or "").lower() in ("edge", "msedge") else DEFAULT_UA
    ctx = session.new_context(user_agent=ua, viewport={"width": 1366, "height": 768})
    ctx.add_init_script(STEALTH_JS)
    return ctx.new_page()


def save_cookies(page: Page, path: str):
    """保存登录态；路径无目录、写盘失败都不应中断主流程。"""
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(page.context.cookies(), f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_cookies(page: Page, path: str) -> bool:
    """加载登录态；文件缺失/内容非法时安全返回 False，不抛异常。"""
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or not data:
            return False
        page.context.add_cookies(data)
        return True
    except Exception:
        return False


def _goto(page: Page, url: str, timeout: int = 60000):
    """用 domcontentloaded 打开页面：学习通后台请求不断，networkidle 永远等不到。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(2000)
    except Exception as e:
        raise RuntimeError("打开 %s 失败：%s\n请检查网络连接后重试" % (url, e))


def _is_login_url(page: Page) -> bool:
    url = (page.url or "").lower()
    if not url or url == "about:blank":
        return True
    return "passport" in url or "/login" in url


def _wait_login_done(page: Page, timeout: int, logger) -> bool:
    """轮询等待离开登录页，比 wait_for_url 死等某个固定地址更稳。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = (page.url or "").lower()
        except Exception:
            url = ""
        if url and "chaoxing" in url and "passport" not in url and "/login" not in url:
            return True
        page.wait_for_timeout(1000)
    return False


def login_by_password(page: Page, username: str, password: str, logger):
    if not username or not password:
        raise RuntimeError(
            "account.username / account.password 为空。请填写账号密码，"
            "或把 account.type 改为 \"qrcode\" 使用手机扫码登录。"
        )
    _goto(page, "https://passport2.chaoxing.com/login")
    # 尝试切换到“账号登录”
    for sel in ['a:has-text("账号登录")', "#accountLogin", ".loginSwitch"]:
        try:
            if page.locator(sel).count() and page.locator(sel).first.is_visible():
                page.locator(sel).first.click()
                break
        except Exception:
            pass
    filled = False
    for acct_sel in [
        'input[name="phone"]',
        'input[name="uname"]',
        "#phone",
        "#uname",
        'input[placeholder*="手机"], input[placeholder*="账号"], input[placeholder*="学号"]',
    ]:
        try:
            if page.locator(acct_sel).count():
                page.fill(acct_sel, username)
                filled = True
                break
        except Exception:
            pass
    if not filled:
        raise RuntimeError(
            "找不到账号输入框。学习通登录页结构可能已改版，"
            "建议把 account.type 改为 \"qrcode\" 用扫码登录。"
        )
    for pwd_sel in ['input[name="pwd"]', "#pwd", 'input[type="password"]']:
        try:
            if page.locator(pwd_sel).count():
                page.fill(pwd_sel, password)
                break
        except Exception:
            pass
    for agree in ['input[name="remember"]', "#remember"]:
        try:
            if page.locator(agree).count():
                page.check(agree)
        except Exception:
            pass
    try:
        page.click('button:has-text("登录"), #loginBtn, .loginBtn', timeout=10000)
    except Exception as e:
        raise RuntimeError("点击登录按钮失败：%s" % e)
    if not _wait_login_done(page, 40, logger):
        raise RuntimeError(
            "登录未成功：请检查账号密码是否正确、网络是否正常；"
            "若页面要求扫码或验证码，请把 config.json 中 account.type 改为 qrcode 后重试"
        )
    logger.info("密码登录成功")


def login_by_qrcode(page: Page, logger, timeout: int = 300):
    _goto(page, "https://passport2.chaoxing.com/login")
    # 部分版本默认是账号密码登录，需要切到二维码
    for sel in ['a:has-text("二维码")', "#qrcode", ".qrcode"]:
        try:
            if page.locator(sel).count() and page.locator(sel).first.is_visible():
                page.locator(sel).first.click()
                break
        except Exception:
            pass
    # 尽量等到二维码真正出现再提示，避免用户看不到图
    try:
        page.wait_for_selector(
            'img[src*="qrcode"], .qrcode-img, #qrcode img, #qrlogin img', timeout=15000
        )
    except Exception:
        pass
    logger.info("请用学习通 App 扫描浏览器中的二维码登录（%d 秒内）..." % timeout)
    if not _wait_login_done(page, timeout, logger):
        raise RuntimeError("扫码登录超时：请在弹出的浏览器中完成扫码，或重新运行再试")
    logger.info("扫码登录成功")
