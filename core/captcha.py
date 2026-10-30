# -*- coding: utf-8 -*-
"""学习通「长时间挂机验证码」自动识别与填写。

页面结构（学习通播放器实测，参考 cxmooc-tools vcode.ts）：
    <img  id="imgVerCode">   验证码图片（服务端动态生成）
    <input id="code">        输入框
    <a     id="sub">         提交按钮

图片是 4 位左右的字符验证码，本地用 ddddocr（onnxruntime 离线模型）识别，
不需要联网、不依赖第三方打码平台。识别后填入并提交，再核对弹窗是否消失；
失败会点图片刷新重试。
"""

import re
import threading
import time

# 检测当前 frame 里是否存在验证码弹窗；顺带兼容「图片没有 id」的老皮肤
_DETECT_JS = r"""
() => {
  const inp = document.querySelector('input#code');
  const sub = document.querySelector('a#sub');
  if (!inp || !sub) return null;
  const vis = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' &&
           r.width > 10 && r.height > 5;
  };
  if (!vis(inp) || !vis(sub)) return null;
  let el = document.querySelector('#imgVerCode');
  if (!el) {
    const box = inp.closest('div, form, table') || inp.parentElement;
    el = box ? box.querySelector('img, canvas') : null;
  }
  if (!el) return null;
  if (!vis(el)) return null;
  const r = el.getBoundingClientRect();
  if (!el.id) el.id = '__wbCapEl';
  return {sel: '#' + el.id, w: Math.round(r.width), h: Math.round(r.height)};
}
"""

_OCR_CLEAN = re.compile(r"[^0-9A-Za-z]")

_cfg = {"enabled": True, "max_retries": 5, "min_interval": 10.0}
_ocr = None
_ocr_lock = threading.Lock()
_ocr_broken = False
_last_attempt = 0.0


class _NullLogger(object):
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


def configure(cfg, logger=None):
    """从 config.json 的 captcha 段读取配置，并预热 OCR。"""
    if isinstance(cfg, dict):
        for k in ("enabled", "max_retries", "min_interval"):
            if k in cfg:
                _cfg[k] = cfg[k]
    if logger is not None and _cfg.get("enabled", True):
        if _get_ocr(logger) is not None:
            logger.info("验证码自动识别已启用（ddddocr 离线模型）")


def _get_ocr(logger):
    global _ocr, _ocr_broken
    if _ocr_broken:
        return None
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                try:
                    import ddddocr

                    try:
                        _ocr = ddddocr.DdddOcr(show_ad=False)
                    except Exception:
                        # 打包成 exe 后若默认相对路径找不到模型，显式指到 _MEIPASS 下
                        import os as _os
                        import sys as _sys

                        base = getattr(_sys, "_MEIPASS", "")
                        for name in ("common_old.onnx", "common.onnx"):
                            path = _os.path.join(base, "ddddocr", name)
                            if base and _os.path.exists(path):
                                _ocr = ddddocr.DdddOcr(
                                    show_ad=False, import_onnx_path=path
                                )
                                break
                        if _ocr is None:
                            raise
                except Exception as e:  # 缺少依赖时降级，不影响主流程
                    _ocr_broken = True
                    logger.warning("验证码 OCR 组件不可用（%s），将跳过验证码自动识别" % e)
                    return None
    return _ocr


def _frames(page):
    frames = [page]
    try:
        frames += list(page.frames)
    except Exception:
        pass
    return frames


def find(page, frames=None):
    """返回 (frame, info) 或 (None, None)。

    frames 限定要检查的 frame（默认全部）。学习通课程页会预加载十几个任务点
    iframe，其中可能有冻结/跨域的 frame，evaluate 会一直不返回；所以调用方
    应优先只传「顶层页 + 当前视频 frame」。
    """
    for f in (frames if frames is not None else _frames(page)):
        try:
            info = f.evaluate(_DETECT_JS)
        except Exception:
            continue
        if info and info.get("sel"):
            return f, info
    return None, None


def present(page):
    return find(page)[0] is not None


def _refresh(frame):
    try:
        frame.locator("#imgVerCode").first.click(timeout=2000)
        return True
    except Exception:
        pass
    try:
        frame.locator("input#code").first.press("Enter", timeout=1500)
    except Exception:
        pass
    return False


def _prep(png):
    """小图放大 2 倍再识别，ddddocr 对偏小的验证码更稳。"""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(png)).convert("RGB")
        if im.width < 160:
            im = im.resize((im.width * 2, im.height * 2), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return png


def _read_code(frame, info, logger):
    sel = info.get("sel") or "#imgVerCode"
    try:
        png = frame.locator(sel).first.screenshot(type="png", timeout=5000)
    except Exception as e:
        logger.warning("验证码截图失败：%s" % e)
        return ""
    if not png:
        return ""
    ocr = _get_ocr(logger)
    if ocr is None:
        return ""
    try:
        raw = ocr.classification(_prep(png)) or ""
    except Exception as e:
        logger.warning("验证码识别失败：%s" % e)
        return ""
    return _OCR_CLEAN.sub("", raw)


def handle_captcha(page, logger=None, frames=None):
    """检测并处理验证码。

    frames：限定检查范围（建议只传顶层页 + 当前视频 frame，避免扫到冻结 frame）。
    返回 True 表示「当前存在验证码弹窗」（已尝试识别/提交），
    调用方应暂停播放进度判断，等验证码处理完再继续。
    """
    global _last_attempt
    log = logger or _NullLogger()
    if not _cfg.get("enabled", True):
        return False

    frame, info = find(page, frames)
    if frame is None:
        return False

    now = time.time()
    if now - _last_attempt < float(_cfg.get("min_interval", 10.0)):
        return True  # 冷却中，避免同一张图被反复识别
    _last_attempt = now

    log.info("检测到验证码弹窗（%dx%d），开始自动识别…" % (info.get("w", 0), info.get("h", 0)))
    retries = max(1, int(_cfg.get("max_retries", 5)))
    for attempt in range(1, retries + 1):
        code = _read_code(frame, info, log)
        if not code:
            log.info("验证码第 %d/%d 次识别为空，刷新重试" % (attempt, retries))
            _refresh(frame)
            page.wait_for_timeout(1200)
            continue

        log.info("验证码第 %d/%d 次识别结果：%s" % (attempt, retries, code))
        try:
            inp = frame.locator("input#code").first
            inp.click(timeout=2000)
            inp.fill("", timeout=2000)
            inp.type(code, delay=60)
            page.wait_for_timeout(250)
            frame.locator("a#sub").first.click(timeout=3000)
        except Exception as e:
            log.warning("填写/提交验证码失败：%s" % e)
            page.wait_for_timeout(800)
            continue

        page.wait_for_timeout(2200)
        if find(page, frames)[0] is None:
            log.info("验证码已通过（%s），继续学习" % code)
            _last_attempt = 0.0
            return True

        log.info("验证码未通过（%s），刷新后重试" % code)
        _refresh(frame)
        page.wait_for_timeout(1200)

    log.warning("验证码多次尝试仍未通过，稍后会自动再试")
    return True
