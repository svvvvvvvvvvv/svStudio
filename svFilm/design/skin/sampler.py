# -*- coding: utf-8 -*-
"""肤色 v7：**脸皮肤掩膜 + 颊 ROI + 文献统计量**。

四步（每一步都有出处，见 `CALIBER.md`）：
  1. 掩膜：`face_skin` —— 只干一件事：排除手/头发/衣服/背景
  2. ROI：眼-嘴中点外移 0.15 脸宽，椭圆半径 0.13 脸宽
  3. 剔除：ROI 内 L 的 80 分位以上（镜面）· 10 分位以下（阴影）· 偏离中位 ΔE > 10（离群）
  4. **分量中位**（L*/a*/b* 各自取中位）→ C*/h*/ITA°；有效像素 < 300 ⇒ 弃权

为什么不再是"四条证明单点"：
  四条证明（局部平整 / 多尺度一致 / 代表性 / L·h 合理）是**没有语义掩膜时的手工替代品**，
  用来回答"这个像素是不是皮肤"。有了掩膜，这个问题已经被回答，剩下的只是"取哪块、怎么统计"。
  文献上的答案是：取一块 ROI，剔掉镜面与阴影，取**分量中位**。

`fixed_xy` 保留：③ 沿用 ① 的 ROI 中心，保证跨态可比（今天最大的假差来源就是取样点漂移）。
"""
from __future__ import annotations

import cv2
import numpy as np

R_ROI = 0.13          # ROI 半径 / 脸宽
OFF_X = 0.15          # 颊心相对"眼-嘴中点"的外移量（脸宽）
DOWN_Y = 0.00         # 下移量（脸宽）。实测 0.05 会把 ROI 推到脸颊外面（canary20i）
NOSE_R = 0.24         # 鼻子排除圆半径（脸宽）
ANCHOR_R = 0.30       # 锚区：颊圆并集的半径（脸宽）
SPEC_PCT = 80.0       # 镜面剔除：ROI 内 L 的上分位
DARK_PCT = 10.0       # 阴影剔除
MIN_C = 10.0          # 皮肤彩度下限（灰/白/黑都在这之下）
DE_MED = 10.0         # 离群剔除
MIN_PX = 300          # 弃权门（低于此，中位不稳）
FACE_W_MIN = 80.0     # 1080 域脸宽下限
MIN_PX_LOOSE = 150    # 宽松口径的样本门（低一档：它会出更多值，但要标记来源）
OFF_FACE_MAX = 12.0   # 肤色 L 与"脸区中位 L"的最大允许差（超了说明 ROI 跑到墙/衣服/高光上）


def _lab_from(img_lab, crop):
    if img_lab is not None:
        return img_lab[:3]
    u = (np.clip(crop, 0, 1) * 255).astype(np.uint8)
    lab = cv2.cvtColor(u, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
    a = lab[..., 1].astype(np.float32) - 128.0
    b = lab[..., 2].astype(np.float32) - 128.0
    return L, a, b


def ita(L: float, b: float) -> float:
    """ITA°（Individual Typology Angle）：越大越白。"""
    return float(np.degrees(np.arctan(np.clip((L - 50.0) / max(abs(b), 1e-3), -1e3, 1e3))))


def ita_class(v: float) -> str:
    """ITA 分级（文献常用边界）。"""
    for lim, name in ((55, 'very_light'), (41, 'light'), (28, 'intermediate'),
                      (10, 'tan'), (-30, 'brown')):
        if v > lim:
            return name
    return 'dark'



def _nose_center(meta, le, re, lm, rm):
    """鼻子中心：优先用五点里的 nose，没有就由眼/嘴推。"""
    n = meta.get('nose') if isinstance(meta, dict) else None
    if n is not None:
        return np.asarray(n, float)
    eye = 0.5 * (le + re)
    mouth = 0.5 * (lm + rm)
    v = mouth - eye
    return eye + 0.55 * v


def _ellipse(shape, cx, cy, rx, ry):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return (((xx - cx) / max(rx, 1e-6)) ** 2 + ((yy - cy) / max(ry, 1e-6)) ** 2) <= 1.0


def _fit_mask(mask, shape):
    """掩膜对齐到目标图域（分割在 256 域，逐像素统计在 1080 域）。

    2026-10-30 修：不缩放就 `m &= mask` 会 ValueError
    （(720,1080) vs (256,256)），整个测量直接崩。
    """
    if mask is None:
        return None
    m = np.asarray(mask, dtype=np.float32)
    if m.shape == shape:
        return m
    import cv2
    return cv2.resize(m, (shape[1], shape[0]), interpolation=cv2.INTER_LINEAR)


def _roi_stats(L, a, b, C, cx, cy, fw, mask=None, min_px=None, exclude=None):
    rx, ry = R_ROI * fw, R_ROI * 0.85 * fw
    m = _ellipse(L.shape, cx, cy, rx, ry)
    if mask is not None:
        m &= (_fit_mask(mask, L.shape) > 0.5)
    if exclude is not None:
        m &= ~exclude            # ★ 鼻子/鼻翼阴影不许进 ROI                 # ★ ROI 必须先落在脸上（几何外推会跑到背景/衣服上）
    m &= (C >= MIN_C)
    _mp = MIN_PX if min_px is None else int(min_px)
    if int(m.sum()) < _mp:
        return None
    lo, hi = np.percentile(L[m], DARK_PCT), np.percentile(L[m], SPEC_PCT)
    m2 = m & (L >= lo) & (L <= hi)        # 去阴影 / 去镜面
    if int(m2.sum()) >= _mp:
        m = m2
    Lm, am, bm = (float(np.median(L[m])), float(np.median(a[m])), float(np.median(b[m])))
    m3 = m & (np.hypot(a - am, b - bm) <= DE_MED)   # 去离群
    if int(m3.sum()) >= _mp:
        m = m3
        Lm, am, bm = (float(np.median(L[m])), float(np.median(a[m])), float(np.median(b[m])))
    ys, xs = np.nonzero(m)
    return dict(L=Lm, a=am, b=bm, C=float(np.hypot(am, bm)),
                h=float(np.degrees(np.arctan2(bm, am)) % 360.0),
                ita=ita(Lm, bm), n=int(m.sum()),
                xy=[int(xs.mean()), int(ys.mean())] if len(xs) else None)


def sample_skin(crop, meta, img_lab=None, mapper=None, fixed_xy=None, skin_mask=None,
                loose: bool = False):
    """肤色读数。返回 {ok, why, lit, shadow, sides, w, side_dE, region, fw}。

    `meta` 需含 le/re/lm/rm（五点）与 fw（脸宽）；`img_lab` = (L,a,b,(C,h))；
    `mapper` 把"检测域坐标"映射到 `img_lab` 所在的域；`fixed_xy` 固定 ROI 中心。
    """
    L, a, b = _lab_from(img_lab, crop)
    C = np.hypot(a, b)
    fw = float(meta['fw'])
    # ★ 脸小**不弃权**（2026-10-30 SV）：脸检出来了，就一定有一块皮肤在那儿。
    #   脸小只影响"样本多少"，不影响"有没有"；用 small_face 标记 + 按面积放宽样本门，
    #   而不是直接拒绝。真不值钱的是"没检到脸"（no_face）与"样本少到 30 px 以下"。
    small_face = bool(img_lab is not None and fw < FACE_W_MIN)
    if loose:
        skin_mask = None
    if skin_mask is None and not loose:
        try:
            from .face_mask import face_skin as _fs
            u8 = crop if crop.dtype == np.uint8 else (np.clip(crop, 0, 1) * 255).astype(np.uint8)
            skin_mask = _fs(u8)
        except Exception:                                  # noqa: BLE001
            skin_mask = None
    le, re = np.asarray(meta['le'], float), np.asarray(meta['re'], float)
    lm, rm = np.asarray(meta['lm'], float), np.asarray(meta['rm'], float)
    if mapper is not None:
        le, re, lm, rm = (np.asarray(mapper['pt'](p[0], p[1]), float) for p in (le, re, lm, rm))
        fw = fw * mapper['s']
    _dy = np.array([0.0, DOWN_Y * fw])
    centers = [0.5 * (le + lm) + np.array([-OFF_X * fw, 0.0]) + _dy,
               0.5 * (re + rm) + np.array([OFF_X * fw, 0.0]) + _dy]
    if fixed_xy is not None:
        centers = [np.asarray(fixed_xy, float)]
    # ★ 鼻子排除圆：鼻翼的阴影、鼻梁高光都不该进肤色
    nose = _nose_center(meta, le, re, lm, rm)
    exclude = _ellipse(L.shape, nose[0], nose[1], NOSE_R * fw, NOSE_R * fw)
    # 样本门：脸小时 ROI 本身就小，按面积放宽（不是无限放宽 —— 30 px 以下是真没东西）
    if loose:
        _mp = MIN_PX_LOOSE
    elif small_face:
        _mp = max(60, int(MIN_PX * 0.4))
    else:
        _mp = MIN_PX
    sides = [_roi_stats(L, a, b, C, cx, cy, fw, mask=skin_mask, min_px=_mp,
                        exclude=exclude)
             for cx, cy in centers]

    def _anchor_result():
        """整脸椭圆 ∩ 掩膜 ∩ ¬鼻 的分量中位（脸内、无鼻影、无墙）。"""
        if skin_mask is None or loose:
            return None
        ax = 0.5 * (le[0] + re[0])
        ay = 0.5 * (le[1] + rm[1])
        anc = _ellipse(L.shape, ax, ay, 0.55 * fw, 0.75 * fw)
        anc &= (_fit_mask(skin_mask, L.shape) > 0.5) & (C >= MIN_C) & ~exclude
        if int(anc.sum()) < 300:
            return None
        La = float(np.median(L[anc]))
        am = float(np.median(a[anc]))
        bm = float(np.median(b[anc]))
        ys, xs = np.nonzero(anc)
        return dict(L=La, a=am, b=bm, C=float(np.hypot(am, bm)),
                    h=float(np.degrees(np.arctan2(bm, am)) % 360.0),
                    ita=ita(La, bm), n=int(anc.sum()),
                    xy=[int(xs.mean()), int(ys.mean())] if len(xs) else None)

    good = [s for s in sides if s]
    if not good:
        # ★ ROI 一个都没成（样本太少）⇒ 先试锚区，别直接掉到"无掩膜"那档
        anc = _anchor_result()
        if anc is not None:
            return dict(ok=True, why='anchor_only', lit=anc, shadow=None, sides=sides,
                        w=0.55, side_dE=None, region='face_anchor', fw=fw,
                        small_face=small_face, src='anchor')
        return dict(ok=False, why='roi_empty', sides=sides, fw=fw)
    good.sort(key=lambda s: s['L'])
    lit = good[-1]
    # ★ 离脸检查（2026-10-30 20 张 canary 抓到）：强光高键的片子上，256 分辨率的掩膜
    #   会把亮墙/白衣服也标成"脸皮肤"，ROI 中位因此虚高 20~30 L*（02_t1 读出 L86，
    #   而那张的脸在阴影里）。拿"脸框内那圈（掩膜 ∩ 椭圆）的中位 L"当锚，
    #   肤色偏离超过 OFF_FACE_MAX 就弃权 —— 宁可弃权，不许给出一个亮墙当脸。
    if (skin_mask is not None) and (not loose):
        anc = _anchor_result()
        if anc is not None and abs(float(lit['L']) - float(anc['L'])) > OFF_FACE_MAX:
            # ★ 不解弃权，改用**锚区本身**（2026-10-30 实测）：13_t1 的颊 ROI 落到头发(34.9)、
            #   无掩膜 loose 落到亮墙(88.6)，而锚区是 59.6 —— 那才是这张脸。
            return dict(ok=True, why='anchor', lit=anc, shadow=None, sides=sides,
                        w=0.6, side_dE=None, region='face_anchor', fw=fw,
                        small_face=small_face, src='anchor',
                        roi_L=float(lit['L']))
    shadow = good[0] if len(good) > 1 else None
    de = None
    if shadow is not None:
        de = float(np.hypot(lit['a'] - shadow['a'], lit['b'] - shadow['b']))
    w = 1.0 if de is None else float(np.clip(1.0 - max(0.0, de - 6.0) / 18.0, 0.2, 1.0))
    return dict(ok=True, why='ok', lit=lit, shadow=shadow, sides=sides,
                w=w, side_dE=de, region='cheek', fw=fw,
                small_face=small_face,
                src=('loose' if loose else 'strict'))
