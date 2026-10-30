# -*- coding: utf-8 -*-
r"""_lib / measure.py —— 测量管线。

来源 = `颜色体检_1013\m1_measure.py`（**全项目测量基准**，已自证与基线 max|差| = 0.0000）。
★ 本模块的**算式逐字保留**，只做了两件不影响口径的事：
  ① 把底盘路径/预设表参数化（专题可传自己的 `presets` / `camera_dir` / `side`）；
  ② 把 `to900` 公开为 `to_domain`（`to900` 保留为别名）。

## 口径（逐字照抄，不发明）
```
统计域   = 长边 900（先 LANCZOS 缩放、再 to_lab）—— 与 datapack_1011/build.py 的 `u8w -> lab` 一致
近中性轴 = C* <= 8，五档 (0,20,40,60,80,100)，**逐像素中位**（build.py:526-535）
D1_midC  = L* 15-40 / 40-65 / 65-90 的 C* 中位（全像素，与 build.py 的 D1 同定义）
暖/冷区  = 暖 (hue<90 | hue>=315) & C*>=6；冷 (180<=hue<300) & C*>=6（build.py:513-514）
分色相带 = B6：blue 240-270 / cyan 180-210 / green 90-150 / yellow 60-90 / orange 30-60 / red 0-30
颊       = **三种口径，由 `SV_CHEEK` 选**（见 `roi_masks`，`CHEEK_LAST()` 可回读实际用了哪个）：
           · `skin`（**缺省**，10-18 定）= FACE 掩膜 ∩ 「皮肤似」像素筛
             （L*∈[30,92] ∧ C*∈[5,22] ∧ a*∈[2,26] ∧ b*∈[4,28]，900 域 Lab 上判，n ≥ 200）
           · `circle` = YuNet 取圆(眼中心 +- 0.26*face_w, y = 眼<->嘴角 55%, r = 0.115*face_w)
             ∩ FACE(3)（旧口径，留作 A/B）
           · 兜底 = `skin` 算不出来时（< 200 px 或异常）自动退回 `circle`
           ⚠ `person.masks` 的 SKIN(2) 通道**不是肤色**（与 FACE 零重叠）⇒ 别拿它当脸
手臂     = SKIN(2) 去 (∪ 颊圆 x1.6)；不 ∩ FACE（与 919 四臂读数同口径）
⚠ `stat()` 的**进入门槛是 30 px**，而 `AGENTS` §一.6 要求**颊掩膜 ≥ 300 px 才算脸读数**
  —— 这个 300 的筛子**不在本模块**（在专题脚本/判分侧）。本模块**故意不改 30**：
  改它会让 3 张图（10-16 实测 93/125/175 px）的 `cheek` 从"有值"变 None ⇒ 改读数 = 改口径。
⚠ 不写 `=>`（雅黑无字形）；不用 % 格式化注记。
```

## 主函数（签名）
```
to_domain(im, side=900)                 -> PIL.Image         长边缩到 side（LANCZOS）
to_lab900(im, side=900)                 -> ndarray(H,W,3)    float64，0..1 输入域
stat(lab, m)                            -> dict|None         ROI 中位读数（L/C/a/b/h/Crank）
measure(lab, cheekM, armM)              -> dict              ★ 全量读数（口径的核心）
make_detector(model=YUNET)              -> cv2.FaceDetectorYN
roi_masks(im9, det)                     -> (cheekM, armM, circles, nface)
read_image(path, det=None, side=900)    -> dict(lab, im9, cheekM, armM, circles, nface, reading)
read_arms(...)                          -> dict             多臂批量（专题批量的入口）
```

⚠ **口径的历史包袱（现状已写在上面的口径块里）**：旧注记说"颊 = 圆 ∩ (SKIN ∪ FACE)"，
   而代码从来没有 `∪ SKIN` 这一支（SKIN 与 FACE 零重叠 ⇒ 并集等于 FACE）。10-18 起缺省
   走 **`SV_CHEEK=skin`**（FACE ∩ 皮肤似像素筛），旧圆口径保留为 `SV_CHEEK=circle`。
   ★ **本库不擅自改口径** —— 那是全项目测量基准，要改必须由用户拍板；
     本次只把「宽当长边」那个 bug 修掉（竖构图下肤色口径原来会静默失效）。
"""
from __future__ import annotations

import io
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageOps

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:                  # 让 `import _lib.measure` 两种用法都成立
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402
import _lib.jsonsafe as _J                                                    # noqa: E402

SIDE = 900
MODEL = _lib.YUNET
MEASURE_VERSION = _lib.MEASURE_VERSION      # 口径版本（改动测量算法必须 bump）
B6 = [('blue', 240, 270), ('cyan', 180, 210), ('green', 90, 150),
      ('yellow', 60, 90), ('orange', 30, 60), ('red', 0, 30)]
NA_BANDS = ('0_20', '20_40', '40_60', '60_80', '80_100')
D1_BANDS = ((15, 40), (40, 65), (65, 90))
# 默认三臂（`颜色体检_1013\m1_measure.py` 的 PRESETS）
# ★★ 10-15：预设只剩「鹿井预设」一条 ⇒ 默认臂只剩它。
PRESETS = [('鹿井预设', 'kurai')]

_eng = [False]
# ★ 最近一次 `roi_masks()` 用的颊口径与结果（给"这次到底按哪个口径量的"留一条机器可读的账）
_CHEEK_LAST = dict(mode='skin', skin_n=None, err=None, applied=None)


def CHEEK_LAST():
    r"""最近一次 `roi_masks()` 的颊口径账（**不改变任何读数**，只做可观测性）。

    @returns {dict} mode（`SV_CHEEK` 的取值）/ skin_n / applied（是否真的用了肤色口径）/ err
    """
    return dict(_CHEEK_LAST)


def ensure_engine():
    r"""★ 首次用到 svFilm 之前调 `bootstrap_engine()`（`.pth` 坑）。@returns {None}"""
    if not _eng[0]:
        _lib.bootstrap_engine()
        _eng[0] = True


def open_rgb(path):
    r"""打开 -> EXIF 转正 -> RGB。@returns {PIL.Image}"""
    return ImageOps.exif_transpose(Image.open(path)).convert('RGB')


# ---------------------------------------------------------------- 统计域

def to_domain(im, side=SIDE):
    r"""长边缩到 `side`（LANCZOS）。@returns {PIL.Image}"""
    k = side / float(max(im.size))
    return im.resize((max(1, int(round(im.width * k))), max(1, int(round(im.height * k)))),
                     Image.LANCZOS)


# 原名别名（专题脚本迁移期用）
to900 = to_domain


def to_lab900(im, side=SIDE, do_exif=True):
    r"""PIL 图 -> 900 域 Lab 的 float64 ndarray（0..1 输入域，再 `color.to_lab`）。

    ★ 与 `datapack_1011/build.py` 的 `u8w -> lab` 同口径：先缩放、再 to_lab。
    @param im {PIL.Image} 任意尺寸
    @param side {int} 长边
    @param do_exif {bool} 是否先做 EXIF 转正（统计域已转正过就传 False）
    @returns {ndarray(H,W,3)} float64
    """
    ensure_engine()
    from svFilm import color
    if do_exif:
        im = ImageOps.exif_transpose(im)
    d9 = np.asarray(to_domain(im.convert('RGB'), side), np.float64) / 255.0
    return color.to_lab(np.ascontiguousarray(np.clip(d9, 0, 1)))


# ---------------------------------------------------------------- ROI

def vmed(a, b):
    r"""向量中位的色相角（度）。@returns {float} 0..360"""
    return math.degrees(math.atan2(float(np.median(b)), float(np.median(a)))) % 360.0


def stat(lab, m):
    r"""ROI 中位读数。`m` 为 None 或像素 < 30 => None。

    @returns {dict|None} n / L / C / a / b / h / Crank（Crank = 该 ROI 内 C 低于全图 C 中位的比例）
    """
    if m is None or int(m.sum()) < 30:
        return None
    L = lab[..., 0][m]
    a = lab[..., 1][m]
    b = lab[..., 2][m]
    C = np.hypot(a, b)
    return dict(n=int(m.sum()), L=float(np.median(L)), C=float(np.median(C)),
                a=float(np.median(a)), b=float(np.median(b)),
                h=vmed(a / np.maximum(C, 1e-6), b / np.maximum(C, 1e-6)),
                Crank=float((C < np.median(np.hypot(lab[..., 1], lab[..., 2]))).mean()))


def measure(lab, cheekM, armM):
    r"""★ 全量读数（**口径的核心，逐字保留**）。

    @param lab {ndarray(H,W,3)} 900 域 Lab
    @param cheekM {ndarray(H,W) bool|None} 颊掩膜（圆 ∩ FACE）
    @param armM {ndarray(H,W) bool|None} 臂掩膜（SKIN 去颊圆 x1.6）
    @returns {dict}
      `neutral_axis` 五档 a/b/C/n · `neutral_share` · `D1_midC` 三档 ·
      `rel`(warmC/coolC/warmC_minus_coolC/warmC_over_coolC/warm_share/cool_share/blue_over_orange) ·
      `bands` 六带 share/C · `img`(L/C/Cmean) · `cheek` · `arm` · `roi`(仅颊臂都有时)
    """
    L = lab[..., 0]
    a_ = lab[..., 1]
    b_ = lab[..., 2]
    C = np.hypot(a_, b_)
    Hue = np.degrees(np.arctan2(b_, a_)) % 360.0
    npx = float(L.size)
    o = {}
    nb = C <= 8.0
    ax = {}
    for lo, hi in zip((0, 20, 40, 60, 80), (20, 40, 60, 80, 100)):
        mm = nb & (L >= lo) & (L < hi)
        ax['%d_%d' % (lo, hi)] = dict(a=float(np.median(a_[mm])), b=float(np.median(b_[mm])),
                                      C=float(np.median(C[mm])), n=int(mm.sum()))
    o['neutral_axis'] = ax
    o['neutral_share'] = float(nb.mean() * 100.0)
    o['D1_midC'] = [float(np.median(C[(L >= lo) & (L < hi)])) for lo, hi in D1_BANDS]
    warm = ((Hue < 90) | (Hue >= 315)) & (C >= 6)
    cool = (Hue >= 180) & (Hue < 300) & (C >= 6)
    nw, nc = int(warm.sum()), int(cool.sum())
    wc = float(np.median(C[warm])) if nw > 200 else float('nan')
    cc = float(np.median(C[cool])) if nc > 200 else float('nan')
    bands = {}
    for nm, lo, hi in B6:
        mm = (Hue >= lo) & (Hue < hi)
        bands[nm] = dict(share=float(int(mm.sum()) / npx * 100.0),
                         C=float(np.median(C[mm])) if int(mm.sum()) else float('nan'))
    o['rel'] = dict(warmC=wc, coolC=cc, warmC_minus_coolC=wc - cc,
                    warmC_over_coolC=(wc / cc if nc > 200 and cc else float('nan')),
                    warm_share=float(nw / npx * 100.0), cool_share=float(nc / npx * 100.0),
                    blue_over_orange=(bands['blue']['share'] / bands['orange']['share']
                                      if bands['orange']['share'] else float('nan')))
    o['bands'] = bands
    o['img'] = dict(L=float(np.median(L)), C=float(np.median(C)), Cmean=float(C.mean()))
    o['cheek'] = stat(lab, cheekM)
    o['arm'] = stat(lab, armM)
    if o['cheek'] and o['arm']:
        o['roi'] = dict(dC_arm=o['cheek']['C'] - o['arm']['C'],
                        dL_arm=o['cheek']['L'] - o['arm']['L'],
                        dhue_arm=((o['cheek']['h'] - o['arm']['h'] + 180) % 360) - 180,
                        C_over_imgC=o['cheek']['C'] / o['img']['C'] if o['img']['C'] else float('nan'))
    return o


# ---------------------------------------------------------------- 掩膜

def make_detector(model=MODEL):
    r"""YuNet 人脸检测器（用哪一档尺寸都行，调 `setInputSize` 即可）。@returns {cv2.FaceDetectorYN}"""
    import cv2
    return cv2.FaceDetectorYN.create(model, '', (320, 320), 0.5, 0.3, 5000)


def face_circles(im9, det):
    r"""YuNet@该尺寸 -> 两颊圆 + 脸宽 + 眼 y。

    @returns {dict|None} `circles` [(x,y,r),(x,y,r)] / `face_w` / `eye_y` / `cx` / `cy` / `nface`
    """
    import cv2
    W9, H9 = im9.width, im9.height
    bgr = cv2.cvtColor(np.asarray(im9.convert('RGB'), np.uint8), cv2.COLOR_RGB2BGR)
    det.setInputSize((W9, H9))
    _, faces = det.detect(bgr)
    if faces is None or not len(faces):
        return dict(circles=[], face_w=0.0, eye_y=0.0, cx=0.0, cy=0.0, nface=0)
    f = max(faces, key=lambda z: z[2] * z[3])
    w = float(f[2])
    le, re = f[6:8], f[4:6]
    lm, rm = f[12:14], f[10:12]
    ey = 0.5 * (float(le[1]) + float(re[1]))
    my = 0.5 * (float(lm[1]) + float(rm[1]))
    cy = ey + 0.55 * (my - ey)
    cx = 0.5 * (float(le[0]) + float(re[0]))
    dx = 0.26 * w
    r = max(4.0, 0.115 * w)
    return dict(circles=[(float(cx - dx), float(cy), float(r)), (float(cx + dx), float(cy), float(r))],
                face_w=w, eye_y=ey, cx=cx, cy=cy, nface=int(len(faces)))


def roi_masks(im9, det):
    r"""返回 (cheekM, armM, circles, nface)：颊 = 圆 ∩ FACE；臂 = SKIN 去圆 x1.6（逐字保留）。

    @param im9 {PIL.Image} **已经缩到统计域**的图
    @param det {cv2.FaceDetectorYN}
    @returns {tuple} (cheekM|None, armM|None, circles, nface)
    """
    ensure_engine()
    from svFilm import person as P
    H9, W9 = im9.height, im9.width
    c = face_circles(im9, det)
    circles = c['circles']
    yy, xx = np.ogrid[:H9, :W9]
    cir = np.zeros((H9, W9), bool)
    for (ox, oy, orr) in circles:
        cir |= ((yy - oy) ** 2 + (xx - ox) ** 2) <= orr * orr
    mk = P.masks(np.asarray(im9.convert('RGB'), np.uint8).astype(np.float64) / 255.0,
                 (int(P.SKIN), int(P.FACE)))
    sk = np.asarray(mk[int(P.SKIN)]) > 0.5
    fc = np.asarray(mk[int(P.FACE)]) > 0.5
    if sk.shape[:2] != (H9, W9):
        sk = np.asarray(Image.fromarray((sk * 255).astype(np.uint8)).resize((W9, H9), Image.NEAREST)) > 127
        fc = np.asarray(Image.fromarray((fc * 255).astype(np.uint8)).resize((W9, H9), Image.NEAREST)) > 127
    mm = cir & fc          # 旧口径（圆 ∩ FACE）—— **保留做 A/B**（`SV_CHEEK=circle` 可切回）
    # ★★★ 新口径（10-18 定）：**脸 = 肤色像素的亮度**，不用几何 ROI。
    #   实测依据：旧圆法在仰拍近景上把 1105 px 采在**眼睛**上（两眼 L* 差 7.3、C* 27.5 不像皮肤）；
    #   而"FACE 区 ∩ 皮肤似像素"的读数 C* 12~20、a* 7~16，且大师样本落在他自己的脸带内 ✓
    #   皮肤似 = L*∈[30,92] ∧ C*∈[5,22] ∧ a*∈[2,26] ∧ b*∈[4,28]（在 900 域 lab 上判）
    #   ⚠ `P.masks` 的 SKIN 通道**不是肤色**（与 FACE 零重叠、脸框内读数 L*≈19）⇒ 只能用 FACE + 上述筛。
    mm_skin = None
    # ★ 修（10-18）：原为 `to_lab900(im9, im9.width)` —— 把**宽**当长边传进去。
    #   横构图（宽 = 长边）时 `k = 1` 恰好不变；**竖构图**（如 600x900）`k = 600/900`
    #   ⇒ `_lab` 被缩成 400x600，与 `fc`(600x900) 形状不符 ⇒ `fc & _L` 抛
    #   `ValueError: operands could not be broadcast` ⇒ 被下面的 `except` 吞掉 ⇒
    #   `mm_skin = None` ⇒ **`SV_CHEEK=skin` 在竖构图上静默失效、退回旧圆口径**。
    #   现在传**真实长边**（`max(im9.size)` ⇒ 横竖都是 k = 1，不缩放），并把异常记在
    #   `_CHEEK_LAST` 里（不再静默）。本批 21 张全是横构图 ⇒ 读数逐位不变。
    global _CHEEK_LAST
    _CHEEK_LAST = dict(mode=(os.environ.get('SV_CHEEK') or 'skin').strip().lower(),
                       skin_n=None, err=None)
    try:
        _lab = to_lab900(im9, max(im9.size), do_exif=False)   # ★ 用库自己的 Lab（与读数同一套口径）
        _L, _a, _b = _lab[..., 0], _lab[..., 1], _lab[..., 2]
        _C = np.hypot(_a, _b)
        _skin = (fc & (_L >= 30) & (_L <= 92) & (_C >= 5) & (_C <= 22)
                 & (_a >= 2) & (_a <= 26) & (_b >= 4) & (_b <= 28))
        if int(_skin.sum()) >= 200:
            mm_skin = _skin
        _CHEEK_LAST['skin_n'] = int(_skin.sum())
    except Exception as _e:                                                      # noqa: BLE001
        # ★ 不许静默吞：记下原因（调用方可读 `measure.CHEEK_LAST()`），并在 stderr 留一行
        mm_skin = None
        _CHEEK_LAST['err'] = '%s: %s' % (type(_e).__name__, str(_e)[:160])
        try:
            import sys as _sys
            print('[measure 警告] 肤色掩膜算不出来 ⇒ 退回「圆 ∩ FACE」旧口径：%s'
                  % _CHEEK_LAST['err'], file=_sys.stderr)
        except Exception:                                                        # noqa: BLE001
            pass
    _mode = (os.environ.get('SV_CHEEK') or 'skin').strip().lower()
    if _mode == 'skin' and mm_skin is not None:
        mm = mm_skin
    _CHEEK_LAST['applied'] = (_mode == 'skin' and mm_skin is not None)
    cheekM = mm if int(mm.sum()) >= 40 else None
    armM = None
    if circles and int(sk.sum()) >= 200:
        excl = np.zeros((H9, W9), bool)
        for (ox, oy, orr) in circles:
            excl |= ((yy - oy) ** 2 + (xx - ox) ** 2) <= (orr * 1.6) ** 2
        am = sk & (~excl)
        if int(am.sum()) >= 200:
            armM = am
    return cheekM, armM, circles, c['nface']


def read_image(path, det=None, side=SIDE, open_kw=None):
    r"""一张图 -> 统计域 lab + 掩膜 + 全量读数。

    @param path {str} 图片路径
    @param det {cv2.FaceDetectorYN|None} None 则内部建一个（批量时请**传进来复用**）
    @param side {int} 统计域长边
    @returns {dict} im9 / lab / cheekM / armM / circles / nface / cheek_n / arm_n / reading
    """
    im0 = Image.open(path, **(open_kw or {}))
    im0 = ImageOps.exif_transpose(im0).convert('RGB')
    im9 = to_domain(im0, side)
    det = det or make_detector()
    cheekM, armM, circles, nface = roi_masks(im9, det)
    lab = to_lab900(im9, side, do_exif=False)
    return dict(im9=im9, lab=lab, cheekM=cheekM, armM=armM, circles=circles, nface=nface,
                cheek_n=int(cheekM.sum()) if cheekM is not None else 0,
                arm_n=int(armM.sum()) if armM is not None else 0,
                reading=measure(lab, cheekM, armM))


def read_arms(paths, det=None, side=SIDE, masks_from=None):
    r"""多臂同内容批量：**掩膜只算一次**（第一张的），后续沿用 => 同口径。

    @param paths {dict} {arm: path}
    @param det {cv2.FaceDetectorYN|None}
    @param side {int}
    @param masks_from {dict|None} 直接复用 `read_image` 的结果（省掉重复检测）
    @returns {dict} {arm: dict(reading=..., _src=path, cheek_n=..., arm_n=..., circles=..., nface=...)}
    """
    det = det or make_detector()
    out = {}
    ref = masks_from
    if ref is None:
        first = next(iter(paths.values()))
        ref = read_image(first, det, side)
        out['_mask_from'] = first
    for arm, p in paths.items():
        if not p or not os.path.isfile(p):
            out[arm] = None
            continue
        im9 = to_domain(ImageOps.exif_transpose(Image.open(p)).convert('RGB'), side)
        if im9.size != ref['im9'].size:
            out[arm] = dict(reading=None, _src=p, err='size %r != ref %r' % (im9.size, ref['im9'].size))
            continue
        lab = to_lab900(im9, side, do_exif=False)
        out[arm] = dict(reading=measure(lab, ref['cheekM'], ref['armM']), _src=p,
                        cheek_n=ref['cheek_n'], arm_n=ref['arm_n'],
                        circles=ref['circles'], nface=ref['nface'])
    return out


# ---------------------------------------------------------------- clip

def clip_stats(rgb_u8, thr=254):
    r"""clip 计数（F1 界形态：`clip 计数 = 0`）。

    @param rgb_u8 {ndarray(H,W,3) uint8}
    @param thr {int} 判"顶到"的显示码阈值（254 = 贴顶；255 太严）
    @returns {dict} any_pct / all3_pct / per_channel_pct / n
    """
    a = np.asarray(rgb_u8)
    hi = (a >= thr)
    per = [float(hi[..., i].mean() * 100.0) for i in range(3)]
    return dict(any_pct=float(hi.any(axis=2).mean() * 100.0),
                all3_pct=float(hi.all(axis=2).mean() * 100.0),
                per_channel_pct=per, thr=int(thr), n=int(a.shape[0] * a.shape[1]))


# ---------------------------------------------------------------- 落盘

def dump_json(obj, path, indent=1, newline='\n'):
    r"""原子写 JSON（旧名字；**实现已合并到 `jsonsafe.write_json_atomic`**）。@returns {str}"""
    _lib.deprecation('measure.dump_json', 'jsonsafe.write_json_atomic')
    return _J.write_json_atomic(obj, path, indent=indent, newline=newline)
