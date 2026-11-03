# -*- coding: utf-8 -*-
"""LR 桥的**最小生产客户端**：ADD / SELECT / EXPORTTIF / PROOF / POINTSWATCH / MASKPARAM。

生产只需要这几条命令（`LR设计.md` §5 接口事实）：
  · 写 XMP → ADD 进目录 → 导出 ③（EXPORTTIF 导的是**当前选中**那张，必须先 SELECT）；
  · 调一根→量全局：`PROOF` 单张 JPEG 1024/q90/sRGB/不锐化，不走最终 TIFF；
  · 局部：先用 `POINTSWATCH` 建**肤色**（local point-color）蒙版/色票，
    再用 `MASKPARAM` 写局部滑杆（直接 `MASK ... LocalExposure2012=` 不落地）；
  · `MASKPARAM` 必须**回读**校验：读回来的值不匹配就当这一步没做，不许静默通过。

桥本身在 `lrfinish/lrplugin`（Lightroom 插件），命令队列在 `lrfinish/lr_cmd`。
约定：每条命令带 id，桥回 `<id>|OK ...`；超时返回 `TIMEOUT`（不是空串）。
"""
from __future__ import annotations

import os
import shutil
import sys
import time

_FINISH = r'E:\svStudio\svFilm\lrfinish'
if _FINISH not in sys.path:
    sys.path.insert(0, _FINISH)

import paths                                                    # noqa: E402

paths.ensure_import_path()
import bridge_client                                            # noqa: E402

run = bridge_client.run


def ping(timeout: float = 5.0) -> str:
    return run('PING', timeout=timeout)


def add(path: str, timeout: float = 240.0) -> str:
    # ★ 必须先归一化：LR 的 ADD 只吃纯反斜杠。若路径里混了 '/'（例如从
    #   Bash/MSYS 传进来的 `E:/ds/photo\...`），Lua 侧解析失败 ⇒ 回 `ok=false`，
    #   现象是“ADD 失败”且随后 LR 直接退出，很容易误判成插件或沙箱问题。
    path = os.path.normpath(path)
    r = run('ADD ' + path, timeout=timeout)
    if 'ADDED' not in r.upper() and 'EXIST' not in r.upper():
        raise RuntimeError('ADD 失败 %s: %s' % (os.path.basename(path), r[:160]))
    return r


def select(path: str, timeout: float = 120.0) -> str:
    return run('SELECT ' + os.path.normpath(path), timeout=timeout)


def export_tif(src: str, out_dir: str, tag: str = '', timeout: float = 240.0) -> str:
    """SELECT 当前图 → EXPORTTIF 到独立子目录 → 返回 TIF 路径。

    ★ 不传边长参数：实测 `EXPORTTIF <dir> 2048` 会被吞成 1000px，
      不传才是原生尺寸（缩放交给测量层统一到交付域）。
    ★ 每次用**独立子目录**：EXPORTTIF 可能把当前选中之外的残留也带出来。
    """
    sub = os.path.normpath(os.path.join(out_dir, tag or ('exp_%d' % int(time.time() * 1000))))
    if os.path.isdir(sub):
        shutil.rmtree(sub, ignore_errors=True)
    os.makedirs(sub, exist_ok=True)
    select(src)
    r = run('EXPORTTIF %s' % sub, timeout=timeout)
    for _ in range(20):
        fs = [os.path.join(sub, f) for f in os.listdir(sub)
              if f.lower().endswith(('.tif', '.tiff'))]
        if fs:
            return sorted(fs)[0]
        time.sleep(0.25)
    raise RuntimeError('EXPORTTIF 没有产出 TIF（%s）: %s' % (src, str(r)[:160]))


def proof(src: str, out_dir: str, size: int = 1024, quality: float = 0.90,
          timeout: float = 240.0) -> str:
    """单张 proof JPEG（当前设置、1024、q90、sRGB、不锐化）；返回文件路径。

    只用于“调一根→量全局”；肤色/局部/细高光仍看最终 TIFF。
    """
    sub = os.path.join(out_dir, 'proof_%d' % int(time.time() * 1000))
    os.makedirs(sub, exist_ok=True)
    select(src)
    r = run('PROOF %s %s %d %.2f' % (src, sub, int(size), float(quality)),
            timeout=timeout)
    fs = [os.path.join(sub, f) for f in os.listdir(sub)
          if f.lower().endswith(('.jpg', '.jpeg'))]
    if not fs:
        raise RuntimeError('PROOF 没有产出 JPEG（%s）: %s' % (src, str(r)[:160]))
    return sorted(fs)[0]


def point_watch(path: str, h: float, s: float, l: float, rng: float,
                sat_scale: float, lum_scale: float = 0.0,
                hue_shift: float = 0.0,
                timeout: float = 180.0) -> str:
    """建**肤色蒙版**（local point-color 色票）并写 HueShift / SatScale / LumScale。"""
    return run('POINTSWATCH %s %.6f %.6f %.6f %.4f %.4f %.4f %.4f'
               % (path, float(h), float(s), float(l), float(rng),
                  float(hue_shift), float(sat_scale), float(lum_scale)),
               timeout=timeout)


def mask_param(path: str, key: str, value: float, index: int = 1,
               timeout: float = 180.0) -> float:
    """写第 index 组蒙版的局部滑杆，**回读校验**；不匹配就抛。"""
    r = run('MASKPARAM %s %s %.6f %d' % (path, key, float(value), int(index)),
            timeout=timeout)
    if 'target=MISMATCH' in r:
        raise RuntimeError('%s 目标没切过去：%s' % (key, r[:160]))
    rb = None
    if 'readback=' in r:
        token = r.split('readback=')[-1].split()[0]
        try:
            rb = float(token)
        except ValueError:
            rb = None
    if rb is None or abs(rb - float(value)) > 1e-6:
        raise RuntimeError('%s 没落下去（回读 %s ≠ %.6f）：%s'
                           % (key, rb, float(value), r[:160]))
    return rb
