# -*- coding: utf-8 -*-
"""一次产出 §13.2 的全部字段（大师作品重测用）。

    & $py -X utf8 -m design.measure_all --dir <图片目录> --corpus 鹿井 --out m_鹿井_v3.json [--limit 20]
    & $py -X utf8 -m design.measure_all --files a.jpg b.jpg

口径（每条见 design/skin/CALIBER.md 与 设计真源 §13）：
  · 检测在长边 1080，统计在**原生分辨率**；色彩 CIELAB D65
  · 脸 = YuNet 五点 + 框；FACE 区 = 框内椭圆；PERSON = 1−BG（selfie_multiclass 1~5 类）
  · 肤色 = design.skin.sample_skin（掩膜 + 颊 ROI + 分量中位 + ITA°）
  · 弃权一律出 null + err，**不填 0**
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional

import cv2
import numpy as np

_SV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SV not in sys.path:
    sys.path.insert(0, _SV)
_CORP = r'E:\ds\photo\_corpus\kurai\full'
if _CORP not in sys.path:
    sys.path.append(_CORP)

from design.skin import face_mask as FM          # noqa: E402
from design.skin import sample_skin              # noqa: E402

YUNET = os.path.join(_SV, 'svFilm', '_models', 'yunet.onnx')
DET_LONG = 1080

# ---- 口径常量（改任何一个都要重新算 measure_sha）
CFG = dict(
    det_long=DET_LONG,
    roi_r=0.13, roi_off=0.15, spec_pct=80.0, dark_pct=10.0, min_c=10.0,
    skin_min_px=300,
    near_r=0.90,          # 近邻背景半径 / 脸宽
    rim_r=(1.20, 1.60),   # 轮廓环 / 脸宽
    hair_band=(1.00, 1.40),
    white_pct=95.0,
    neutral_bands=5, hue_bands=6,
    ita=True,
)


def measure_sha() -> str:
    return hashlib.sha1(json.dumps(CFG, sort_keys=True).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- 基础

def _lab(rgb: np.ndarray):
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) * 100.0 / 255.0
    a = lab[..., 1].astype(np.float32) - 128.0
    b = lab[..., 2].astype(np.float32) - 128.0
    return L, a, b, np.hypot(a, b), np.degrees(np.arctan2(b, a)) % 360.0


def _load(path: str):
    from PIL import Image
    im = Image.open(path).convert('RGB')
    W0, H0 = im.size
    return im, W0, H0


def _detect(rgb: np.ndarray):
    H, W = rgb.shape[:2]
    det = cv2.FaceDetectorYN.create(YUNET, '', (W, H), 0.6, 0.3, 5000)
    n, faces = det.detect(cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB))
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda r: r[2] * r[3])
    return dict(box=[float(f[0]), float(f[1]), float(f[2]), float(f[3])],
                le=[float(f[4]), float(f[5])], re=[float(f[6]), float(f[7])],
                nose=[float(f[8]), float(f[9])], lm=[float(f[10]), float(f[11])],
                rm=[float(f[12]), float(f[13])], fw=float(f[2]),
                fh=float(f[3]), score=float(f[14]))


def _ellipse(shape, cx, cy, rx, ry):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    return (((xx - cx) / max(rx, 1e-6)) ** 2 + ((yy - cy) / max(ry, 1e-6)) ** 2) <= 1.0


def _ring(shape, cx, cy, r0, r1):
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    d = np.hypot(xx - cx, yy - cy)
    return (d > r0) & (d <= r1)


def _pct(x, q):
    return float(np.percentile(x, q)) if len(x) else None


def _med(x):
    return float(np.median(x)) if len(x) else None


# ---------------------------------------------------------------- 逐图

def measure_one(path: str, light_probe=None) -> dict:
    out: dict = dict(file=path, err=None, caliber=dict(CFG, sha=measure_sha()))
    try:
        im, W0, H0 = _load(path)
        s = min(1.0, DET_LONG / max(W0, H0))
        det_rgb = np.asarray(im.resize((int(round(W0 * s)), int(round(H0 * s))),
                                       resample=3), np.uint8)     # LANCZOS
        native = np.asarray(im, np.uint8)
        Ln, an, bn, Cn, hn = _lab(native)
        out['meta'] = dict(orig=[W0, H0], det=[det_rgb.shape[1], det_rgb.shape[0]])

        # ---- 整幅
        out['img'] = dict(p1=_pct(Ln, 1), p5=_pct(Ln, 5), p10=_pct(Ln, 10),
                          p25=_pct(Ln, 25), p50=_pct(Ln, 50), p75=_pct(Ln, 75),
                          p90=_pct(Ln, 90), p95=_pct(Ln, 95), p99=_pct(Ln, 99),
                          clip_pct=float((Ln >= 99).mean() * 100),
                          contrast=_pct(Ln, 95) - _pct(Ln, 5))
        im9 = out['img']
        out['shape'] = dict(toe=im9['p25'] - im9['p5'], shoulder=im9['p95'] - im9['p75'],
                            contrast_win=im9['p75'] - im9['p25'],
                            skew=(im9['p50'] - im9['p5']) / max(im9['p95'] - im9['p5'], 1e-6))

        # ---- 分割（在检测域算，放大回原生）
        probs = FM._probs(det_rgb)                       # (6,h,w)
        def back(arr, how=cv2.INTER_LINEAR):
            return cv2.resize(arr, (W0, H0), interpolation=how)
        pm = back((probs[1:6].max(axis=0) > 0.5).astype(np.uint8), cv2.INTER_NEAREST) > 0
        fs = back(probs[3])
        hair = back(probs[1])
        bg = ~pm
        out['env_share'] = float(bg.mean())
        out['person'] = dict(share=float(pm.mean()), L=_med(Ln[pm]) if pm.any() else None,
                             p95=_pct(Ln[pm], 95))
        Lbg = Ln[bg]
        out['bg'] = dict(L=_med(Lbg), C=_med(Cn[bg]), a=_med(an[bg]), b=_med(bn[bg]),
                         L_p10=_pct(Lbg, 10), L_p90=_pct(Lbg, 90), L_p95=_pct(Lbg, 95),
                         n=int(bg.sum()))

        # ---- 脸
        det = _detect(det_rgb)
        out['det'] = dict(name='yunet', score=None if not det else det['score'])
        if det is None:
            out['face'] = dict(n=0)
            out['skin'] = None
            out['skin_err'] = 'no_face'
        else:
            cx = (det['box'][0] + det['box'][2] / 2) / s
            cy = (det['box'][1] + det['box'][3] / 2) / s
            fw = det['fw'] / s
            fh = det['fh'] / s
            face = _ellipse(Ln.shape, cx, cy, 0.42 * fw, 0.55 * fh)
            out['face'] = dict(n=int(face.sum()), w_native=fw, size=float(face.mean()),
                               mask_share=float(face.mean()),
                               box=[det['box'][0] / s, det['box'][1] / s, fw, fh])
            # 近邻背景
            nb = _ellipse(Ln.shape, cx, cy, CFG['near_r'] * fw, CFG['near_r'] * fw) & bg
            out['near_bg'] = dict(n=int(nb.sum()),
                                  L_p50=_med(Ln[nb]) if nb.sum() >= 2000 else None,
                                  L_p90=_pct(Ln[nb], 90) if nb.sum() >= 2000 else None)
            # 肤色（v7；坐标用检测域→原生映射）
            meta = dict(le=np.array(det['le']) / s, re=np.array(det['re']) / s,
                        lm=np.array(det['lm']) / s, rm=np.array(det['rm']) / s,
                        nose=np.array(det['nose']) / s, fw=fw)
            r = sample_skin(native, meta, img_lab=(Ln, an, bn, Cn, hn),
                            mapper=None, skin_mask=cv2.resize(fs, (W0, H0)) if fs.shape != Ln.shape else fs)
            r = r or {}
            lit = r.get('lit') or {}
            if r.get('ok'):
                out['skin'] = dict(L=lit.get('L'), C=lit.get('C'), h=lit.get('h'),
                                   a=lit.get('a'), b=lit.get('b'), ita=lit.get('ita'),
                                   n=lit.get('n'), xy=lit.get('xy'),
                                   side_dE=r.get('side_dE'), w=r.get('w'))
                out['skin_err'] = None
            else:
                out['skin'] = None
                out['skin_err'] = r.get('why')
            # 光向量
            lum = Ln
            face_v = face & (fs > 0.5)
            if face_v.sum() >= 500:
                yy, xx = np.nonzero(face_v)
                left = face_v & (np.mgrid[0:Ln.shape[0], 0:Ln.shape[1]][1] < cx)
                right = face_v & ~left
                d = None
                if left.sum() > 200 and right.sum() > 200:
                    d = _med(lum[right]) - _med(lum[left])      # 正 = 画面右更亮
                clump = None
                if yy.size >= 500:
                    A = np.c_[xx - xx.mean(), yy - yy.mean(), np.ones_like(xx)]
                    coef, *_ = np.linalg.lstsq(A, lum[yy, xx], rcond=None)
                    res = lum[yy, xx] - A @ coef
                    rng = (_pct(lum[yy, xx], 95) or 1) - (_pct(lum[yy, xx], 5) or 0)
                    clump = float(np.std(res) / max(rng, 1e-6))
                band = _ring(Ln.shape, cx, cy, CFG['hair_band'][0] * fw, CFG['hair_band'][1] * fw)
                hb = band & (hair > 0.5)
                hvf = (float((_pct(lum[hb], 90) or 0) - (out['skin'] or {}).get('L', 0))
                       if hb.sum() >= 300 and out.get('skin') else None)
                out['light'] = dict(d_lr_img=d,
                                    ratio_near90=(out['skin']['L'] / out['near_bg']['L_p90']
                                                  if (out.get('skin') and out['near_bg']['L_p90']) else None),
                                    hvf=hvf, clump=clump)
            else:
                out['light'] = dict(d_lr_img=None, ratio_near90=None, hvf=None, clump=None)
            # 轮廓
            ring = _ring(Ln.shape, cx, cy, CFG['rim_r'][0] * fw, CFG['rim_r'][1] * fw) & ~face
            sl = (out.get('skin') or {}).get('L')
            out['rim'] = dict(bright_share=(float((lum[ring] > sl + 8).mean())
                                            if (ring.sum() > 300 and sl) else None),
                              n=int(ring.sum()))

        # ---- 白点 / 中性轴 / 色相六段 / 暖冷 / 光比
        wm = bg & (Ln >= (_pct(Ln[bg], CFG['white_pct']) or 1e9))
        out['white_point'] = dict(n=int(wm.sum()),
                                  a=_med(an[wm]) if wm.sum() >= 500 else None,
                                  b=_med(bn[wm]) if wm.sum() >= 500 else None,
                                  C=_med(Cn[wm]) if wm.sum() >= 500 else None,
                                  L_thr=_pct(Ln[bg], CFG['white_pct']))
        lo, hi = _pct(Ln, 0), _pct(Ln, 100)
        edges = np.linspace(lo, hi + 1e-6, CFG['neutral_bands'] + 1)
        out['neutral_axis'] = [dict(L=(edges[i] + edges[i + 1]) / 2,
                                    a=_med(an[(Ln >= edges[i]) & (Ln < edges[i + 1])]),
                                    b=_med(bn[(Ln >= edges[i]) & (Ln < edges[i + 1])]))
                               for i in range(CFG['neutral_bands'])]
        he = np.arange(0, 361, 360 // CFG['hue_bands'])
        out['hue_bands'] = [dict(h0=float(he[i]), h1=float(he[i + 1]),
                                 share=float(((hn >= he[i]) & (hn < he[i + 1])).mean()),
                                 C=_med(Cn[(hn >= he[i]) & (hn < he[i + 1])]))
                            for i in range(len(he) - 1)]
        b_hi = _med(bn[bg & (Ln >= (_pct(Ln[bg], 90) or 1e9))]) if bg.sum() > 1000 else None
        b_lo = _med(bn[bg & (Ln <= (_pct(Ln[bg], 10) or -1e9))]) if bg.sum() > 1000 else None
        out['warm_cool_b'] = (None if (b_hi is None or b_lo is None) else float(b_hi - b_lo))
        fl = (out.get('skin') or {}).get('L')
        out['light_ratio'] = (None if (not fl or not out['bg']['L_p90'])
                              else float(out['bg']['L_p90'] / max(fl, 1e-6)))
    except Exception as e:                                    # noqa: BLE001
        out['err'] = '%s: %s' % (type(e).__name__, str(e)[:160])
    return out


# ---------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='')
    ap.add_argument('--files', nargs='*', default=[])
    ap.add_argument('--corpus', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--ext', default='jpg,jpeg,png,tif,tiff')
    a = ap.parse_args(argv)

    paths = list(a.files)
    if a.dir:
        exts = tuple('.' + e.strip().lower() for e in a.ext.split(','))
        for root, _dirs, fs in os.walk(a.dir):
            for f in sorted(fs):
                if f.lower().endswith(exts):
                    paths.append(os.path.join(root, f))
    paths = sorted(set(paths))
    if a.limit:
        paths = paths[:a.limit]
    if not paths:
        print('没有图'); return 2

    rows = []
    for i, p in enumerate(paths, 1):
        r = measure_one(p)
        rows.append(r)
        sk = r.get('skin') or {}
        print('%3d/%3d %-42s %s' % (
            i, len(paths), os.path.basename(p)[:42],
            ('L%.1f C%.1f h%.0f ITA%+.0f n%d' % (sk['L'], sk['C'], sk['h'],
                                                 sk.get('ita') or 0, sk.get('n') or 0))
            if sk else ('弃权:%s' % r.get('skin_err'))))
    payload = dict(_meta=dict(corpus=a.corpus, n=len(rows), caliber=dict(CFG, sha=measure_sha())),
                   per_image=rows, bad=[r['file'] for r in rows if r.get('err')])
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print('\n写出 %s（%d 行）' % (a.out, len(rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
