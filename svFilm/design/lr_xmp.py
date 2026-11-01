# -*- coding: utf-8 -*-
"""LR 的 **XMP 作业单** 读写（取代"通过桥逐键写"）。

为什么走 XMP（10-28 实测）：桥逐键写有三个治不好的病 —— 键名靠猜、结构化键
（PointColors/曲线）写不进、状态只活在会话里。XMP 是一份有文档、可 diff、
可回放的文件。

实测硬约束（缺一条就整份静默失效，LR 一个数都不读、不报错）：
  1. 必须有 `<?xpacket ...?>` 包头
  2. `crs:AlreadyApplied="False"`
  3. `crs:HasSettings="True"`
WB：默认 `As Shot`；卡级 WB 要写 `WhiteBalance="Custom"` + `Temperature/Tint`。

PointColor 另有一条：**必须带 `crs:ColorVariance`**，否则 LR 把色票吞掉、
规范成 19 个 −1 的哨兵（静默）。

写进 TIF 用 TIFF tag 700（TIF 没有 sidecar），不需要 exiftool。
"""
from __future__ import annotations

import colorsys
import math
from typing import Dict, List, Optional

from PIL import Image

CRS_VERSION = '18.1'
PROCESS_VERSION = '15.4'

# 色相窗（默认 Range）：全效 ±1/6、归零 ±1/2 —— 取自 LR 真记录
HUE_WINDOW = (0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0)
# 饱和/亮度窗的半宽（绝对量）：全效 ±0.18、归零 ±0.73 —— 同上
FULL_HALF = 0.18
NONE_HALF = 0.73
# ★ 官方约束：SrcHue 只能取 0~6（弧度）。色相环最后 16°（343.8°~360° = 6.0~6.283）
#   落在范围外，写进去 LR 静默丢弃（实测 356.8° 写 6.2273 → 一点都不动）。
HUE_MAX = 5.95
# 只按色相选，浓淡/明暗全开（实测："按取样值收窗"会废掉，全开有效）
WIDE_WINDOW = (0.0, 0.0, 1.0, 1.0)


def _window(center, full=FULL_HALF, none=NONE_HALF):
    return [max(0.0, center - none), max(0.0, center - full),
            min(1.0, center + full), min(1.0, center + none)]


def point_color(rgb, sat_scale=0.0, hue_shift=0.0, lum_scale=0.0, range_amount=0.5,
                wide_sat=True):
    """按 LR 的真实 19 数字格式造一份 PointColor 记录（**局部肤色色票**）。

    rgb       : 取样色（sRGB 0..1）——取自该图自己的脸颊像素，不用 Lab 反算
    sat_scale : −1..1（= LR 面板 ±100）
    hue_shift : −1..1（= ±100）
    wide_sat  : 饱和窗全开（0,0,1,1）——实测收窗会自己掐自己
    """
    h, l, s = colorsys.rgb_to_hls(float(rgb[0]), float(rgb[1]), float(rgb[2]))
    vals = [h * 2 * math.pi, s, l,
            float(hue_shift), float(sat_scale), float(lum_scale),
            float(range_amount)]
    vals += list(HUE_WINDOW)
    vals += [0.0, 0.0, 1.0, 1.0] if wide_sat else _window(s)
    vals += list(WIDE_WINDOW)          # 亮度窗也全开（收窗实测完全失效）
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

    scalars      : {'Exposure2012': 0.3, 'WhiteBalance': 'Custom', ...}
    curve        : [(x, y), ...] 0..255 的主色调曲线
    point_colors : 19 数字记录列表（**局部肤色色票**；全局色票不用）
    """
    scalars = dict(scalars or {})
    if curve:
        scalars.setdefault('ToneCurveName2012', 'Custom')
    if 'WhiteBalance' not in scalars:
        scalars['WhiteBalance'] = 'As Shot'
    body = ['<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>',
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="svStudio">',
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
            '  <rdf:Description rdf:about=""',
            '   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"',
            '   crs:Version="%s"' % CRS_VERSION,
            '   crs:ProcessVersion="%s"' % PROCESS_VERSION,
            '   crs:AlreadyApplied="False"',
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
        # ★ 缺这一段，LR 会把色票吃掉（静默）；LR 自己写真色票时也写 0
        body.append('   <crs:ColorVariance>')
        body.append('    <rdf:Seq>')
        body.append('     <rdf:li>0.000000</rdf:li>')
        body.append('    </rdf:Seq>')
        body.append('   </crs:ColorVariance>')
    body += ['  </rdf:Description>', ' </rdf:RDF>', '</x:xmpmeta>',
             '<?xpacket end="w"?>']
    return '\n'.join(body)


def write_tif(src: str, dst: str, xmp_text: str) -> str:
    """把 XMP 写进 TIF 自身的 tag 700（像素不动，只换容器）。"""
    im = Image.open(src)
    im.save(dst, format='TIFF', tiffinfo={700: xmp_text.encode('utf-8')})
    return dst


def read_xmp(path: str) -> Optional[str]:
    """读回文件里的 XMP（验 LR 到底认了多少）。"""
    tag = Image.open(path).tag_v2.get(700)
    return bytes(tag).decode('utf-8', 'replace') if tag else None
