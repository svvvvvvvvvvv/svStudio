# -*- coding: utf-8 -*-
"""Skin-zone helpers: load + YuNet detector (trimmed; no old 52-shape deps)."""
from __future__ import annotations
import cv2, numpy as np, paths

MODEL = str(paths.ENGINE / 'svFilm' / '_models' / 'yunet.onnx')
det = cv2.FaceDetectorYN.create(MODEL, '', (320, 320), 0.5, 0.3, 5000)
LONG = 1024


def load(p, long=LONG):
    im = cv2.imdecode(np.frombuffer(open(p, 'rb').read(), np.uint8), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    sc = 65535.0 if im.dtype == np.uint16 else 255.0
    if im.ndim == 2:
        im = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float64) / sc
    h, w = im.shape[:2]
    if max(h, w) > long:
        r = long / float(max(h, w))
        im = cv2.resize(im, (int(w * r), int(h * r)), interpolation=cv2.INTER_AREA)
    return np.clip(im, 0.0, 1.0)
