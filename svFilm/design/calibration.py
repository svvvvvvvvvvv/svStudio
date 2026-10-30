# -*- coding: utf-8 -*-
"""LR 旋钮的**单位与灵敏度**；能查到的用定义，查不到的降级成"台账观察"。

2026-10-30 重构（起因：LR 侧常数审计）
-------------------------------------
旧版把一批"每单位抬多少 L\\*"写成常数表，其中多条的样本只有 n=1~2，
而且**量纲就不对**（同一个滑杆在 L*=30 与 L*=70 处效率不同，一个常数必然错）。
按纪律（复核 2：n<5 只许进台账；§3：查不到公开闭式就不许编斜率），现在分三类：

A. **定义/单位换算**（精确，不是标定）
   · `LocalExposure2012` 文件值 = EV/4      —— 4 档 = 满刻度 1.0
   · 局部族其余滑杆文件值 = UI/100           —— LR 的局部 XMP 约定
   出处：autoshade `docs/TECH_STACK.md`「XMP and Lightroom interoperability」
   （对真实 LR 导出实测）＋我们自己的 `_calib_fill` 三档对拍。
B. **标准定义式**（可算，不是拟合）
   · 亮度 ↔ 曝光 ↔ 码值：`photometry`（CIELAB / sRGB）
   · 没有公开传递函数的滑杆：**方向 + 一档 + ③ 验**（不建模）
C. **台账观察**（n<5，只用于预测/记账，**不许当"正确值"**）
   · 局部 fill 文件 0.25（1 档）在两张图上实测 ≈ 1.06~1.16 EV 的效果
   · LocalShadows 满档在 DSCF1307 上脸 +7.1 L*、画面 p99 只 +0.1
   · Saturation +30 在 C*=20 的肤色上 +6.63 C*（相对刻度会预测 +6.0）
   · SplitToning 阴影轮 S=20/40/60 → 背景 b50 −0.198/−0.182/−0.173 b*/点
这些数保留在下面的 `LEDGER` 里供对拍，但**不再进入剂量公式**。

单位
----
· 全局 `Saturation` / `Vibrance`：UI 刻度 −100..100
· 局部 `LocalShadows2012` 等：写 XMP 时 = UI/100
· `LocalExposure2012`：写 XMP 时 = EV/4
"""
from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------- A. 单位换算

LOCAL_EXPOSURE_PER_EV = 1.0 / 4.0      # 文件值 = EV/4（4 档 = 1.0）
LOCAL_UI_SCALE = 1.0 / 100.0           # 局部其余滑杆：文件值 = UI/100

# 幅度上限是**政策**（一次最多补多少），不是物理：
SAT_LIMIT = 30.0
SHADOW_LIMIT = 60.0


def sat_for_dc(dc: float, C0: Optional[float] = None) -> float:
    """要把肤色 C\\* 挪 `dc` 需要多少全局 Saturation 单位。

    ★ 为什么不再用 `0.221 C*/单位`：Saturation 是**相对**刻度（百分比），
    ΔC 与当前 C\\* 成正比。实测（n=1，C*=20，S=30）：20.00 → 26.63，
    相对增益 1.3315 ⇒ 约 1.105×“1% 刻度”。旧常数 0.221 只在 C*≈20 附近成立；
    C*=10 时会把剂量放大一倍。
    这里取**公开语义**：ΔC = C0 · S/100（S 以 UI 计），实测的 +10% 偏差
    记进台账（n=1，等 n≥5 再决定要不要 κ）。

    `C0` 缺省 20.0 只为兼容旧调用；新代码必须给。
    """
    if not dc:
        return 0.0
    c0 = 20.0 if not C0 or float(C0) <= 1e-6 else float(C0)
    v = 100.0 * float(dc) / c0
    return max(-SAT_LIMIT, min(SAT_LIMIT, v))


# ---------------------------------------------------------------- C. 台账（n<5）

LEDGER: Dict[str, dict] = {
    'local_fill_ev': dict(
        value=1.11, unit='倍（相对 1.00 EV）', n=2, where='_calib_fill（1231/1665）',
        note='文件 0.25 的效果约 1.06~1.16 EV；只作预测旁证，不进入剂量'),
    'local_shadows_face': dict(
        value=7.1, unit='L*/满档 1.0', n=1, where='_calib_sh2（DSCF1307）',
        note='脸 +7.1；画面 p99 只 +0.1。n=1，只说明"对白端很轻"'),
    'local_shadows_median': dict(
        value=14.7, unit='L*/满档 1.0', n=1, where='_calib（DSCF1231）',
        note='人的中位 +5.88 @0.40；与 face 的 7.1 不是同一个量，不可混用'),
    'sat_relative': dict(
        value=1.105, unit='实测增益 / 相对刻度预测', n=1, where='_calib（sat30）',
        note='C 20.00→26.63；相对刻度预测 26.0；差 +2.4%'),
    'split_shadow_b': dict(
        value=0.18, unit='b*/点（阴影轮，背景 b50）', n=1, where='_calib3（DSCF1608）',
        note='S=20/40/60 → −0.198/−0.182/−0.173；递减，线性只在低档成立'),
    'split_shadow_face_leak': dict(
        value=0.13, unit='b*/点（脸 b*）', n=1, where='_calib3',
        note='阴影轮会把脸的暗侧一起染冷 ⇒ 必须配主体保护'),
    'blacks_p1': dict(
        value=0.15, unit='L*/点（−10 附近）', n=1, where='_calib3',
        note='−10/−20/−30 → 0.150/0.115/0.103，递减；只作"大位移会钝"的旁证'),
    'curve_dark': dict(
        value=0.30, unit='L*/码（x≈32）', n=1, where='_calib3',
        note='曲线下压 6/12/18 → 0.36/0.31/0.28；只作局部灵敏度'),
}

# 局部 fill 的 p99 响应（台账）：两张图 11.2 / 1.3 L*/档 —— 第二张已撞 100。
# 它说明"p99 的响应取决于是否饱和"，不能写成一个传递常数；白端预测改用精确式。
LOCAL_FILL_P99_LEDGER = (11.2, 1.3)


def describe() -> str:
    """口径一句话（报告里引用）。"""
    return ('LR 常数只有两类：单位换算（EV/4、UI/100）· 标准定义式（photometry）；'
            '没有公开传递函数的旋钮一律方向 + 一档步进')
