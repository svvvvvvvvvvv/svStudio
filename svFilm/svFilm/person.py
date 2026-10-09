# -*- coding: utf-8 -*-
r"""**人在哪** —— 只要一个"人物整体位置"的**粗掩膜**，只服务**光位判断**。

## 为什么只剩这么点东西
09-29 定：**认人 / 认脸整套（`face.py` + `facegain.py` + `region.py`）删掉**
（原话：「跑的太慢了也没有达到我想要的效果」）。
但**光位**（逆光 / 侧逆光 / 顺平光）要判 —— 而光位的判据是「**主体 vs 它身后的背景**」
⇒ **必须有一个"主体在哪"**。
⇒ 本模块就只干这一件事：**一个粗的 `person` 掩膜**，别的（脸 / 皮肤 / 头发 / 五官 / 景别）**一概不做**。

## 为什么用这个模型（性能账）
| 来源 | 每张 | 说明 |
|---|---|---|
| `selfie_multiclass_256x256.tflite`（**本模块**，仓库自带 `_models/`）| **~0.3 s** | mediapipe `ImageSegmenter`，输入**固定 256×256** ⇒ 快 |
| `birefnet-portrait`（随整套认人一起删掉的那条）| ~7 s | 更准，但**不要这个开销** |

⇒ 481 张一轮：**约 2.4 分钟**（birefnet 那条要 **40 分钟以上**）。

## ⚠ 已知短板（如实说，不许假装没有）
`selfie_multiclass` 是**视频会议自拍（头肩）**模型 ⇒ **全身 / 人小 / 白衣服低对比会整块漏**。
对光位判断的后果，分两种：
  · **人小 / 只有背景** ⇒ 主体区太小 ⇒ **判不出来** ⇒ 本模块返回 `None`，`back` 轴写 `-`
    —— **弃权是合法输出**，不许硬给一个"顺平光"。
  · **只检出头肩** ⇒ 拿头肩当"主体"去量"背景比主体亮多少"，**方向仍然对**
    （逆光的因果就是"主体欠曝、背景过亮"）⇒ **可以接受**，但要知道"主体"其实是头肩。
⚠ 反面证据（当时换 birefnet 的理由，留着别忘）：`selfie_multiclass` 在
  「举巨大波点球」那张上把人的占比从 **11.6% 涨到 57.5%** ⇒ 它也会**多框**。
  所以下游用 `person` 时**必须**既能弃权、也别把它当精确边界。

## 线程
mediapipe 的 `ImageSegmenter` 实例**不是线程安全的** ⇒ 建实例与调用**都串行化**（一把锁）。
⚠ 模型路径不能带中文（mediapipe 硬限制）⇒ 走 `_ascii()` 复制到临时目录再用。
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

# 模型一次前向就输出这 6 类（见 `masks()`）。⚠ 第 3 类 = **脸上的皮肤**。
NCLASS = 6
BG, HAIR, SKIN, FACE, CLOTH, OTHER = range(NCLASS)


class PersonUnavailable(RuntimeError):
    """模型 / 依赖拿不到。调用方**降级**（光位那一轴给 `None`），**不崩**。"""


def _ascii(path):
    """mediapipe 只吃 ASCII 路径；本工程路径含中文 ⇒ 复制到临时目录再用。"""
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
    """分割器单例。★ 建实例与调用都必须在 `_LOCK` 下（mediapipe 实例不是线程安全的）。"""
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
    """能不能跑（给自检 / 报告用）。跑一次 `_segmenter()`；失败 ⇒ False（并记住原因）。"""
    try:
        _segmenter()
        return True
    except Exception as e:                                        # noqa: BLE001
        _WHY[0] = str(e)[:160]
        return False


def why():
    """上次 `available()` 失败的原因（没失败过 ⇒ `None`）。"""
    return _WHY[0]


def masks(disp, want=(BG,)):
    r"""一次前向 → **指定类别**的置信掩膜（与 `disp` 同尺寸，0~1）；拿不到 ⇒ 抛 `PersonUnavailable`。

    类别号：`BG=0` 背景 · `HAIR=1` 头发 · `SKIN=2` 身体皮肤 · **`FACE=3` 脸上的皮肤** ·
    `CLOTH=4` 衣服 · `OTHER=5` 其他。

    ★★★ 为什么要有这个函数：`selfie_multiclass` **一次前向就把 6 类全算完了**
    ⇒ 取第 3 类（脸上的皮肤）的**边际成本是 0** —— 这**不是**"再跑一次人脸识别"，
      也不是把 `birefnet` 那条高开销的路接回来（那条已随认人整套删掉）。
    ⚠ 用途**只限证据**（光位判据的一条线索）；**不许**拿它去复建"肤色调色层"
      （10-08 评估过、裁定不做，理由见本模块 `person()` 的 docstring）。
    """
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
    r"""**人在哪** —— 返回与 `disp` 同尺寸的粗掩膜（0~1）；拿不到 ⇒ 抛（调用方弃权）。

    口径 = `selfie_multiclass` 的 **「1 − 背景类」**（= 当年那个 ~0.3 s 的老口径）。
    ⚠ **只取这一项**：不跑人脸检测、不要 skin / hair —— 光位只需要"主体在哪"。
    ⚠ 返回 `None`（模型不可用）时，调用方**必须**能弃权，别硬判。

    ## ★ 10-08 记录（**已评估、暂不采用**，别再重复调研）
    这个模型一次输出 **6 类**（`0=背景 1=头发 2=身体皮肤 3=脸皮肤 4=衣服 5=其他`），
    本节只取第 0 类。⇒ **"皮肤在哪"的掩膜本来就在内存里，取它的边际成本是 0**
      （不是"再跑一次人脸识别"；同一次前向已经有了）。
    10-08 实测（全 747 张鹿井 vs 我们）：脸 **C\* 差 −8.1**、**b\* 差 −7.0**、hue 差 −2.3°，
    而删掉肤色层之后**没有任何一层能只改皮肤**（`band_gain` 按色相 ⇒ 脸和橙道具一起走）。
    ⇒ 当时评估过"用 `2 ∪ 3` 的**并集**掩膜"复建肤色段（并集 = 皮肤在脸/脖子/手臂上连续，
      正是当年"脸↔脖子断层"那个删除理由的结构性解法）。
    **裁定：不做**（不引入会动画面的新层）。要捡起来时，这一段的结论可直接用。
    """
    return np.clip(1.0 - masks(disp, (BG,))[BG], 0.0, 1.0)


def person_face(disp):
    r"""**一次前向**同时给「人在哪」与「脸上的皮肤在哪」—— 边际成本 0。

    ★ 10-09 起用：光位判据的第三条线索 = **脸上的受光**（人像布光实践里最可靠的证据，
      见 `scene.light_evidence` 与技能 §192）。**只用掩膜**，不恢复 `face.py` 那套。

    @returns `(person, face)` 两张与 `disp` 同尺寸的 0~1 掩膜；
      模型不可用 ⇒ 抛 `PersonUnavailable`（调用方**弃权**，不许硬判）。
    """
    m = masks(disp, (BG, FACE))
    return np.clip(1.0 - m[BG], 0.0, 1.0), m[FACE]
