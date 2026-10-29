# -*- coding: utf-8 -*-
# ⚠⚠ 本模块属于**旧的两层卡体系（已废）**。设计真源 §48：只有一种卡（匹配卡）。
#   · 新代码请用 `design/matchcard.py`（卡 = 介质 + 画像 + 分面标签 + 样片），不要从 cards.py 取卡
#   · 本模块暂时保留只为让现有 `flow.choose` 还能跑；flow 改用 matchcard 之后**直接删掉**
#   · 别在这里加东西、别在这里修 bug —— 修了也是给要删的东西修
#

"""画像 -> 配方可行性：参照卡给的缺口，**逐个问"有没有旋钮关得掉"**。

为什么要有这一层（2026-10-29）：
  参照卡只有**画像**（那套长什么样），没有**配方**（怎么渲出来）。
  所以匹配之后必须回答：我们手上这张生产卡，**能不能做到那个像**？
  不能的话，缺的是"量程"还是"介质"？

每个旋钮的**可达量**都带出处（能实测的写实测，不能算的写"无公开闭式"）：

  杠杆                 作用于   每单位           量程              出处
  ─────────────────────────────────────────────────────────────────────
  couplers.amount      C        +2.3 C          0 ~ 2.0（作者给）  旧实测
                       h        +19.9           符号随语料变       旧实测
  print γ_blue         C        +14 C           0.25 ~ 4.0         旧实测
  print γ（色相配比）   h        ≈145 / 单位     0.25 ~ 4.0         旧实测（符号须实测）
  LR 曝光              L        0.231·(L+16)/EV —                   CIELAB 可推
                       └ 受限：不能超过引擎报的 headroom_ev
  LR 点颜色            C        ×2.2（两票）    —                  本机实测上限
                       h        ±30°（±1）       —                  约定，非文档
  LR 曲线              各锚点   ±3 L*           —                   S3 预算
  换卷 / 换纸          形状     无界            —                   手册总账：形状只能换卷纸

用法：
    from design.feasibility import gap_table, verdict
    rows = gap_table(batch_prof, ref.profile, recs)
    print(verdict(rows))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .lr import eval_slope

# ---- 可达量（带出处；改这里就要改注释） ----
AMOUNT_C_PER_UNIT = 2.3          # 旧实测
AMOUNT_RANGE = (0.0, 2.0)        # 作者给 0~2.0
AMOUNT_H_PER_UNIT = 19.9         # 旧实测（符号随语料变，不可靠）
GBLUE_C_PER_UNIT = 14.0          # 旧实测
GAMMA_RANGE = (0.25, 4.0)        # GUI 量程
GAMMA_H_PER_UNIT = 145.0         # 旧实测（色相配比）
PC_C_MAX_RATIO = 2.2             # 本机实测上限（两票叠加）
PC_H_MAX_DEG = 30.0              # 约定
CURVE_MAX_L = 3.0                # S3 预算


@dataclass
class Row:
    dim: str
    gap: float                 # 我们要改多少（target − current）
    levers: List[tuple] = field(default_factory=list)   # [(名字, 需要的量, 可达?, 说明)]
    reachable: bool = False
    needs: str = ''            # 关不掉时：缺什么

    def __str__(self):
        ls = ' ｜ '.join('%s%s' % (n, '✓' if ok else '✗') for n, _, ok, _ in self.levers)
        return '  %-4s 缺口 %+7.1f ｜ %-14s ｜ %s%s' % (
            self.dim, self.gap, ls, '可达' if self.reachable else '**不可达**',
            ('  缺：' + self.needs) if self.needs else '')


def _amount_room():
    """amount 还能往上走多少单位（按保守:当前视为 1.2）。"""
    return max(0.0, AMOUNT_RANGE[1] - 1.2)


def gap_table(batch: Dict[str, float], target: Dict[str, float],
              headroom_ev: Optional[float] = None) -> List[Row]:
    """逐维算缺口与可达性。batch/target 都是 profile 字典（L/C/h/p50/p95…）。"""
    rows = []

    # ---- 肤色 L ----
    gL = float(target.get('L', np.nan)) - float(batch.get('L', np.nan))
    if np.isfinite(gL):
        r = Row('L', gL)
        cur = float(batch.get('L'))
        slope = eval_slope(cur)
        need_ev = gL / slope
        if need_ev <= 0:
            r.levers.append(('曝光(降)', round(-need_ev, 2), True, '降低不受余量限制'))
            r.reachable = True
        else:
            hr = headroom_ev if headroom_ev is not None else 0.0
            ok = need_ev <= hr + 1e-6
            r.levers.append(('曝光(升)', round(need_ev, 2), ok,
                             '余量 %.2f 档' % hr))
            r.reachable = ok
            if not ok:
                r.needs = '引擎余量不足（只有 %.2f 档，要 %.2f 档）' % (hr, need_ev)
        rows.append(r)

    # ---- 肤色 C ----
    gC = float(target.get('C', np.nan)) - float(batch.get('C', np.nan))
    if np.isfinite(gC) and gC > 0:
        r = Row('C', gC)
        cur = float(batch.get('C'))
        # 1) amount
        room_amt = _amount_room() * AMOUNT_C_PER_UNIT
        r.levers.append(('amount', round(gC / AMOUNT_C_PER_UNIT, 2), gC <= room_amt + 1e-6,
                         '可达 +%.1f C' % room_amt))
        # 2) γ_blue（脏解：会改色相/中性轴）
        need_g = gC / GBLUE_C_PER_UNIT
        ok_g = GAMMA_RANGE[0] <= 1.0 + need_g <= GAMMA_RANGE[1]   # 量程是绝对值
        r.levers.append(('γ_blue', round(need_g, 3), ok_g,
                         '能到但会把色相/中性轴带飞'))
        # 3) 点颜色（局部，上限 ×2.2）
        need_ratio = float(target.get('C', cur)) / max(cur, 1e-6)
        r.levers.append(('点颜色', round(need_ratio, 2), need_ratio <= PC_C_MAX_RATIO,
                         '上限 ×%.1f' % PC_C_MAX_RATIO))
        # 注意：不要用 `_` 既当解包变量又当过滤条件（会被后者覆盖）
        r.reachable = any(ok for name, _v, ok, _note in r.levers
                          if name != 'γ_blue' and ok)
        if not r.reachable:
            r.needs = '彩度差超出所有旋钮量程 ⇒ **换卷/换纸**（形状类）'
        rows.append(r)
    elif np.isfinite(gC):
        rows.append(Row('C', gC, [('—', 0, True, '不需要加')], True))

    # ---- 肤色 h ----
    gh = float(target.get('h', np.nan)) - float(batch.get('h', np.nan))
    if np.isfinite(gh):
        gh = ((gh + 180) % 360) - 180
        r = Row('h', gh)
        need_g = abs(gh) / GAMMA_H_PER_UNIT
        ok_g = GAMMA_RANGE[0] <= 1.0 + need_g <= GAMMA_RANGE[1]   # 量程是绝对值
        r.levers.append(('γ(配比)', round(need_g, 3), ok_g,
                         '便宜；符号必须实测'))
        r.levers.append(('点颜色', round(abs(gh), 1),
                         abs(gh) <= PC_H_MAX_DEG, '上限 ±%.0f°' % PC_H_MAX_DEG))
        r.reachable = any(ok for _n, _v, ok, _note in r.levers)
        if not r.reachable:
            r.needs = '色相超出 γ 与点颜色 ⇒ 换卷/换纸'
        rows.append(r)

    # ---- 高光上沿 ----
    gp = float(target.get('p95', np.nan)) - float(batch.get('p95', np.nan))
    if np.isfinite(gp):
        r = Row('p95', gp)
        ok = abs(gp) <= CURVE_MAX_L * 1.15      # 15% 容差：刚好超一点别判成要换介质
        r.levers.append(('曲线', round(gp, 1), ok, '预算 ±%.0f L*' % CURVE_MAX_L))
        r.reachable = ok
        if not ok:
            r.needs = '超出曲线预算 ⇒ 介质形状（换卷/换纸）'
        rows.append(r)
    return rows


def verdict(rows: List[Row]) -> str:
    bad = [r for r in rows if not r.reachable]
    if not bad:
        return '✓ 现有生产卡能关掉所有缺口（在量程内）'
    lines = ['✗ 关不掉的 %d 项：' % len(bad)]
    for r in bad:
        lines.append('   %s 缺口 %+.1f ⇒ %s' % (r.dim, r.gap, r.needs))
    dims = {r.dim for r in bad}
    if 'C' in dims or 'h' in dims or 'p95' in dims:
        lines.append('   共同指向：**换卷 / 换纸**（手册总账：能改分色形状的只剩这条）')
    return '\n'.join(lines)
