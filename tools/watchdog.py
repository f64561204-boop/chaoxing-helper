# -*- coding: utf-8 -*-
"""学习通助手看门狗：盯住进程与日志，卡死/崩溃时自动重启续播。

用法（独立进程常驻）：
    python tools/watchdog.py

日志：<桌面目录>/data/watchdog.log
停止：在 data 目录放一个 watchdog.stop 空文件。
"""
import json
import os
import re
import subprocess
import sys
import time

def _default_dir():
    """定位助手安装目录。

    优先级：环境变量 CX_HELPER_DIR > 常见安装位置（桌面 / %LOCALAPPDATA%）。
    不硬编码任何用户名，方便开源共享。
    """
    env = os.environ.get("CX_HELPER_DIR")
    if env and os.path.isfile(os.path.join(env, "学习通自动学习助手.exe")):
        return env
    home = os.path.expanduser("~")
    cands = [
        os.path.join(home, "Desktop", "学习通自动学习助手"),
        os.path.join(os.environ.get("LOCALAPPDATA", home), "Programs", "学习通自动学习助手"),
        os.path.join(home, "学习通自动学习助手"),
    ]
    for c in cands:
        if os.path.isfile(os.path.join(c, "学习通自动学习助手.exe")):
            return c
    return cands[0]


EXE_DIR = _default_dir()
EXE = os.path.join(EXE_DIR, "学习通自动学习助手.exe")
DATA = os.path.join(EXE_DIR, "data")
RUN_LOG = os.path.join(DATA, "run.log")
WLOG = os.path.join(DATA, "watchdog.log")
STOP = os.path.join(DATA, "watchdog.stop")

try:
    with open(os.path.join(EXE_DIR, "config.json"), encoding="utf-8") as _f:
        _pl = json.load(_f).get("player", {}) or {}
    SPEED = float(_pl.get("speed", 1.0) or 1.0)
    if _pl.get("auto_speed", True):
        # 自适应倍速：卡顿时会自动降档，ETA 必须按最低档算，否则会误判卡死
        SPEED = min(SPEED, float(_pl.get("auto_speed_min", 1.0) or 1.0))
except Exception:
    SPEED = 1.0
SPEED = max(0.5, min(2.0, SPEED))

TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
START_RE = re.compile(r"视频已开始播放（总时长 (\d+) 秒")
LOADED_RE = re.compile(r"找到音视频，开始播放")
DONE_RE = re.compile(r"任务点已标记完成|视频播放完成|--- 处理：|已切到本节")

COOLDOWN = 300  # 重启后 5 分钟内不再评估，避免重启风暴
BOOT = time.time()  # 启动宽限期：前 5 分钟只看进程，不判卡死（避免旧日志误判）
_last_restart = 0.0


def log(msg):
    line = "%s [WATCHDOG] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(WLOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def helper_alive():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq 学习通自动学习助手.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore", timeout=15,
        ).stdout
        return "学习通自动学习助手" in out
    except Exception:
        return False


def kill_all():
    log("结束助手进程与 Playwright 驱动…")
    subprocess.run(["taskkill", "/F", "/IM", "学习通自动学习助手.exe"],
                   capture_output=True, timeout=20)
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
        "Where-Object { $_.CommandLine -like '*playwright*driver*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=30)
    time.sleep(6)


def start_helper():
    log("启动助手：%s" % EXE)
    try:
        subprocess.Popen([EXE], cwd=EXE_DIR, close_fds=True)
    except Exception as e:
        log("启动失败：%s" % e)
        return
    time.sleep(20)


def read_lines(n=120):
    try:
        with open(RUN_LOG, encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def parse_ts(line):
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def evaluate():
    """返回卡死原因字符串；正常返回 None。"""
    lines = read_lines()
    if not lines:
        return None
    now = time.time()
    PENDING_RE = re.compile(r"找到音视频，开始播放|--- 处理：|已切到本节")
    # 程序已明确放弃某个任务后，理应很快进入下一个；迟迟没动静说明卡住了
    GIVEUP_RE = re.compile(r"始终没有播起来|未播完，不记进度")

    # 从后往前找「最后一条事件」，只按它判断，避免被上一条视频的完成信号干扰
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        ts = parse_ts(ln)

        m = START_RE.search(ln)
        if m:
            dur = int(m.group(1))
            if not ts:
                return None
            ok = any(DONE_RE.search(x) for x in lines[i + 1:])
            if ok:
                return None
            eta = ts + max(600, dur / SPEED + 300)
            if now > eta:
                return "视频起播于 %s（时长 %ds），到 ETA 仍无完成信号" % (
                    time.strftime("%H:%M:%S", time.localtime(ts)), dur)
            return None

        if PENDING_RE.search(ln):
            if ts and now - ts > 300:
                return "最后事件「%s」已过去 5 分钟仍无进展" % ln.strip()[:60]
            return None

        if GIVEUP_RE.search(ln):
            # 已明确放弃该任务，正常情况下几秒内就会进入下一个任务
            if ts and now - ts > 300:
                return "任务失败后 5 分钟仍无新进展，疑似卡死"
            return None

        if re.search(r"任务点已标记完成|视频播放完成", ln):
            return None

    try:
        age = now - os.path.getmtime(RUN_LOG)
    except Exception:
        age = 0
    if age > 720:
        return "日志已 %.0f 分钟无更新" % (age / 60)
    return None


def main():
    global _last_restart
    log("看门狗启动，监控 %s" % EXE)
    cycle = 0
    while True:
        time.sleep(60)
        cycle += 1
        if os.path.exists(STOP):
            log("检测到停止文件，退出。")
            return

        if not helper_alive():
            log("ALERT: 助手进程不在 → 重启")
            kill_all()
            start_helper()
            _last_restart = time.time()
            continue

        if time.time() - _last_restart < COOLDOWN:
            continue
        if time.time() - BOOT < 300:
            continue

        reason = evaluate()
        if reason:
            log("ALERT: %s → 杀掉并重启续播" % reason)
            kill_all()
            start_helper()
            _last_restart = time.time()

        if cycle % 10 == 0:
            log("心跳：进程存活，已监控 %d 个周期" % cycle)


if __name__ == "__main__":
    main()
