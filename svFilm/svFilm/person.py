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
# ★★ **第二级用**：一个**专门的人脸检测器**（只用来回答"脸在哪"，给 `face_mask_in()` 指路）。
#   ⚠ 它**不参与测光口径** —— 测光仍然是 `FACE` 类（"脸上的皮肤"），见 `face_mask_in()`。
YUNET = os.path.join(MDIR, 'yunet.onnx')

_LOCK = threading.Lock()
_SEG = [None]
_FD = [None]
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


def skin_like(disp):
    r"""**肤色近似掩膜**（YCbCr 经典规则，0..1 RGB）—— 只用来**收窄 FACE 掩膜**。

    ★ 为什么需要：`FACE` 类含**眼/眉/唇/牙/鼻孔**；实测（2328）FACE 区 12538 px 里只有 1558 px
      是"皮肤似"像素，其余把**眼睛的高光**混进测光 ⇒ 脸读数失真（皮肤中位 80 vs 圆口径 71）。
    ★ 本函数**不改任何值时/门限/斜率**，只把"脸上哪些像素算脸"从"整块 FACE"改成"FACE ∩ 肤色"。
    ⚠ 判据用 sRGB 域（`disp` 就是显示参考），与 `_lib/measure` 的 Lab 窗**精神一致、数值不等**
      —— 两侧各在自己的域里自洽（纪律：两套度量不可互驱）。
    """
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    if d.ndim != 3 or d.shape[2] < 3:
        return None
    r, g, b = d[..., 0], d[..., 1], d[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 0.5
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 0.5
    return (y >= 0.20) & (cb >= 0.33) & (cb <= 0.53) & (cr >= 0.50) & (cr <= 0.72)


def person_face(disp):
    r"""**一次前向**同时给「人在哪」与「脸上的皮肤在哪」—— 边际成本 0。

    ★ 光位判据的第三条线索 = **脸上的受光**（见 `scene.light_evidence`）。
    ★★ `getattr(C, 'PERSON_FACE_SKIN', True)`：把 FACE 掩膜**再与肤色近似取交**
       （去掉眼/眉/唇/牙/鼻孔；关掉即逐位回到旧行为）。"""
    m = masks(disp, (BG, FACE))
    f = np.asarray(m[FACE], np.float64)
    if bool(getattr(C, 'PERSON_FACE_SKIN', True)):
        sk = skin_like(disp)
        if sk is not None and sk.shape == f.shape:
            f = f * sk.astype(f.dtype)
    return np.clip(1.0 - m[BG], 0.0, 1.0), f


# ---------------------------------------------------------------------------
# **第二级：找脸兜底**（一级 `person_face` 拿不到脸档时才用）
#
# 分工：`face_box()` 只回答"**脸在哪**"（专门的人脸检测器，快 ~20 ms）；
#       `face_mask_in()` 把那块裁出来、**喂给同一个分割模型** ⇒ 拿到的还是 `FACE` 类（"脸上的皮肤"）
#       ⇒ **口径与一级同源**，锚 `(w, b)` 不用重标。
# ⚠ **不许把 `face_box()` 的框直接当测光区**：实测比皮肤掩膜系统性暗 0.13~0.28 EV（框里有眼、
#   眉、发际、背景角）⇒ 会把已经调好的图一起推亮。框只用来**指路**。
# ⚠ 全部失败路径都返回 `None` / `(None, 0.0, 0)` ⇒ 调用方**弃权**（弃权是合法终态）。
# ---------------------------------------------------------------------------


def _detector():
    r"""人脸检测器（懒建 + 串行）；拿不到 ⇒ `PersonUnavailable`。"""
    if _FD[0] is None:
        with _LOCK:
            if _FD[0] is None:
                if not os.path.exists(YUNET):
                    raise PersonUnavailable('缺人脸检测模型：%s' % YUNET)
                import cv2
                _FD[0] = cv2.FaceDetectorYN.create(
                    _ascii(YUNET), '', (320, 320),
                    float(getattr(C, 'FACE_FALLBACK_SCORE', 0.5)), 0.3, 5000)
    return _FD[0]


def face_box(disp):
    r"""**脸在哪** —— 用专门的人脸检测器取**面积最大**的那张脸（= 主体）。
    @returns `(x, y, w, h, score)`（`disp` 像素坐标）；没检出 / 拿不到模型 ⇒ `None`。"""
    import cv2
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    u8 = np.ascontiguousarray((d * 255.0 + 0.5).astype(np.uint8))
    bgr = np.ascontiguousarray(cv2.cvtColor(u8, cv2.COLOR_RGB2BGR))
    h, w = bgr.shape[:2]
    det = _detector()
    with _LOCK:                                   # ★ 检测器实例同样不保证线程安全
        det.setInputSize((w, h))
        _, f = det.detect(bgr)
    if f is None or len(f) == 0:
        return None
    r = max(f, key=lambda z: float(z[2]) * float(z[3]))
    return float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[14])


def face_mask_in(disp, box, margin=None):
    r"""按框裁出来 → **同一个分割模型**再前向一次 ⇒ 「脸上的皮肤」掩膜（与 `disp` 同尺寸、二值）。
    @returns `(mask, cover_of_crop, ref_px)`：
      `mask` = 0/1 浮点（框外恒 0）· `cover_of_crop` = 皮肤占**裁块**的比例（合理性判据用）·
      `ref_px` = 裁块面积（交给 `exposure.set_mask` 当覆盖率的分母）· 失败 ⇒ `(None, 0.0, 0)`。"""
    import cv2
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    H, W = d.shape[:2]
    if box is None:
        return None, 0.0, 0
    mg = float(getattr(C, 'FACE_FALLBACK_MARGIN', 0.35) if margin is None else margin)
    x, y, w, h = (float(v) for v in box[:4])
    x0 = int(max(0, x - w * mg))
    x1 = int(min(W, x + w * (1.0 + mg)))
    y0 = int(max(0, y - h * mg))
    y1 = int(min(H, y + h * (1.0 + mg)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, 0.0, 0
    crop = d[y0:y1, x0:x1]
    b = np.asarray(masks(crop, (FACE,))[FACE], np.float32) > 0.5
    cover = float(b.mean())
    out = np.zeros((H, W), np.float32)
    out[y0:y1, x0:x1] = b.astype(np.float32)
    return out, cover, int((x1 - x0) * (y1 - y0))


def face_fallback(disp):
    r"""**找脸兜底**：检测器找框 → 裁块重认皮肤 → 过合理性判据。
    @returns `(mask, ref_px)`；任一环不成立 ⇒ `None`（调用方弃权）。"""
    try:
        box = face_box(disp)
    except Exception:                                                    # noqa: BLE001
        return None
    if box is None:
        return None
    try:
        m, cover, ref = face_mask_in(disp, box)
    except Exception:                                                    # noqa: BLE001
        return None
    if m is None:
        return None
    lo = float(getattr(C, 'FACE_FALLBACK_MIN_COVER', 0.03))
    hi = float(getattr(C, 'FACE_FALLBACK_MAX_COVER', 0.85))
    if not (lo <= cover <= hi):
        return None
    return m, ref
