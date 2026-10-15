# -*- coding: utf-8 -*-
r"""**人在哪** —— 只要一个"人物整体位置"的**粗掩膜**，只服务**光位判断**。

**边界**：**只做"人在哪"，不做人脸识别 / 认人 / 认脸**（那套已删）—— 光位的判据是「主体 vs 它身后
的背景」⇒ 必须有一个"主体在哪"。
**模型**：`selfie_multiclass_256x256.tflite`（仓库自带 `_models/`，固定 256×256 输入）⇒ 每张约 0.3 s；
birefnet 那条要 ~7 s ⇒ 不要那个开销。
**短板**：头肩模型 ⇒ 全身 / 人小 / 白衣服低对比会整块漏，也会**多框** ⇒ 人小就返回 `None`（弃权是
合法输出）。实例不是线程安全的 ⇒ 建实例与调用都串行化；模型路径不能带中文。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading

import numpy as np

from . import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
MDIR = os.path.join(HERE, '_models')
SELFIE = os.path.join(MDIR, 'selfie_multiclass_256x256.tflite')
SEG_SIDE = 256                      # 模型输入边长（固定；改它不会变快，只会变准一点点）

_LOCK = threading.Lock()
_SEG = [None]
_WHY = [None]

# 一次前向输出这 6 类（见 `masks()`）。⚠ 第 3 类 = **脸上的皮肤**。
NCLASS = 6
BG, HAIR, SKIN, FACE, CLOTH, OTHER = range(NCLASS)


class PersonUnavailable(RuntimeError):
    r"""模型 / 依赖拿不到 —— 调用方**降级**（光位那一轴给 `None`），**不崩**。"""


def _ascii(path):
    r"""mediapipe 只吃 ASCII 路径 ⇒ 含中文时复制到临时目录。"""
    try:
        path.encode('ascii')
        return path
    except UnicodeEncodeError:
        pass
    tp = os.path.join(tempfile.gettempdir(), os.path.basename(path))
    if not os.path.exists(tp) or os.path.getsize(tp) != os.path.getsize(path):
        shutil.copy(path, tp)
    return tp


def _segmenter():
    if _SEG[0] is None:
        with _LOCK:
            if _SEG[0] is None:                       # 双检：两个线程同时"第一次"进来也只建一个
                try:
                    import mediapipe as mp
                    from mediapipe.tasks import python as mpp
                    from mediapipe.tasks.python import vision
                except Exception as e:                                # noqa: BLE001
                    raise PersonUnavailable('mediapipe 不可用：%s' % e)
                p = str(getattr(C, 'PERSON_MODEL', '') or SELFIE)
                if not os.path.exists(p):
                    raise PersonUnavailable('缺模型文件：%s' % p)
                opt = vision.ImageSegmenterOptions(
                    base_options=mpp.BaseOptions(model_asset_path=_ascii(p)),
                    output_confidence_masks=True, output_category_mask=False)
                _SEG[0] = (vision.ImageSegmenter.create_from_options(opt), mp)
    return _SEG[0]


def available():
    try:
        _segmenter()
        return True
    except Exception as e:                                        # noqa: BLE001
        _WHY[0] = str(e)[:160]
        return False


def why():
    return _WHY[0]


def masks(disp, want=(BG,)):
    r"""一次前向 → **指定类别**的置信掩膜（与 `disp` 同尺寸）；拿不到 ⇒ 抛 `PersonUnavailable`。
    类别：`BG=0` · `HAIR=1` · `SKIN=2` · **`FACE=3` 脸上的皮肤** · `CLOTH=4` · `OTHER=5`。
    ★★ 一次前向 6 类全算完 ⇒ 取第 3 类**边际成本是 0**；⚠ 只限证据用，不许复建"肤色调色层"。"""
    import cv2
    seg, mp = _segmenter()
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    u8 = np.ascontiguousarray((d * 255.0 + 0.5).astype(np.uint8))
    H, W = u8.shape[:2]
    small = np.ascontiguousarray(cv2.resize(u8, (SEG_SIDE, SEG_SIDE),
                                            interpolation=cv2.INTER_AREA))
    with _LOCK:                       # ★ mediapipe 实例不是线程安全的 ⇒ 调用串行化
        r = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=small))
    out = {}
    for k in want:
        k = int(k)
        cm = np.asarray(r.confidence_masks[k].numpy_view(), np.float32)
        out[k] = np.clip(cv2.resize(cm, (W, H), interpolation=cv2.INTER_LINEAR),
                         0.0, 1.0).astype(np.float32)
    return out


def person(disp):
    r"""**人在哪** —— 与 `disp` 同尺寸的粗掩膜（0~1），口径 = 「1 − 背景类」。"""
    return np.clip(1.0 - masks(disp, (BG,))[BG], 0.0, 1.0)


def person_face(disp):
    r"""**一次前向**同时给「人在哪」与「脸上的皮肤在哪」—— 边际成本 0。

    ★ 光位判据的第三条线索 = **脸上的受光**（见 `scene.light_evidence`）。"""
    m = masks(disp, (BG, FACE))
    return np.clip(1.0 - m[BG], 0.0, 1.0), m[FACE]
