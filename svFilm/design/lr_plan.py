# -*- coding: utf-8 -*-
"""一张图的 LR 计划（**数据模型**，不含求解）。

单位就是 LR 面板上的数（UI），只有少数例外单独注明：

    wb.temperature  K（LR 温度滑杆；卡级，整批一个）
    wb.tint         LR tint 刻度（卡级；无公开刻度，见 lr_solve.wb_of）
    exposure_ev     档（LR Exposure2012；+1 = 线性光 ×2）
    curve           16 点 LUT，(显示码 x, 显示码 y)，0..255
    local.exposure_ev  档（写盘时 ×1/4 → LocalExposure2012，见 calibration）

设计顺序（`LR设计.md` §3 / `纪律.md` 第 4 条）：

    WB（卡级）→ 曝光/明暗（曝光补残差；Basic 四根默认 0；曲线补形状）
    → 颜色（**蒙版里的 Point Color** 调肤色）→ 局部（肤色蒙版）→ 细节

本模块只描述"要写什么"，不决定"写多少"：
  · 求解在 `lr_solve.py`（纯算，可离线复算）；
  · 落盘在 `lr_exec.py`/`lrsink.py`（XMP + 桥）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class WhiteBalance:
    """卡级 WB **目标/记账**（`LR设计.md` §2）。

    ★ `temperature` 不是场景 CCT，也不是解码温度：卡白点来自已调色的主片，
      约等于 D65；解码 WB 用相机/介质。这里只在确实做了**校准过的相对校正**
      时填（0 = 不落盘）。卡白点本身放 `card_white`。
    """

    temperature: float = 0.0          # 0 = 不落盘；非 0 必须是校准过的相对校正
    tint: float = 0.0
    source: str = ''                  # look-white-target / 校准来源
    card_white: Optional[List[float]] = None   # 卡白点 [L*, a*, b*]
    ours_white: Optional[List[float]] = None   # ① 的白点中位 [L*, a*, b*]
    note: str = ''

    def is_set(self) -> bool:
        return abs(float(self.temperature or 0.0)) > 1e-9


@dataclass
class PointColor:
    """肤色点颜色（**蒙版里的色票**，不是全局色票）。

    机制（昨天调研/跑通的那套）：在 subject 蒙版组里加一张 local point-color
    色票，取样色 = 这张脸的真实像素；`SatScale` 是**相对饱和刻度**，
    由肤色 C/L 关系给量。只动肤色窗内的像素，色相不在这里拧（归卡级 WB）。
    """

    h: float = 0.0                    # 取样色相（colorsys 0..1；写 SrcHue 时 ×2π，夹 5.95）
    s: float = 0.0
    l: float = 0.0
    range: float = 0.5
    sat_scale: float = 0.0            # -1..1（相对饱和刻度）
    raw: float = 0.0                  # 记账：C/L 关系残差（SatScale 之前）
    rgb: Optional[List[int]] = None   # 取样像素（真实像素，不反算）

    def is_set(self) -> bool:
        """有色票 = 有肤色蒙版（SatScale 可以是 0，蒙版本事仍要建）。"""
        return bool(self.rgb)


@dataclass
class Local:
    """一张卡一个局部蒙版：**肤色/脸**（`LR设计.md` §3.3）。

    蒙版 = LR 的 subject 组 + local point-color 肤色色票（SDK 没有 facialSkin
    子类型）；不是直接拿 subject 整身蒙版提亮 —— 后者会把白衫一起抬爆。
    """

    mask: str = 'skin'                # 只有 skin（脸/肤色范围）
    exposure_ev: float = 0.0          # 补脸的档数（写盘 = EV/4）
    shadows: float = 0.0              # UI；局部提暗部（人身上的暗部）
    highlights: float = 0.0           # UI；局部压人身上的亮部
    saturation: float = 0.0           # UI；肤色饱和（与点颜色二选一）

    def is_set(self) -> bool:
        return (abs(float(self.exposure_ev or 0.0)) > 1e-9
                or abs(float(self.shadows or 0.0)) > 1e-9
                or abs(float(self.highlights or 0.0)) > 1e-9
                or abs(float(self.saturation or 0.0)) > 1e-9)


@dataclass
class LRPlan:
    """一张图的 LR 参数（单位 = LR 面板数）。"""

    # ---- 卡级
    wb: WhiteBalance = field(default_factory=WhiteBalance)
    # ---- 图级：Basic（先整幅，后局部）
    exposure_ev: float = 0.0
    contrast: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    # ---- 图级：曲线（只补形状）
    curve: Optional[List[Tuple[int, int]]] = None
    # ---- 图级：肤色（**只用蒙版里的 Point Color**；不做 HSL）
    point_color: PointColor = field(default_factory=PointColor)
    # ---- 图级：冷暖/分色（按卡方向；剂量由目检验证）
    split_shadow_hue: float = 0.0
    split_shadow_sat: float = 0.0
    split_highlight_hue: float = 0.0
    split_highlight_sat: float = 0.0
    split_balance: float = 0.0
    # ---- 图级：局部（一张卡一个肤色蒙版）
    local: Local = field(default_factory=Local)
    # ---- 记账
    notes: Dict[str, object] = field(default_factory=dict)
    ledger: List[str] = field(default_factory=list)
    target: Dict[str, object] = field(default_factory=dict)
    card_dev: Dict[str, object] = field(default_factory=dict)

    # ---------------------------------------------------------------- 落盘

    def as_xmp_scalars(self) -> Dict[str, object]:
        """→ XMP 扁平键（0 不写；曲线/点颜色/蒙版单独处理）。"""
        out: Dict[str, object] = {}

        def put(key: str, v, eps: float = 1e-9):
            if v is None:
                return
            if isinstance(v, (int, float)) and abs(float(v)) <= eps:
                return
            out[key] = float(v)

        put('Exposure2012', self.exposure_ev)
        put('Contrast2012', self.contrast)
        put('Highlights2012', self.highlights)
        put('Shadows2012', self.shadows)
        put('Whites2012', self.whites)
        put('Blacks2012', self.blacks)
        put('SplitToningShadowHue', self.split_shadow_hue)
        put('SplitToningShadowSaturation', self.split_shadow_sat)
        put('SplitToningHighlightHue', self.split_highlight_hue)
        put('SplitToningHighlightSaturation', self.split_highlight_sat)
        put('SplitToningBalance', self.split_balance)
        # ★ WB **不写进 XMP**：TIFF/JPEG 在 LR 里的 Temp/Tint 是**相对量程**
        #   （Adobe：非 RAW 的色温和色调控件范围/单位不同），写开尔文会错。
        #   卡级 WB 在**引擎 RAW 解码层**落（`flow.render_card` 的 wb），这里只记账。
        return out

    def basic_text(self) -> str:
        """Basic 一行（终端/目检页）。"""
        parts = []
        for name, v in (('曝光', self.exposure_ev), ('对比', self.contrast),
                        ('高光', self.highlights), ('阴影', self.shadows),
                        ('白', self.whites), ('黑', self.blacks)):
            if abs(float(v or 0.0)) > 1e-9:
                parts.append('%s%+.2f' % (name, float(v)) if name == '曝光'
                             else '%s%+.0f' % (name, float(v)))
        return ' '.join(parts) or '不动'

    def color_text(self) -> str:
        parts = []
        if self.point_color.is_set():
            parts.append('点颜色 SatScale %+.2f' % self.point_color.sat_scale)
        if abs(self.split_shadow_sat) > 1e-9 or abs(self.split_highlight_sat) > 1e-9:
            parts.append('分色 暗%.0f°/%.0f 高%.0f°/%.0f'
                         % (self.split_shadow_hue, self.split_shadow_sat,
                            self.split_highlight_hue, self.split_highlight_sat))
        return ' ｜ '.join(parts) or '不动'

    def local_text(self) -> str:
        if not self.local.is_set():
            return '不动'
        parts = []
        if abs(self.local.exposure_ev) > 1e-9:
            parts.append('曝光%+.2f档' % self.local.exposure_ev)
        if abs(self.local.shadows) > 1e-9:
            parts.append('阴影%+.0f' % self.local.shadows)
        if abs(self.local.highlights) > 1e-9:
            parts.append('高光%+.0f' % self.local.highlights)
        if abs(self.local.saturation) > 1e-9:
            parts.append('饱和%+.0f' % self.local.saturation)
        return '肤色蒙版 ' + ' '.join(parts)
