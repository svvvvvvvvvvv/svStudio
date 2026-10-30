# -*- coding: utf-8 -*-
"""一次算（§52 / §61）：把"可算的三根"做成纯函数，不碰 LR、不读文件。

三根 = ① 全局曝光 · ② 曲线位移 · ③ 局部曝光（主体蒙版）。
它们都有闭式，所以"一次算"才成立；半可算的四根（白/黑/高光/阴影）**不在这里**，
只在出片后撞界时做最小修正（§61.2）。

★ 为什么要专门的模块：这段算法以前散在 `lr.solve_one` 里，和 LR 桥、xmp、验收搅在一起，
  一改就动全身，于是只能"跑一轮看给多少"（那正是要废的做法）。抽成纯函数之后：
  输入是测量 + 卡的带，输出是参数 + 账，可以单测、可以对拍、可以不渲。

单位：
  L*  —— CIELAB D65
  EV  —— 档（1 EV = 光量 ×2）
  LR 的 `LocalExposure2012` 刻度 = EV ÷ 4
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Band = Tuple[float, float]

# ★ 2026-10-30：换算统一到 `photometry`（CIELAB 定义 + sRGB 定义）。
#   这里保留同名导出，老调用点不用改；实现与 `design.expose` 同一份。
from .photometry import ev_for_l, slope_l_per_ev

_CUBE = 1.0 / 3.0


def eval_slope(L: float) -> float:
    """dL*/dEV 的导数（随起点变，**不许当常数用**）。跨档请用精确式。"""
    return slope_l_per_ev(float(L))


def ev_to_reach(cur: float, band: Band) -> float:
    """把 L* 从 `cur` 推到 `band` 里最近的那个边（已经在带内 → 0）。

    精确反解（Cube-root 段等价于 `3·log2((Lt+16)/(L0+16))`；线性段也覆盖）：
    旧注释里那条 3·log2 只在立方根段成立，L*<8 时要用 κ 段。
    """
    lo, hi = float(band[0]), float(band[1])
    if lo > hi:
        lo, hi = hi, lo
    if cur < lo:
        target = lo
    elif cur > hi:
        target = hi
    else:
        return 0.0
    if target <= 0.0:
        return -math.inf
    return ev_for_l(float(cur), float(target))


def ev_to_reach_linear(cur: float, band: Band) -> float:
    """线性近似（留着对拍用；**生产不用它**）。"""
    lo, hi = float(min(band)), float(max(band))
    target = lo if cur < lo else (hi if cur > hi else cur)
    return (target - cur) / max(eval_slope(cur), 1e-6)


def local_exposure_for(ev: float) -> float:
    """EV → LR 的 `LocalExposure2012`（**文件值 = EV/4**）。

    2026-10-30 把这个 /4 的出处补齐（原来只写"查不到、待定"）：
    · LR 局部「曝光」滑杆的量程是 ±4 档（UI），XMP 的局部族按 UI/100 存；
    · 外部实测（autoshade `docs/TECH_STACK.md`「XMP and Lightroom
      interoperability」）明确：`LocalExposure2012` 文件值 = EV/4，导入端 ×4；
    · 我们自己的 `_calib_fill` 两图三档也吻合：文件 0.25 的实际效果约 1.06~1.16 EV。
    ⇒ 4.0 是**单位换算**（4 档 = 满刻度 1.0），不是拟合系数。
    """
    from .calibration import LOCAL_EXPOSURE_PER_EV
    return float(ev) * LOCAL_EXPOSURE_PER_EV


def local_ev_to_reach(cur: float, band: Band) -> float:
    """局部曝光要多少档（同一套 CIELAB 反解，只是作用在人身上）。"""
    return ev_to_reach(cur, band)


# ---------------------------------------------------------------- 账

@dataclass
class Entry:
    """一笔账（§61.5）。**一条一笔**，归到具体一层，带建议动作。"""
    dim: str
    got: Optional[float]
    want: Optional[Band]
    gap: float
    layer: str
    action: str = 'none'

    def __str__(self) -> str:
        w = '' if self.want is None else '[%.1f, %.1f]' % self.want
        g = '—' if self.got is None else '%.1f' % self.got
        return '%-14s 得 %-6s 要 %-14s 差 %+5.1f  ← %s%s' % (
            self.dim, g, w, self.gap, self.layer,
            '' if self.action == 'none' else '  ⇒ ' + self.action)


def _gap(cur: float, band: Band) -> float:
    lo, hi = min(band), max(band)
    if cur < lo:
        return cur - lo
    if cur > hi:
        return cur - hi
    return 0.0


def curve_shifts(shape: Dict[str, float], want: Dict[str, Band],
                 budget: float = 3.0) -> Tuple[Dict[str, float], List[Entry]]:
    """② 曲线：按形状的四项（toe/shoulder/contrast_win/skew）算锚点位移。

    ★ 只修**形状**，不动 p50（S3 的兄弟条款：曝光管落点，曲线管形状）。
    ★ 每项位移 ≤ `budget` L*；超了就截断并记账（不许加宽预算 —— S5）。
    """
    out: Dict[str, float] = {}
    led: List[Entry] = []
    for dim, band in want.items():
        cur = shape.get(dim)
        if cur is None:
            continue
        g = _gap(float(cur), band)
        if abs(g) < 0.5:
            continue
        shift = max(-budget, min(budget, -g))
        out[dim] = round(shift, 2)
        if abs(g) > budget:
            led.append(Entry(dim='shape.' + dim, got=float(cur), want=band,
                             gap=g, layer='curve',
                             action='撞曲线预算（%.0f L*），只修得动 %.1f' % (budget, shift)))
    if 'skew' in out:                     # skew 是比值，不该按 L* 位移
        out.pop('skew')
    return out, led


# ---------------------------------------------------------------- 一次算

@dataclass
class Plan:
    exposure_ev: float = 0.0
    curve: Dict[str, float] = field(default_factory=dict)
    local_ev: float = 0.0
    local_exposure: float = 0.0
    local_shadows: float = 0.0
    local_highlights: float = 0.0
    ledger: List[Entry] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d['ledger'] = [str(e) for e in self.ledger]
        return d


def solve_pass(meas: Dict[str, object], card: Dict[str, object]) -> Plan:
    """一次算：曝光（整幅）→ 曲线（形状）→ 局部（人）。**不循环、不读 ③**。

    `meas`：本批的测量（§62.1 的逐图量取中位）
        p50 · p95 · p5 · face_L · person_L · person_p95 · shape{toe,shoulder,contrast_win,skew}
    `card`：匹配卡给的可行域（§62.2），值是 (lo, hi)
        p50 · p95 · p5 · face_L · person_med · shape{...}
    """
    plan = Plan()
    p50 = meas.get('p50')
    if p50 is not None and card.get('p50'):
        plan.exposure_ev = round(ev_to_reach(float(p50), card['p50']), 3)   # type: ignore[arg-type]
        g = _gap(float(p50), card['p50'])                                   # type: ignore[arg-type]
        if abs(g) >= 0.5:
            plan.ledger.append(Entry('p50', float(p50), card['p50'], g, 'exposure'))

    plan.curve, led = curve_shifts(dict(meas.get('shape') or {}),
                                   dict(card.get('shape') or {}))
    plan.ledger += led

    person = meas.get('person_L')
    if person is not None and card.get('person_med'):
        plan.local_ev = round(local_ev_to_reach(float(person), card['person_med']), 3)  # type: ignore[arg-type]
        plan.local_exposure = round(local_exposure_for(plan.local_ev), 5)
        g = _gap(float(person), card['person_med'])                                       # type: ignore[arg-type]
        if abs(g) >= 0.5:
            plan.ledger.append(Entry('person_med', float(person), card['person_med'], g, 'local'))

    # 脸进不了区：归 ⑦（局部），不是 ② 曝光 —— 这条是 §38 的边界
    face = meas.get('face_L')
    if face is not None and card.get('face_L'):
        g = _gap(float(face), card['face_L'])                                             # type: ignore[arg-type]
        if abs(g) >= 0.5:
            plan.ledger.append(Entry('face_L', float(face), card['face_L'], g, 'local',
                                     action='脸的账在 ⑦；② 不许为脸动'))
    return plan
