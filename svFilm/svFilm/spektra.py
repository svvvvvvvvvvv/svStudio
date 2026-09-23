# -*- coding: utf-8 -*-
r"""spektrafilm 装载器 + 大图条带包装 —— **这一层不再做"选卷/配纸/调旋钮"**。

## 09-23 边界重划后它只剩两件事

  1. `_sf()` —— 懒加载 spektrafilm，并**钉死加载的是仓库自带那一份**（别被 pip 装的老副本抢先）。
  2. `_install_band()` —— 大图按行切条带（原图尺寸出图靠它才跑得动，且逐位不变）。

**胶片风格怎么来的**：不在这一层。`presets.py` 直接读 `data/presets/*.json`
（public GUI 导出的完整参数快照，9 条），`_sf()` 只是给它把 spektrafilm 找出来。

## spektrafilm 是什么

不用 LUT，**重建整条光化学管线**：负片曝光 → 显影 → 印相 → 扫描，
全部从 `density_curves`(H&D) + `log_sensitivity`(光谱) + 染料密度推出来。
所以它自带：H&D 曲线、`dir_couplers`（彩度）、分通道颗粒、halation、柔光、输出锐化
⇒ 我们自己**不许再叠一层**（历史上叠过，09-23 全删了）。

## ⚠ 许可
spektrafilm 的 profiles 与生成的 LUT = **CC BY-SA 4.0 + 自定义前言**
（署名 + 相同方式共享）。用了它，我们仓库的分发也要跟着这条。

## ⚠ 落点谁定
**曝光归 svFilm（`tone.py`），在 spektrafilm 之前** —— 曝光是"给胶片多少光"。
spektrafilm 自己那套 `auto_exposure` 只保证"负片正确曝光"，不管好看 ⇒ 必须关掉
（`presets.render` 里已经关了）。
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
    """懒加载 spektrafilm（**注意**：必须能找到它的 src 目录）。

    找的顺序（第一个存在的胜出）：
      ① 环境变量 `SPEKTRAFILM_ROOT`
      ② `<本仓库根>/_tools/spektrafilm/src`       ← 仓库自带（vendor 进来的，clone 完就有）
      ③ `<本仓库根的上级>/_tools/spektrafilm/src` ← 老布局（引擎当年住在 摄影助手/svFilm 时用的）
    都没有 ⇒ 直接报清楚该怎么办，别让它冒一个莫名其妙的 ImportError。
    """
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
        # ★★ 断言真的加载到了 `root` 那一份 —— **别再让它静默拿别的副本**。
        #   本机 spektrafilm 是 `pip install -e` 装的，而那个 editable 安装指向一个
        #   **已经退休的老目录**（site-packages 里 `__editable__*.pth` 只有一行老路径）。
        #   我们把仓库自带那份插进 `sys.path` 最前 ⇒ 正常情况加载的就是它。
        #   但只要有谁"直接 import 一下"（不走这个函数），就会拿到老副本且**不报错**
        #   ⇒ 验的是老代码、结论不可信（又一种"假绿"）。这里钉死它。
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


# =====================================================================================
# ★★★ 大图按行切条带：干掉「整张画面 × 81 光谱波段」那个中间量（09-15 SV 选「A」）
# -------------------------------------------------------------------------------------
# 为什么必须切（**实测撞出来的，不是保守估计**）：原图尺寸 7752×5178 出图会当场死 ——
#   `spektrafilm/model/develop.py` 的
#       density_spectral = contract('ijk, lk->ijl', density_cmy, channel_density)
#   对每个像素拿 3 个 CMY 密度值，乘 (81 波段 × 3) 的密度谱 ⇒ 得到 81 个波段。
#   `(7752 × 5178) × 81 × 8B = 24.2 GiB` 一次分配，而本机提交上限只剩 ~10 GB。
#
# 为什么切了**逐位不变**（关键，别当近似看）：
#   出问题的那条链是 `printing.py` 的 `_film_cmy_to_print_log_raw`（以及 scanning.py 里
#   对应的 `cmy_to_log_xyz`），**整条链全是逐像素的**：3 通道进 → 81 波段 → 3 通道出
#   （`contract("ijk, kl->ijl", light, sensitivity)`），81 **纯属中间量**；
#   `_compute_exposure_factor_midgray` 只依赖固定数据、跟像素无关。
#   ⇒ 每个输出像素**只依赖同一个输入像素** ⇒ 按行切开分别算，拼回来数学上完全相同。
#   实测（(935,1400,3)，整张 vs 条带）：**最大逐位差 0.000e+00**。
#
# ⚠⚠ **绝不改 vendor 文件**：`_tools/README.md` 写明 spektrafilm 是「改动：无，原样拷的」，
#   升级就靠重新 `git archive` 覆盖 ⇒ 在它上面动刀 = 升级必冲突 + 那句话变假。
#   所以在这里做**类级 monkey-patch**（`selftest` 有检查钉着 vendor 里不许出现我们的标记）。
#
# ⚠ 只在 `use_lut=False` 那条路拦：LUT 那条路本来就在 3 维 CMY 空间里查表，不展开 81 波段。
# ⚠ 影响面：`spectral_compute_enlarger`（印相/放大机）与 `spectral_compute_scanner`（扫描）。
# =====================================================================================
BAND_ROWS = 192          # 一条带多少行（192 行 × 7752 宽 × 81 波段 × 8B ≈ 0.9 GB 中间量）
BAND_MIN_PX = 1000000    # 小于这么多像素**不切** —— 700 预览那档保持原来那条路，行为一模一样
_BAND_LOCK = threading.Lock()
_BANDED = [False]
_VENDOR_MARK = 'SVFILM_BAND'      # 只允许出现在我们自己文件里的标记（selftest 拿它查 vendor）


def band_on():
    """自检用：条带包装**装上了没有**（装上 = 大图能出原图尺寸）。"""
    return bool(_BANDED[0])


def _install_band():
    """给 spektrafilm 的两个逐像素「3 → 81 → 3」运算套上按行切条带的外壳。幂等。"""
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
                    # ★ 那条 `@timeit` 装饰器是 `timings[key] = elapsed`（**覆盖**）⇒
                    #   我们逐条带调用它，留下的会是**最后一条带**的时间（假数）。
                    #   这里把**总时间**写回去，别让计时骗人。
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

