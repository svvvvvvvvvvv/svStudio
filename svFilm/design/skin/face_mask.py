# -*- coding: utf-8 -*-
"""脸部皮肤掩膜（MediaPipe `selfie_multiclass_256x256` 的第 3 类）。

6 个类别：0 背景 · 1 头发 · 2 身体皮肤（手/胳膊）· 3 **脸皮肤** · 4 衣服 · 5 其他

所以 `face_skin` 天然排除：头发 · 手 · 衣服 · 背景 —— 这正是"手托腮/拿杯子/被花挡"
那类照片唯一可靠的处理方式（四条几何证明区分不了"手"和"脸"，两者都是皮肤且各自自洽）。

模型：`svFilm/_models/selfie_multiclass_256x256.tflite`（仓库自带）。
★ 不依赖 `svFilm.person` —— 那个模块在 `ff194eb`（drop the tone-chasing layer）时删了；
  这里只重建**推理**，不含任何曝光逻辑。
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# _ROOT = E:\svStudio\svFilm（仓库根）；模型在 仓库根\svFilm\_models\
MODEL = os.path.join(_ROOT, 'svFilm', '_models', 'selfie_multiclass_256x256.tflite')

_LOCK = threading.Lock()
_SEG = None
_MP = None
_SIDE = 256


def _seg():
    global _SEG, _MP, _SIDE
    if _SEG is None:
        import mediapipe as mp
        from mediapipe.tasks import python as _mpp
        from mediapipe.tasks.python import vision as _mpv
        if not os.path.isfile(MODEL):
            raise FileNotFoundError('找不到 selfie_multiclass 模型：%s' % MODEL)
        base = _mpp.BaseOptions(model_asset_path=MODEL)
        opts = _mpv.ImageSegmenterOptions(
            base_options=base, output_category_mask=False,
            output_confidence_masks=True, running_mode=_mpv.RunningMode.IMAGE)
        _SEG, _MP, _SIDE = _mpv.ImageSegmenter.create_from_options(opts), mp, 256
    return _SEG, _MP, _SIDE


def _probs(rgb: np.ndarray) -> np.ndarray:
    """(6, H, W) 类别置信度，放回原图尺寸。"""
    seg, mp, side = _seg()
    u8 = np.ascontiguousarray(rgb.astype(np.uint8))
    small = np.ascontiguousarray(cv2.resize(u8, (side, side), interpolation=cv2.INTER_AREA))
    with _LOCK:
        r = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=small))
    out = np.empty((6, u8.shape[0], u8.shape[1]), np.float32)
    for i in range(6):
        out[i] = cv2.resize(np.asarray(r.confidence_masks[i].numpy_view(), np.float32),
                            (u8.shape[1], u8.shape[0]), interpolation=cv2.INTER_LINEAR)
    return out


def face_skin(rgb: np.ndarray) -> np.ndarray:
    """第 3 类：脸皮肤。"""
    return _probs(rgb)[3]


def body_skin(rgb: np.ndarray) -> np.ndarray:
    """第 2 类：身体皮肤（手、胳膊）—— 用来**排除**托腮的手。"""
    return _probs(rgb)[2]


def hair(rgb: np.ndarray) -> np.ndarray:
    """第 1 类：头发 —— 用来**排除**刘海。"""
    return _probs(rgb)[1]


def person(rgb: np.ndarray, thr: float = 0.5) -> np.ndarray:
    """人 = 1~5 类任意 > thr（= 1−背景），给 §13 的 `person.*` 用。"""
    return _probs(rgb)[1:6].max(axis=0) > thr
