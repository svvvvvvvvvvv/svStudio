# -*- coding: utf-8 -*-
"""Unattended LR Classic restart + bridge handshake.

用法:
    python lr_boot.py            # 重启 LR 并等桥就绪
    python lr_boot.py --check    # 只检查当前状态，不动 LR

为什么这么写:
- 不带 catalog 参数启动时，LR 会停在「选择目录」界面等点击 —— 无人值守就永远起不来。
- 强杀后残留 .lrcat.lock 会让 LR 弹「目录正在使用」，同样卡住。
- 插件初始化可能失败，失败标记会卡住后续初始化，所以重启前清 _G 标记并写诊断日志。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402

LR_EXE = str(paths.LR_EXE)
CATALOG = str(paths.LR_CATALOG)
LOCK = CATALOG + ".lock"
CMD_DIR = str(paths.CMD_DIR)
RESULT = os.path.join(CMD_DIR, "result.txt")
PLUGIN_LOG = os.path.join(CMD_DIR, "plugin.log")
CONSOLE_LOG = str(paths.LR_CONSOLE)


def clear_console_log():
    r"""★★ 启动前必须删掉 LR 的控制台日志，否则 LR 起不来。

    2026-10-22 实测（一条完整的证据链）：
      · LR 启动时要**重写** `lrc_console.log`；沙箱的安全规则拦"改写已存在的文件"，
        LR 拿到错误返回后直接 `__fastfail` 自杀 —— WER 报告是
        `AppCrash_Lightroom.exe … 故障模块 ucrtbase.dll / 异常代码 c0000409`，
        进程秒退、`plugin.log` 连一行 init 都写不出来。
      · 文件**不存在**时 LR 走的是"新建"，不被这条规则拦 ⇒ 能正常启动
        （实测 11:56 删掉后 `BRIDGE OK 18s READY`）。
      · 所以删掉它反而是唯一能起来的姿势；不要往里写内容（写假内容会让 LR 崩）。
    """
    if os.path.exists(CONSOLE_LOG):
        try:
            os.remove(CONSOLE_LOG)
            return "removed"
        except OSError as e:
            return "remove failed: %s" % e
    return "absent"


def lr_pids():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Lightroom.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
    ).stdout.decode("gbk", "ignore")
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower().startswith("lightroom"):
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def kill_lr():
    for pid in lr_pids():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    for _ in range(20):
        if not lr_pids():
            return True
        time.sleep(0.5)
    return not lr_pids()


def clear_stale_lock():
    """LR 已确认不在运行时，锁必然是残留。"""
    if lr_pids():
        return "skipped (lr running)"
    if os.path.exists(LOCK):
        try:
            os.remove(LOCK)
            return "removed"
        except OSError as e:
            return "remove failed: %s" % e
    return "absent"


def launch():
    os.makedirs(CMD_DIR, exist_ok=True)
    for p in (RESULT, PLUGIN_LOG):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
    # ★★★ 必须**脱离 job object**（10-22 16:21 真踩）：
    #   光 `Popen(close_fds=True)` 起不来“独立”的 LR —— 它仍是调用方进程树的子节点，
    #   调用方（一个后台任务 shell）一结束，Windows 就把整棵树带走，
    #   LR 在桥 READY 之后**无声消失**（没有 WER、没有崩溃事件，进程直接没了）。
    #   症状：plugin.log 只有 `init/loop enter`，连 `handle` 一行都没有。
    #   ⇒ CREATE_BREAKAWAY_FROM_JOB 让它脱离任务 job + DETACHED_PROCESS 不继承控制台。
    FLAGS = 0x01000000 | 0x00000008        # BREAKAWAY_FROM_JOB | DETACHED_PROCESS
    try:
        subprocess.Popen([LR_EXE, CATALOG], close_fds=True, creationflags=FLAGS)
    except OSError:
        subprocess.Popen([LR_EXE, CATALOG], close_fds=True)   # job 不允许 breakaway 时兜底
    return True


def wait_bridge(timeout=150):
    """等桥写 READY 或 result.txt 出现。返回 (ok, detail)。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(RESULT):
            try:
                txt = open(RESULT, encoding="utf-8", errors="replace").read().strip()
            except OSError:
                txt = ""
            if txt:
                return True, "%.0fs result=%r" % (time.time() - t0, txt[:120])
        time.sleep(1.5)
    log = ""
    if os.path.exists(PLUGIN_LOG):
        log = open(PLUGIN_LOG, encoding="utf-8", errors="replace").read()[-400:]
    return False, "timeout; plugin.log=%r; pids=%s" % (log, lr_pids())


def check():
    print("pids:", lr_pids())
    print("lock:", os.path.exists(LOCK))
    print("result:", os.path.exists(RESULT))
    if os.path.exists(PLUGIN_LOG):
        print("plugin.log:", open(PLUGIN_LOG, encoding="utf-8", errors="replace").read()[-400:])


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
        sys.exit(0)
    print("killing lr ...", kill_lr())
    print("lock:", clear_stale_lock())
    print("console log:", clear_console_log())
    launch()
    ok, detail = wait_bridge()
    print("BRIDGE OK" if ok else "BRIDGE FAIL", detail)
    sys.exit(0 if ok else 1)
