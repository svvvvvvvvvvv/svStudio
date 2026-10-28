# -*- coding: utf-8 -*-
"""LR 侧求解器：**七步，一次算一次验，不闭环**。

    ① 定"看哪里"（选择：脸 / 人 / 整幅）
    ② 曝光 Exposure      → 脸进合理区（T4）
    ③ 高光 Highlights    → 不爆白（T1）
    ④ 曲线              → **只修不变量 + 落地意图**（不是去对别人的分位）
    ⑤ 全局饱和           → 只在整幅明显偏灰时
    ⑥ 点颜色            → 肤色（T5 + 意图）
    ⑦ 验收三门

设计出处：分工设计 第六部分 §31-§36 + 第三部分（没有"该到多少"）。

★ 曲线的语义（2026-10-29 改）：
  旧：拿"某一批成片的分位"当靶，造一条曲线把它对上。
  新：**没有靶**。曲线只做两件事——
      (a) 修不变量：爆白就压低端、堵黑就抬趾部
      (b) 落地意图：更亮/更暗 ⇒ 小幅整体位移
  且每锚点位移 ≤3 L*（S3）。**不做形状匹配。**

本文件只算值、只判定，**不直接驱动 LR**（驱动在 lrsink.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .curve import plan_curve
from .invariants import T1, T2
from .intent import Intent

# ---------------------------------------------------------------- 常数与出处
#
# ★ 中间调斜率：**能推**，不用猜
#   CIELAB: L* = 116·(Y/Yn)^(1/3) − 16 ；曝光是线性光倍数 Y ∝ 2^EV
#   ⇒ dL*/dEV = (116/3)·ln2·(Y/Yn)^(1/3) = 0.2310·(L*+16)
#   验证：L*=50 -> 15.2 ｜ 70 -> 19.9 ｜ 90 -> 24.5
#   （原来用的 30 偏高 1.5 倍，而且与 L* 无关 —— 已替换）
LAB_SLOPE_K = 0.2310
#
# ★ 高光端斜率：理论值不适用（印相肩部压缩），只能实测。实测 8~16 L*/EV。
#   见 flow.render_with_headroom 的注释与 DSCF1289 那组数据。
SLOPE_HIGH_MIN, SLOPE_HIGH_MAX = 8.0, 16.0
#
# ★ 点颜色：**每一次推满（±1）能补多少比例**——这是**政策**，不是物理常数。
#   原因：Point Color 的 SatScale 没有公开传递函数（见 lrmodel.py）。
#   政策含义：缺口 ≤ 这个比例时，一次推满；缺口更大时分多次/报欠账。
PC_FULL_CLOSES = 0.35
#
# ★ 意图：±1 意图 = ±0.5 档。这是**接口定义**（"+1 是什么意思"），不是物理断言。
INTENT_EV_PER_UNIT = 0.5


def eval_slope(L: float) -> float:
    """中间调 dL*/dEV（CIELAB 立方根律，L 依赖）。"""
    return LAB_SLOPE_K * (max(0.0, float(L)) + 16.0)


def ev_for_dL(dL: float, L: float) -> float:
    """把"要改多少 L*"换成档数（在给定 L* 处取局部斜率）。"""
    s = eval_slope(L)
    return float(dL) / s if s > 1e-9 else 0.0



@dataclass
class LRPlan:
    """一张图的 LR 参数（单位就是 LR 面板上的数）。"""
    exposure_ev: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    saturation: float = 0.0
    vibrance: float = 0.0
    curve: Optional[List[Tuple[int, int]]] = None
    point_color: Optional[Dict[str, float]] = None
    point_color_rgb: Optional[list] = None      # ②处取样色（过完曲线后），造 19 数字要用它
    notes: Dict[str, str] = field(default_factory=dict)

    def as_xmp_scalars(self) -> Dict[str, float]:
        return dict(Exposure2012=self.exposure_ev, Highlights2012=self.highlights,
                    Shadows2012=self.shadows, Whites2012=self.whites,
                    Blacks2012=self.blacks, Saturation=self.saturation,
                    Vibrance=self.vibrance)


# ---------------------------------------------------------------- ① 看哪里

def pick_lookat(rec_engine: dict) -> str:
    """有脸→face，没脸但有人→person，都没有→matrix（H2 降级链）。"""
    skin = rec_engine.get('skin')
    fw = rec_engine.get('face_w_native') or 0
    if skin and fw >= 40:
        return 'face'
    if (rec_engine.get('env_share') or 0) < 0.95:
        return 'person'
    return 'matrix'


# ---------------------------------------------------------------- ② 曝光

def solve_exposure(rec_engine: dict, target_L: float = 72.0,
                   L_lo: float = 55.0, L_hi: float = 85.0,
                   intent: Optional[Intent] = None) -> float:
    """一次算到位。`target_L` 只是合理区里的一个落点，不是某个靶。"""
    lookat = pick_lookat(rec_engine)
    if lookat == 'face':
        cur = (rec_engine.get('skin') or {}).get('L')
    elif lookat == 'person':
        cur = None
    else:
        cur = (rec_engine.get('img') or {}).get('p50')
    if cur is None:
        return 0.0
    tgt = min(max(target_L, L_lo), L_hi)
    ev = ev_for_dL(tgt - cur, cur)          # 斜率随 L* 变（CIELAB）
    if intent is not None:
        # 意图只改**曝光**，且不许把脸推出合理区（S4）
        ev += getattr(intent, 'brighter', 0.0) * INTENT_EV_PER_UNIT
        newL = cur + ev * eval_slope(cur)
        if newL > L_hi:
            ev -= ev_for_dL(newL - L_hi, cur)
        elif newL < L_lo:
            ev += ev_for_dL(L_lo - newL, cur)
    return ev


# ---------------------------------------------------------------- ③ 收白

def solve_highlights(rec_after_ev: dict, clip_max: Optional[float] = None):
    """高光收白：**没有公式可算**，所以只给"政策性"一档，并把欠账记下来。

    为什么删掉原来的公式（2026-10-29 自审）：
      原来是 `-(over-1)*40`，纯拍脑袋；而 `Highlights` 在 Adobe 文档里
      **没有公开传递函数**（见 lrmodel.py 的"没有公开闭式"一节）。
      给一个编出来的斜率比不给更糟 —— 它会让人以为这是算出来的。

    现在只做两件事：
      1. 超标就报**欠账**（这活本该由引擎余量 T0 解决，不是 LR 补）
      2. 需要先兜一下就用 HIGHLIGHT_POLICY 这一档固定值，标成 policy

    返回 (value, note)；value=0.0 表示不动。
    """
    cm = T1['clip_max'] if clip_max is None else clip_max
    img = rec_after_ev.get('img') or {}
    clip = img.get('clip_pct') or 0.0
    if clip <= cm:
        return 0.0, None
    note = ('clip=%.2f%% > %.2f%% => 引擎欠账（T0 没留够余量）；'
            'LR 这里给的是政策值 %+.0f，不是算出来的'
            % (clip, cm, HIGHLIGHT_POLICY))
    return float(HIGHLIGHT_POLICY), note


# 政策档：不是公式。等实测标定后替换（当前只保证方向：压）
HIGHLIGHT_POLICY = -35.0

# ---------------------------------------------------------------- ④ 曲线

def solve_curve(rec_engine: dict, intent: Optional[Intent] = None,
                budget: float = 3.0) -> Tuple[Optional[List[Tuple[int, int]]], dict]:
    """**只修不变量 + 落地意图**，不对任何"别人的分位"造形。

    返回 (16 点曲线 | None, 说明)。None = 不需要曲线或预算内无解。
    """
    img = rec_engine.get('img') or {}
    q = {str(k): img.get('p%d' % k) for k in (5, 25, 50, 75, 95)}
    if not all(isinstance(v, (int, float)) for v in q.values()):
        return None, {'why': '缺分位'}
    _ = intent          # 明确：曲线不看意图
    src = dict(q)
    dst = dict(q)

    # ★ 意图**不由曲线落地**（2026-10-29 修正：曝光与曲线都加一次 = 意图被算两次）。
    #   亮暗意图归曝光滑杆；曲线只修不变量。

    # (a) 不变量：爆白压低端、堵黑抬趾部
    clip = img.get('clip_pct') or 0.0
    fixed, unmet = [], {}
    if clip > T1['clip_max']:
        want = clip - T1['clip_max']            # 想压多少（L*）
        d = min(budget, want)
        dst['95'] -= d
        dst['75'] -= d * 0.5
        fixed.append('压白端 %.2f' % d)
        if want > d + 1e-9:
            unmet['白端'] = round(want - d, 2)
    p1 = img.get('p1') or 0.0
    if p1 < T2['p1_min']:
        want = T2['p1_min'] - p1
        d = min(budget, want)
        dst['5'] += d
        dst['25'] += d * 0.5
        fixed.append('抬黑端 %.2f' % d)
        if want > d + 1e-9:
            unmet['黑端'] = round(want - d, 2)

    if src == dst:
        return None, {'why': '无需修正（不变量都过、意图为空）'}
    pts, info = plan_curve(src, dst, budget=budget)
    info['fixed'] = fixed
    if unmet:
        # 曲线上限 3 L* 修不了的量 ⇒ 这不是曲线的活（S3/S5：回引擎要余量）
        info['unmet'] = unmet
    return pts, info


# ---------------------------------------------------------------- ⑤ 饱和

def solve_saturation(rec_engine: dict, intent: Optional[Intent] = None) -> float:
    """只在"整幅明显偏灰"或意图要浓淡时动；幅度小，且**不许用来补肤色**。"""
    d = getattr(intent, 'denser', 0.0) if intent else 0.0
    return max(-15.0, min(15.0, d * 15.0))


# ---------------------------------------------------------------- ⑥ 点颜色

def solve_point_color(rec: dict, intent: Optional[Intent] = None,
                      C_min: float = 10.0) -> Optional[Dict[str, float]]:
    """肤色剂量（批次一套）。**新体系里没有"肤色靶"**，所以只做两件事：

      (a) 不变量：肤色 C 低于 C_min（发灰）⇒ 推一点
      (b) 意图：更浓/更淡
    色相（shift）默认不动 —— 没有靶就没有"该转多少度"。
    """
    skin = rec.get('skin') or {}
    C = skin.get('C')
    if C is None:
        return None
    scale = 0.0
    if C < C_min:
        # 缺口比例 ÷ "推满能补多少"（**政策**，非物理）；夹 ±1
        gap = C_min / max(C, 1e-6) - 1.0
        scale = gap / PC_FULL_CLOSES
    d = getattr(intent, 'denser', 0.0) if intent else 0.0
    scale += d * 0.5                       # 意图只加一半幅度，另一半交给整体饱和（政策）
    return dict(scale=max(-1.0, min(1.0, scale)), shift=0.0)


# ---------------------------------------------------------------- 主流程

def solve_one(rec_engine: dict, rec_after_ev: Optional[dict] = None,
              rec_final: Optional[dict] = None, intent: Optional[Intent] = None,
              log=print) -> LRPlan:
    """单图的 LR 计划。缺的中间态留空，等测量接上再填。"""
    plan = LRPlan()
    plan.notes['lookat'] = pick_lookat(rec_engine)
    plan.exposure_ev = round(solve_exposure(rec_engine, intent=intent), 3)

    base = rec_after_ev or rec_engine
    hv, hnote = solve_highlights(base)
    plan.highlights = round(hv, 1)
    if hnote:
        plan.notes['highlight_debt'] = hnote
    pts, info = solve_curve(rec_engine, intent=intent)
    plan.curve = pts
    plan.notes['curve'] = ('无需' if pts is None else
                           '修正 %s ｜ 最大位移 %.2f' % (info.get('fixed') or '意图',
                                                       info.get('max_abs_delta') or 0))
    plan.saturation = round(solve_saturation(rec_engine, intent), 1)

    pc = solve_point_color(rec_final or rec_engine, intent=intent)
    if pc and (abs(pc['scale']) > 1e-6 or abs(pc['shift']) > 1e-6):
        plan.point_color = pc
        # 取样色要取**过完曲线之后**的（点颜色作用在 ② 上）
        rgb = ((rec_final or rec_engine).get('skin') or {}).get('rgb')
        if rgb:
            from .curve import apply_curve_rgb
            plan.point_color_rgb = list(apply_curve_rgb(rgb, plan.curve or []))
            plan.notes['point_color_rgb_src'] = '②处（①的取样色过曲线 LUT）'
    return plan
