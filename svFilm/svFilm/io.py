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
from . import rawmeta

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



def clip_guard(lin, cfg=C):
    """入口高光护栏：给入口增益设一个**只往下**的上限（按"允许裁切的像素比例"）。

    为什么必须在**入口**做：高光的梯度只要过了下游的 `np.clip(lin, 0, 1)` 就没了，
    L4 的 `guard.CAP_WHITE_FRAC` 只能把一块平板压暗，**救不回形状**（见 SKILL.md §19.2）。

    依据（三方一致，见 SKILL.md §19.3）：
      * Adobe DNG 规范：`BaselineExposure` = "高光还能往回捞多少 EV 而不真裁切" —— 是**余量**，
        **不是"该加的增益"**；
      * RawTherapee：EV=0 = **增益刚好让最亮的通道不裁切**，Auto 用 **Clip%**（默认 0.2%）定白点；
      * darktable filmic："把中间调调对，**高光别管**"。

    返回 (lin_out, k)。**k ≤ 1，不裁切的图 k=1 ⇒ 逐位不变。** 判据用线性亮度
    （`color.luma`，与 `apply_entry_curve` 同一个量），"裁切"阈值 = 显示域 `ENTRY_CLIP_LEVEL` 的线性值。
    """
    if not getattr(cfg, 'ENTRY_CLIP_GUARD', False):
        return lin, 1.0
    allow = float(getattr(cfg, 'ENTRY_CLIP_ALLOW', 0.0))
    lvl = float(color.s2l(float(getattr(cfg, 'ENTRY_CLIP_LEVEL', 254.0 / 255.0))))
    y = color.luma(np.clip(lin, 0.0, None))
    if float(np.mean(y >= lvl)) <= allow:
        return lin, 1.0
    # ★ P2-9：下界是**保险丝**（config.ENTRY_CLIP_GUARD_LO，默认 0.60 ⇒ 最多压 −0.74EV），
    #   原来写死 0.02（≈ −5.6EV）—— 遇到大面积真过曝会把正常曝光的部分也一起拖黑。
    lo = float(getattr(cfg, 'ENTRY_CLIP_GUARD_LO', 0.60))
    hi = 1.0                              # 裁切比例随 k 单调不减 ⇒ 二分
    for _ in range(int(getattr(cfg, 'CLIP_GUARD_ITERS', 28))):
        mid = 0.5 * (lo + hi)
        if float(np.mean(y * mid >= lvl)) <= allow:
            lo = mid
        else:
            hi = mid
    return lin * lo, float(lo)


def _load_raw_public(path, max_side):
    r"""RAW → 场景线性，走 **spektrafilm 自己的加载**（与 public GUI 同一条）。

    ★ 为什么要单开这一条：9 条预设的**印相曝光 `pe` 是按 public 那个输入标定的**
      （线性图中位亮度 Y ≈ 0.056）。本仓库自己的入口会把画面提到"场景线性光"（Y ≈ 0.42，
      差 **2.9 档**）—— 那 2.9 档在印相时又被提一回 ⇒ **过曝发白**。

    ⚠ 这一条**不做**白平衡 / 入口成形 / 落点 / 高光护栏 —— 那些是"自己调胶片感"时代的做法，
      喂的是我们自己标的真卷；换成 public 的预设之后它们就不适用了。
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


def load_raw(path, max_side=C.MAX_SIDE):
    """RAW 解码 + IDT（白平衡 / 色彩矩阵由 rawpy 完成；**基线曝光**按机型表 + DR tag 补回）。

    ⚠ `config.ENTRY_LOADER` 决定走哪条：`'public'`（默认，与 public GUI 同一条）或
      `'own'`（本仓库自己的入口成形）。两条路的差别与理由见 `_load_raw_public` 的注释。
    """
    if str(getattr(C, 'ENTRY_LOADER', 'own')).lower() == 'public':
        return _load_raw_public(path, max_side)

    import rawpy
    from . import cameras

    kw = dict(C.RAW_DECODE)
    exif = None
    make = model = None
    thumb = None
    with rawpy.imread(path) as raw:      # 一次打开：解码 + 缩略图（拿 EXIF 和机型）
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
        rgb = raw.postprocess(use_camera_wb=kw['use_camera_wb'],
                              no_auto_bright=kw['no_auto_bright'],
                              output_bps=kw['output_bps'],
                              gamma=kw['gamma'],
                              half_size=kw['half_size'],
                              output_color=rawpy.ColorSpace.sRGB)

    rgb = _resize(rgb, max_side)           # 先在 uint16 上缩，省一次 40MP 的浮点转换
    lin = rgb.astype(np.float64) / 65535.0
    lin = np.clip(lin, 0.0, None)          # 只挡负值，不夹上限（高光余量要留着给 L1）

    # ---- 基线曝光（baseline exposure）：相机故意欠曝那几档，在这里一次补回来 ----
    # 相机厂商把"把中点抬到 18%"的活留给转换器做（DNG 里叫 baseline exposure），
    # Adobe 系静默做掉。富士还要按 DR 档额外欠曝 1~2 档，档位写在 RAF 的 MakerNote 里。
    cam = dict(cameras.lookup(make, model), make=make, model=model)
    # ---- 元数据（**只读**，不受开关影响）：开关管的是"补不补"，不是"读不读" ----
    dr = None
    raw_ev = None
    if thumb is not None:
        try:
            mn = rawmeta.thumb_makernote(thumb)
            dr = rawmeta.fuji_development_dr(mn)
            raw_ev = rawmeta.fuji_raw_ev(mn)
        except Exception:
            dr, raw_ev = None, None
    cam['fuji_dr'] = dr

    # ★★ 09-28 瘦身：**「机型基线曝光」整块删掉**。
    #   理由：删掉 entry_tone 之后，bias 只被写进报告、不参与任何运算（实测空转）。
    #   ⇒ 基线曝光交给**引擎的 camera.auto_exposure**（本来就在跑）。
    #   ⚠ `cameras.py` 里那张实测表先留着（SV 量出来的，且将来引擎若要补偿还用得上）。

    # ---- ★ 入口高光护栏（09-13 SV 拍板）：「按高光不裁切定零点」 ----
    # 放在这里 = **在 `np.clip(lin, 0, 1)` 之前**，所以高光的梯度还救得回来。
    # k ≤ 1 只往下压；不裁切的图 k=1 ⇒ 逐位不变（X-T30 III 三张实测 k≈1.0）。
    if C.ENTRY_CLIP_GUARD:
        lin, _gk = clip_guard(lin, C)
        cam['entry_clip_guard_k'] = _gk
        if _gk < 0.999:
            cam['bias_source'] = '%s +高光护栏 ×%.3f' % (cam.get('bias_source', ''), _gk)

    # ★ 09-14 SV 选「D 肤色优先 + C 相机直出回落」：老 `idt_wb` 限幅 ±0.25、实测只动 0.13 个 b\* ⇒ 基本没在工作。
    # ★★ 09-28 瘦身：旧的「肤色优先白平衡」已删（它和 L4 的 skin_hue 是同一件事）。
    #   中性色偏由解码那一步的 PUBLIC_WB 负责（5200K / ProPhoto），此处不再二次白平衡。
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
