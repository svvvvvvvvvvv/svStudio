# -*- coding: utf-8 -*-
"""入口关口 + 出口。**整个工程里唯一允许出现"机型"三个字的地方。**

入口要做完三件事，做完之后下游不许再关心这张图是谁拍的：
  1) 解码：RAW 走 rawpy（线性、相机白平衡、关自动亮度）；JPG 走 PIL。
  2) IDT（输入设备变换）：白平衡 + 色彩矩阵 + 黑白电平 —— RAW 由 rawpy 一次做完；
     JPG 已经在相机里做完，拿不回来，所以是"显式降级"。
  3) 归一：对外只吐 lin（白点 1.0）、disp（0~1）两个域。
"""
from __future__ import annotations

import io as _stdlib_io
import os

import numpy as np
from PIL import Image, ImageOps

from . import color
from . import config as C

try:
    import cv2
except Exception:      # pragma: no cover
    cv2 = None

RAW_EXT = {'.raf', '.arw', '.cr2', '.cr3', '.nef', '.dng', '.orf', '.rw2', '.pef', '.srw', '.raw'}
STD_EXT = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp'}


class Sample:
    __slots__ = ('lin', 'disp', 'kind', 'path', 'name', 'exif', 'cam')

    def __init__(self, lin, disp, kind, path, exif=None, cam=None):
        self.lin = lin
        self.disp = disp
        self.kind = kind
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.exif = exif
        self.cam = cam if cam is not None else {}


def kind_of(path):
    e = os.path.splitext(path)[1].lower()
    if e in RAW_EXT:
        return 'raw'
    if e in STD_EXT:
        return 'std'
    raise ValueError('不认识的扩展名: %s' % path)


def _resize(arr, max_side):
    """缩到长边 max_side。大图先缩后转浮点 —— 40MP 转 float32 再缩要多花好几秒。"""
    h, w = arr.shape[:2]
    long_side = max(h, w)
    if max_side is None or long_side <= max_side:
        return arr
    s = float(max_side) / float(long_side)
    nw, nh = int(round(w * s)), int(round(h * s))
    if nw < 1 or nh < 1:
        raise ValueError('缩得太小')
    if cv2 is not None and arr.dtype == np.uint16:
        return cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_AREA)
    if cv2 is not None:
        return cv2.resize(arr.astype(np.float32), (nw, nh),
                          interpolation=cv2.INTER_AREA).astype(np.float64)
    ch = [np.asarray(Image.fromarray(arr[..., i].astype(np.float32), mode='F')
                     .resize((nw, nh), Image.BILINEAR), np.float64) for i in range(arr.shape[-1])]
    return np.stack(ch, axis=-1)


def _exif_orientation_fixed(im):
    """exif_transpose 之后把 orientation 标签抹成 1，否则存回去会被转两次。"""
    ex = im.getexif()
    if not ex:
        return None
    if 274 in ex:
        ex[274] = 1
    try:
        return ex.tobytes()
    except Exception:
        return None



def load_std(path, max_side=C.MAX_SIDE):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert('RGB')
    arr = np.asarray(im, np.float64) / 255.0
    arr = _resize(arr, max_side)
    disp = np.clip(arr, 0.0, 1.0)
    # ★★ 09-28 瘦身：**不做二次白平衡**（中性色偏由解码那一步的 PUBLIC_WB 负责）。
    #   旧的 `idt_wb`（近中性灰世界）已删 —— 它和入口白平衡是同一件事、且实测几乎没在工作。
    lin = color.s2l(disp)
    return Sample(lin, disp, 'jpg', path, _exif_orientation_fixed(im),
                  dict(kind='jpg', note='无 IDT 余量（相机曲线已压过）'))



def load_raw(path, max_side=C.MAX_SIDE):
    r"""RAW → **场景线性**（只有这一条路，没有开关）。

    ★★ 09-30 删掉「`public` / `own` 二选一」+ 本仓库自己那条 `'own'` 入口路
      —— **只留一条：RAW → 场景线性 → 引擎**。
      连带删掉：入口提亮 / 入口成形 / 逐张落点 / 高光护栏 `clip_guard` / 机型基线曝光
                / `ENTRY_LOADER` / `ENTRY_CLIP_*` / `CLIP_GUARD_ITERS` / `RAW_DECODE` /
                `rawmeta.py`。
      为什么（三条）：
        ① `ENTRY_LOADER` 早已是 `'public'` ⇒ 那条路**永不进入，是死代码**；
        ② 9 条预设的印相曝光 `pe` 是按**这个输入**标定的（线性中位 Y≈0.056）；
           自己那条会把画面提到 Y≈0.42（**差 2.9 档**）⇒ 喂预设就过曝发白；
        ③ **能在引擎里实现的就别自己再留一套** —— 入口提亮本来就由
           `camera.auto_exposure`（7 种测光）在做；高光该由引擎的 `preflash_exposure`
           / `lightness_compression` 负责。
      ⚠ 白平衡仍在这一步（`PUBLIC_WB` 那组 config），它直接喂给 vendor 的加载器。
      依据与理由见技能 §144。
    """
    import rawpy
    from . import cameras
    from . import spektra

    spektra._sf()                       # ★ 先钉扎：保证下面 import 到的是仓库自带那份 vendor
    from spektrafilm.utils import load_and_process_raw_file

    exif = make = model = thumb = None
    with rawpy.imread(path) as raw:     # 只为了抠内嵌 JPG 拿 EXIF / 机型（不解码传感器数据）
        try:
            th = raw.extract_thumb()
            if th.format == rawpy.ThumbFormat.JPEG:
                thumb = th.data
                tmp = ImageOps.exif_transpose(Image.open(_stdlib_io.BytesIO(th.data)))
                ex = tmp.getexif()
                make = str(ex.get(271) or '') or None
                model = str(ex.get(272) or '') or None
                exif = _exif_orientation_fixed(tmp)
        except Exception:
            pass

    lin = load_and_process_raw_file(
        path,
        white_balance=str(getattr(C, 'PUBLIC_WB', 'custom')),
        temperature=float(getattr(C, 'PUBLIC_WB_TEMPERATURE', 5200.0)),
        tint=float(getattr(C, 'PUBLIC_WB_TINT', 1.0)),
        output_colorspace=str(getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB')),
        output_cctf_encoding=False,
    )
    lin = np.clip(np.asarray(lin, np.float64), 0.0, None)
    lin = _resize(lin, max_side)

    cam = dict(cameras.lookup(make, model), make=make, model=model)
    cam['entry_loader'] = 'public'
    cam['entry_shape'] = 'public GUI 的加载（wb %s %sK / %s / 无 CCTF）' % (
        getattr(C, 'PUBLIC_WB', 'custom'), getattr(C, 'PUBLIC_WB_TEMPERATURE', 5200.0),
        getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB'))
    cam['bias_source'] = '无（输入＝public 的加载，不做入口提亮）'
    disp = np.clip(color.l2s(lin), 0.0, 1.0)
    return Sample(lin, disp, 'raw', path, exif, cam)


def load(path, max_side=C.MAX_SIDE, src=None):
    """src: None/'auto' | 'raw' | 'jpg'（jpg = 走解码器出来的显示域，不是机内 JPEG）"""
    k = kind_of(path)
    if src in (None, 'auto'):
        src = 'raw' if k == 'raw' else 'jpg'
    if src == 'raw':
        if k != 'raw':
            raise ValueError('要求 RAW，但 %s 不是 RAW' % path)
        return load_raw(path, max_side)
    return load_std(path, max_side)


def find_pair(path, prefer='raw'):
    """给一个 stem 或任一路径，找同名的 RAW/JPG 对。返回实际存在的路径。"""
    stem, ext = os.path.splitext(path)
    ext = ext.lower()
    if prefer == 'raw' and ext in RAW_EXT:
        return path
    if prefer == 'jpg' and ext in STD_EXT:
        return path
    order = (RAW_EXT, STD_EXT) if prefer == 'raw' else (STD_EXT, RAW_EXT)
    for exts in order:
        for e in sorted(exts):
            cand = stem + e
            if os.path.exists(cand):
                return cand
    return path


def save(disp, path, exif=None, quality=C.JPEG_QUALITY):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    im = Image.fromarray(color.display_to_u8(disp))
    kw = dict(quality=int(quality), subsampling=0, optimize=True)
    if exif:
        kw['exif'] = exif
    im.save(path, **kw)
    return path
