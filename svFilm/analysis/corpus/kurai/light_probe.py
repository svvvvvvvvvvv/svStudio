# -*- coding: utf-8 -*-
"""光向量探针：从**单张成片**估"光从哪来"。

理论（写死在这里，判据改了要一起改）：
  1. 单图没有绝对深度/光源距离 ⇒ 只能估**方向在像平面上的投影**（azimuth 左/右 + 高度）。
     前后（z）分量靠间接证据：脸 vs 近邻背景的亮度、发丝轮廓光。
  2. **脸上明暗梯度**：Lambertian 凸面 + 远光源 ⇒ 亮度沿"指向光源"的方向增大。
     在**脸局部坐标**里量（x = 眼线、y = 眼→嘴），免掉歪头/转头把梯度算错。
  3. **发丝轮廓光**：逆光时头发在光源**侧**更亮 —— 这是"背光"的直接证据（皮肤掩膜给不了）。
  4. **脸 vs 近邻背景**：背光时脸低于四周。必须用**近邻**背景（远处太阳楼会骗人，1231 已证）。
  5. **斑驳度 hot**：脸内 Y > 1.6×脸中位 的占比（沿用旧 lighttype 的定义）⇒ 缝隙光的直接证据。

不用的证据（都栽过，写在这里防止再拿它当判据）：
  · 全幅背景 p90（1231 假逆光）· 固定像素环带 rim（1526 假阳 / 1665·1422 假阴）
  · 两颊 abs 差（丢符号，且顺光也有 1 档）

用法：
    python light_probe.py --test          # 4 张已知答案，打印所有量
    python light_probe.py --dir ... --out ... [--limit N] [--jobs 4]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

BASE = r'E:\ds\photo\_corpus\kurai\full'
for p in (BASE, r'E:\ds\photo\_corpus\kurai\skin_v2'):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv2                                                # noqa: E402
import measure_all as MA                                  # noqa: E402
import person_masks as PM                                 # noqa: E402

VERSION = 'light_v1'


def Y_of_L(L):
    L = np.asarray(L, float)
    return np.where(L > 8.0, ((L + 16.0) / 116.0) ** 3, L / 903.3)


def _face_axes(fi):
    """脸局部坐标：ex = 眼线（→ 脸右），ey = 眼→嘴（→ 脸下）。返回 (origin, ex, ey)。"""
    le, re, lm, rm = fi['le'], fi['re'], fi['lm'], fi['rm']
    eye = 0.5 * (le + re)
    mouth = 0.5 * (lm + rm)
    ex = re - le
    n = float(np.linalg.norm(ex))
    if n < 1e-6:
        return None
    ex = ex / n
    ey = mouth - eye
    ey = ey - float(ey @ ex) * ex
    n2 = float(np.linalg.norm(ey))
    if n2 < 1e-6:
        return None
    return eye, ex, ey / n2


def probe(path):
    """一张图 → 光向量证据包（不抛异常，失败给 why）。"""
    rec = dict(file=os.path.basename(path), _path=path, ver=VERSION)
    try:
        im = MA.load_1080(path)
    except Exception as ex:                                # noqa: BLE001
        rec['why'] = 'load:%s' % str(ex)[:60]
        return rec
    H, W = im.shape[:2]
    L, A, B, C, hh = MA.lab_of(im)
    Lmed_frame = float(np.median(L))
    rec['frame_L'] = Lmed_frame

    fi, det = MA._detect(path, im)
    if fi is None:
        rec['why'] = 'no_face'
        return rec
    rec['det'] = det
    fw = float(fi['w'])
    rec['face_w'] = fw
    if fw < 40.0:
        rec['why'] = 'face_small'
        return rec

    try:
        mk = PM.masks(im, (PM.FACE, PM.HAIR, PM.CLOTH, PM.BG))
        face = np.asarray(mk[PM.FACE]) > 0.5
        hair = np.asarray(mk[PM.HAIR]) > 0.5
        bg = np.asarray(mk[PM.BG]) > 0.5
    except Exception as ex:                                # noqa: BLE001
        rec['why'] = 'mask:%s' % str(ex)[:60]
        return rec
    if int(face.sum()) < 500:
        rec['why'] = 'face_px'
        return rec

    ax = _face_axes(fi)
    if ax is None:
        rec['why'] = 'axes'
        return rec
    eye, ex, ey = ax
    yy, xx = np.mgrid[0:H, 0:W]
    dx = xx - eye[0]
    dy = yy - eye[1]
    U = dx * ex[0] + dy * ex[1]          # 负 = 脸左（画面左），正 = 脸右
    V = dx * ey[0] + dy * ey[1]          # 负 = 眼睛以上，正 = 嘴那边

    Lf = L[face]
    fmed = float(np.median(Lf))
    fp10, fp90 = float(np.percentile(Lf, 10)), float(np.percentile(Lf, 90))
    rec['face'] = dict(L_med=fmed, p10=fp10, p90=fp90, n=int(face.sum()))

    # ---- 1) 脸上明暗梯度（左右 / 上下，脸局部坐标）
    lf = face & (U < 0)
    rf = face & (U > 0)
    tf = face & (V < 0)
    bf = face & (V > 0)
    if int(lf.sum()) >= 300 and int(rf.sum()) >= 300:
        ml = float(np.median(L[lf]))
        mr = float(np.median(L[rf]))
        rec['d_lr'] = mr - ml              # >0 ⇒ 脸的右侧（眼线方向）更亮
        rec['face_lr'] = [ml, mr]
        # ★ 换成**画面坐标**的方位：脸右侧在画面里是哪边，由眼线向量决定
        #   （正脸时人的右眼在画面左边 ⇒ ex[0]<0 ⇒ 画面方位与脸方位反号）
        rec['d_lr_img'] = (mr - ml) * (-1.0 if ex[0] < 0 else 1.0)
        rec['ex_x'] = float(ex[0])
    tv = float(np.median(L[tf])) if int(tf.sum()) >= 300 else None
    bv = float(np.median(L[bf])) if int(bf.sum()) >= 300 else None
    if tv is not None and bv is not None:
        rec['d_tb'] = tv - bv              # >0 ⇒ 上更亮 ⇒ 光来自上方
        rec['face_tb'] = [tv, bv]

    # ---- 2) 发丝轮廓光
    if int(hair.sum()) >= 300:
        Lh = L[hair]
        hp90 = float(np.percentile(Lh, 90))
        rec['hair'] = dict(p90=hp90, med=float(np.median(Lh)), n=int(hair.sum()),
                           vs_face=hp90 - fmed)
        # 最亮那批发丝在哪一侧（用 u 的均值符号）
        thr = float(np.percentile(Lh, 90))
        hi = hair & (L >= thr)
        if int(hi.sum()) >= 100:
            rec['hair']['u_hi'] = float(np.mean(U[hi]))
            rec['hair']['v_hi'] = float(np.mean(V[hi]))

    # ---- 3) 近邻背景（脸/衣服外扩 0.9 个脸宽之内的背景）
    try:
        person = ~bg
        k = int(max(9, round(0.9 * fw)))
        near = cv2.dilate(person.astype(np.uint8),
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
        near_bg = bg & near
        if int(near_bg.sum()) >= 2000:
            Ln = L[near_bg]
            rec['near_bg'] = dict(med=float(np.median(Ln)),
                                  p90=float(np.percentile(Ln, 90)),
                                  n=int(near_bg.sum()))
            rec['ratio_near90'] = fmed / max(float(np.percentile(Ln, 90)), 1e-6)
            rec['ratio_near_med'] = fmed / max(float(np.median(Ln)), 1e-6)
    except Exception as ex:                                # noqa: BLE001
        rec['near_err'] = str(ex)[:60]

    # ---- 4) 斑驳度：两套都算，取校准后再定
    Yf = Y_of_L(Lf)
    ymed = float(np.median(Yf))
    rec['hot'] = float((Yf > 1.6 * max(ymed, 1e-9)).mean())
    # 去掉脸上的一阶明暗斜坡，看剩下的"斑"有多响（缝隙光的直接证据）
    try:
        blur = cv2.GaussianBlur(L.astype(np.float32), (0, 0),
                                sigmaX=max(2.0, 0.25 * fw))
        resid = (L - blur)[face]
        span = max(fp90 - fp10, 1e-6)
        rec['clump'] = float(np.std(resid) / span)
        rec['clump_abs'] = float(np.std(resid))
    except Exception as ex:                                # noqa: BLE001
        rec['clump_err'] = str(ex)[:60]


    # ---- 5) 脸 vs 整幅
    rec['ratio_frame'] = fmed / max(Lmed_frame, 1e-6)
    rec['ratio_frame_med'] = fmed / max(float(np.median(L)), 1e-6)
    return rec


# ---------------------------------------------------------------- 判型（提议，待目检校准）

def classify(r, th=None):
    """返回 (kind, azimuth, conf, why)。azimuth: −1 = 光来自**画面左**，+1 = 画面右。

    ★ 为什么**没有 top（顶光）**：上下半脸的差 `d_tb` 被眼/眉/嘴污染 ——
      上半脸含眼睛眉毛（暗），下半脸是脸颊下巴（亮）⇒ 语料 p50 就是 **−7.6**（下比上亮），
      这是测量 artifact，不是光。顶光要量鼻影长度/方向，得 3D 模型或landmark级分析 —— 本版不做。
    ★ 方位（az）混了**转头（yaw）**：脸转过去时可见的那半张脸本来就更亮。
      没有 yaw 估计就分不开 ⇒ 可信度里要扣，别当真理。
    """
    if r.get('why'):
        return 'unknown', None, 0.0, r['why']
    t = dict(clump=0.53, back=0.78, back_side=0.78, dhalf=8.0)
    t.update(th or {})
    d = r.get('d_lr_img', r.get('d_lr'))
    az = None if d is None else float(np.clip(d / 20.0, -1.0, 1.0))
    cl = r.get('clump') or 0.0
    rn = r.get('ratio_near90')
    hvf = (r.get('hair') or {}).get('vs_face')
    ad = 0.0 if d is None else abs(d)
    if cl >= t['clump']:
        return 'dappled', az, min(1.0, cl), 'clump=%.2f（脸上有斑）' % cl
    if rn is not None and rn < t['back'] and ad < t['dhalf']:
        c = min(1.0, 1.0 - rn)
        if (hvf or 0) > 8:
            c = min(1.0, c + 0.1)
        return 'back', az, c, '近邻背景亮(rn90=%.2f)、左右几乎不变(dLR=%.1f)' % (rn, ad)
    if rn is not None and rn < t['back_side'] and ad >= t['dhalf']:
        return 'back_side', az, min(1.0, 0.55 + (1.0 - rn) * 0.4), \
            '近邻偏亮(rn90=%.2f) 且左右差 %.1f' % (rn, ad)
    if ad >= t['dhalf']:
        return 'side', az, min(1.0, ad / 22.0), '左右差 %.1f（背景不亮）' % ad
    rf = r.get('ratio_frame') or 0.0
    if rf >= 0.95:
        return 'front', az, min(1.0, max(0.0, rf - 0.8)), '脸不低于整幅(%.2f)、左右平(%.1f)' % (rf, ad)
    return 'unknown', az, 0.0, '证据都不够（rf=%.2f rn=%s cl=%.2f）' % (rf, rn, cl)


def _worker(args):
    path = args
    try:
        r = probe(path)
        k, az, conf, why = classify(r)
        r.update(kind=k, azimuth=az, conf=round(float(conf), 2), kind_why=why)
        return r
    except Exception as ex:                                # noqa: BLE001
        return dict(file=os.path.basename(path), why='ERR %s' % str(ex)[:80])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--dir', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()

    if a.test:
        import glob as _g
        paths = [r'E:\ds\photo\_run_1029_卡1_v6\final\%s_final.tif' % s
                 for s in ('DSCF1608', 'DSCF1665', 'DSCF1231', 'DSCF1526',
                           'DSCF1422', 'DSCF1627')]
        # 语料里被标成 dappled 的目录（正样本）
        for d in ('125_鹿井小渔_n6a3ca43f', '116_鹿井小渔_n6a50db76'):
            fs = sorted(_g.glob(os.path.join(r'E:\ds\大师成片\鹿井', d, '*.jpg')))[:2]
            paths += fs
        for p in paths:
            if not os.path.isfile(p):
                print('缺', p)
                continue
            t0 = time.time()
            r = probe(p)
            k, az, conf, why = classify(r)
            print('%-22s %-10s az=%-6s ｜ dLR_img=%-6s d_tb=%-6s clump=%-5s hot=%-5s rn90=%-5s rf=%-5s hvf=%-6s ｜ %s (%.1fs)'
                  % (os.path.basename(p)[:22], k,
                     ('%+.2f' % az) if az is not None else '—',
                     _f(r.get('d_lr_img')), _f(r.get('d_tb')), _f(r.get('clump'), 2),
                     _f(r.get('hot'), 2), _f(r.get('ratio_near90'), 2),
                     _f(r.get('ratio_frame'), 2),
                     _f((r.get('hair') or {}).get('vs_face')), why, time.time() - t0))
        return

    if not a.dir:
        raise SystemExit('需要 --dir 或 --test')
    files = []
    for e in ('*.jpg', '*.jpeg', '*.png', '*.tif', '*.tiff'):
        files += glob.glob(os.path.join(a.dir, '**', e), recursive=True)
    files = sorted(set(f for f in files if not os.path.basename(f).startswith('_')))
    if a.limit:
        files = files[:a.limit]
    print('图 %d 张' % len(files))
    t0 = time.time()
    out = []
    if a.jobs > 1:
        from multiprocessing import Pool
        with Pool(a.jobs) as pool:
            for i, r in enumerate(pool.imap_unordered(_worker, files, chunksize=2), 1):
                out.append(r)
                if i % 25 == 0:
                    print('  %d/%d  %.0fs' % (i, len(files), time.time() - t0))
    else:
        for i, f in enumerate(files, 1):
            out.append(_worker(f))
            if i % 25 == 0:
                print('  %d/%d' % (i, len(files)))
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as fh:
            json.dump(dict(ver=VERSION, n=len(out), rows=out), fh, ensure_ascii=False, indent=1)
        print('-> %s（%.0f 秒）' % (a.out, time.time() - t0))


def _f(v, n=1):
    return '—' if v is None else ('%.' + str(n) + 'f') % v


if __name__ == '__main__':
    main()
