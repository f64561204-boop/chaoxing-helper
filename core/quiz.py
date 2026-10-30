import re

from playwright.sync_api import Page

from .answerer import AIAnswerer

QUESTION_SELECTOR = ".questionLi, .questionBD, .topicDetail, .questionItem"

OPTION_SELECTOR = ".optionDetail, .option, li, label"

TYPE_WORDS = {"单选", "多选题", "多选", "判断题", "判断", "填空题", "填空", "简答题", "简答", "问答题"}

OPTION_PREFIX = re.compile(r"^[A-Ha-h][\.\、,）\)．:：]\s*")
NUM_PREFIX = re.compile(r"^[（(]?\d+[）)、\.．]\s*")
# (2分) / [单选题] 之类的题型与分值标记
SCORE_PREFIX = re.compile(r"^\s*[（(\[【]\s*(?:\d+\s*分|单选|多选|判断|填空|简答)[）)\]】]\s*")

JUDGE_TRUE = ["正确", "对", "√", "是", "true", "T"]
JUDGE_FALSE = ["错误", "错", "×", "否", "false", "F"]


def detect_quiz(page: Page, timeout: int = 6):
    """判断当前是否在测验/作业页，返回对应 frame；否则 None。

    题目区是异步渲染的，点开任务点后立刻查常常为空，
    这里最多轮询 timeout 秒，避免把测验误判成视频。
    """
    import time

    deadline = time.time() + max(0, timeout)
    while True:
        frames = [page]
        try:
            frames += list(page.frames)
        except Exception:
            pass
        for f in frames:
            # 只认"可见"的题目：上一个任务点残留的隐藏 iframe 里也有题目 DOM，
            # 不加可见性判断会把它误当成当前测验，从而对着旧题目作答。
            try:
                loc = f.locator(QUESTION_SELECTOR)
                if loc.count() and loc.first.is_visible():
                    return f
            except Exception:
                continue
            # 兜底：有提交按钮 + 有单选/多选框
            try:
                has_submit = f.locator('button:has-text("提交"), button:has-text("交卷")').count()
                has_input = f.locator('input[type="radio"], input[type="checkbox"]').count()
                if has_submit and has_input and f.locator('input[type="radio"], input[type="checkbox"]').first.is_visible():
                    return f
            except Exception:
                continue
        if time.time() >= deadline:
            return None
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return None


def _extract_stem(text: str) -> str:
    """从题目区块文本里取出题干：跳过题型标签与选项行，取最长的一行。"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    candidates = []
    for line in lines:
        if line in TYPE_WORDS:
            continue
        if OPTION_PREFIX.match(line):
            continue
        if len(line) <= 1:
            continue
        line = NUM_PREFIX.sub("", line)
        line = SCORE_PREFIX.sub("", line).strip()
        if not line:
            continue
        candidates.append(line)
    if not candidates:
        return (lines[0] if lines else "").strip()
    return max(candidates, key=len)


def _extract_options(container):
    """提取选项元素（去重，保持顺序）。"""
    try:
        els = container.locator(OPTION_SELECTOR).all()
    except Exception:
        return []
    result, seen = [], set()
    for el in els:
        try:
            txt = re.sub(r"\s+", "", el.inner_text() or "")
        except Exception:
            continue
        if not txt:
            continue
        if txt in seen:
            continue
        # 过滤掉把整段题干/选项区文本都包进去的容器元素
        if len(txt) > 200:
            continue
        seen.add(txt)
        result.append(el)
    return result


def _is_selected(el) -> bool:
    try:
        cls = (el.get_attribute("class") or "").lower()
        if any(k in cls for k in ("cur", "selected", "checked", "active", "on")):
            return True
        if el.locator('input[type="radio"]:checked, input[type="checkbox"]:checked').count():
            return True
    except Exception:
        pass
    return False


def answer_questions(frame, answerer: AIAnswerer, auto_submit: bool, logger):
    """
    提取当前测验页所有题目，调用 AI 判断并勾选/填写。
    返回 (正确作答数, 总题数, 失败题数)
    """
    try:
        containers = frame.locator(QUESTION_SELECTOR).all()
    except Exception as e:
        logger.warning("读取题目列表失败：%s" % e)
        return 0, 0, 0

    total = len(containers)
    if total == 0:
        logger.warning("未提取到任何题目")
        return 0, 0, 0

    ok = fail = 0
    for idx, c in enumerate(containers, 1):
        try:
            text = c.inner_text()
        except Exception:
            fail += 1
            continue

        qtype = "single"
        if "多选" in text:
            qtype = "multiple"
        elif "判断" in text:
            qtype = "judge"
        elif "填空" in text or "简答" in text:
            qtype = "fill"

        option_els = _extract_options(c)
        options = []
        for o in option_els:
            try:
                options.append(o.inner_text().strip())
            except Exception:
                options.append("")

        question = _extract_stem(text)
        logger.info("[题%d/%d] %s | %s" % (idx, total, qtype, question[:50]))

        result = answerer.ask(question, options, qtype)
        if result.get("raw") is None and qtype in ("single", "multiple"):
            logger.warning("题 %d 未取得 AI 回答（接口失败或返回为空）" % idx)

        if _apply(frame, c, option_els, result, logger):
            ok += 1
        else:
            fail += 1
            logger.warning("题 %d 作答未应用（AI 结果：%s）" % (idx, result.get("raw")))

    if auto_submit and total > 0 and fail == 0:
        try:
            btn = frame.locator('button:has-text("提交"), button:has-text("交卷")').first
            btn.click(timeout=10000)
            frame.wait_for_timeout(1200)
            # 确认弹窗
            for b in frame.locator('button:has-text("确定"), button:has-text("确认")').all():
                try:
                    if b.is_visible():
                        b.click()
                except Exception:
                    pass
            frame.wait_for_timeout(1000)
            logger.info("已自动提交测验")
        except Exception as e:
            logger.warning("自动提交失败（可手动提交）：%s" % e)
    elif fail:
        logger.warning("有 %d 题未能作答，已跳过自动提交，请人工核对" % fail)

    return ok, total, fail


def _apply(frame, container, option_els, result, logger) -> bool:
    try:
        rtype = result.get("type")

        if rtype in ("single", "multiple"):
            indices = result.get("indices") or []
            if not indices:
                return False
            applied = False
            for i in indices:
                if not (0 <= i < len(option_els)):
                    continue
                el = option_els[i]
                # 多选：已选中的不要再点，否则会被取消
                if rtype == "multiple" and _is_selected(el):
                    applied = True
                    continue
                el.click()
                frame.wait_for_timeout(300)
                applied = True
            return applied

        if rtype == "judge":
            want = (result.get("text") or "").strip()
            keywords = JUDGE_FALSE if "错" in want or "否" in want else JUDGE_TRUE
            for o in option_els:
                try:
                    txt = o.inner_text().strip()
                except Exception:
                    continue
                if any(k in txt for k in keywords):
                    o.click()
                    frame.wait_for_timeout(300)
                    return True
            return False

        if rtype == "fill":
            answer = (result.get("text") or "").strip()
            if not answer:
                return False
            inp = container.locator(
                'input[type="text"], input:not([type]), textarea'
            ).first
            if inp.count():
                inp.fill(answer)
                return True
            return False

    except Exception as e:
        logger.warning("作答应用失败: %s" % e)
        return False
    return False
