# -*- coding: utf-8 -*-
"""曝光之后那一态（① → ①'）：**纯算，不渲、不驱动 LR**。

为什么需要这一层（设计 §35 数据接口 + 自审 P1-2）：
    LR 的"收白"要判的是「**提完曝光之后**会不会爆」，
    而原代码拿的是 **① 的 clip** —— 顺序倒了（先收白、后算曝光）。
    这里把 ①' 能算的部分算出来，让 H3（白端保护）/ L2（提脸要有白端预算）有依据。

**能算的**（每条都有出处）：
  · 分位与脸 L 的平移：CIELAB 立方根律 `dL*/dEV = 0.2310·(L*+16)`（可推，不是拟合）
  · 色相 h **不变**：线性增益让 a/b 同比缩放 ⇒ 角度不动
    （与 §10.2「曲线不伤色相」同一套推理：等比缩放不转角）
  · 爆白的**下界**：若 `p95' ≥ 100` ⇒ 至少 5% 像素到顶；`p99' ≥ 100` ⇒ 至少 1%
    （这是不等式，不是模型：分位点被推出 100 就一定有那么多的像素到顶）
  · 预算：引擎 `render_safe` 已经算好 `headroom_ev`（"还能抬几档不剪"）
    ⇒ `ev ≤ headroom` 不新增剪裁；`ev − headroom` = **超预算几档**，必须记欠账

**算不了的（不许编）**：
  · 提完之后"爆白占比到底是多少 %" —— 需要上尾直方图，现在测量只给 p99 + clip_pct
    ⇒ 只报下界与超预算档数，不报假百分比
  · Highlights 压多少能把 p95 拉回 —— 无公开传递函数（见 `lrmodel.py`）

用法：
    from design.expose import after_ev, eval_slope, ev_for_dL
    p = after_ev(rec_engine, ev=0.45)
    p['pred']['img']['p95'], p['over_ev'], p['clip_lb']
"""
from __future__ import annotations

from typing import Dict, Optional

# ---------------------------------------------------------------- 可推的斜率
#
# CIELAB: L* = 116·(Y/Yn)^(1/3) − 16 ；曝光是线性光倍数 Y ∝ 2^EV
# ⇒ dL*/dEV = (116/3)·ln2·(Y/Yn)^(1/3) = 0.2310·(L*+16)
# 验证：L*=50 → 15.2 ｜ 70 → 19.9 ｜ 90 → 24.5
# （旧值 30 与 L* 无关，偏高 1.5 倍 —— 已替换）
LAB_SLOPE_K = 0.2310


def eval_slope(L: float) -> float:
    """中间调 dL*/dEV（CIELAB 立方根律，L 依赖）。"""
    return LAB_SLOPE_K * (max(0.0, float(L)) + 16.0)


def ev_for_dL(dL: float, L: float) -> float:
    """要改 dL* 需要几档曝光（在 L 处的局部斜率）。"""
    s = eval_slope(L)
    return float(dL) / s if s > 1e-9 else 0.0


# ---------------------------------------------------------------- 曝光后的态

QUANTILES = (1, 5, 25, 50, 75, 95, 99)


def _shift_L(L: Optional[float], ev: float) -> Optional[float]:
    """线性光增益 2^ev 对某个 L* 的影响（局部斜率，一次算不迭代）。"""
    if L is None:
        return None
    return float(min(100.0, L + ev * eval_slope(L)))


def after_ev(rec: dict, ev: float, headroom: Optional[float] = None) -> Dict:
    """预测 ① 加 `ev` 档之后的态。返回 dict：

        pred  预测的分位 / 脸 L（img、skin）
        over_ev   超预算几档（0 = 在引擎给的余量内）
        in_budget 是否在预算内
        clip_lb   爆白**下界**（由分位是否被推到 100 得到；None = 分位没被推满，下界就是 ① 的现值）
        white_over_L  上端**越过白平台多少 L\\***（未截断；0 = 没过）
        notes     人话说明（写进 LRPlan.notes，不许静默）

    只算"线性平移"这一层；高光肩部的压缩不在这一层（那是纸/曲线的事）。
    """
    img = rec.get('img') or {}
    skin = rec.get('skin') or {}
    hr = rec.get('headroom_ev') if headroom is None else headroom

    pred_img = {('p%d' % k): _shift_L(img.get('p%d' % k), ev) for k in QUANTILES}
    pred_img['contrast'] = (None if (pred_img.get('p95') is None or pred_img.get('p5') is None)
                            else pred_img['p95'] - pred_img['p5'])
    pred_skin = dict(L=_shift_L(skin.get('L'), ev), C=skin.get('C'), h=skin.get('h'))

    notes = []
    # 爆白下界：分位被推出 100 ⇒ 至少那么多像素到顶（不等式，不是模型）
    clip0 = img.get('clip_pct')
    lb, why = clip0, []
    for q, at_least in ((99, 1.0), (95, 5.0)):
        v = pred_img.get('p%d' % q)
        if v is not None and v >= 100.0:
            why.append('p%d 被推到 100 ⇒ 至少 %.0f%% 像素到顶' % (q, at_least))
            lb = max(lb or 0.0, at_least)
    if lb is None:
        lb = 0.0
    if why:
        notes.append('；'.join(why) + '（这是下界，不是估算）')

    # 白端**过了多少**（不夹到 100）：`_shift_L` 把上端夹在 100，
    # 一夹就丢掉"过了多远" ⇒ ③ 没法判断该压多少。这里按未截断的线性平移算。
    over_L = 0.0
    for q in (99, 95):
        L0 = img.get('p%d' % q)
        if L0 is None:
            continue
        over_L = max(over_L, float(L0) + ev * eval_slope(L0) - 100.0)
    over_L = max(0.0, over_L)

    # 预算：引擎已经给了"还能抬几档"
    over = 0.0
    in_budget = True
    if hr is not None and ev > float(hr) + 1e-9:
        over = float(ev) - float(hr)
        in_budget = False
        notes.append('曝光超引擎余量 %.2f 档（余量 %.2f，要 %.2f）'
                     '⇒ 必须由 Highlights 收白兜住，③ 出片后验 clip' % (over, float(hr), ev))

    return dict(ev=float(ev), pred=dict(img=pred_img, skin=pred_skin),
                headroom=None if hr is None else float(hr),
                over_ev=round(over, 3), in_budget=in_budget,
                clip_lb=round(float(lb), 3), clip0=round(float(clip0 or 0.0), 3),
                white_over_L=round(over_L, 3), notes=notes)


def headroom_note(headroom: Optional[float]) -> str:
    """余量的一句话（给报告用）。"""
    if headroom is None:
        return '引擎没报余量（headroom 缺）⇒ 曝光不受预算约束，只能事后验'
    return '引擎余量 %.2f 档（这是"还能抬几档不剪"，是产出不是目标）' % float(headroom)
