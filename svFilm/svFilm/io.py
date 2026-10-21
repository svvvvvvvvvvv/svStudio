# -*- coding: utf-8 -*-
r"""入口关口 + 出口。**整个工程里唯一允许出现"机型"三个字的地方。**

入口做完三件事，之后下游不许再关心这张图是谁拍的：① 解码（RAW 走 rawpy；JPG 走 PIL）② IDT
（白平衡 + 色彩矩阵 + 黑白电平：RAW 由 rawpy 一次做完，JPG 已在相机里做完 ⇒ "显式降级"）③ 归一。
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
    r"""缩到长边 max_side。大图先缩后转浮点（否则多花好几秒）。"""
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
    r"""exif_transpose 后把 orientation 抹成 1，否则存回去会被转两次。"""
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
    # ★★ **不做二次白平衡**：中性色偏由解码那一步的 `PUBLIC_WB` 负责。
    lin = color.s2l(disp)
    return Sample(lin, disp, 'jpg', path, _exif_orientation_fixed(im),
                  dict(kind='jpg', note='无 IDT 余量（相机曲线已压过）'))



def load_raw(path, max_side=C.MAX_SIDE, wb=None):
    r"""RAW → **场景线性**（只有这一条路，没有开关）。

    ★ `wb`（可选）= 该**预设**自己的白平衡：`{white_balance, temperature, tint}`；
      缺省/None ⇒ 回退全局 `PUBLIC_WB*`。★ 这是“每条预设一个白点”的落点。

    ★★ **只留一条：RAW → 场景线性 → 引擎**（public GUI 的加载口径）：自己那条入口提亮路是死代码；
      预设的印相曝光 `pe` 按**这个输入**标定，提亮好几档就过曝发白。

    ★ `config.FAST_RAW=True`（默认）走 `fastraw.decode`：RAW 只开一次，WB 适应/tint/空间换算
      合成一次 3×3 —— **数学等价**，26 MP 上省 10 s 量级。关掉即回到 vendor 原路。"""
    import rawpy
    from . import cameras
    from . import spektra

    spektra._sf()                       # ★ 先钉扎：保证下面 import 到的是仓库自带那份 vendor

    _wb = wb or {}
    _white_balance = str(_wb.get('white_balance', getattr(C, 'PUBLIC_WB', 'custom')))
    _temperature = float(_wb.get('temperature', getattr(C, 'PUBLIC_WB_TEMPERATURE', 5200.0)))
    _tint = float(_wb.get('tint', getattr(C, 'PUBLIC_WB_TINT', 1.0)))

    if getattr(C, 'FAST_RAW', True):
        from . import fastraw
        lin, exif, make, model = fastraw.decode(
            path, max_side, wb=_wb, exif_fix=_exif_orientation_fixed)
    else:
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
            white_balance=_white_balance,
            temperature=_temperature,
            tint=_tint,
            output_colorspace=str(getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB')),
            output_cctf_encoding=False,
        )
        lin = np.clip(np.asarray(lin, np.float64), 0.0, None)
        lin = _resize(lin, max_side)

    cam = dict(cameras.lookup(make, model), make=make, model=model)
    cam['entry_loader'] = 'public'
    cam['entry_shape'] = 'public GUI 的加载（wb %s %sK / tint %s / %s / 无 CCTF）' % (
        _white_balance, _temperature, _tint,
        getattr(C, 'PUBLIC_COLORSPACE', 'ProPhoto RGB'))
    cam['bias_source'] = '无（输入＝public 的加载，不做入口提亮）'
    disp = np.clip(color.l2s(lin), 0.0, 1.0)
    return Sample(lin, disp, 'raw', path, exif, cam)


def load(path, max_side=C.MAX_SIDE, src=None, wb=None):
    k = kind_of(path)
    if src in (None, 'auto'):
        src = 'raw' if k == 'raw' else 'jpg'
    if src == 'raw':
        if k != 'raw':
            raise ValueError('要求 RAW，但 %s 不是 RAW' % path)
        return load_raw(path, max_side, wb=wb)
    return load_std(path, max_side)


def find_pair(path, prefer='raw'):
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
    kw = dict(quality=int(quality), subsampling=0, optimize=True)   # subsampling=0 = 4:4:4
    if exif:
        kw['exif'] = exif
    im.save(path, **kw)
    return path
