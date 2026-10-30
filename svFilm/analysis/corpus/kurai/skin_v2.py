# -*- coding: utf-8 -*-
"""skin_v2 的最小替代（原模块随工作目录删除，从未入库）。

接口与 measure_all / skin_point 需要的四个函数一致：
  detect(im)            -> face bbox (x, y, w, h) 或 None
  detect_upscale(p,im)  -> (bbox, det)
  seg_locate(im)        -> bbox 或 None
  align_crop(im, bbox)  -> (256 裁剪, meta{le,re,lm,rm,fw})

⚠ 五点是用 bbox 推的**近似**（Haar 只给框）；同一批候选之间可比，
  但与大师侧（真人脸五点）的绝对值会有偏差 ⇒ face_C/face_h 只当**参考**，主判据用
  contrast / toe / shoulder / warm_cool_b。
"""
from __future__ import annotations

import cv2
import numpy as np

_CAS = None
MARGIN = 1.8
SIDE = 256


def _arr(im):
    if hasattr(im, 'convert'):
        return np.asarray(im.convert('RGB'))
    return np.asarray(im)


def _cascade():
    global _CAS
    if _CAS is None:
        _CAS = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    return _CAS


def detect(im) -> tuple | None:
    a = _arr(im)
    g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    if g.dtype != np.uint8:
        g = (np.clip(g, 0, 1) * 255 + 0.5).astype(np.uint8) if float(g.max()) <= 1.0 else g.astype(np.uint8)
    g = cv2.equalizeHist(g)
    faces = _cascade().detectMultiScale(g, 1.1, 5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
    return (int(x), int(y), int(w), int(h))


def detect_upscale(path, im):
    fi = detect(im)
    return fi, {'src': path}


def seg_locate(im):
    return detect(im)


def align_crop(im, bbox):
    a = _arr(im)
    H, W = a.shape[:2]
    if bbox is None:
        x, y, w, h = 0, 0, W, H
    else:
        x, y, w, h = bbox
    cx, cy = x + w / 2.0, y + h / 2.0
    s = max(w, h) * MARGIN
    x0, y0 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2))
    x1, y1 = int(min(W, cx + s / 2)), int(min(H, cy + s / 2))
    crop = a[y0:y1, x0:x1]
    if crop.size == 0:
        crop = a
    crop = cv2.resize(crop, (SIDE, SIDE), interpolation=cv2.INTER_AREA)
    fw = SIDE * (w / max(s, 1e-6))
    meta = dict(le=(SIDE * 0.34, SIDE * 0.38), re=(SIDE * 0.66, SIDE * 0.38),
                lm=(SIDE * 0.36, SIDE * 0.72), rm=(SIDE * 0.64, SIDE * 0.72),
                fw=float(fw))
    return crop, meta
