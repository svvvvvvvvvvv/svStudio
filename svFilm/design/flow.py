# -*- coding: utf-8 -*-
"""整条引擎侧的流程：**选卡 → 出质感参数 → 渲一次 → 交 LR**。

单位是「卡」（见 `cards.py`）：一个卡 = 一卷 + 一张纸 + 一组质感档位 + 风格档。
对外只有卡；引擎侧那个配置文件只是卡的落地实现。

流程：
    need       ← 场景测量 + 意图（只用场景侧的量，不用自己出片的偏差）
    rank_cards ← 现有卡之间排序（**能立刻用**）
    rank_stocks← 卷之间排序（**值得做新卡的参考**，不是能选的）
    texture    ← 选中那张卡的质感参数（查表 + 换算 + 档位，逐条带出处）
    render     ← 注入式：渲一次、量一次
    handoff    ← 只告诉 LR"这张底片是什么"，不给靶

用法：
    from design.flow import choose, describe
    offer = choose(recs_engine, intent)
    print(describe(offer))
"""
from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import measure as M
from .cards import Card, cards as all_cards
from .cards_match import CardHit, rank_cards, table_cards
from .intent import Intent
from .match import Need, need_from_batch, rank as rank_stocks
from .stocks import Registry, registry
from .texture import Style, TexturePlan, plan_texture, warn

# ★ 引擎用**它自己的测光**出片：`presets.render_safe`（两端都不死，取最暗可行解）
#   ⚠ 它守的是 p95 ≤ p95_max（默认 96），而这是**引擎自己的安全阀**，不是我们的靶。
#   ⚠ 它的白端循环只有 3 次 ×1.15、不看目标值 ⇒ 别指望用 p95_max 控高光（实测死参数）。
#   引擎不管脸、不管落点 —— 那是 LR 的事（分工见 design/__init__.py）。
BLACK_MAX = 3.0
P95_ENGINE_SAFE = 96.0
# ★ 两条斜率都是实测出来的，各有适用范围，别混用
SLOPE_MID_L_PER_EV = 30.0    # 中间调（曝光落点用这个）
SLOPE_HIGH_L_PER_EV = 8.0    # 高光端（余量/收白用这个）


@dataclass
class Choice:
    hit: CardHit
    texture: TexturePlan
    note: str = ''

    @property
    def card(self) -> Card:
        return self.hit.card

    @property
    def stock(self) -> str:
        return self.hit.card.stock

    @property
    def paper(self) -> str:
        return self.hit.card.paper

    @property
    def renderable(self) -> bool:
        return True                       # 卡本身就是"能渲"的组合


@dataclass
class Offer:
    choices: List[Choice] = field(default_factory=list)
    new_card_candidates: List[str] = field(default_factory=list)
    need: Optional[Need] = None

    def best(self) -> Optional[Choice]:
        return self.choices[0] if self.choices else None


def choose(recs_engine: Dict[str, dict], intent: Optional[Intent] = None,
           style: Optional[Style] = None, reg: Optional[Registry] = None,
           topn: int = 3) -> Offer:
    reg = reg or registry()
    tb = all_cards()
    need = need_from_batch(recs_engine, intent)
    offer = Offer(need=need)
    for h in rank_cards(tb, reg, need)[:topn]:
        tp = plan_texture(h.card.stock, h.card.paper, long_side=1080, style=style)
        offer.choices.append(Choice(hit=h, texture=tp))
    for h in rank_stocks(reg, need)[:3]:
        offer.new_card_candidates.append(h.stock.name)
    return offer


def describe(offer: Offer) -> str:
    reg = registry()
    lines = ['【选卡】']
    lines.append(table_cards(all_cards(), reg, offer.need))
    if offer.new_card_candidates:
        lines.append('')
        lines.append('【值得做新卡的卷】（参考，不是现在能选的）：')
        lines.append('  ' + ', '.join(offer.new_card_candidates))
        lines.append('  ! 换卷 = 做一张新卡，不是改一个键'
                     '（卷/纸在卡构建时传进 params，overrides 改不了）')
    if offer.choices:
        lines.append('')
        lines.append('【选中卡的质感参数】（查表 + 换算 + 档位，逐条带出处）')
        lines.append(str(offer.choices[0].texture))
        lines.extend(warn(offer.choices[0].texture, long_side=1080).splitlines())
    return '\n'.join(lines)


# ---------------------------------------------------------------- 渲染（真路径）



def headroom_clip(disp, ev: float = 1.0) -> float:
    """**余量测试**：把已渲好的片子按线性域乘 2^ev，量"还会剪多少"。

    为什么这样测（2026-10-29）：
      · p99 只是**代理指标**——高光端斜率实测 8~16 L*/EV，所以 p99=92 并不等于
        "还能抬 1 EV"。真正要回答的是"抬了之后剪不剪"。
      · 这个问题**不需要重渲**：显示域 → 线性 → ×2^ev → 回显示域 → 量 clip。
    """
    import numpy as _np
    u = _np.clip(_np.asarray(disp, _np.float64), 0.0, 1.0)
    lin = _np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    lin2 = _np.clip(lin * (2.0 ** ev), 0.0, 1.0)
    out = _np.where(lin2 <= 0.0031308, lin2 * 12.92,
                    1.055 * _np.power(lin2, 1.0 / 2.4) - 0.055)
    L2 = _lab_of_disp(out)
    return float((L2 > 99.0).mean() * 100.0)


def headroom_ev(disp, clip_max: float = 0.5, hi: float = 3.0) -> float:
    """**这张底片还能抬几档**（抬到 clip = clip_max 为止）。

    这是引擎该报给 LR 的数：LR 的曝光计划不许超过它，否则计划本身就会毁图
    （2026-10-29 首次真跑发现：引擎为留余量压暗 -> 脸 L≈45 -> LR 要 +2.4 档，
      而 +1 档已经剪 8.69% ⇒ 计划不可行，系统却没拦）。
    """
    lo = 0.0
    if headroom_clip(disp, hi) <= clip_max:
        return float(hi)
    for _ in range(12):
        mid = 0.5 * (lo + hi)
        if headroom_clip(disp, mid) <= clip_max:
            lo = mid
        else:
            hi = mid
    return round(float(lo), 2)


def render_card(choice: Choice, raf_paths: Dict[str, str], out_dir: str,
                p95_max: Optional[float] = None, black_max: Optional[float] = None,
                log=print) -> Dict[str, str]:
    """用引擎自带的 `render_safe` 渲一批：**余量留给 LR**，出 16-bit TIF。

    raf_paths = {stem: RAF 路径}
    返回 {stem: TIF 路径}
    """
    import os
    from svFilm import presets, io                     # 引擎侧（卡就是靠它落地的）
    from svFilm import lrbase

    os.makedirs(out_dir, exist_ok=True)
    saf = getattr(choice.card, 'safety', None) or {}
    p95_max = float(saf.get('p95_max', P95_ENGINE_SAFE) if p95_max is None else p95_max)
    black_max = float(saf.get('black_max', BLACK_MAX) if black_max is None else black_max)
    out = {}
    for stem, raf in raf_paths.items():
        log('  渲 %s ← %s（卡 %s，p95_max=%.1f）' % (stem, os.path.basename(raf),
                                                     choice.card.name, p95_max))
        # io.load_raw 返回 Sample（.lin = 场景线性）；引擎要的是数组
        lin = io.load_raw(raf).lin
        # 引擎用**它自己的测光**出片（不管脸；脸是 LR 的事）
        # render_safe = "两端都不死，取最暗可行解"（黑端二分 6 次；白端只微调 3 次）
        disp = presets.render_safe(lin, choice.card.name,
                                   p95_max=float(saf.get('p95_max', 96.0)),
                                   black_max=float(saf.get('black_max', 3.0)))
        dst = os.path.join(out_dir, '%s_engine.tif' % stem)
        lrbase.save_tiff16(disp, dst)
        out[stem] = dst
        hr_ev = headroom_ev(disp)
        with open(os.path.join(out_dir, '%s_headroom.json' % stem), 'w',
                  encoding='utf-8') as fh:
            json.dump(dict(stem=stem, headroom_ev=hr_ev, limit_clip=0.5,
                           note='引擎自己的测光（render_safe）；脸归 LR'),
                  fh, ensure_ascii=False, indent=1)
        log('  可抬 %.2f 档（到 clip 0.5%% 为止）' % hr_ev)
        # ★ 余量不能外包给 render_safe：它的白端循环只有 3 次 ×1.15，对高光亮的图不够。
        #   ⇒ 渲完必须自己量一次 T0；不达标就明说，不许静默放过。
        # ★ 余量判据：**加 X 档之后还剪不剪**（比 p99 更贴用途）
        ev_plan = 1.0
        c0 = float((_lab_of_disp(disp) > 99).mean() * 100.0)
        c1 = headroom_clip(disp, ev_plan)
        log('  余量：当前 clip=%.2f%% ｜ +%.1fEV 后 clip=%.2f%%（限 0.5%%）'
            % (c0, ev_plan, c1))
        if c1 > 0.5:
            log('  ! 余量不足：抬 %.1f EV 就剪 %.2f%% ⇒ 引擎该再暗一点（调卡的 safety）'
                % (ev_plan, c1))
    return out


def _lab_of_disp(disp):
    """显示域 → L*（0..100），与测量层同口径（浮点，不量化）。"""
    import numpy as np
    from design.measure import measure_sha  # noqa: F401  仅保证模块可用
    u = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lin = np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4.0 / 29.0)
    return 116.0 * f[..., 1] - 16.0


# ---------------------------------------------------------------- 渲染/交接（注入式）

def render_stage(choice: Choice, stems: List[str],
                 render_fn: Optional[Callable] = None,
                 measure_fn: Optional[Callable] = None) -> Dict[str, dict]:
    """渲一次并量 ①。渲染与测量都由调用方注入，本层不绑任何具体实现。

    render_fn(stem, card) -> 出片路径
    measure_fn(path)      -> measure_all 的单图记录
    """
    if render_fn is None:
        raise RuntimeError(
            '没有注入渲染入口。要么注入 render_fn(stem, card)->路径，'
            '要么用 flow.render_card(choice, raf_paths, out_dir)。'
            '当前可用卡：%s' % ', '.join(all_cards().keys()))
    if measure_fn is None:
        raise RuntimeError('没有注入测量函数（用 measure_all 那套）')
    return {stem: measure_fn(render_fn(stem, choice.card)) for stem in stems}


def handoff(choice: Choice, recs_engine: Dict[str, dict]) -> str:
    """给 LR 的交接：只说"这张底片是什么"，不给靶。"""
    return ('底片 %s ｜ n=%d ｜ %s'
            % (choice.card.label(), len(recs_engine), M.summarize(recs_engine)))
