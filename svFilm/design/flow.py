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
# ★★★ 余量只有在"上端没被砍"时才是一个可用的数（2026-10-29 实测，见下）
#   `scan_white_level` 是**上端硬天花**：它按 sRGB 显示码切顶。
#     white_level=1.00 ⇒ 天花 L*≈100（能到顶、clip 可测）
#     white_level=0.95 ⇒ 天花 L*≈95.5（96~100 那 4~5 L* 直接归零）
#   ⇒ 降它换来的"余量"是个恒等式：headroom_ev ≡ −log2(Y(天花))，砍多少报多少，一进一出等于零。
#   实测 10/10 吻合（推算 0.167~0.223 / 实测 0.13~0.19，差 ≤0.04 EV），
#   代价是中位 4.65%（1.5~14%）的像素被从 ≥96 压到 95.5 以下、直方图 96+ 段精确归零。
#   ⇒ **不许再用降白端去制造余量**。天花低于此值 ⇒ 余量判为不可信。
CEILING_MIN_L = 99.5
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

    ⚠ **这个数只在上端没被砍时可信**。若底的最高 L* 明显低于 100（`scan_white_level`
      小于 1），它报出来的就是"你刚砍掉的天花板高度" —— 恒等式，不是收益。
      调用方要先用 `ceiling_L()` 判一下；`headroom_report()` 已经把这两件事绑在一起。
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


def ceiling_L(disp) -> float:
    """这张底片的**上端天花**（全图最高 L*）。"""
    import numpy as _np
    return float(_np.max(_lab_of_disp(disp)))


def _code_to_L(code: float) -> float:
    """sRGB 显示码 → L*（内部口径，与 `_lab_of_disp` 一致）。"""
    import numpy as _np
    c = max(0.0, min(1.0, float(code)))
    y = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 116.0 * (y ** (1.0 / 3.0)) - 16.0


def headroom_report(disp, clip_max: float = 0.5, white_level: float = None,
                    max_px: int = 1_200_000) -> dict:
    """余量 + 它**可不可信**，一起报。

    ★ 判据（2026-10-29 实测）：`scan_white_level` 是**上端硬天花**。
      它小于 1 时，图会被切在 `code=white_level` 那条线上（0.95 ⇒ L*95.5），
      而 `headroom_ev` 这时精确等于"砍掉的高度"（10/10 吻合）⇒ 报给 LR 是假预算。

    ★ 判别"被砍"和"这张本来就暗"必须分开（否则低反差图全成误报）：
      被砍 ⇒ 分布**正好顶到**白端那条线（实测天花 ≈ 预期天花）；
      本来就暗 ⇒ 天花明显**低于**这条线（1289/1608/1726 就是这种）。

    `white_level`：卡里那个白端值。不传 ⇒ 按 1.0 处理并降级为"疑似"（说明里写明）。
    `max_px`：余量是**二分搜索**，要跑十几次全图转换，原生尺寸会吃爆内存
    ⇒ 超过就**走位抽样**（不是缩放：抽样不改分布，缩放会削掉极值）。
    """
    import math as _math
    import numpy as _np
    top = ceiling_L(disp)
    wl_known = white_level is not None
    wl = 1.0 if white_level is None else float(white_level)
    expect = _code_to_L(wl)
    # ★ 判据只看把锯子设成多少，**不看这张图亮不亮**：
    #   白端 < 1 ⇒ 整批的天花被那条线限住 ⇒ 余量结构上不会超过"砍掉的高度"，
    #   是硬不变量，与图像内容无关（1726 的天花离切线 1.3 L*，按"顶到没顶到"判会漏掉）。
    cut = expect < CEILING_MIN_L

    arr = _np.asarray(disp)
    n = int(arr[..., 0].size) if arr.ndim >= 2 else int(arr.size)
    step = 1
    while n // (step * step) > max_px:
        step += 1
    sub = arr[::step, ::step] if arr.ndim >= 2 else arr[::step]
    ev = headroom_ev(sub, clip_max=clip_max)

    y = ((top + 16.0) / 116.0) ** 3 if top > -16.0 else 0.0
    taut = float(-_math.log2(y)) if y > 0 else None       # 恒等式预言的值
    if cut:
        why = ('白端 scan_white_level=%.2f（期望天花 L*%.1f，实测 L*%.1f）⇒ '
               '天花被那条线限住，报的 %.2f 档最多就是砍掉的高度（恒等式 %s），不是可用余量'
               '（要抬就把白端还原成 1.0）'
               % (wl, expect, top, ev,
                  '%.2f' % taut if taut is not None else '—'))
    elif not wl_known:
        why = '天花 L*%.1f；没传白端值，按 1.0 处理（若卡里不是 1.0，这个判定不作数）' % top
    else:
        why = '天花 L*%.1f，白端 1.0 ⇒ 余量可信' % top
    return dict(headroom_ev=ev, ceiling_L=round(top, 2), valid=bool(not cut),
                ceiling_expect_L=round(expect, 2), white_level=wl,
                tautology_ev=(round(taut, 3) if taut is not None else None), why=why)


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
    # ★ 白端要**记下来**：它决定这张底片的天花，也决定"余量"这个数可不可信
    try:
        import svFilm.presets as _presets
        white_level = float(_presets.load_raw(choice.card.name)['simulation']['scan_white_level'])
    except Exception:
        white_level = None
    if white_level is not None and white_level < 1.0:
        log('  ! 卡的白端 scan_white_level=%.2f < 1.0：天花会被切在 L*%.1f，'
            '换来的余量是零和（砍多少报多少）⇒ 出片后余量判为不可信'
            % (white_level, _code_to_L(white_level)))
    out = {}
    for stem, raf in raf_paths.items():
        log('  渲 %s ← %s（卡 %s，p95_max=%.1f）' % (stem, os.path.basename(raf),
                                                     choice.card.name, p95_max))
        # io.load_raw 返回 Sample（.lin = 场景线性）；引擎要的是数组
        lin = io.load_raw(raf).lin
        # 引擎用**它自己的测光**出片（不管脸；脸是 LR 的事）
        # render_safe = "两端都不死，取最暗可行解"（黑端二分 6 次；白端只微调 3 次）
        disp = presets.render_safe(lin, choice.card.name,
                                   p95_max=p95_max, black_max=black_max)
        dst = os.path.join(out_dir, '%s_engine.tif' % stem)
        lrbase.save_tiff16(disp, dst)
        out[stem] = dst
        # ★ 余量要连"可不可信"一起报（2026-10-29：降白端换余量是零和，实测见常数区）
        hr = headroom_report(disp, white_level=white_level)
        with open(os.path.join(out_dir, '%s_headroom.json' % stem), 'w',
                  encoding='utf-8') as fh:
            json.dump(dict(stem=stem, headroom_ev=hr['headroom_ev'],
                           ceiling_L=hr['ceiling_L'], valid=hr['valid'],
                           ceiling_expect_L=hr['ceiling_expect_L'],
                           white_level=hr['white_level'],
                           tautology_ev=hr['tautology_ev'], why=hr['why'],
                           limit_clip=0.5,
                           note='引擎自己的测光（render_safe）；脸归 LR'),
                  fh, ensure_ascii=False, indent=1)
        if hr['valid']:
            log('  可抬 %.2f 档（到 clip 0.5%% 为止）｜天花 L*%.1f'
                % (hr['headroom_ev'], hr['ceiling_L']))
        else:
            log('  !!! 余量不可信：%s' % hr['why'])
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


# ---------------------------------------------------------------- 按暗部锚点定曝光

# ★★★ 为什么要这个（2026-10-29 实测，一条搜索轨迹把两道锁都照出来了）
#   引擎的 `render_safe` 上有**两道互相抵消的锁**：
#     ① 黑端搜索：pe 越大越暗，找"黑端刚好 ≤ black_max"的那一档；
#     ② 暗端回拉：`SAFE_P5_MIN=6.0`，p5 < 6 就把 pe ×0.9 提亮（最多 3 次）。
#   实测 DSCF1422 的轨迹（pe 越大越暗，因此 p95 越小）：
#     black_max=3 ⇒ 收敛 pe=0.655（黑1.77% p5=6.23 p95=81.0），②不动
#     black_max=5 ⇒ 收敛 pe=0.716（黑4.84% p5=5.04 p95=77.4），②再拉回 p95≈81.6
#   ⇒ **把 black_max 从 3 开到 5，精确等于没改**（成品 p95 81.0 → 81.6，反而亮了一点）。
#   ⇒ 而且 p5 最终总是 ≈6.0~6.2：**引擎真正钉住的是暗部锚点，不是黑端预算**。
#
#   推论（设计层）：引擎现在交的是"正确曝光的底片"，不是"让出余量的底片"。
#   要让出余量只有一个地方可动 —— 把**暗部锚点整体下移**。
#   所以这里不再用 black_max 那一套，只留一根锚点：p5 锚到哪，余量就是"比 6.0 低多少"。
P5_ANCHOR_DEFAULT = 6.0


def solve_pe_for_p5(measure, lo: float, hi: float, target: float, iters: int = 8):
    """二分 pe，使 `measure(pe)` 的 p5 落到 target。

    `measure(pe) -> p5`。**pe 越大越暗 ⇒ p5 随 pe 单调降**（实测轨迹证实）。
    返回 (pe, p5)。到不了 target 就返回夹到边界的那个 —— 调用方自己判能不能接受。
    """
    plo, phi = float(lo), float(hi)
    best = None
    for _ in range(int(iters)):
        mid = 0.5 * (plo + phi)
        cur = float(measure(mid))
        best = (mid, cur)
        if cur > target:            # 还不够暗 ⇒ 再压
            plo = mid
        else:                       # 太暗了 ⇒ 回一点
            phi = mid
    return best if best else (0.5 * (lo + hi), float(target))


def render_p5_anchor(lin, name: str, p5_anchor: float = P5_ANCHOR_DEFAULT,
                     pe_lo: float = None, pe_hi: float = None,
                     iters: int = 8, cfg=None, log=print):
    """按**暗部锚点**出片：把这张的 p5 锚到 `p5_anchor`，上端随之跑。

    `p5_anchor` 就是"引擎让出多少余量"的唯一表达：
      · 6.0 = 现状（引擎自己的测光，等于正确曝光，余量 0）
      · 3.5 ≈ 让出 0.5 档（p5 处 dL*/dEV = 0.231×(6+16) = 5.08）
    """
    from svFilm import presets

    base = presets.pe_of(name) * float(getattr(cfg or presets.C, 'SPEK_PE_SHIFT', 1.0) or 1.0)
    lo = base * 0.20 if pe_lo is None else float(pe_lo)
    hi = base * 3.00 if pe_hi is None else float(pe_hi)

    def _p5(pe):
        d = presets.render(lin, name, cfg or presets.C, print_exposure=float(pe))
        return float(__import__('numpy').percentile(_lab_of_disp(d), 5))

    pe, got = solve_pe_for_p5(_p5, lo, hi, float(p5_anchor), iters=iters)
    disp = presets.render(lin, name, cfg or presets.C, print_exposure=float(pe))
    if abs(got - float(p5_anchor)) > 0.8:
        log('  ! 锚点没到：要 p5=%.1f，实到 %.1f（pe=%.4f 已撞边界）'
            % (float(p5_anchor), got, pe))
    return disp, dict(pe=round(float(pe), 6), p5_anchor=float(p5_anchor),
                      p5_got=round(float(got), 2))


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
