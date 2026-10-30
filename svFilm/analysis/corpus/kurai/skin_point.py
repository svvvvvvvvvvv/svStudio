# -*- coding: utf-8 -*-
"""肤色取点 v6：**画圆 → 圆里取一个点 → 证明它是皮肤**（不用直方图）。

SV 2026-10-28：圆都已经画出来了，就在圆里取一个点，并证明它不是头发/眼睛。

候选点 = 圆内"离圆心近 + 局部最平整 + 颜色最接近圆内中位"的那个像素。
证明（四条，全部 O(n) 盒滤波，不用直方图）：
  ① 局部平整：9×9 窗内色散小（站在发丝/睫毛/硬边上会被这条挡）
  ② **多尺度一致**：0.06/0.10/0.14 脸宽三个窗的中位色彼此接近
     —— 皮肤在任何尺度上是同一个颜色；**跨在边界上时，一换尺度颜色就跳**
  ③ 代表性：点的颜色与圆内中位色的 ΔE 小（保证取的是这块脸，不是角落）
  ④ 合理性：L、h 落在宽窗内（整块不是皮肤时兜底）
镜像点一致性只作**诊断**：两侧真会因侧光不同而不同，那是光照事实，不是测量错误。
"""
from __future__ import annotations

import os

import cv2
import numpy as np

MIN_C = 4.0
R_CHEEK = 0.30           # 圆半径 / 脸宽
R_INNER = 0.70           # 候选点只取圆心附近这些比例内
W_SCALES = (0.06, 0.10, 0.14)   # 多尺度的窗口半径（脸宽比例）
MAD_MAX = 6.0            # ①局部色散上限（a/b 两通道 std 之和；真实皮肤纹理/噪声就有 3~5）
SCALE_DE_MAX = 4.0       # ②多尺度之间 ΔE 上限
REP_DE_MAX = 8.0         # ③与圆内中位的 ΔE 上限
L_RANGE = (38.0, 96.0)
H_RANGE = (5.0, 95.0)   # 2026-10-29 放宽：原 22 会把我们自家偏红的肤色判成非皮肤 ⇒ 挡住待测缺陷本身
# ★ 2026-10-29 新增两条（起因：02_t1 那张远景小脸，四点全过却取到了鼻梁高光 L87.8/C12.8）：
#   ① 取样点不能是这块脸的**最亮/最暗** —— 高光和阴影都不是皮肤的代表色。
#      做法：点必须在脸框内 L 分布的 [P_LO, P_HI] 百分位之间。
#   ② 脸太小就别测 —— 关键点几何本身不可靠。阈值按 1080 域算。
PCT_LO, PCT_HI = 8.0, 92.0
FACE_W_MIN = 40.0        # 1080 域像素
R_FALLBACK = 0.22         # 回退区（额头/下巴）的圆半径 / 脸宽
FALLBACK_W_PENALTY = 0.75  # 回退区的置信度折扣（受光与脸颊不同，可比性弱一档）



def _circles(meta):
    le, re, lm, rm = meta['le'], meta['re'], meta['lm'], meta['rm']
    eye_mid = 0.5 * (le + re)
    mouth_mid = 0.5 * (lm + rm)
    inter = float(np.linalg.norm(re - le))
    fw = max(float(meta['fw']), 2.2 * inter)
    cx = 0.5 * (eye_mid[0] + mouth_mid[0])
    cy = eye_mid[1] + 0.64 * (mouth_mid[1] - eye_mid[1])
    r = R_CHEEK * fw
    return [(cx - 0.35 * fw, cy), (cx + 0.35 * fw, cy)], r, fw


def _fallback_centers(meta):
    """额头 / 下巴的中心（脸局部坐标，与原图域无关，由五点推）。

    up = 从嘴中心指向眼中心的单位向量；额头沿 up 走 0.55 个脸宽，
    下巴沿 -up 走 0.42 个脸宽（这两个比例是相对**眼-嘴距**，不是脸宽，避免大脸小脸不一致）。
    """
    le, re, lm, rm = meta['le'], meta['re'], meta['lm'], meta['rm']
    eye = 0.5 * (np.asarray(le, float) + np.asarray(re, float))
    mouth = 0.5 * (np.asarray(lm, float) + np.asarray(rm, float))
    v = eye - mouth
    n = float(np.hypot(*v))
    if n < 1e-6:
        return []
    up = v / n
    fw = float(meta.get('fw') or 0.0) or n * 1.6      # 脸宽（点间距离的量级不同，用脸宽更稳）
    # ★ 下巴优先于额头：刘海的普遍性远高于下巴被挡（1726 就是刘海把额头盖住了）
    return [tuple(mouth - up * 0.30 * fw),            # 下巴（沿眼→嘴反方向再走 0.30 脸宽）
            tuple(eye + up * 0.85 * fw)]              # 额头（0.85 脸宽，避开眉/刘海）


def _disk(shape, cx, cy, r):
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    return ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r


def make_mapper(meta, fi, side=256, margin=1.8):
    """256 裁剪坐标 → 原图（1080）坐标。

    ★ 不能用"两点相似变换"拟合（2026-10-29 踩过）：`align_crop` 在脸朝左时会把 x 轴
      翻转（`if ex[0] < 0: ex = -ex`），那是**镜像**，两点相似变换表示不了，会把点映到
      错的半边（实测一张图 L 从 87.8 变成 42.0，落进阴影）。
      正解：照 `align_crop` 的公式**重建那个仿射 A**，再求逆。
    """
    le, re, lm, rm = fi['le'], fi['re'], fi['lm'], fi['rm']
    eye = 0.5 * (le + re)
    mouth = 0.5 * (lm + rm)
    ex = re - le
    n = float(np.linalg.norm(ex))
    if n < 1e-6:
        return None
    ex = ex / n
    ey = mouth - eye
    ey = ey - (ey @ ex) * ex
    n = float(np.linalg.norm(ey))
    if n < 1e-6:
        return None
    ey = ey / n
    if ex[0] < 0:
        ex = -ex
    center = eye + 0.5 * (mouth - eye)
    x, y, w, h = fi['bbox']
    sA = side / (margin * max(w, h))
    A = np.array([[sA * ex[0], sA * ex[1], side / 2.0 - sA * (ex @ center)],
                  [sA * ey[0], sA * ey[1], side / 2.0 - sA * (ey @ center)],
                  [0.0, 0.0, 1.0]], float)
    Ai = np.linalg.inv(A)

    def pt(cx, cy):
        v = Ai @ np.array([cx, cy, 1.0])
        return (float(v[0]), float(v[1]))
    return dict(pt=pt, s=1.0 / sA, theta=float(np.arctan2(A[1, 0], A[0, 0])))


def lab_of(im):
    """1080 图的 Lab（L 0–100，a/b −128..127）。全文件唯一的颜色来源。"""
    u = (np.clip(im, 0, 1) * 255).astype(np.uint8)
    lab = cv2.cvtColor(u, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
    a = lab[..., 1].astype(np.float32) - 128.0
    b = lab[..., 2].astype(np.float32) - 128.0
    return L, a, b, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360.0


def _box_stats(x, r):
    """盒滤波求局部均值与色散（O(n)，比滑窗快一个量级）。"""
    k = int(2 * r + 1)
    m = cv2.boxFilter(x, -1, (k, k), normalize=True, borderType=cv2.BORDER_REPLICATE)
    m2 = cv2.boxFilter(x * x, -1, (k, k), normalize=True, borderType=cv2.BORDER_REPLICATE)
    return m, np.sqrt(np.maximum(m2 - m * m, 0.0))


def _med_color(a, b, L, m):
    if int(m.sum()) < 20:
        return None
    return (float(np.median(a[m])), float(np.median(b[m])), float(np.median(L[m])))


def sample_skin(crop, meta, img_lab=None, mapper=None, fixed_xy=None, skin_mask=None):
    """`skin_mask`：脸部皮肤置信度（同尺寸，0~1，见 `faceskin.face_skin`）。

    给了就**只在这个掩膜里取点** —— 手（身体皮肤）、头发、衣服、背景全部排除。
    这是 1726（手托腮）那类照片唯一可靠的做法：四条证明区分不了"手"和"脸"，
    语义分割能。给了掩膜后，遮挡回退（额头/下巴）也随之安全，默认启用。
    """
    return _sample_skin_impl(crop, meta, img_lab=img_lab, mapper=mapper,
                             fixed_xy=fixed_xy, skin_mask=skin_mask)


def sample_skin_v6(crop, meta, img_lab=None, mapper=None, fixed_xy=None, skin_mask=None):
    if img_lab is not None:
        L, a, b, C, h = img_lab
        centers, r, fw = _circles(meta)
        if mapper is not None:
            centers = [mapper['pt'](cx, cy) for cx, cy in centers]
            r = r * mapper['s']
            fw = fw * mapper['s']
    else:
        lab = cv2.cvtColor((np.clip(crop, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2LAB)
        L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
        a = lab[..., 1].astype(np.float32) - 128.0
        b = lab[..., 2].astype(np.float32) - 128.0
        C = np.hypot(a, b)
        h = np.degrees(np.arctan2(b, a)) % 360.0
        centers, r, fw = _circles(meta)
    # ★ 脸太小就不测（只在原图域判，因为那时 fw 才是 1080 像素）
    if img_lab is not None and fw < FACE_W_MIN:
        return dict(ok=False, why='face_small', fw=fw, sides=[], reliable=False)
    okm = C >= MIN_C
    # 局部平整度（用 a、b 的合起来散度）
    _, sa = _box_stats(a, 4)
    _, sb = _box_stats(b, 4)
    local_mad = sa + sb
    out_sides = []
    # ★ 遮挡回退（2026-10-29）：两颊被手/杯/花挡住时，退到额头或下巴再取
    cand = [(cx, cy, r, 'cheek') for (cx, cy) in centers]
    # ★ 默认**不开**：1726 实测回退区取到了刘海（h=71.3 / C=10.8 / 位置在发际线），
    #   四条证明区分不了头发和皮肤（两者都自洽）。宁可弃权，不许静默取错。
    #   要试就设 SV_SKIN_FALLBACK=1；正式解需要**人脸语义分割**（face parsing）才能上。
    _fallback_on = (skin_mask is not None) or (
        os.environ.get('SV_SKIN_FALLBACK', '').strip() not in ('', '0', 'false'))
    if _fallback_on:
        for (cx, cy) in _fallback_centers(meta):
            cand.append((cx, cy, R_FALLBACK * r / max(R_CHEEK, 1e-6), 'fallback'))
    for _ci, (cx, cy, _r, _grp) in enumerate(cand):
        disk = _disk(L.shape, cx, cy, _r) & okm
        if skin_mask is not None:
            disk &= (skin_mask > 0.5)          # ★ 只在真·脸部皮肤里找
        if int(disk.sum()) < 150:
            out_sides.append(dict(ok=False, why='disk_empty', n=int(disk.sum())))
            continue
        med = _med_color(a, b, L, disk)
        # 候选：圆心附近、局部最平整、且接近圆内中位；**排除圆内最亮/最暗的那撮**
        # ★ 百分位必须按**圆内自己**的分布算（按整图或整脸算都会被背景稀释，实测 02_t1 就漏过去了）
        Ld = L[disk]
        L_lo_p = float(np.percentile(Ld, PCT_LO))
        L_hi_p = float(np.percentile(Ld, PCT_HI))
        inner = _disk(L.shape, cx, cy, R_INNER * _r) & disk
        inner &= (L >= L_lo_p) & (L <= L_hi_p)
        if int(inner.sum()) < 80:
            inner = disk & (L >= L_lo_p) & (L <= L_hi_p)
        if int(inner.sum()) < 40:
            out_sides.append(dict(ok=False, why='inner_empty'))
            continue
        dE_med = np.hypot(a - med[0], b - med[1])
        if fixed_xy is not None and _ci == 0:
            # ★ 固定取样点：位置沿用上游那一态，彻底消掉"挑点漂移"
            ix = int(np.clip(fixed_xy[0], 0, L.shape[1] - 1))
            iy = int(np.clip(fixed_xy[1], 0, L.shape[0] - 1))
            if not np.isfinite(local_mad[iy, ix]):
                ix, iy = (int(fixed_xy[0]), int(fixed_xy[1]))
        else:
            score = local_mad + 0.35 * dE_med
            score = np.where(inner, score, np.inf)
            iy, ix = np.unravel_index(int(np.argmin(score)), score.shape)
        # 多尺度：三个窗的中位色
        sc = []
        for wr in W_SCALES:
            win = _disk(L.shape, ix, iy, wr * fw) & okm
            mc = _med_color(a, b, L, win)
            if mc is None:
                sc = []
                break
            sc.append(mc)
        if not sc:
            out_sides.append(dict(ok=False, why='scale_empty'))
            continue
        de_scale = max(float(np.hypot(sc[i][0] - sc[j][0], sc[i][1] - sc[j][1]))
                       for i in range(len(sc)) for j in range(i + 1, len(sc)))
        hh = float(np.degrees(np.angle(np.mean(np.exp(
            1j * np.radians(h[_disk(L.shape, ix, iy, W_SCALES[1] * fw) & okm]))))) % 360.0)
        Cc = float(np.hypot(sc[1][0], sc[1][1]))
        Lc = sc[1][2]
        rep_de = float(np.hypot(sc[1][0] - med[0], sc[1][1] - med[1]))
        side_mad = float(np.median(local_mad[_disk(L.shape, ix, iy, W_SCALES[1] * fw) & okm]))
        in_L = L_RANGE[0] <= Lc <= L_RANGE[1]
        in_h = H_RANGE[0] <= hh <= H_RANGE[1]
        why = []
        if side_mad > MAD_MAX:
            why.append('flat')
        if de_scale > SCALE_DE_MAX:
            why.append('scale')
        if rep_de > REP_DE_MAX:
            why.append('rep')
        if not in_L:
            why.append('L_out')
        if not in_h:
            why.append('h_out')
        out_sides.append(dict(ok=not why, why='+'.join(why) if why else 'ok',
                              region=_grp,
                              L=Lc, C=Cc, a=sc[1][0], b=sc[1][1], h=hh,
                              n=int((_disk(L.shape, ix, iy, W_SCALES[1] * fw) & okm).sum()),
                              mad=side_mad, dE_scale=de_scale, dE_rep=rep_de,
                              xy=[int(ix), int(iy)]))
    sides = [s for s in out_sides if s.get('n')]
    if not sides:
        return dict(ok=False, why='no_circle')
    good = [s for s in sides if s.get('ok')]
    # ★ 颊优先：只要有一个颊通过，就**只在颊里选**（跨态可比）；两颊都不行才用额头/下巴
    cheeks = [s for s in sides if s.get('region') == 'cheek']
    good_cheek = [s for s in cheeks if s.get('ok')]
    if good_cheek:
        good = good_cheek
        sides = cheeks
    pool = good or sides
    pool.sort(key=lambda s: s['n'], reverse=True)
    lit = pool[0]
    sh = pool[1] if (len(pool) > 1 and 'a' in pool[1]) else None
    side_de = None
    if sh is not None:
        side_de = float(np.hypot(lit['a'] - sh['a'], lit['b'] - sh['b']))
    # ★ 置信度只用**两侧不一致**一个依据（2026-10-29 数据）：套内偏离 vs 各指标，
    #   只有两侧ΔE 有信号（r=+0.355），mad/跨尺度ΔE/像素数/脸宽 全部 ≈0。
    #   所以加权只给这一个：两侧越一致越可信。范围 0.2~1.0，永远不给 0（避免整批丢样本）。
    w = 1.0 if side_de is None else float(np.clip(1.0 - max(0.0, side_de - 6.0) / 18.0, 0.2, 1.0))
    if lit.get('region') == 'fallback':
        w = float(w) * FALLBACK_W_PENALTY      # 额头/下巴的受光与脸颊不同 ⇒ 降权
    return dict(ok=bool(lit.get('ok')), why=lit.get('why'), lit=lit, shadow=sh,
                sides=sides, w=w, region=lit.get('region'),
                side_dE=side_de, fw=fw, reliable=bool(lit.get('ok')))

# ---------------------------------------------------------------- v7（2026-10-30 起生产）
# 口径：脸皮肤掩膜 + 颊 ROI + 去镜面/阴影/离群 + **分量中位** + ITA°
# 见 _corpus/kurai/full/skin_v7.py 的文件头（含文献出处）
def sample_skin(crop, meta, img_lab=None, mapper=None, fixed_xy=None, skin_mask=None):
    """肤色（v7）。签名与 v6 一致，调用方不用改。"""
    from skin_v7 import sample_skin as _v7
    return _v7(crop, meta, img_lab=img_lab, mapper=mapper,
               fixed_xy=fixed_xy, skin_mask=skin_mask)
