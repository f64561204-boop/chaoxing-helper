import logging
import os
import sys


def _fix_console_encoding():
    """Windows 控制台默认编码可能无法输出部分字符，统一成 UTF-8 容错模式。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def get_logger(name: str = "chaoxing", level: str = "INFO", log_file: str = None):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    _fix_console_encoding()
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file:
        try:
            folder = os.path.dirname(os.path.abspath(log_file))
            if folder:
                os.makedirs(folder, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            # 日志写不了不能影响主流程（例如文件被杀毒软件占用）
            pass

    return logger
