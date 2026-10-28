# -*- coding: utf-8 -*-
"""整条引擎侧的流程：**选卡 → 出质感参数 → 渲一次 → 交 LR**。

单位是「卡」（见 `cards.py`）：一个卡 = 一卷 + 一张纸 + 一组质感档位 + 风格档。
对外只有卡；预设文件只是卡的落地实现。

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

# ★ 引擎自带的 render_safe 就是"余量留给 LR"（p95_max / black_max）
#   它的默认 p95_max=96 比我们的 T0（p99 ≤ 92）宽 ⇒ 这里按 T0 反推一个更紧的值。
#   反推依据：典型分布 p99 − p95 ≈ 4 L* ⇒ p95_max ≈ 92 − 4 = 88。
#   ⚠ 这个偏移只核过一次经验值，首次真渲后应实测校准。
P95_FOR_T0 = 88.0
BLACK_MAX = 3.0
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

def render_place_face(lin, name: str, target_face_L: float = 70.0,
                      face_tol: float = 2.0, iters: int = 3,
                      pe0: Optional[float] = None, log=print):
    """引擎的正确目标：**把脸放到该在的地方**；余量是结果，不是目标。

    为什么改（2026-10-29 首次真跑）：
      原来 `render_with_headroom` 追"p99 ≤ 92"，结果是"为留余量把脸压到 L40"
      ⇒ LR 要 +2.4 档 ⇒ 又需要更大余量 —— 一个循环论证。
      实测这张卡做不到两全：脸 67.7 时 p99=97.4；压到 p99=92.7 时脸只剩 51.7。

    正确顺序：
      1. 引擎按测光把**脸放到 target_face_L**（一次算 + 最多 2 次修正，用割线）
      2. 量"还剩多少余量"（可抬几档到 clip 0.5%）—— **这是产出**
      3. LR 的曝光计划不得超过这个余量（超了就报欠账）

    返回 (disp, info)
    """
    from svFilm import presets
    info = dict(target_face=target_face_L, attempts=[])
    pe = float(pe0 if pe0 is not None else presets.pe_of(name))
    hist = []
    disp = None
    for i in range(max(2, iters)):
        disp = presets.render(lin, name, print_exposure=pe)
        L = _lab_of_disp(disp)
        import numpy as _np
        fL = _face_L(disp)
        info['attempts'].append(dict(pe=round(pe, 4),
                                     faceL=None if fL is None else round(fL, 2),
                                     p99=round(float(_np.percentile(L, 99)), 2),
                                     clip=round(float((L > 99).mean() * 100), 2)))
        log('    pe=%.4f -> 脸L=%s p99=%.1f clip=%.2f%%'
            % (pe, ('%.1f' % fL) if fL is not None else '无脸',
               _np.percentile(L, 99), (L > 99).mean() * 100))
        if fL is None:
            info.update(pe=round(pe, 4), ok=False, why='没有脸，无法按脸落点')
            return disp, info
        if abs(fL - target_face_L) <= face_tol:
            info.update(pe=round(pe, 4), ok=True)
            return disp, info
        # 割线：脸 L 对 log2(pe) 的斜率（方向：pe 越大越暗）
        hist.append((float(_np.log2(pe)), fL))
        if len(hist) >= 2:
            (x0, y0), (x1, y1) = hist[-2], hist[-1]
            slope = (y1 - y0) / (x1 - x0) if abs(x1 - x0) > 1e-6 else -14.0
            if abs(slope) < 1e-3:
                slope = -14.0
        else:
            slope = -14.0
        dx = (target_face_L - fL) / slope
        dx = max(-0.6, min(0.6, dx))
        pe *= 2.0 ** dx
    info.update(pe=round(pe, 4), ok=False, why='没收敛到脸落点')
    return disp, info


def render_with_headroom(lin, name: str, target_p99: float = 92.0,
                         face_floor: float = 58.0, iters: int = 5,
                         pe0: Optional[float] = None, log=print):
    """按「量 p99 → 反解 print_exposure」给底片留余量（**不依赖 render_safe**）。

    为什么不用 `render_safe`（2026-10-29 实测，DSCF1289 / 卡38）：
      · 它的白端循环是 `for _ in range(3): pe *= 1.15`，**不看目标值**；
        只要 p95 超标就跑满 3 次 ⇒ `p95_max=80` 与 `88` 给出**同一张图**
        （p95=95.6 / p99=98.9 逐项相同）⇒ 该参数在高光亮的图上是死参数。
      · 它的曝光实际由**黑端二分**决定（最大化 pe 且黑不死），白端只被微调 3 次。

    这里用**割线法**（两点实测自己算斜率）：
      · 方向：print_exposure 越大 = 越暗
      · 斜率不是常数：实测 8~16 L*/EV（pe 越低越缓、越高越陡）⇒ 固定斜率会过冲
    返回 (disp, info)；info['attempts'] 里有每次的 pe / p99。
    """
    from svFilm import presets
    import numpy as _np

    def measure(pe):
        disp = presets.render(lin, name, print_exposure=pe)
        L = _lab_of_disp(disp)
        return disp, float(_np.percentile(L, 99)), float(_np.percentile(L, 95)), \
            float((L > 99).mean() * 100.0)

    pe = float(pe0 if pe0 is not None else presets.pe_of(name))
    info = dict(target=target_p99, face_floor=face_floor, attempts=[], bound=None)
    hist = []                       # [(log2(pe), p99)]
    disp = None
    for i in range(max(2, iters)):
        disp, p99, p95, clip = measure(pe)
        fL = _face_L(disp)
        log('    pe=%.4f -> p95=%.1f p99=%.1f clip=%.2f%% 脸L=%s'
            % (pe, p95, p99, clip, ('%.1f' % fL) if fL is not None else '无脸'))
        info['attempts'].append(dict(pe=round(pe, 4), p99=round(p99, 2),
                                     p95=round(p95, 2), clip=round(clip, 2),
                                     faceL=None if fL is None else round(fL, 2)))
        # ★ 脸到下限就别再压了：留余量不该以毁掉主体为代价
        if fL is not None and fL <= face_floor:
            info.update(pe=round(pe, 4), ok=False, bound='face',
                        why='脸已到下限 %.0f，白端仍 p99=%.1f（目标 %.0f）'
                            % (face_floor, p99, target_p99))
            log('  ! 两约束打架：脸到下限，白端仍超 ⇒ 这张卡肩部太窄，换卡或不留这么多余量')
            return disp, info
        hist.append((float(_np.log2(pe)), p99))
        if abs(p99 - target_p99) <= 1.0:
            info.update(pe=round(pe, 4), ok=True, slope=None, bound='p99')
            return disp, info
        # 下一步：割线法；只有一点时用 12 L*/EV 的初值
        if len(hist) >= 2:
            (x0, y0), (x1, y1) = hist[-2], hist[-1]
            slope = (y1 - y0) / (x1 - x0) if abs(x1 - x0) > 1e-6 else -12.0
            if abs(slope) < 1e-3:
                slope = -12.0
            info['slope'] = round(float(slope), 2)
        else:
            slope = -12.0
        dx = (target_p99 - p99) / slope          # slope 是 dL/dlog2(pe)，负值
        dx = max(-0.6, min(0.6, dx))             # 单步不超过 0.6 EV，防过冲
        pe *= 2.0 ** dx
    last = info['attempts'][-1]
    info.update(pe=round(pe, 4), ok=abs(last['p99'] - target_p99) <= 2.0)
    return disp, info


def _face_L(disp, floor: float = 55.0):
    """脸亮度的**便宜代理**：人脸框中央 60% 的 L* 中位（不做皮肤挑选）。

    用途只是"别把脸压死"，所以不需要皮肤判别；要精确的肤色仍走测量层。
    返回 (L, 是否检到脸)。
    """
    try:
        import sys
        P = r'E:\ds\photo\_corpus\kurai\skin_v2'
        if P not in sys.path:
            sys.path.insert(0, P)
        import skin_v2
        fi = skin_v2.detect(disp)
        if not fi:
            return None
        x, y, w, h = fi['bbox']
        L = _lab_of_disp(disp)
        H, W = L.shape
        x0 = int(max(0, x + 0.2 * w)); x1 = int(min(W, x + 0.8 * w))
        y0 = int(max(0, y + 0.2 * h)); y1 = int(min(H, y + 0.8 * h))
        if x1 <= x0 or y1 <= y0:
            return None
        return float(_np_median(L[y0:y1, x0:x1]))
    except Exception:
        return None


def _np_median(a):
    import numpy as _np
    return _np.median(a)


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
    p95_max = float(saf.get('p95_max', P95_FOR_T0) if p95_max is None else p95_max)
    black_max = float(saf.get('black_max', BLACK_MAX) if black_max is None else black_max)
    out = {}
    for stem, raf in raf_paths.items():
        log('  渲 %s ← %s（卡 %s，p95_max=%.1f）' % (stem, os.path.basename(raf),
                                                     choice.card.name, p95_max))
        # io.load_raw 返回 Sample（.lin = 场景线性）；引擎要的是数组
        lin = io.load_raw(raf).lin
        # 1) 先把脸放到该在的地方（余量是结果）
        disp, hr = render_place_face(lin, choice.card.name, target_face_L=70.0, log=log)
        if not hr.get('ok'):
            log('  ! 脸落点没收敛：%s' % (hr.get('attempts') or hr.get('why')))
        dst = os.path.join(out_dir, '%s_engine.tif' % stem)
        lrbase.save_tiff16(disp, dst)
        out[stem] = dst
        hr_ev = headroom_ev(disp)
        with open(os.path.join(out_dir, '%s_headroom.json' % stem), 'w',
                  encoding='utf-8') as fh:
            json.dump(dict(stem=stem, headroom_ev=hr_ev, limit_clip=0.5,
                           attempts=hr.get('attempts')), fh, ensure_ascii=False, indent=1)
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


def _np_ok():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


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
