import re

from playwright.sync_api import Page

COURSE_URL_HINTS = ("courseid", "course/team", "mycourse", "/course/", "visit/interaction")

# 默认英语课程跳过名单：课程名包含即跳过（大小写不敏感）。
# 与 config.json 的 courses.exclude_keywords 合并，始终生效，用户可追加自己的排除词。
DEFAULT_EXCLUDE_KEYWORDS = ["英语", "English", "english"]


def list_courses(page: Page, logger):
    """打开课程列表页并解析课程。

    学习通改版频繁，class 名不可靠，这里改为「按链接特征」识别课程入口，
    比写死 .courseItem 之类的选择器稳定得多。
    """
    try:
        page.goto(
            "https://mooc1.chaoxing.com/visit/courses",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(3000)
    except Exception as e:
        logger.warning("打开课程列表页失败：%s" % e)
        return []

    courses, seen = [], set()
    try:
        anchors = page.locator("a[href]").all()
    except Exception as e:
        logger.warning("读取课程链接失败：%s" % e)
        return []

    for a in anchors:
        try:
            href = a.get_attribute("href") or ""
            if not href or href.startswith("javascript"):
                continue
            low = href.lower()
            if not any(k in low for k in COURSE_URL_HINTS):
                continue
            name = (a.inner_text() or "").strip().split("\n")[0].strip()
            if not name or len(name) < 2:
                continue
            if href in seen:
                continue
            seen.add(href)
            if not href.startswith("http"):
                href = "https://mooc1.chaoxing.com" + ("" if href.startswith("/") else "/") + href
            courses.append({"name": name, "url": href})
        except Exception:
            continue

    # 去重同名课程（同一门课可能有多个入口链接）
    unique, names = [], set()
    for c in courses:
        key = re.sub(r"\s+", "", c["name"])
        if key in names:
            continue
        names.add(key)
        unique.append(c)

    logger.info("检测到 %d 门课程" % len(unique))
    if not unique:
        logger.warning(
            "未解析到课程。可能原因：未登录 / 页面结构变化 / 账号下暂无课程。"
            "可把 config.json 的 debug 设为 true 后重跑，会保存 data/debug.html 便于排查。"
        )
    return unique


def is_english_course(name: str) -> bool:
    """课程名是否命中默认英语跳过名单（大小写不敏感）。"""
    low = (name or "").lower()
    return any(k.lower() in low for k in DEFAULT_EXCLUDE_KEYWORDS)


def select_target(courses, keywords, exclude_keywords, logger):
    if not keywords:
        selected = list(courses)
    else:
        selected = [c for c in courses if any(k in c["name"] for k in keywords)]
    if exclude_keywords:
        selected = [c for c in selected if not any(k in c["name"] for k in exclude_keywords)]
    logger.info("筛选后需处理 %d 门课程" % len(selected))
    return selected
