# -*- coding: utf-8 -*-
"""给每个目录标一个**基础印相曝光 PE**（per-directory baseline print exposure）。

为什么（2026-10-31 用户提出）
--------------------------
引擎不负责"放置"，但**必须从一个好曝光起步**：PE 太亮会把高光洗掉、太暗会堵黑，
都是扫描阶段不可逆的损失。PE 是**介质的灵敏度**，属于目录，不是每张图追出来的。

怎么算（不用照片，用参考灰）
--------------------------
· 参考输入：线性 0.184 中灰（与引擎配平用的 18.4% 灰同一个数）
· 目标：这张卡的**介质目标中间调密度** `_target.p50_target_D`
· 求解：对 PE 单调（PE↑ ⇒ 印相更暗 ⇒ Y↓），二分 12 次即可
  每次都是 48×48 的灰块，约 0.1 秒
· 求解时 **必须和真图同一状态**（`auto_exposure` 开）；
  否则测光会把探针本身归一化掉，探不到 PE 的作用（实测关着标会偏 3 档）。

结果写进 `simulation.print_exposure`，并在 `_derived.pe` 记账。
"""
from __future__ import annotations

import math
import os

import numpy as np

_GRAY = 0.184


def _srgb_to_linear(v: float) -> float:
    v = float(v)
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _probe_pe(medium: dict, pe: float) -> float:
    """用参考灰渲一次，返回输出中灰的**线性 Y**。"""
    from svFilm import presets
    g = np.full((48, 48, 3), _GRAY, np.float32)
    # ★ 必须和真图**同一状态**：真图是 auto_exposure=True 渲的，探针也要开着，
    #   否则标出来的 PE 落到真图上会偏 3 档（实测：关着标 0.843，真图 p50 86.9 洗白）。
    d = presets.render(g, medium, print_exposure=float(pe))
    return _srgb_to_linear(float(np.mean(d)))


def solve_pe(medium: dict, d_target: float, lo: float = 0.05, hi: float = 6.0,
             iters: int = 14) -> tuple:
    """二分求 PE：让 0.184 参考灰印成密度 `d_target`。返回 (pe, 记录)。"""
    y_target = 10.0 ** (-float(d_target))
    a, b = float(lo), float(hi)
    ya, yb = _probe_pe(medium, a), _probe_pe(medium, b)
    if ya < y_target:      # 连最亮的 PE 都印不到这么亮
        return b, '目标 Y=%.4f 低于量程（Y(%.2f)=%.4f）⇒ 取 hi' % (y_target, b, yb)
    if yb > y_target:      # 连最暗的 PE 都太亮
        return a, '目标 Y=%.4f 高于量程（Y(%.2f)=%.4f）⇒ 取 lo' % (y_target, a, ya)
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
    """给 64 个目录各解一个 PE，写进它们的介质。"""
    import json

    from .medium import load, save
    root = r'E:\svStudio\svFilm\analysis\corpus\kurai'
    cards = json.load(open(os.path.join(root, 'cards_v5.json'), encoding='utf-8'))['cards']
    out = {}
    for c in cards:
        name = c['name']
        m = load(name)
        if not m:
            out[name] = dict(ok=False, why='没有介质')
            continue
        tg = m.get('_target') or {}
        d50 = tg.get('p50_target_D')
        if d50 is None:
            out[name] = dict(ok=False, why='没有 p50 目标密度')
            continue
        pe, why = solve_pe(m, float(d50))
        sim = dict(m.get('simulation') or {})
        sim['print_exposure'] = pe
        m['simulation'] = sim
        m['_derived'] = dict(m.get('_derived') or {}, pe=pe, pe_reason=why)
        save(name, m, note='print_exposure = 参考灰 18.4% → 本卡目标密度（二分求得）')
        out[name] = dict(ok=True, pe=pe, why=why)
    json.dump(out, open(os.path.join(root, 'card_pe.json'), 'w', encoding='utf-8', newline='\n'),
              ensure_ascii=False, indent=1)
    return out
