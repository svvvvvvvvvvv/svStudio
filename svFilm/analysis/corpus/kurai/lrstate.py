# -*- coding: utf-8 -*-
"""LR 状态机：干净副本 + 全零态 + 单旋钮步进 + 导出量测。

为什么要"干净副本"：`WhiteBalance` 一旦被 advDevelopSettings 改成 Custom 就回不去
（GLOBAL 只吃数字，写不了 'As Shot'），而 Custom 下残留的 Temperature/Tint 会一直
参与渲染 —— 实测把同一张脸从 C=13.7/h=63 推到 C=24.3/h=86（看起来像"引擎 TIF 与 LR
零档不同色"，其实是探针自己弄脏的）。所以每个 WB 臂都用 ADD 一份新副本。
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, r'E:\svStudio\svFilm\lrfinish')
sys.path.insert(0, r'E:\svStudio\svFilm\lrfinish\opt')
import paths                                    # noqa: E402

paths.ensure_import_path()
from bridge_client import run                   # noqa: E402


TMP = r'E:\ds\photo\_corpus\kurai\full\_probe_lr'

# 我们用的全部数值键；全零 = "LR 不做任何调整"
ZERO = {
    'Exposure2012': 0.0, 'Contrast2012': 0.0, 'Highlights2012': 0.0,
    'Shadows2012': 0.0, 'Whites2012': 0.0, 'Blacks2012': 0.0,
    'HueAdjustmentOrange': 0.0, 'SaturationAdjustmentOrange': 0.0,
    'LuminanceAdjustmentOrange': 0.0, 'HueAdjustmentYellow': 0.0,
    'SaturationAdjustmentYellow': 0.0, 'LuminanceAdjustmentYellow': 0.0,
    'HueAdjustmentGreen': 0.0, 'SaturationAdjustmentGreen': 0.0,
    'HueAdjustmentBlue': 0.0, 'SaturationAdjustmentBlue': 0.0,
    'SplitToningShadowHue': 0.0, 'SplitToningShadowSaturation': 0.0,
    'SplitToningHighlightHue': 0.0, 'SplitToningHighlightSaturation': 0.0,
    'SplitToningBalance': 0.0, 'ShadowTint': 0.0,
    # ★ 校准面板（原色）：真键名 = XMP 原名 crs:RedHue / crs:RedSaturation …
    #   2026-10-28 实测：写成 'RedPrimarySaturation' 那套 ⇒ 回读 nil（**LR 静默丢弃**），
    #   写成 'RedSaturation' ⇒ 回读 VERIFIED。旧名导致"原色没权限"的历史结论全是假的。
    'RedHue': 0.0, 'RedSaturation': 0.0,
    'GreenHue': 0.0, 'GreenSaturation': 0.0,
    'BlueHue': 0.0, 'BlueSaturation': 0.0,
    'Vibrance': 0.0, 'Saturation': 0.0,
}

LINEAR_CURVE = 'ToneCurvePV2012:' + ','.join(
    '%d,%d' % (i * 255 // 15, i * 255 // 15) for i in range(16))


def spec(d):
    return ','.join('%s=%.4f' % (k, v) for k, v in d.items())


def expo(src, tag, edge=None):
    """导出并返回 TIF 路径。

    ★★ 边长参数**不要传**：实测（10-28）
        `EXPORTTIF <dir> 2048` / `2400` → 出 **1000×666**（约束被吞，固定 1000）
        `EXPORTTIF <dir>`             → 出**原生尺寸**（1400×933）
      1000 px 上量皮肤会让小脸掉进 band 兜底（实测 2891/3024 在 1400 上是 main，
      在 1000 px 导出上就 invalid）。所以一律**原生导出**，缩放交给 `measure.load()`
      统一到 1080 域。
    ★★ `EXPORTTIF` 导的是**当前选中**那张，不是参数里那张。批次循环里"先全部应用、
       再统一导出"会三张都导成最后一张（实测过：三张量出完全相同的数）。
       所以导出前必须显式 SELECT。
    """
    od = os.path.join(TMP, tag)
    os.makedirs(od, exist_ok=True)
    for f in os.listdir(od):
        try:
            os.remove(os.path.join(od, f))
        except OSError:
            pass
    run('SELECT ' + src, timeout=120)
    r = run('EXPORTTIF %s' % od, timeout=240)
    time.sleep(0.25)
    fs = [os.path.join(od, f) for f in os.listdir(od) if f.lower().endswith(('.tif', '.tiff'))]
    if not fs:
        raise RuntimeError('no tif for %s (%s)' % (tag, r))
    return fs[0]


_N = [0]


def fresh(src, name):
    """复制一份新副本并 ADD 进 catalog（WB 回到 As Shot）。

    ★ 文件名必须每次唯一：同名文件删了再 ADD 会被 LR 拒（已进 catalog，
      `AgImportSession.addOnePhotoToLibrary: failed to import photo`）。
    """
    os.makedirs(TMP, exist_ok=True)
    _N[0] += 1
    stamp = '%03d_%d' % (_N[0], int(time.time() * 1000) % 100000)
    dst = os.path.join(TMP, '%s__%s.tif' % (name, stamp))
    shutil.copy2(src, dst)
    r = run('ADD ' + dst, timeout=240)
    if 'ADDED' not in r and 'EXISTS' not in r.upper():
        raise RuntimeError('ADD failed: %s' % r)
    run('SELECT ' + dst, timeout=120)
    return dst


def zero(src):
    run('CURVES %s %s' % (src, LINEAR_CURVE), timeout=180)
    r = run('GLOBAL %s %s' % (src, spec(ZERO)), timeout=120)
    return r


def apply(src, kv):
    return run('GLOBAL %s %s' % (src, spec(kv)), timeout=120)


def measure_all(src, tag):
    from measure import measure          # 旧尺子，仅诊断用；新测量在 measure_all.py
    m = measure(expo(src, tag))
    return summary(m)


def summary(m):
    f = m.get('face') or {}
    c = m.get('color') or {}
    return dict(
        L=f.get('L'), C=f.get('C'), h=f.get('h'), n=f.get('n'),
        face_mode=m.get('face_mode'), face_valid=m.get('face_valid'),
        fg=m.get('fg'), p99=m.get('fg_p99'), p1=m.get('fg_p1'),
        sa=c.get('shadow'), ma=c.get('mid'), ha=c.get('high'),
    )


# 干净的源：一支引擎 TIF（把 LR 侧全部参数压成"零"）
def base_source():
    return r'E:\ds\photo\_corpus\kurai\full\look_first_919\engine_tif\DSCF1289_svFilm_full.tif'
