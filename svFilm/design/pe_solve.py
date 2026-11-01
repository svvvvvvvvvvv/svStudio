# -*- coding: utf-8 -*-
"""给每个目录标一个**基础印相曝光 PE**（per-directory baseline print exposure）。

为什么（2026-10-31 用户提出；2026-11-01 修正）
---------------------------------------------
PE 是**介质的物理基准**，不是把卡的 p50 look 硬印出来。
用 18.4% 参考灰印到**固定 L*50**（D≈0.735）；同一个 (卷, 纸, push) 应得同一个 PE。
卡 p50 的放置交给 LR 逐图 Exposure，不再由 PE 承担。

怎么算
------
· 参考输入：线性 0.184 中灰
· 目标：**固定** L*50 的线性 Y（`l_to_y(50)`），不是每张卡的 p50_target_D
· 求解：对 PE 单调（PE↑ ⇒ 印相更暗 ⇒ Y↓），二分 12 次即可
· 求解时和真图同一状态（`auto_exposure` 关，固定 PE）；探针也必须关。

结果写进 `simulation.print_exposure`，并在 `_derived.pe` 记账。
"""
from __future__ import annotations

import math

import numpy as np

from .photometry import l_to_y

_GRAY = 0.184
_REF_L = 50.0
_REF_D = -math.log10(l_to_y(_REF_L))


def _srgb_to_linear(v: float) -> float:
    v = float(v)
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _probe_pe(medium: dict, pe: float) -> float:
    """用参考灰渲一次，返回输出中灰的**线性 Y**。"""
    from svFilm import presets
    g = np.full((48, 48, 3), _GRAY, np.float32)
    # ★ 必须和真图**同一状态**：真图现在是 auto_exposure=False（固定 PE），
    #   探针也必须关；否则标出来的 PE 落到真图上会偏。
    m = dict(medium)
    sim = dict(m.get('simulation') or {})
    sim['auto_exposure'] = False
    m['simulation'] = sim
    d = presets.render(g, m, print_exposure=float(pe))
    return _srgb_to_linear(float(np.mean(d)))


def solve_pe(medium: dict, d_target: float, lo: float = 0.05, hi: float = 6.0,
             iters: int = 14) -> tuple:
    """二分求 PE：让 0.184 参考灰印成密度 `d_target`。返回 (pe, 记录)。"""
    y_target = 10.0 ** (-float(d_target))
    a, b = float(lo), float(hi)
    ya, yb = _probe_pe(medium, a), _probe_pe(medium, b)
    if ya < y_target:      # 最小 PE 仍太暗：目标比最亮端还亮
        return a, '目标 Y=%.4f 高于最亮端（Y(%.2f)=%.4f）⇒ 取 lo（最亮）' % (y_target, a, ya)
    if yb > y_target:      # 最大 PE 仍太亮：目标比最暗端还暗
        return b, '目标 Y=%.4f 低于最暗端（Y(%.2f)=%.4f）⇒ 取 hi（最暗）' % (y_target, b, yb)
    for _ in range(iters):
        mid = 0.5 * (a + b)
        ym = _probe_pe(medium, mid)
        if ym > y_target:      # 太亮 ⇒ 要更暗 ⇒ 加大 PE
            a = mid
        else:
            b = mid
    pe = 0.5 * (a + b)
    y = _probe_pe(medium, pe)
    return round(pe, 4), ('参考灰 18.4%% → 目标 D %.3f（Y %.4f）；实测 Y %.4f ⇒ PE %.4f'
                          % (d_target, y_target, y, pe))


def apply_all_pe() -> dict:
    """给每个目录解一个 PE：参考灰 18.4% → 固定 L*50；写回目录 JSON。"""
    from .matchcard import raw_cards
    from .medium import load, save, update_card
    out = {}
    for c in raw_cards():
        name = c['name']
        m = load(name)
        if not m:
            out[name] = dict(ok=False, why='没有介质')
            continue
        pe, why = solve_pe(m, _REF_D)
        sim = dict(m.get('simulation') or {})
        sim['print_exposure'] = pe
        m['simulation'] = sim
        m['_derived'] = dict(m.get('_derived') or {}, pe=pe, pe_reason=why)
        save(name, m, note='print_exposure = 参考灰 18.4% → 固定 L*50（二分求得）')
        update_card(name, pe=pe, pe_why=why)
        out[name] = dict(ok=True, pe=pe, why=why)
    return out
