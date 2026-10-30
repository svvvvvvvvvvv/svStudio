# -*- coding: utf-8 -*-
"""LR 的数值口径索引：**哪些能算、哪些不能算、出处在哪**。

★ 2026-10-30：本模块**不参与求解链**（没有调用者），只做"查得到/查不到"的
登记簿。真正算数的实现在：
    · `photometry.py`   —— L* ↔ 线性光 ↔ EV ↔ sRGB 码（标准定义式）
    · `analysis/corpus/kurai/_lr_models/` —— 2026-10-31 矩阵得到的分析模型
      （`lr_response.py` / borrowed `tone_model.py`），**只做分析，不进生产**
    · `calibration.py`  —— 单位换算 + n<5 台账
    · `color_math.py`   —— Lab ↔ XYZ ↔ CCT / HSL（标准定义式）
保留它是因为历史文档和代码注释都指向这里；删掉会让"为什么没有公式"失去索引。

能算（有出处，精确）
--------------------
E1 曝光滑杆单位 = **档（f-stop / EV）**
   Adobe：「The exposure values are in increments equivalent to f-stop (aperture)
   increments in your camera.」
   → https://helpx.adobe.com/lightroom-classic/using/image-tone-color.html
   ⇒ 滑杆 +1 ⇒ 线性域 ×2。精确关系。
E2 `ToneCurveName2012` = `Linear` / `Custom`；写自定义点必须置 `Custom`。
E3 `PointColors` 的 19 数字布局 = `SatScale, LumScale, RangeAmount` + 三组窗口。
E4 `PointColors` 必须配 `ColorVariance`；缺了 LR 把色票归一成 19 个 −1（静默）。
E5 `SrcHue` 合法范围 **0~6 弧度**；超出静默丢弃（实测夹 5.95）。
E6 局部族的文件刻度（对真实 LR XMP 实测，见 autoshade `docs/TECH_STACK.md`）：
   · `LocalExposure2012` 文件值 = **EV/4**（4 档 = 满刻度 1.0）；
   · 其余局部滑杆（contrast/highlights/shadows/whites/blacks/clarity/dehaze/
     texture/saturation/temperature/tint/local sharpness/luminance NR）文件值 = **UI/100**；
   · `LocalHue` 文件值 = **度/180**。
E7 曲线 LUT 的横轴是**显示伽马码**（0..255），不是 L*。曝光是曲线之前的
   线性光增益：`E' = sRGB_encode(sRGB_decode(x)·2^EV)`（autoshade `tone_exposure_curve`）。
   ⇒ L* 与码之间必须走 sRGB 传输函数；`L*=50` 对应码 ≈119，不是 127.5。

没有公开闭式（**不许编斜率**）
-----------------------------
H1 `Highlights` / `Shadows` / `Whites` / `Blacks` / `Contrast`
   Adobe 只说"按亮度区域自适应、保护其它区域"，没有公开传递函数。
   → https://helpx.adobe.com/lightroom-classic/desktop/help/tone-control-adjustment.html
   ⇒ 2026-10-31 的 86 臂矩阵只用于**分析**：borrowed `tone_model` 被否掉
     （MAE 2.61 / P90 6.42 / max 15.83），k(L) 也只是一阶近似。
     **生产不用这些模型**：没有公开传递函数 ⇒ 方向 + 一档 + ③ 验。
H2 `Saturation` / `Vibrance`
   Adobe 只说 Vibrance 保护高饱和与肤色；没有公开公式。
   ⇒ Saturation 按**相对刻度**处理（HSL 的 S 是百分比）：`ΔC ≈ C0·S/100`；
     Vibrance 只当方向。
H3 HSL 色相滑杆的 ±100 映射
   社区约定 ±100 ≈ ±30°，不是 Adobe 文档值。肤色色相优先用
   `color_math.lab_to_hsl` 先算"要转多少度"，再决定用不用这根滑杆。
H4 点颜色的 SatScale / LumScale
   无公开传递函数；只有实测台账（`calibration.LEDGER`）。

出处清单
--------
· CIELAB：CIE 15:2018 / ISO 11664-4；Bruce Lindbloom, "Lab to XYZ"。
· sRGB：IEC 61966-2-1:1999。
· 分位数与单调映射可交换：概率论基本性质（Q_p(g(X))=g(Q_p(X))）。
· 单调三次插值：Fritsch & Carlson, SIAM J. Numer. Anal. 17(2), 1980。
· LR 实测刻度 / 色调模型结构：autoshade
  `docs/TECH_STACK.md` §XMP and Lightroom interoperability、`src/render/tone.rs`。
"""
from __future__ import annotations

from typing import Dict, Optional

# ---- 常数的唯一出处（改这里就够了；但实际换算在 photometry / calibration） ----
EXPOSURE_UNIT = 'stop'          # E1：滑杆单位 = 档
HW_LIMIT = 100.0                # 高光/阴影那组滑杆的上下限
HSL_HUE_DEG_PER_100 = 30.0      # H3：约定（非文档）
SRC_HUE_MAX = 5.95              # E5：实测，0~6 弧度制，夹 5.95
LOCAL_EXPOSURE_PER_EV = 0.25    # E6：文件值 = EV/4
LOCAL_UI_SCALE = 0.01           # E6：其余局部滑杆文件值 = UI/100

SOURCES: Dict[str, str] = dict(
    cielab='https://en.wikipedia.org/wiki/CIELAB_color_space',
    srgb='https://www.w3.org/TR/css-color-4/#predefined-sRGB',
    adobe_exposure=('https://helpx.adobe.com/lightroom-classic/using/'
                    'image-tone-color.html'),
    adobe_tone=('https://helpx.adobe.com/lightroom-classic/desktop/help/'
                'tone-control-adjustment.html'),
    autoshade_tech=('https://github.com/skymanbp/autoshade/blob/main/docs/'
                    'TECH_STACK.md'),
    autoshade_tone=('https://github.com/skymanbp/autoshade/blob/main/src/render/'
                    'tone.rs'),
    fritsch_carlson='Fritsch & Carlson, SIAM J. Numer. Anal. 17(2), 1980',
)


def linear_gain(ev: float) -> float:
    """E1：+1 档 = 线性域 ×2。**精确关系**。"""
    return 2.0 ** float(ev)


def stops_for_delta_L(dL: float, slope_L_per_ev: float) -> float:
    """把 ΔL* 换成档数（**旧接口**）。

    ⚠ 2026-10-30：不要再用它做生产换算 —— 斜率随 L* 变，跨档会错 5~15%。
    用 `photometry.ev_for_l(L0, L1)`（Y 比精确反解）。
    """
    if abs(slope_L_per_ev) < 1e-6:
        raise ValueError('slope 不能为 0；中间调与高光端斜率不同，必须分别给')
    return float(dL) / float(slope_L_per_ev)


def clamp_slider(v: float, lo: float = -HW_LIMIT, hi: float = HW_LIMIT) -> float:
    return max(lo, min(hi, float(v)))


def hsl_hue_from_deg(deg: float) -> float:
    """H3：把"要转多少度"换成 HSL 色相滑杆值（约定，非文档）。"""
    return clamp_slider(deg * 100.0 / HSL_HUE_DEG_PER_100)


def src_hue_rad(deg: float) -> float:
    """E5：色相角（度）→ 点颜色 SrcHue（弧度，0~2π，夹到 5.95）。"""
    r = (float(deg) % 360.0) * 3.141592653589793 / 180.0
    return min(SRC_HUE_MAX, max(0.0, r))


def describe_limits() -> str:
    """把"哪些能算、哪些不能算"一次说清（给人和给代码看同一份）。"""
    lines = [
        '能算（有出处）：',
        '  曝光      +1 档 = 线性 ×2                [Adobe LR 帮助]',
        '  亮度      L*↔Y↔EV↔sRGB 码              [CIELAB / IEC 61966-2-1]',
        '  曲线      横轴是显示伽马码；写点集置 ToneCurveName2012=Custom',
        '  点颜色    19 数字 = 3 标量 + 16 窗口数；必须配 ColorVariance；SrcHue 0~6 弧度',
        '  局部刻度  LocalExposure2012=EV/4；其余 UI/100；LocalHue=度/180',
        '',
        '不能算（无公开闭式）：',
        '  Highlights / Shadows / Whites / Blacks / Contrast  ⇒ 方向 + 一档 + ③ 验收',
        '  Saturation / Vibrance                             ⇒ Saturation 相对刻度；Vibrance 只给方向',
        '  HSL 的 ±100 映射 / PointColor 幅度                  ⇒ 约定或台账，不进常数表',
    ]
    return '\n'.join(lines)


def source_table() -> Optional[str]:
    """把出处压缩成可贴进报告的表（Markdown）。"""
    return '\n'.join('- `%s` — %s' % (k, v) for k, v in SOURCES.items())
