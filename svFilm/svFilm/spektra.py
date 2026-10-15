# -*- coding: utf-8 -*-
r"""spektrafilm 装载器 + 大图条带包装 —— **这一层不做"选卷/配纸/调旋钮"**。

只剩两件事：`_sf()` 懒加载并**钉死仓库自带那一份**；`_install_band()` 大图按行切条带（原图尺寸出图
靠它才跑得动，且逐位不变）。胶片风格不在这层：`presets.py` 直接读 `data/presets/*.json`。
spektrafilm 不用 LUT，**重建整条光化学管线** ⇒ 自带 H&D 曲线、颗粒、halation、柔光、锐化 ⇒ 我们自己
**不许再叠一层**；⚠ profiles / LUT = CC BY-SA 4.0 + 自定义前言。
"""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

from . import config as C

_LOCK = threading.Lock()
_PARAMS = {}          # (film, print) -> 已 init 的 params（**复用**，init 不便宜）
_SF = [None]          # 懒加载的 spektrafilm 模块


def _sf():
    r"""懒加载 spektrafilm（**必须能找到它的 src 目录**）：① `SPEKTRAFILM_ROOT`；② `<仓库根>/_tools/
    spektrafilm/src`；③ `<上级>/_tools/spektrafilm/src`。"""
    if _SF[0] is None:
        here = os.path.abspath(__file__)
        repo = os.path.dirname(os.path.dirname(here))          # .../svFilm（仓库根）
        cands = [
            os.environ.get('SPEKTRAFILM_ROOT'),
            os.path.join(repo, '_tools', 'spektrafilm', 'src'),
            os.path.join(os.path.dirname(repo), '_tools', 'spektrafilm', 'src'),
        ]
        root = next((c for c in cands if c and os.path.isdir(c)), None)
        if root is None:
            raise RuntimeError(
                '找不到 spektrafilm（真卷要用它）。三个办法任选一个：\n'
                '  ① pip install -e <spektrafilm 目录>\n'
                '  ② 设环境变量 SPEKTRAFILM_ROOT=<spektrafilm>/src\n'
                '  ③ 把本仓库 clone 完整（自带 _tools/spektrafilm/）\n'
                '下面这些位置都试过了，一个都不存在：\n    ' + '\n    '.join(str(c) for c in cands))
        if root not in sys.path:
            sys.path.insert(0, root)
        from spektrafilm.runtime import init_params, simulate    # noqa: E402
        # ★★ 断言真加载到了 `root` 那一份（`pip install -e` 的 `.pth` 可能指向已退休的老目录）。
        import spektrafilm as _sfmod                              # noqa: E402
        _got = os.path.abspath(getattr(_sfmod, '__file__', '') or '')
        _want = os.path.abspath(root)
        if not _got.startswith(_want + os.sep):
            raise RuntimeError(
                'spektrafilm 加载到的**不是**我们指定的那一份（有人抢先 import 了别的副本！）\n'
                '  应该来自: %s\n  实际加载: %s\n'
                '  ⇒ 真卷会跑在**别的代码**上，结论不可信。多半是本机 pip 装的那份\n'
                '    （editable 安装、指向已退休的老目录）在路径里抢先了。' % (_want, _got))
        _SF[0] = (init_params, simulate)
        _install_band()          # ★ 大图按行切条带（原图尺寸要靠它才跑得动）
    return _SF[0]


# ★★★ 大图按行切条带：干掉「整张画面 × 81 光谱波段」那个中间量
# 为什么必须切：`develop.py` 把每个像素的 3 个 CMY 密度乘成 81 波段 ⇒ (像素数 × 81 × 8B)
#   一次分配，原图尺寸就是几十 GB。
# 为什么**逐位不变**：那条链（`printing._film_cmy_to_print_log_raw` /
#   `scanning.cmy_to_log_xyz`）**全是逐像素的**（3 通道进 → 81 波段 → 3 通道出，81 纯属
#   中间量）⇒ 按行切开算完拼回来数学上完全相同。
# ⚠⚠ **绝不改 vendor 文件** ⇒ 这里做**类级 monkey-patch**（升级靠 `git archive` 覆盖）。
BAND_ROWS = 192          # 一条带多少行（192 行 × 7752 宽 × 81 波段 × 8B ≈ 0.9 GB 中间量）
BAND_MIN_PX = 1000000    # 小于这么多像素**不切** —— 700 预览那档保持原来那条路，行为一模一样
_BAND_LOCK = threading.Lock()
_BANDED = [False]
_VENDOR_MARK = 'SVFILM_BAND'      # 只允许出现在我们自己文件里的标记（selftest 拿它查 vendor）


def band_on():
    r"""自检用：条带包装装上了没有。"""
    return bool(_BANDED[0])


def _install_band():
    r"""给那两个逐像素「3 → 81 → 3」运算套上按行切条带的外壳。幂等。"""
    with _BAND_LOCK:
        if _BANDED[0]:
            return
        from spektrafilm.runtime.services.spectral_lut_compute import SpectralLUTService
        for _name in ('spectral_compute_enlarger', 'spectral_compute_scanner'):
            _orig = getattr(SpectralLUTService, _name)
            if getattr(_orig, '_svfilm_banded', False):
                continue

            def _make(orig, name):
                def _banded(self, cmy_data, spectral_calculation, data_min, data_max,
                            *, use_lut=False):
                    if use_lut:
                        return orig(self, cmy_data, spectral_calculation, data_min, data_max,
                                    use_lut=True)
                    cmy = np.asarray(cmy_data)
                    if cmy.ndim != 3 or (cmy.shape[0] * cmy.shape[1]) < BAND_MIN_PX:
                        # 小图（700 预览那种）走原路 —— 保证老行为一个字节都不变
                        return orig(self, cmy_data, spectral_calculation, data_min, data_max,
                                    use_lut=False)
                    t0 = time.perf_counter()
                    out = None
                    n = cmy.shape[0]
                    for y0 in range(0, n, BAND_ROWS):
                        y1 = min(y0 + BAND_ROWS, n)
                        part = orig(self, cmy[y0:y1], spectral_calculation,
                                    data_min, data_max, use_lut=False)
                        if out is None:
                            out = np.empty(cmy.shape[:2] + (int(part.shape[-1]),), part.dtype)
                        out[y0:y1] = part
                    # ★ `@timeit` 是 `timings[key] = elapsed`（覆盖）⇒ 逐条带调用只留最后一条带。
                    try:
                        self.timings['%s.%s' % (type(self).__name__, name)] = \
                            time.perf_counter() - t0
                    except Exception:                            # noqa: BLE001
                        pass
                    return out
                _banded._svfilm_banded = True
                _banded.__name__ = name
                _banded.__doc__ = ('%s\n\n★ svFilm 的条带包装（%s）：'
                                   '大图按 %d 行切开跑，逐位不变。vendor 未被改动。'
                                   % (orig.__doc__ or '', _VENDOR_MARK, BAND_ROWS))
                return _banded

            setattr(SpectralLUTService, _name, _make(_orig, _name))
        _BANDED[0] = True

