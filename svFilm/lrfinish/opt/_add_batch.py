# -*- coding: utf-8 -*-
r"""把一批底片 import 进 LR 目录（换批次跑之前必须先做）。

★ 为什么必须：桥的 `SELECT/EXPORTTIF` 是按**路径**在目录里找照片的。
  把 tif 拷到新目录 = 新路径 ⇒ 目录里没有 ⇒ SELECT 静默跳过，
  EXPORTTIF 什么都不产出 ⇒ 上层脚本看到的是"量不到脸"（10-23 20:1x 踩过，
  919 那 7 张全废，导出目录是空的）。

用法：python opt/_add_batch.py <目录>
"""
from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, r'E:\lr_agent\opt')
sys.path.insert(0, r'E:\lr_agent\prototype')

import lr_boot                       # noqa: E402
from bridge_client import run        # noqa: E402


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else r'E:\Debug_svStudio\svFilm_out\_b2048_919'
    srcs = sorted(glob.glob(os.path.join(d, '*_svFilm_full.tif')))
    if not srcs:
        print('目录里没有底片：%s' % d); return

    if lr_boot.lr_pids():
        lr_boot.kill_lr(); time.sleep(3)
    print('日志清理: %s ｜ 锁: %s' % (lr_boot.clear_console_log(), lr_boot.clear_stale_lock()))
    lr_boot.launch()
    ok, msg = lr_boot.wait_bridge(150)
    print('桥：%s %s' % ('就绪' if ok else '未就绪', msg))
    if not ok:
        return

    for p in srcs:
        r = run('ADD ' + p, timeout=120)
        print('%-28s %s' % (os.path.basename(p), r))


if __name__ == '__main__':
    main()
