# -*- coding: utf-8 -*-
"""LR 的 **XMP 作业单** 读写（取代"通过桥逐键写"）。

为什么换（10-28 实测）：桥写键有三个治不好的病 —— 键名靠猜（`RedPrimarySaturation`
写了等于没写、`ColorGradeShadowHue` 根本不走这个键）、结构化键写不进（遮色片 / Point
Color）、写完的状态只活在 LR 会话里。XMP 是一份有文档、有实例（LR 自己写的 114 键）、
可 diff 可回放的文件。

实测得到的三条硬约束（缺一条就整份静默失效，LR 一个数都不读、不报错）：
  1. 必须有 `<?xpacket ...?>` 包头
  2. `crs:AlreadyApplied="False"`
  3. `crs:WhiteBalance="As Shot"`

PointColor 另有一条：**必须带 `crs:ColorVariance`**，否则 LR 把色票吞掉、
规范成 19 个 −1 的哨兵（静默）。

写进 TIF 用 TIFF tag 700（TIF 没有 sidecar），不需要 exiftool。
"""
from __future__ import annotations

import colorsys
import math

import cv2
import numpy as np
from PIL import Image

# LR 15.1 自己写出来的版本号（别写比它新的：没收益）
CRS_VERSION = '18.1'
PROCESS_VERSION = '15.4'

# 色相窗（默认 Range）：相对取样值 0.5，±1/6 全效、±1/2 归零 —— 取自 LR 真记录
HUE_WINDOW = (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)
# 饱和/亮度窗的半宽（绝对量）：全效 ±0.18、归零 ±0.73 —— 同上
FULL_HALF = 0.18
NONE_HALF = 0.73
# ★ 官方约束：SrcHue 只能取 0~6（弧度）。色相环最后 16°（343.8°~360° = 6.0~6.283）
#   **落在范围外**，写进去 LR 静默丢弃。实测：356.8° 的图写 6.2273 → 一点不动；
#   压到 5.95 → 立刻生效（C 10.45→18.62）。所以一律夹到 5.95。
HUE_MAX = 5.95
# 只按色相选，浓淡/明暗全开（实测："改名下一档窗"会废掉，全开有效）
WIDE_WINDOW = (0.0, 0.0, 1.0, 1.0)


def lab_to_rgb(L, C, h):
    """Lab(L*,C*,h°) → sRGB 0..1（uint8 域，与 measure.lab 互逆）。"""
    a = C * math.cos(math.radians(h))
    b = C * math.sin(math.radians(h))
    arr = np.array([[[L * 255.0 / 100.0, a + 128.0, b + 128.0]]], np.uint8)
    out = cv2.cvtColor(arr, cv2.COLOR_LAB2RGB)[0, 0].astype(float) / 255.0
    return [float(min(1.0, max(0.0, v))) for v in out]


def _window(center, full=FULL_HALF, none=NONE_HALF):
    return [max(0.0, center - none), max(0.0, center - full),
            min(1.0, center + full), min(1.0, center + none)]


def hsl_hue(rgb):
    """sRGB → HSL 色相（圈 0..1）。LR 的色相滑杆用的是这个轴，不是 Lab 的。"""
    h, _, _ = colorsys.rgb_to_hls(float(rgb[0]), float(rgb[1]), float(rgb[2]))
    return h


def sample_cheek(path, long=1080):
    """**吸管**：从图里脸颊那一块取真实的 HSL 色相/饱和/亮度（取中位，色相用圆中位）。

    ★ 为什么不能用 Lab 中位反算（10-28 实测教训）：Lab 的 (L,C,h) 三个中位拼起来
      并不对应任何一个真实像素的颜色；用它反算出的 HSL 色相可以偏出几十度，
      点颜色的色相窗就整个打空 —— 症状是"色票写进去了、渲染零变化"。
      实测：同一张图，Lab 反算的色相 354.5° 完全不动，改用 342° 或 0° 立刻生效。
    """
    import os as _os
    import sys as _sys
    _sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\skin_v2')
    import skin_v2
    im = skin_v2.load_side(path, long)
    if im is None:
        return None
    fi = skin_v2.detect(im)
    if not fi:
        return None
    crop, meta = skin_v2.align_crop(im, fi)
    fc = skin_v2.face_mask(crop)
    centers, einfo = skin_v2.ellipses(meta)
    mouth = skin_v2.mouth_mask(meta)
    yy, xx = np.ogrid[:crop.shape[0], :crop.shape[1]]
    eye_mid = 0.5 * (meta['le'] + meta['re'])
    mouth_mid = 0.5 * (meta['lm'] + meta['rm'])
    fw = einfo['fw']
    nose = (np.abs(xx - 0.5 * (eye_mid[0] + mouth_mid[0])) < 0.14 * fw) & \
           (yy > eye_mid[1] - 0.02 * fw) & (yy < mouth_mid[1] + 0.02 * fw)
    m = np.zeros(crop.shape[:2], bool)
    for cx, cy in centers:
        m |= skin_v2.ellipse_mask(cx, cy, einfo['rx'], einfo['ry'])
    m &= fc & (~mouth) & (~nose)
    if int(m.sum()) < 50:
        return None
    px = crop[m]
    # 向量化 HSL
    mx = px.max(axis=1)
    mn = px.min(axis=1)
    d = mx - mn
    l = (mx + mn) / 2.0
    s = np.where(l > 0.5, d / np.maximum(2.0 - mx - mn, 1e-6), d / np.maximum(mx + mn, 1e-6))
    r, g, b = px[:, 0], px[:, 1], px[:, 2]
    h = np.zeros_like(l)
    nz = d > 1e-9
    hh = np.zeros_like(l)
    hh = np.where((mx == r) & nz, ((g - b) / np.maximum(d, 1e-9)) % 6.0, hh)
    hh = np.where((mx == g) & nz, (b - r) / np.maximum(d, 1e-9) + 2.0, hh)
    hh = np.where((mx == b) & nz, (r - g) / np.maximum(d, 1e-9) + 4.0, hh)
    h = (hh / 6.0) % 1.0
    # 只取真有颜色的像素（去高光/死黑）
    keep = (d > 0.02) & (s > 0.02)
    if int(keep.sum()) >= 50:
        h, s, l = h[keep], s[keep], l[keep]
    # 色相圆中位
    ang = h * 2 * np.pi
    hmed = (np.angle(np.mean(np.exp(1j * ang))) % (2 * np.pi)) / (2 * np.pi)
    return dict(hue=float(hmed), sat=float(np.median(s)), lum=float(np.median(l)),
                n=int(h.size))


def point_color_from_sample(smp, sat_scale=0.0, hue_shift=0.0, lum_scale=0.0,
                            range_amount=0.5, wide_sat=True):
    """直接用吸管结果（hue/sat/lum）造记录，跳过 Lab→RGB 那一步。

    ★ 两条硬规则（都是实测出来的，不是猜的）：
      ① `SrcHue` 夹到 ≤5.95 —— 官方量程 0~6，色相环最后 16° 在范围外，超了静默失效。
      ② 饱和窗/亮度窗全开 —— 窗一旦按"取样值 ±0.18/±0.73"去开，LR 不认（实测失效）；
         全开反而是对的语义：**这个色相上的所有浓淡都归我管**。
    """
    hue = min(float(smp['hue']) * 2 * math.pi, HUE_MAX)
    vals = [hue, smp['sat'], smp['lum'],
            float(hue_shift), float(sat_scale), float(lum_scale),
            float(range_amount)]
    vals += list(HUE_WINDOW)
    vals += list(WIDE_WINDOW if wide_sat else _window(smp['sat']))
    vals += list(WIDE_WINDOW)
    return ', '.join('%.6f' % v for v in vals)


def point_color(rgb, sat_scale=0.0, hue_shift=0.0, lum_scale=0.0, range_amount=0.5,
                wide_sat=True):
    """按 LR 的真实 19 数字格式造一份 PointColor 记录。

    rgb       : 取样色（sRGB 0..1）——应取自**该图自己的脸颊**
    sat_scale : −1..1（= LR 面板上的 ±100）；正=更浓
    hue_shift : −1..1（= ±100），正=往色相环下一格（红→黄方向）
    wide_sat  : **饱和窗开到全宽**（0,0,1,1）。

    ★ 为什么默认开宽（10-28 实测）：LR 的默认饱和窗是围着**取样时的饱和度**开的
      （全效 ±0.18、归零 ±0.73）。我们一推饱和，像素就浮出窗口 ⇒ 自己掐自己。
      实测：取样 s 高的图（暖橙黄，h 27~51）能到 ×2.0~2.27；
      取样 s 低的图（偏红，h 4~16）卡在 ×1.17~1.34。窗口开宽后只剩"色相选择"，
      这正是我们要的语义：**这块色相上的所有浓淡都归我管**。
    """
    h, l, s = colorsys.rgb_to_hls(float(rgb[0]), float(rgb[1]), float(rgb[2]))
    vals = [h * 2 * math.pi, s, l,
            float(hue_shift), float(sat_scale), float(lum_scale),
            float(range_amount)]
    vals += list(HUE_WINDOW)
    vals += [0.0, 0.0, 1.0, 1.0] if wide_sat else _window(s)
    vals += _window(l)
    return ', '.join('%.6f' % v for v in vals)


def _scalar_attrs(params):
    """扁平标量键 → XMP 属性行。值按 LR 的习惯带符号写。"""
    out = []
    for k, v in params.items():
        if isinstance(v, bool):
            out.append('   crs:%s="%s"' % (k, 'True' if v else 'False'))
        elif isinstance(v, (int, float)):
            out.append('   crs:%s="%+g"' % (k, v))
        else:
            out.append('   crs:%s="%s"' % (k, v))
    return '\n'.join(out)


def build_xmp(scalars=None, curve=None, point_colors=None):
    """造一份完整 XMP 包。

    scalars      : {'Saturation': 40, 'Vibrance': 11, ...}（键名不带 crs:）
    curve        : [(x, y), ...] 0..255 的主色调曲线（会自动写 ToneCurveName2012="Custom"）
    point_colors : point_color() 返回的字符串列表
    """
    scalars = dict(scalars or {})
    if curve:
        scalars.setdefault('ToneCurveName2012', 'Custom')
    body = ['<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>',
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="svStudio">',
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            '  <rdf:Description rdf:about=""',
            '   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"',
            '   crs:Version="%s"' % CRS_VERSION,
            '   crs:ProcessVersion="%s"' % PROCESS_VERSION,
            '   crs:AlreadyApplied="False"',
            '   crs:WhiteBalance="As Shot"',
            '   crs:HasSettings="True"']
    attrs = _scalar_attrs(scalars)
    if attrs:
        body.append(attrs)
    body[-1] = body[-1] + '>'
    if curve:
        body.append('   <crs:ToneCurvePV2012>')
        body.append('    <rdf:Seq>')
        for x, y in curve:
            body.append('     <rdf:li>%d, %d</rdf:li>' % (round(x), round(y)))
        body.append('    </rdf:Seq>')
        body.append('   </crs:ToneCurvePV2012>')
    if point_colors:
        body.append('   <crs:PointColors>')
        body.append('    <rdf:Seq>')
        for rec in point_colors:
            body.append('     <rdf:li>%s</rdf:li>' % rec)
        body.append('    </rdf:Seq>')
        body.append('   </crs:PointColors>')
        # ★ 缺这一段，LR 会把色票吃掉（静默）
        body.append('   <crs:ColorVariance>')
        body.append('    <rdf:Seq>')
        # ★ 2026-10-29：LR 自己写的是 **-50**，我们一直写 0 —— 实测 0 时点颜色完全无效
        #   （一张图三档 sat/hue/lum 全部无反应；见 design 的对齐审计）
        body.append('     <rdf:li>-50.000000</rdf:li>')
        body.append('    </rdf:Seq>')
        body.append('   </crs:ColorVariance>')
    body += ['  </rdf:Description>', ' </rdf:RDF>', '</x:xmpmeta>',
             '<?xpacket end="w"?>']
    return '\n'.join(body)


def write_tif(src, dst, xmp_text):
    """把 XMP 写进 TIF 自身的 tag 700（像素不动，只换容器）。"""
    im = Image.open(src)
    im.save(dst, format='TIFF', tiffinfo={700: xmp_text.encode('utf-8')})
    return dst


def read_xmp(path):
    """读回文件里的 XMP（验 LR 到底认了多少）。"""
    tag = Image.open(path).tag_v2.get(700)
    return bytes(tag).decode('utf-8', 'replace') if tag else None
