# -*- coding: utf-8 -*-
"""跑一个目录：每张图取**一个肤色**（v6：圆里取点 + 四条证明）。

2026-10-29 按数据落地的三条优化：
  ① **速度 6×**：整图分位自己用 numpy 算，不再调 `measure()`（它要跑两遍检测，是 0.4 s/张 的大头）
  ② **置信度只用两侧ΔE**：套内偏离 vs 各指标实测，只有两侧ΔE 有信号（r=+0.355），
     其余（mad / 跨尺度ΔE / 像素数 / 脸宽 / 整图亮度）全部 ≈0 ⇒ 加权只给这一个，不给假精确
  ③ **两侧分别存**：侧光强的图，下游可以选更像的那侧或降权，而不是被迫接受单侧
外加两条保险：
  · `--annot N`：存 N 张"圆 + 选中点"标注小图，供人工抽检（唯一能抓系统性取错的手段）
  · 记 ICC 名：一旦混进 P3/AdobeRGB，所有数会系统性错 20%，局部检查发现不了

横轴口径：光线档位看**整图**（p50），不看脸。

用法：
  python run_skin_points.py --dir "E:\\ds\\大师成片\\鹿井" --out skin_points_鹿井.json --annot 20
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
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
import skin_point                                         # noqa: E402


def hmed(hs):
    a = np.radians(np.asarray(hs, float))
    return float(np.degrees(np.angle(np.mean(np.exp(1j * a)))) % 360.0)


def img_stats(im):
    """整图 L* 分位（自己算，不调 measure —— 速度差 6 倍）。"""
    u = (np.clip(im, 0, 1) * 255).astype(np.uint8)
    lab = cv2.cvtColor(u, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
    return dict(p25=float(np.percentile(L, 25)), p50=float(np.percentile(L, 50)),
                p75=float(np.percentile(L, 75)), p95=float(np.percentile(L, 95)),                clip=float((L > 99).mean() * 100.0))


def icc_name(path):
    try:
        icc = Image.open(path).info.get('icc_profile')
        if not icc:
            return None
        from PIL import ImageCms
        import io
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
    except Exception:
        return 'ERR'



# ---- A 组（免费层）：全部在同一张 1080 Lab 图上算，与肤色同域 ----
NEUTRAL_C = 10.0        # "低彩度"阈值（中性轴、中性参考共用）
HUE_MIN_C = 10.0        # "有色"阈值（色相六段）
HUE_SECTORS = [('red', 20, 60), ('orange', 60, 85), ('yellow', 85, 110),
               ('green', 110, 180), ('cyan', 180, 250), ('blue', 250, 20)]
NEUTRAL_REF_L = 85.0    # 中性参考：L 高且低彩度（近似白裙/白墙）


def _in_sector(h, lo, hi):
    return (h >= lo) & (h < hi) if lo < hi else ((h >= lo) | (h < hi))


def full_features(im):
    """A1 分位 / A2 clip / A3 shape / A4 中性轴 / A5 色相六段 / A7 中性参考。

    全部用 skin_point.lab_of() 那张图 ⇒ 与肤色同一个图像域、同一个 Lab 定义。
    A6 背景 a/b 需要掩膜（慢），另走 --with-bg。
    """
    L, a, b, C, h = skin_point.lab_of(im)
    qs = {q: float(np.percentile(L, q)) for q in (1, 5, 25, 50, 75, 95, 99)}
    span = qs[95] - qs[5]
    out = dict(
        img=dict(p1=qs[1], p5=qs[5], p25=qs[25], p50=qs[50], p75=qs[75],
                 p95=qs[95], p99=qs[99],
                 clip_pct=float((L > 99).mean() * 100.0)),
        shape=dict(toe=qs[25] - qs[5], shoulder=qs[95] - qs[75],
                   contrast_win=qs[75] - qs[25],
                   skew=((qs[50] - qs[5]) / span if span > 1e-6 else None)))
    neut = C <= NEUTRAL_C
    axis = {}
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        m = neut & (L >= lo) & (L < hi)
        if int(m.sum()) < 100:
            continue
        axis['%d_%d' % (lo, hi)] = dict(a=float(np.median(a[m])), b=float(np.median(b[m])),
                                        C=float(np.median(C[m])), n=int(m.sum()))
    out['neutral_axis'] = axis
    colored = C >= HUE_MIN_C
    hb = {}
    for nm, lo, hi in HUE_SECTORS:
        m = colored & _in_sector(h, lo, hi)
        hb[nm] = dict(share=float(m.mean() * 100.0),
                      C=float(np.median(C[m])) if int(m.sum()) >= 50 else None)
    out['hue_bands'] = hb
    m = (L >= NEUTRAL_REF_L) & (C <= NEUTRAL_C)
    out['neutral_ref'] = (dict(a=float(np.median(a[m])), b=float(np.median(b[m])),
                               C=float(np.median(C[m])), n=int(m.sum()))
                          if int(m.sum()) >= 200 else None)
    out['caliber'] = dict(neutral_c=NEUTRAL_C, hue_min_c=HUE_MIN_C,
                          white_point='bg ∧ L>=p95(bg)',  # A7 重做后的定义（旧 neutral_ref_l 作废）
                          lab='CIELAB D65, L 0-100, 8bit 量化(a/b 分辨率 ~1)')
    return out



# ---- A6/A7：背景与白点（需要 person_masks，慢，用 --with-bg 开） ----
def bg_features(im):
    """A6 背景 a/b ；A7 **白点证人** = 背景里最亮那 5% 像素的 a/b。

    A7 为什么不用"低彩度"筛（2026-10-29 自我否掉的版本）：
      先只挑 C≤10 的像素、再报它的 a/b —— 报出来必然 ≈0，同义反复、零信息。
      改成"背景 ∧ 亮度最高 5%"，它是**真实的白墙/白云**，a/b 才是有意义的白平衡证据。
    """
    import person_masks as PM
    rgb = im.astype(np.float32)
    m = PM.masks(rgb, (PM.FACE, PM.CLOTH, PM.BG))
    bg = np.asarray(m[PM.BG]) > 0.5
    out = dict(bg=None, white_point=None, bg_n=int(bg.sum()))
    if int(bg.sum()) < 2000:
        return out
    L, a, b, C, h = skin_point.lab_of(im)
    out['bg'] = dict(a=float(np.median(a[bg])), b=float(np.median(b[bg])),
                     C=float(np.median(C[bg])), L=float(np.median(L[bg])),
                     n=int(bg.sum()))
    thr = float(np.percentile(L[bg], 95))
    wp = bg & (L >= thr)
    if int(wp.sum()) >= 300:
        out['white_point'] = dict(a=float(np.median(a[wp])), b=float(np.median(b[wp])),
                                  C=float(np.median(C[wp])), L=float(np.median(L[wp])),
                                  n=int(wp.sum()), L_thr=thr)
    return out


def annotate(crop, r, out_path):
    img = (np.clip(crop, 0, 1) * 255).astype(np.uint8).copy()
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    for s in r.get('sides', []):
        if not s.get('n'):
            continue
        x, y = s['xy']
        rad = int(0.10 * r['fw'] * 2)
        col = (0, 220, 0) if s.get('ok') else (0, 140, 255)
        cv2.circle(img, (int(x), int(y)), max(rad, 6), col, 2)
        cv2.drawMarker(img, (int(x), int(y)), (0, 0, 255), cv2.MARKER_CROSS, 10, 1)
        txt = 'L%.0f C%.0f h%.0f' % (s['L'], s['C'], s['h'])
        cv2.putText(img, txt, (max(int(x) - 45, 2), max(int(y) - rad - 5, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    # ★ 不能用 cv2.imwrite：Windows 上含中文的路径它会**静默失败**（返回 False，不报错）。
    #   实测：鹿井 那 20 张抽检图一张都没写出来。改走 imencode + tofile。
    ext = os.path.splitext(out_path)[1] or '.png'
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    buf.tofile(out_path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--annot', type=int, default=0, help='存多少张标注图供抽检')
    ap.add_argument('--with-bg', action='store_true', help='算 A6/A7（背景/白点，慢）')
    ap.add_argument('--annot-dir', default='')
    g = ap.parse_args()

    exts = ('*.jpg', '*.JPG', '*.tif', '*.tiff', '*.png')
    files = sorted(set(f for e in exts
                       for f in glob.glob(os.path.join(g.dir, '**', e), recursive=True)))
    if g.limit:
        files = files[:g.limit]
    adir = g.annot_dir or (os.path.splitext(g.out)[0] + '_annot')
    if g.annot:
        os.makedirs(adir, exist_ok=True)
    print('%s：%d 张' % (g.dir, len(files)))

    rows, bad = [], []
    n_annot = 0
    t0 = time.time()
    for i, p in enumerate(files):
        name = os.path.relpath(p, g.dir).replace('\\', '/')
        try:
            im = skin_v2.load_side(p)
            if im is None:
                bad.append([name, 'decode']); continue
            fi = skin_v2.detect(im)
            if fi is None:
                fi, _ = skin_v2.detect_upscale(p, im)
            if fi is None:
                fi = skin_v2.seg_locate(im)
            if fi is None:
                bad.append([name, 'no_face']); continue
            crop, meta = skin_v2.align_crop(im, fi)
            # ★ 2026-10-29 口径修复：肤色在**原图 1080 域**上取，不在 256 上采样裁剪上取
            mp = skin_point.make_mapper(meta, fi)
            r = skin_point.sample_skin(crop, meta, img_lab=skin_point.lab_of(im), mapper=mp)
            if not r.get('ok'):
                bad.append([name, r.get('why') or 'sample_fail']); continue
            ff = full_features(im)
            if g.with_bg:
                ff.update(bg_features(im))
            lit = r['lit']
            sh = r.get('shadow') or {}
            rows.append(dict(
                file=name,
                L=lit['L'], C=lit['C'], h=lit['h'],
                n=lit['n'], mad=lit['mad'], dE_scale=lit['dE_scale'], dE_rep=lit['dE_rep'],
                xy=lit['xy'], face_w=r['fw'], w=r.get('w', 1.0),
                side_dE=r.get('side_dE'),
                side_lit=dict(L=lit['L'], C=lit['C'], h=lit['h'], n=lit['n'], xy=lit['xy']),
                side_shadow=dict(L=sh.get('L'), C=sh.get('C'), h=sh.get('h'),
                                 n=sh.get('n'), xy=sh.get('xy')) if sh.get('n') else None,
                n_sides=len([s for s in r.get('sides', []) if s.get('n')]),
                **ff,
                icc=icc_name(p)))
            if g.annot and n_annot < g.annot:
                annotate(crop, r, os.path.join(adir, '%03d_%s' % (
                    n_annot, name.replace('/', '_').replace('.jpg', '.png'))))
                n_annot += 1
        except Exception as ex:
            bad.append([name, 'ERR %s' % str(ex)[:60]])
        if (i + 1) % 100 == 0:
            print('  %d/%d  %.0fs' % (i + 1, len(files), time.time() - t0), flush=True)

    bands = {}
    for r in rows:
        b = int((r.get('img') or {}).get('p50', 0) // 5 * 5)
        bands.setdefault(b, []).append(r)
    summ = {}
    for b in sorted(bands):
        g5 = bands[b]
        if len(g5) < 5:
            continue
        w = np.array([x['w'] for x in g5], float)
        summ[str(b)] = dict(
            imgs=len(g5),
            C=round(float(np.average([x['C'] for x in g5], weights=w)), 2),
            C_p25=round(float(np.percentile([x['C'] for x in g5], 25)), 2),
            C_p75=round(float(np.percentile([x['C'] for x in g5], 75)), 2),
            h=round(hmed([x['h'] for x in g5]), 2),
            L=round(float(st.median([x['L'] for x in g5])), 2),
            mean_w=round(float(w.mean()), 2))

    allC = [x['C'] for x in rows]
    ww = np.array([x['w'] for x in rows], float)
    corpus = dict(n=len(rows),
                  C=round(float(np.average(allC, weights=ww)), 2),
                  C_p25=round(float(np.percentile(allC, 25)), 2),
                  C_p75=round(float(np.percentile(allC, 75)), 2),
                  h=round(hmed([x['h'] for x in rows]), 2),
                  L=round(float(st.median([x['L'] for x in rows])), 2),
                  w_mean=round(float(ww.mean()), 2))
    json.dump(dict(_meta=dict(dir=g.dir, n=len(files), ok=len(rows), bad=len(bad),
                              caliber='v6 circle+point+proof · band=image p50 · w=f(sideΔE)',
                              generated=time.strftime('%F %T')),
                   corpus=corpus, bands=summ, per_image=rows, bad=bad),
              open(g.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    dt = time.time() - t0
    print('\nok %d / bad %d  → %s' % (len(rows), len(bad), g.out))
    print('耗时 %.0fs = %.3f s/张' % (dt, dt / max(len(files), 1)))
    print('拒收:', dict(Counter(b[1] for b in bad)))
    print('\n全语料：C %.1f（p25~p75 %.1f~%.1f）｜h %.1f｜L %.1f｜平均置信 %.2f'
          % (corpus['C'], corpus['C_p25'], corpus['C_p75'], corpus['h'], corpus['L'],
             corpus['w_mean']))
    print('\n按整图光线档（p50）：')
    for b in sorted(summ, key=int):
        v = summ[b]
        print('  p50 %-3s 图数 %-4d C %-6.1f [%.1f~%.1f]  h %-6.1f  L %-6.1f  置信 %.2f'
              % (b, v['imgs'], v['C'], v['C_p25'], v['C_p75'], v['h'], v['L'], v['mean_w']))
    if g.annot:
        print('\n抽检图 %d 张 → %s' % (n_annot, adir))


if __name__ == '__main__':
    main()
