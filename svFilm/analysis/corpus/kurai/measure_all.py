# -*- coding: utf-8 -*-
"""全量测量（口径定稿 v1）：**检测在 1080、统计在原生**。

为什么这么分（2026-10-29 自审 + SV 确认）：
  · 缩小到 1080 会压平两端 ⇒ p1/p99/爆白系统性偏低，且不同原图缩小倍数不同，跨语料不可比
  · 所以：**所有颜色/亮度统计都在原生分辨率上做**；只有人脸检测/掩膜在 1080 上做（快），
    再把坐标与掩膜放大回原生。

测什么（按消费者去重，见 `测量计划_1029.md`）：
  场景层（给匹配排序）：天空占比 · 绿植占比 · 背景占比 · 脸占画面 · 脸vs背景亮度比 ·
                        两侧亮度差 · 整幅中位/反差 · 暖冷
  颜色层：肤色 L/C/h + 权重 · 皮肤亮度分布 · 背景 a/b · 白点 a/b · 中性区五段 · 色相六段
  明暗层：七分位 + 爆白 · 形状四项（toe/shoulder/contrast_win/skew）
  元数据：原生长边 · face_w(原生) · ICC · 口径常数

不测：颗粒/光晕/锐度（1080 量不出，用现值）。

用法：
  python measure_all.py --dir <目录> --out <json> [--with-bg] [--limit N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

import cv2
import numpy as np
from PIL import Image

BASE = r'E:\ds\photo\_corpus\kurai\full'
sys.path.insert(0, BASE)
sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\skin_v2')
import skin_v2                                            # noqa: E402
import skin_point as SP                                   # noqa: E402

DET_LONG = 1080
NEUTRAL_C = 10.0
HUE_MIN_C = 10.0
HUE_SECTORS = [('red', 20, 60), ('orange', 60, 85), ('yellow', 85, 110),
               ('green', 110, 180), ('cyan', 180, 250), ('blue', 250, 20)]
SKY_L, SKY_B, SKY_TOP = 50.0, -6.0, 0.55
EXT = ('*.jpg', '*.JPG', '*.jpeg', '*.tif', '*.tiff', '*.png')


def load_1080(path):
    """统一到**交付域 1080**（预设 export 就是这个尺寸）。

    为什么不是"各按原生"：大师原图本来就是 1080 长边，我们成片是 1400；
    各按自己原生测就不可比。统一到 1080（=交付尺寸）= 同域。
    原生长边仍记录在 meta 里，供追溯。
    """
    im = Image.open(path).convert('RGB')
    w, h = im.size
    if max(w, h) != 1080:
        r = 1080.0 / max(w, h)
        im = im.resize((max(1, int(round(w * r))), max(1, int(round(h * r)))), Image.LANCZOS)
    return np.asarray(im, np.float32) / 255.0


_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], np.float32)
_WP = np.array([0.95047, 1.0, 1.08883], np.float32)


def _f_lab(t):
    d = 6.0 / 29.0
    return np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4.0 / 29.0)


def lab_of(im):
    """原生图 → CIELAB(D65)，**纯浮点**，不经 OpenCV 8-bit/16-bit 量化。

    OpenCV 的 cvtColor 不支持 16-bit 三通道 → Lab；而 8-bit 会把 a/b 量化到 1 单位。
    这里自己算：sRGB → 线性 → XYZ(D65) → Lab。精度只受源图位深限制。
    """
    u = np.clip(im, 0.0, 1.0).astype(np.float32)
    lin = np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _M_RGB2XYZ.T
    xyz = xyz / _WP
    f = _f_lab(xyz)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return L, a, b, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360.0


def _in_sector(h, lo, hi):
    return (h >= lo) & (h < hi) if lo < hi else ((h >= lo) | (h < hi))


def _hmed(hs):
    ang = np.radians(np.asarray(hs, float))
    return float(np.degrees(np.angle(np.mean(np.exp(1j * ang)))) % 360.0)


def _detect(path, im1080):
    fi = skin_v2.detect(im1080)
    det = 'main' if fi else None
    if fi is None:
        fi, det = skin_v2.detect_upscale(path, im1080)
    if fi is None:
        fi = skin_v2.seg_locate(im1080)
        det = 'seg_locate' if fi else None
    return fi, det


def _scale_fi(fi, k):
    return dict(bbox=tuple(v * k for v in fi['bbox']),
                le=fi['le'] * k, re=fi['re'] * k, lm=fi['lm'] * k, rm=fi['rm'] * k,
                w=float(fi['w'] * k), h=float(fi['h'] * k),
                centers=(fi['centers'][0] * k, fi['centers'][1] * k),
                r=float(fi['r'] * k))


def measure_one(path, with_bg=True, fixed_xy=None):
    from PIL import Image as _I
    _w0, _h0 = _I.open(path).size
    im = load_1080(path)
    H, W = im.shape[:2]
    k = 1.0
    im1080 = im
    L, a, b, C, h = lab_of(im)

    rec = dict(file=os.path.relpath(path, os.path.dirname(path)),
               meta=dict(size1080=(int(W), int(H)), orig=(int(_w0), int(_h0)),
                         icc=_icc(path)))
    # ---------- 明暗层 ----------
    qs = {q: float(np.percentile(L, q)) for q in (1, 5, 25, 50, 75, 95, 99)}
    span = qs[95] - qs[5]
    rec['img'] = dict(p1=qs[1], p5=qs[5], p25=qs[25], p50=qs[50], p75=qs[75],
                      p95=qs[95], p99=qs[99],
                      contrast=qs[95] - qs[5],
                      clip_pct=float((L > 99).mean() * 100.0))
    rec['shape'] = dict(toe=qs[25] - qs[5], shoulder=qs[95] - qs[75],
                        contrast_win=qs[75] - qs[25],
                        skew=((qs[50] - qs[5]) / span if span > 1e-6 else None))
    # ---------- 颜色层（整幅） ----------
    neut = C <= NEUTRAL_C
    axis = {}
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        m = neut & (L >= lo) & (L < hi)
        if int(m.sum()) < 200:
            continue
        axis['%d_%d' % (lo, hi)] = dict(a=float(np.median(a[m])), b=float(np.median(b[m])),
                                        C=float(np.median(C[m])), n=int(m.sum()))
    rec['neutral_axis'] = axis
    colored = C >= HUE_MIN_C
    rec['hue_bands'] = {}
    for nm, lo, hi in HUE_SECTORS:
        m = colored & _in_sector(h, lo, hi)
        rec['hue_bands'][nm] = dict(share=float(m.mean() * 100.0),
                                    C=float(np.median(C[m])) if int(m.sum()) >= 200 else None)
    # 暖冷：亮部 b 中位 − 暗部 b 中位
    hi_m = L >= qs[75]
    lo_m = L <= qs[25]
    rec['warm_cool_b'] = float(np.median(b[hi_m]) - np.median(b[lo_m]))
    # 天空占比（定义写死）
    top = np.zeros_like(L, bool)
    top[:int(H * SKY_TOP), :] = True
    sky = top & (b <= SKY_B) & (L >= SKY_L) & (C <= 40)
    # ★ 单位：`sky_share` 是**百分数**（×100）；`env_share` 是**比例**（0~1）。
    #   两者不一致，历史上算错过（10-29 我把 sky_share 又乘了 100）。这里补一个
    #   显式的比例版，新代码用 `_frac` 结尾的字段，别再猜单位。
    # ★ 中性面：颜色的锚（色彩恒常性）。低饱和（C<8）且中亮（L 30~80）的像素。
    #   若占比太低 ⇒ 颜色锚不可信，白平衡只能保守修正（见 LR侧设计 §① 颜色锚）。
    try:
        _neu = (C < 8.0) & (L >= 30.0) & (L <= 80.0) & (~sky)
        if int(_neu.sum()) >= 3000:
            rec['neutral'] = dict(share=float(_neu.mean()),
                                  L=float(np.median(L[_neu])),
                                  a=float(np.median(a[_neu])),
                                  b=float(np.median(b[_neu])),
                                  n=int(_neu.sum()))
    except Exception as ex:
        rec['neutral_err'] = str(ex)[:60]

    rec['sky_share'] = float(sky.mean() * 100.0)
    rec['sky_share_frac'] = float(sky.mean())

    # ---------- 掩膜（1080 上算 → 放回原生） ----------
    bg_share = None
    if with_bg:
        try:
            import person_masks as PM
            mk = PM.masks(im1080, (PM.FACE, PM.CLOTH, PM.BG))
            bg1080 = (np.asarray(mk[PM.BG]) > 0.5)
            face1080 = (np.asarray(mk[PM.FACE]) > 0.5)
            bg = cv2.resize(bg1080.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
            fgm = cv2.resize(face1080.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0
            if int(bg.sum()) >= 2000:
                bg_share = float(bg.mean())
                # ★ 2026-10-29：背景只记中位是不够的 —— 逆光时背景**中位落在暗部**
                #   （树皮/石墙），太阳照到的那片才是亮的。判逆光要用**亮部**。
                #   实测（DSCF1608/1665）：脸 46.8/58.0，背景中位 32/43（判成顺光），
                #   而背景 p90 是 78/96（差 30~38 格 ⇒ 真逆光）。
                _Lbg = L[bg]
                rec['bg'] = dict(a=float(np.median(a[bg])), b=float(np.median(b[bg])),
                                 C=float(np.median(C[bg])), L=float(np.median(_Lbg)),
                                 # ★ 2026-10-30：补五点分位与 b* 分位 ——
                                 #   "提亮会不会压扁层次"要用**跨度**判断（p50−p5）、
                                 #   "冷暖分离"要用背景的 b* 高低差判断，中位不够用。
                                 L_p5=float(np.percentile(_Lbg, 5)),
                                 L_p25=float(np.percentile(_Lbg, 25)),
                                 L_p10=float(np.percentile(_Lbg, 10)),
                                 L_p90=float(np.percentile(_Lbg, 90)),
                                 L_p95=float(np.percentile(_Lbg, 95)),
                                 b_p10=float(np.percentile(b[bg], 10)),
                                 b_p50=float(np.percentile(b[bg], 50)),
                                 b_p90=float(np.percentile(b[bg], 90)),
                                 hi_share=float((_Lbg >= np.percentile(_Lbg, 90)).mean()),
                                 n=int(bg.sum()))
                thr = float(np.percentile(L[bg], 95))
                wp = bg & (L >= thr)
                if int(wp.sum()) >= 300:
                    rec['white_point'] = dict(a=float(np.median(a[wp])), b=float(np.median(b[wp])),
                                              C=float(np.median(C[wp])), n=int(wp.sum()),
                                              L_thr=thr)
            # ★★ 近场背景：贴着人的那圈（外扩 2%~10% 画面宽）里、去掉天空的部分。
            #   为什么需要（2026-10-30 SV 指出）：全局背景亮部常常来自**天上的亮部**，
            #   和人物不相邻；拿它当"脸该多亮"的参考，会把脸提到跟身边环境无关的位置 ⇒ 边缘割裂。
            #   1608 实测：全局背景 p90 73.2，而紧贴脸的环带只有 15.7（暗树荫）。
            try:
                person = ((np.asarray(mk[PM.FACE]) > 0.5) | (np.asarray(mk[PM.CLOTH]) > 0.5))
                person = cv2.resize(person.astype(np.uint8), (W, H),
                                    interpolation=cv2.INTER_NEAREST)
                if int(person.sum()) >= 2000:
                    _k1 = max(3, int(W * 0.02)) | 1
                    _k2 = max(5, int(W * 0.10)) | 1
                    _in = cv2.dilate(person, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_k1, _k1))) > 0
                    _out = cv2.dilate(person, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_k2, _k2))) > 0
                    ring = _out & (~_in) & bg & (~sky)
                    if int(ring.sum()) >= 800:
                        _Lr = L[ring]
                        rec['near_bg'] = dict(
                            L_p25=float(np.percentile(_Lr, 25)),
                            L_p50=float(np.percentile(_Lr, 50)),
                            L_p75=float(np.percentile(_Lr, 75)),
                            L_p90=float(np.percentile(_Lr, 90)),
                            b=float(np.median(b[ring])),
                            n=int(ring.sum()),
                            ring_share=float(ring.sum() / max(int(_out.sum()) - int(_in.sum()), 1)))
                        rec['near_bg_why'] = '人物外圈 2%~10% 宽，去天空'
            except Exception as ex:
                rec['near_bg_err'] = str(ex)[:80]

            if int(fgm.sum()) >= 2000:
                rec['face_mask_share'] = float(fgm.mean())
                _Lf = L[fgm]
                _fmed = float(np.median(_Lf))
                # ★ 脸内暗占比：逆光时**脸的大部分像素在阴影里**（专利 CN102013006B 的思路：
                #   前景内暗像素占比超阈值 ⇒ 逆光）。这个量能区分"脸整体偏暗"和"脸亮但有暗侧"。
                # ★ 绝对暗（不是"低于中位"）：逆光时脸的**大部分像素落在很暗的区间**
                #   （专利 CN102013006B 的"前景内暗像素占比"就是绝对暗）。
                #   用"低于中位 −10"定义会永远得到 30~40%（那是分布宽度，不是暗）。
                rec['face_dark_share'] = float((_Lf <= 35.0).mean())
                rec['face_dark_share40'] = float((_Lf <= 40.0).mean())
                rec['face_L_med'] = _fmed
                rec['face_L_p90'] = float(np.percentile(_Lf, 90))
                # ★ 轮廓光：脸掩膜外扩一圈的**环带**里，比脸亮一截的像素占比 + 环带中位亮度。
                #   出处：实操资料一致认为"逆光的魂是发丝边缘那圈亮边"；
                #   判据用环带（不是背景——背景含天空会骗人）。
                try:
                    k9 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
                    k25 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
                    inner = cv2.dilate(fgm.astype(np.uint8), k9) > 0
                    outer = cv2.dilate(fgm.astype(np.uint8), k25) > 0
                    ring = outer & (~inner)
                    if int(ring.sum()) >= 500:
                        _Lr = L[ring]
                        rec['rim'] = dict(
                            L_med=float(np.median(_Lr)),
                            L_p10=float(np.percentile(_Lr, 10)),
                            L_p25=float(np.percentile(_Lr, 25)),
                            L_p50=float(np.median(_Lr)),
                            L_p75=float(np.percentile(_Lr, 75)),
                            L_p90=float(np.percentile(_Lr, 90)),
                            bright_share=float((_Lr >= _fmed + 10.0).mean()),
                            over_share=float((_Lr >= 95.0).mean()),
                            n=int(ring.sum()))
                except Exception as ex:
                    rec['rim_err'] = str(ex)[:60]
        except Exception as ex:
            rec['bg_err'] = str(ex)[:80]

    # ---------- 肤色点（原生像素） ----------
    skin = None
    if im1080 is not None:
        fi, det = _detect(path, im1080)
        if fi is not None:
            fi_n = _scale_fi(fi, k)
            crop, meta = skin_v2.align_crop(im, fi_n)
            if crop is not None:
                r = SP.sample_skin(crop, meta, img_lab=(L, a, b, C, h), fixed_xy=fixed_xy,
                                   mapper=SP.make_mapper(meta, fi_n))
                if r.get('ok'):
                    lit = r['lit']
                    # ★ 取样点的**真实像素**也要存：点颜色要拿它算 SrcHue，
                    #   不能从 Lab 反推（10-28 栽过：虚拟中心色的 HSL 色相是错的）
                    xy = lit.get('xy')
                    rgbv = None
                    if xy:
                        cx, cy = int(round(xy[0])), int(round(xy[1]))
                        rr = 3
                        y0, y1 = max(0, cy - rr), min(H, cy + rr + 1)
                        x0, x1 = max(0, cx - rr), min(W, cx + rr + 1)
                        if y1 > y0 and x1 > x0:
                            rgbv = [float(v) for v in
                                    np.median(im[y0:y1, x0:x1].reshape(-1, 3), axis=0)]
                    skin = dict(L=lit['L'], C=lit['C'], h=lit['h'], n=lit['n'],
                                w=r.get('w'), side_dE=r.get('side_dE'),
                                # ★ 2026-10-30：新取样器（design/skin/sampler.py）不再给
                                #   mad / dE_scale / dE_rep（改成 ita + 掩膜门）。这里保留字段、
                                #   允许 None —— 硬取键会让整个测量直接 KeyError。
                                mad=lit.get('mad'), dE_scale=lit.get('dE_scale'),
                                dE_rep=lit.get('dE_rep'),
                                xy=[float(xy[0]), float(xy[1])] if xy else None,
                                rgb=rgbv,
                                # b* 单独存：冷暖要看 b* 本身，不能只看 C/h
                                b=lit.get('b'),
                                lit=dict(L=lit['L'], C=lit['C'], h=lit['h'], b=lit.get('b')))
                    sh = r.get('shadow')
                    if sh:
                        skin['shadow'] = dict(L=sh['L'], C=sh['C'], h=sh['h'])
                        skin['side_dL'] = float(abs(lit['L'] - sh['L']))
                else:
                    rec['skin_err'] = r.get('why')
                rec['det'] = det
                rec['face_w_native'] = float(fi_n['bbox'][2])
                bx, by, bw, bh = fi_n['bbox']
                rec['face_size'] = float(bw * bh / (W * H) * 100.0)
    rec['skin'] = skin
    # ---------- 场景层 ----------
    if skin and rec.get('bg'):
        rec['light_ratio'] = float(skin['L'] / max(rec['bg']['L'], 1e-6))
        rec['warm_cool_b'] = rec.get('warm_cool_b')
    rec['env_share'] = bg_share
    rec['caliber'] = dict(det_long=DET_LONG, native_stats=True, neutral_c=NEUTRAL_C,
                          hue_min_c=HUE_MIN_C, sky=(SKY_L, SKY_B, SKY_TOP),
                          lab='CIELAB D65 16bit, L 0-100, a/b -128..128',
                          white_point='bg ∧ L>=p95(bg)')
    return rec




def _worker(args):
    """多进程序里的单图任务（必须模块级：Windows spawn 不能 pickle 闭包）。"""
    path, with_bg = args
    try:
        r = measure_one(path, with_bg=with_bg)
        if r.get('skin') is None:
            return (path, None, r.get('skin_err') or 'no_skin')
        return (path, r, None)
    except Exception as ex:
        return (path, None, 'ERR %s' % str(ex)[:70])


def _icc(path):
    try:
        icc = Image.open(path).info.get('icc_profile')
        if not icc:
            return None
        from PIL import ImageCms
        import io
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
    except Exception:
        return 'ERR'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-bg', action='store_true')
    ap.add_argument('--jobs', type=int, default=0, help='并行进程数（0=自动）')
    g = ap.parse_args()
    files = sorted(set(f for e in EXT for f in glob.glob(os.path.join(g.dir, '**', e), recursive=True)))
    if g.limit:
        files = files[:g.limit]
    import multiprocessing as mp
    jobs = g.jobs or max(2, min(16, (os.cpu_count() or 4) - 2))
    print('%s：%d 张 ｜ 并行 %d 进程' % (g.dir, len(files), jobs))
    rows, bad = [], []
    t0 = time.time()
    tasks = [(p, not g.no_bg) for p in files]
    if jobs <= 1:
        it = map(_worker, tasks)
    else:
        from concurrent.futures import ProcessPoolExecutor
        ex = ProcessPoolExecutor(max_workers=jobs)
        it = ex.map(_worker, tasks, chunksize=4)
    try:
        for i, (path, r, err) in enumerate(it):
            if r is None:
                bad.append([os.path.basename(path), err])
            else:
                r['file'] = os.path.relpath(path, g.dir).replace('\\', '/')
                rows.append(r)
            if (i + 1) % 50 == 0:
                print('  %d/%d  %.0fs' % (i + 1, len(files), time.time() - t0), flush=True)
    finally:
        if jobs > 1:
            ex.shutdown(wait=True)
    json.dump(dict(_meta=dict(dir=g.dir, n=len(files), ok=len(rows), bad=len(bad),
                              jobs=jobs, generated=time.strftime('%F %T')),
                   per_image=rows, bad=bad),
              open(g.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    dt = time.time() - t0
    print('ok %d / bad %d  → %s  (%.0fs, %.2f s/张)' % (
        len(rows), len(bad), g.out, dt, dt / max(len(files), 1)))
    print('拒收:', dict(Counter(b[1] for b in bad)))


if __name__ == '__main__':
    main()
