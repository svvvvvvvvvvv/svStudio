# -*- coding: utf-8 -*-
r"""脸 / 人 —— 进 L3 之前**算一次**，位置（脸多亮）和形状（脸多立体）两层共用。

为什么需要这一层（而不是继续用 `local.skin_mask`）：
  `local.skin_mask` 是**颜色**软掩膜（色相 + 彩度 + 亮度三重门），它回答的是
  "这个像素像不像皮肤的颜色" —— 粉衣服、木色、粉墙都会中招，而且它**不知道脸在哪**。
  而"这张脸该多亮""这张脸有多立体"要的是**人的位置**和**五官的位置**，
  只能靠分割 + 五官关键点拿。两者不是一回事，别混。

★ 算一次、共用：分割是这条路上最贵的一步（256×256 前向 + 上采样），
  位置层和形状层各跑一次等于白付两遍。所以这里做成"一次解析、一个 dict"。

★★ 挑脸的闸（不加会出事）：`cv2.FaceDetectorYN` 会**在手上/花束上编出假脸**，
  而且假脸的眼距可能比真脸还大（实测 DSCF2328 机内：3 个候选，假脸 eyed 233 最大，
  但框只有 0.44 落在人物掩膜里）。⇒ **光"取最大眼距"必错**，要按下面的闸筛：
    ① 框 ∩ 人物掩膜 ≥ FACE_GATE_OVP（框必须长在人身上）
    ② 眼距 ≥ FACE_GATE_EYED × 图宽（脸太小量不准）
    ③ 框高 ≥ FACE_GATE_H × 人的外接框高（假框通常很小）

⚠ 模型路径不能带中文（mediapipe 的硬限制），本工程路径里含中文 ⇒ 走 `_ascii()` 复制到临时目录。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading

import numpy as np
from PIL import Image

from . import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
MDIR = os.path.join(HERE, '_models')
YUNET = os.path.join(MDIR, 'yunet.onnx')
SELFIE = os.path.join(MDIR, 'selfie_multiclass_256x256.tflite')
SEG_SIDE = 256                      # 分割模型的输入边长（固定）

# ===== 范围层（person）的**第二个来源**：birefnet-portrait =====
# ★★★ 为什么要加（09-27 SV 拍板）：原来的 `selfie_multiclass` 是**视频会议自拍（头肩）**模型，
#   全身 / 人小 / 白衣服低对比 会**整块漏** —— 实测 DSCF1629（全身白裙）只认出上半身、人占 4.8%，
#   于是"压背景"那一步会**连腿和裙子一起压**（那不是压背景，是压人）。
#   birefnet-portrait 把同一张给到 11.5%，腿和裙子都回来了（15 张全样本目检过）。
# ⚠ 三个已知代价（写在这儿免得以后忘）：
#   ① **慢**：CPU 约 7 s/张（原来 0.3 s）；
#   ② **大**：ONNX 927 MB ⇒ **绝不进仓库**，放用户目录（`config.FACE_BIREFNET` 可指）;
#   ③ **失败模式**：人手上/身上有**巨大占画面的物体**时会把物体一起圈进来
#      （实测"举巨大波点球"那张 11.6% → 57.5%）⇒ 下游拿 `person` 判"人占比"时要留余量。
BIREF_SIDE = 1024                   # birefnet 的固定输入边长
BIREF_DEFAULT = os.path.join(os.path.expanduser('~'), '.rembg', 'models',
                             'birefnet-portrait', 'birefnet-portrait.onnx')
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float64)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float64)
_BIREF = None                       # 会话单例；**False = 已判定不可用，不再重试**
_BIREF_LOCK = threading.Lock()      # 只管"建实例"（onnxruntime 的 run 本身线程安全）

# ★★★ 09-26 线程安全（常驻服务是 `ThreadingHTTPServer`，两个请求会同时打到这里）：
#   · 分割器（mediapipe `ImageSegmenter`）与 YuNet 检测器**都不是线程安全**的
#     —— 实测共享一个 YuNet 实例并发调 `detect()` 直接崩 `cv2.error ... forwardGraph`。
#   · 处理：分割器**只建一个实例**（建实例贵、吃内存）＋ 建与调都放同一把锁下；
#     YuNet 检测器改成**每线程一份**（`threading.local`，它很轻，几十 KB/尺寸），
#     这样"两个请求同时检测"不会互相踩，也不用把检测串行化。
_SEG = None                         # (segmenter, mp)　模块级缓存
_SEG_LOCK = threading.Lock()        # 建实例 + 调 segment 都在它下面
_DET_TLS = threading.local()        # 每线程一份 {尺寸: YuNet detector}



class FaceUnavailable(RuntimeError):
    """依赖/模型缺失。调用方应当**优雅降级**（当作"没人脸"），不是崩。"""


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
    """分割器单例。★ 建实例与调用都必须在 `_SEG_LOCK` 下（mediapipe 实例不是线程安全的）。"""
    global _SEG
    if _SEG is None:
        with _SEG_LOCK:
            if _SEG is None:              # 双检：两个线程同时"第一次"进来也只建一个
                try:
                    import mediapipe as mp
                    from mediapipe.tasks import python as mpp
                    from mediapipe.tasks.python import vision
                except Exception as e:                              # noqa: BLE001
                    raise FaceUnavailable('mediapipe 不可用：%s' % e)
                for p in (YUNET, SELFIE):
                    if not os.path.exists(p):
                        raise FaceUnavailable('缺模型文件：%s' % p)
                opt = vision.ImageSegmenterOptions(
                    base_options=mpp.BaseOptions(model_asset_path=_ascii(SELFIE)),
                    output_confidence_masks=True, output_category_mask=False)
                _SEG = (vision.ImageSegmenter.create_from_options(opt), mp)
    return _SEG


def _detector(w, h):
    """★ 每线程一份（`threading.local`）—— YuNet 实例共享时并发 `detect()` 会崩。"""
    key = (int(w), int(h))
    d = getattr(_DET_TLS, 'det', None)
    if d is None:
        d = _DET_TLS.det = {}
    if key not in d:
        import cv2
        det = cv2.FaceDetectorYN.create(_ascii(YUNET), '', (64, 64),
                                        float(getattr(C, 'FACE_DET_SCORE', 0.55)),
                                        float(getattr(C, 'FACE_DET_NMS', 0.30)))
        det.setInputSize(key)
        d[key] = det
    return d[key]


def birefnet_path():
    """birefnet 模型在哪。`config.FACE_BIREFNET` 优先，否则找用户目录的默认下载位置。
    找不到返回 None ⇒ 调用方**回退**到 mediapipe（不许崩）。"""
    p = str(getattr(C, 'FACE_BIREFNET', '') or '') or BIREF_DEFAULT
    return p if os.path.exists(p) else None


def _birefnet():
    """birefnet 会话单例。★ 建实例贵（927 MB 权重、要几秒）⇒ 全局一份；建的时候加锁。
    （onnxruntime 的 `run()` 本身线程安全，所以"调"不用锁 —— 和 mediapipe 不一样。）"""
    global _BIREF
    if _BIREF is False:                     # 已经判定不可用 ⇒ 不再反复重试（省每张的开销）
        return None
    if _BIREF is None:
        with _BIREF_LOCK:
            if _BIREF is None:
                p = birefnet_path()
                if p is None:
                    _BIREF = False
                    return None
                try:
                    import onnxruntime as ort
                    o = ort.SessionOptions()
                    o.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
                    _BIREF = ort.InferenceSession(p, o, providers=['CPUExecutionProvider'])
                except Exception:                              # noqa: BLE001
                    _BIREF = False
                    return None
    return _BIREF


def person_birefnet(disp):
    """人的范围，走 `birefnet-portrait`。拿不到模型/运行时就返回 `None`（调用方回退）。

    ★★★ 下面五条是**模型自身的口径**，一条都不能改
       （出处：rembg `sessions/birefnet_general.py::BiRefNetSessionGeneral.predict`）：
      ① 输入 `[1,3,1024,1024]`；
      ② resize 到 1024 是 **squash（压扁长宽比）**，**不是 letterbox** ——
         实测 letterbox 会让人**腰斩**（纵跨度 0.96 → 0.43、面积对折），因为模型就是这么训的；
      ③ `im / max(im)`：除的是**这张图自己的最大值**，**不是 255**（暗图会被整体提亮）；
      ④ `(x − ImageNet 均值) / (ImageNet 标准差)`；
      ⑤ 输出**先过 sigmoid**（★ u2net 不需要、birefnet 需要），**再**逐图 min-max。
      ⚠ 改错**不会报错**，只会静默产出一张"看起来像抠图"的错掩膜 —— 最难发现的那类 bug。
    """
    s = _birefnet()
    if s is None:
        return None
    u8 = np.ascontiguousarray((np.clip(disp, 0.0, 1.0) * 255).astype(np.uint8))
    H, W = u8.shape[:2]
    a = np.asarray(Image.fromarray(u8).resize((BIREF_SIDE, BIREF_SIDE), Image.LANCZOS), np.float64)
    a = a / max(float(a.max()), 1e-6)                                          # ③
    x = np.ascontiguousarray(((a - _IMAGENET_MEAN) / _IMAGENET_STD).transpose(2, 0, 1)[None],
                             np.float32)                                       # ④
    p = np.asarray(s.run(None, {s.get_inputs()[0].name: x})[0][0, 0], np.float64)
    p = 1.0 / (1.0 + np.exp(-p))                                               # ⑤ sigmoid
    p = (p - p.min()) / max(float(p.max() - p.min()), 1e-6)                    # ⑤ min-max
    out = Image.fromarray((np.clip(p, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.LANCZOS)
    return np.asarray(out, np.float64) / 255.0


def masks(disp):
    """`disp` = 显示域 (H,W,3) 0~1 的 **RGB**。返回同尺寸的软掩膜 dict：
       bg / person / skin(=脸皮肤+身体皮肤) / face_skin(=只有脸皮肤) / hair(=头发)。

    ⚠ 语义保证：**`person == 1 − bg` 仍然成立**（这条被权重层用到了，别改）。
      09-27 起 `person` 可以来自**另一个模型**（birefnet），但这条恒等式是**我们自己显式构造的**，
      不再依赖"分割的 6 类互斥"。

    ★★★ **两层来源分开了**（09-27 SV 拍板，见文件头注释）：
      · **范围层** `person` / `bg` ← `birefnet-portrait`（**准**：全身/人小/白衣服都不再整块漏）
        拿不到模型或 onnxruntime ⇒ **自动回退** mediapipe 的 `1 − 背景类`（不崩、不报错）。
      · **部位层** `skin` / `face_skin` / `hair` ← 仍然 mediapipe `selfie_multiclass`。
        ⚠ 为什么部位不跟着换：试过 human parsing（SCHP，LIP 20 类），**实测更差** ——
          它会把马路/树/长椅/篮子硬塞进 20 类里的某一类（**封闭类表的必然**），
          而 MediaPipe 这 6 类反而不会把环境涂成人。

    ★ 09-14 补两个键：**`skin` 是"脸皮肤 + 身体皮肤"合起来的**（`cm[3] + cm[2]`）
    ⇒ 拿它当"脸"必然把**手臂/手**一起圈进来（DSCF1954 / DSCF0791 实测）。
    分割本来就是分开的两类，只是没暴露 ⇒ 现在把 **`face_skin`（cm[3]）** 和
    **`hair`（cm[1]，定"头在哪"）** 也单独拿出来。

    ⚠⚠ 09-26：**`face_skin` 是「分割模型圈的脸皮肤」，不是「人脸检测器认到的脸」。**
      检不到脸时它**照样有值**（实测 12 张里 1 张检测器没认出、face_skin 却非空）
      ⇒ 拿它当掩膜在**功能上可以**（侧脸/背影/被挡也能接住，SV 明确说这些片子该管），
      但**报告里不能写成"用了脸"**。下游（`grade` 的 L4）要按 `parse()['face']` 是不是
      `None` 去区分标签 —— 别只看这个掩膜非空就当成"有脸"。
    """
    import cv2
    seg, mp = _segmenter()
    u8 = np.ascontiguousarray((np.clip(disp, 0.0, 1.0) * 255).astype(np.uint8))
    H, W = u8.shape[:2]
    small = np.ascontiguousarray(cv2.resize(u8, (SEG_SIDE, SEG_SIDE),
                                            interpolation=cv2.INTER_AREA))
    with _SEG_LOCK:                    # ★ 09-26：mediapipe 实例不是线程安全的 ⇒ 调用串行化
        r = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=small))
    # 6 类顺序：0=背景 1=头发 2=身体皮肤 3=脸皮肤 4=衣服 5=其他
    cm = [np.asarray(r.confidence_masks[i].numpy_view(), np.float32) for i in range(6)]
    up = lambda a: np.clip(cv2.resize(a, (W, H), interpolation=cv2.INTER_LINEAR), 0, 1)  # noqa: E731
    skin = up(cm[3] + cm[2])           # 原语义（脸+身），别动
    face_skin = up(cm[3])              # ★ 单独的脸皮肤
    hair = up(cm[1])                   # ★ 头发（定"头在哪"的锚）

    # ===== 范围层：优先 birefnet，拿不到就回退 mediapipe =====
    person = None
    if str(getattr(C, 'FACE_PERSON_SRC', 'birefnet')).lower() == 'birefnet':
        person = person_birefnet(disp)
    if person is None:
        person = up(1.0 - cm[0])       # 老口径（= mediapipe 的"人"）
    person = np.clip(person, 0.0, 1.0)
    return dict(bg=np.clip(1.0 - person, 0.0, 1.0), person=person,
                skin=skin, face_skin=face_skin, hair=hair)


def landmarks(disp, person):
    """挑出**那一个人**的脸：返回 (lm, box, eyed, ovp)。`lm` = 5 个关键点 (re, le, no, rml, lml)。
    过不了闸就返回 (None, None, 0, 0) —— **宁可不做，也不要在假脸上动手**。"""
    import cv2
    u8 = np.ascontiguousarray((np.clip(disp, 0.0, 1.0) * 255).astype(np.uint8))
    H, W = u8.shape[:2]
    bgr = u8[:, :, ::-1]
    pm = person > 0.5
    ph = 0.0
    if int(pm.sum()) > int(getattr(C, 'FACE_MIN_PERSON_PX', 500)):
        ys, _xs = np.where(pm)
        ph = float(ys.max() - ys.min())
    try:
        _n, fs = _detector(W, H).detect(np.ascontiguousarray(bgr))
    except Exception:                                           # noqa: BLE001
        fs = None
    if fs is None or not len(fs):
        return None, None, 0.0, 0.0
    best = None
    for f in fs:
        lm = [np.array([float(f[4 + 2 * i]), float(f[5 + 2 * i])]) for i in range(5)]
        bb, eyed = lm_box(lm)
        if bb is None:
            continue
        x, y, w, h = [int(round(v)) for v in bb]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, W), min(y + h, H)
        if x1 - x0 < 16 or y1 - y0 < 16:
            continue
        m = np.zeros((H, W), bool)
        m[y0:y1, x0:x1] = True
        ovp = float((m & pm).sum()) / max(int(m.sum()), 1)
        if ovp < float(getattr(C, 'FACE_GATE_OVP', 0.85)):
            continue
        if eyed < float(getattr(C, 'FACE_GATE_EYED', 0.03)) * W:
            continue
        if ph > 0 and (y1 - y0) < float(getattr(C, 'FACE_GATE_H', 0.08)) * ph:
            continue
        if best is None or eyed > best[2]:
            best = (lm, bb, float(eyed), ovp)
    if best is None:
        return None, None, 0.0, 0.0
    return best


def lm_box(lm, wid=None, top=None, bot=None):
    """★ 用五官自己推框（以眼距为尺度）—— 比 YuNet 那个又大又偏的框稳。
    返回 ([x, y, w, h], 眼距)。"""
    re, le, no = np.asarray(lm[0], np.float64), np.asarray(lm[1], np.float64), np.asarray(lm[2], np.float64)
    eyed = float(np.linalg.norm(le - re))
    if not np.isfinite(eyed) or eyed < 1.0:
        return None, 0.0
    wid = float(getattr(C, 'FACE_BOX_WID', 2.40)) if wid is None else wid
    top = float(getattr(C, 'FACE_BOX_TOP', 1.40)) if top is None else top
    bot = float(getattr(C, 'FACE_BOX_BOT', 1.55)) if bot is None else bot
    cx, cy = 0.5 * (re[0] + le[0]), float(no[1])
    return [cx - wid / 2.0 * eyed, cy - top * eyed, wid * eyed, (top + bot) * eyed], eyed


def parse(disp):
    """**一次解析**：返回 L3 要的全部材料。没人脸时 `face` 为 None（调用方照着降级）。

    dict(
      masks  = dict(bg, person, skin, face_skin, hair)   # 软掩膜，和 disp 同尺寸
      face   = None 或 dict(lm, box=[x0,y0,x1,y1], eyed, ovp, weight=框羽化×person, mid=cx)
      person_weight = 软权重（**背景处恒为 0**）
    )

    ★ 09-14：`face`（正脸框）现在**只服务"挑脸/排假脸"这几道闸**。
    （原来那个靠"脸皮肤连通块 + 头窗口"定"脸在哪"的 `face_region` 已随脸部立体感一起删除。）
    """
    import cv2
    m = masks(disp)
    H, W = disp.shape[:2]
    lm, box, eyed, ovp = landmarks(disp, m['person'])
    out = dict(masks=m, face=None, person_weight=None)
    out['person_weight'] = person_weight(m['person'], (H, W))
    if lm is None:
        return out
    x, y, w, h = box
    x0, y0 = max(int(round(x)), 0), max(int(round(y)), 0)
    x1, y1 = min(int(round(x + w)), W), min(int(round(y + h)), H)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return out
    wf = np.zeros((H, W), np.float32)
    wf[y0:y1, x0:x1] = 1.0
    wf = cv2.GaussianBlur(wf, (0, 0), max(3.0, float(getattr(C, 'FACE_FEATHER_REL', 0.05)) * (x1 - x0)))
    out['face'] = dict(lm=lm, box=[x0, y0, x1, y1], eyed=eyed, ovp=ovp,
                       weight=wf * np.clip(m['person'], 0.0, 1.0),
                       box_weight=wf,
                       mid=0.5 * (float(lm[0][0]) + float(lm[1][0])))
    return out


def person_weight(person, shape=None, sigma_rel=None):
    """★ 人物权重：**在背景掩膜里必须恰好 = 0**。

    分割里 `person == 1 − bg` 恒成立 ⇒ 只在人身边界做高斯羽化会让力道**漏到背景一侧**
    （实测背景被顺手带亮 1.7~5.3 个 L*，而"背景不动"是 SV 明确定下的）。
    `min(blur(person), person)` 也堵不住：`bg > 0.6` 的掩膜像素对应 `person < 0.4`，权重还剩 0.4。
    ⇒ 正解 = 再乘一道 `smoothstep(person, lo, hi)`，让 `person ≤ lo`（⟺ `bg ≥ 1−lo`，覆盖整个背景掩膜）处为 0。
    """
    import cv2
    p = np.clip(np.asarray(person, np.float32), 0.0, 1.0)
    if shape is not None:
        p = cv2.resize(p, (int(shape[1]), int(shape[0])), interpolation=cv2.INTER_LINEAR)
    H, W = p.shape
    sig = max(3.0, (0.006 if sigma_rel is None else float(sigma_rel)) * W)
    soft = cv2.GaussianBlur(p, (0, 0), sig)
    lo = float(getattr(C, 'PERSON_W_LO', 0.45))
    hi = float(getattr(C, 'PERSON_W_HI', 0.80))
    t = np.clip((p - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return soft * (t * t * (3.0 - 2.0 * t))
