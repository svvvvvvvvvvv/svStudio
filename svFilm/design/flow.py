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
import os

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import measure as M
from .intent import Intent
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
    need: Optional[object] = None     # 旧 Need 已随两层卡体系废弃

    def best(self) -> Optional[Choice]:
        return self.choices[0] if self.choices else None


def choose(recs_engine: Dict[str, dict], intent: Optional[Intent] = None,
           style: Optional[Style] = None, reg: Optional[Registry] = None,
           topn: int = 5, paths: Optional[Dict[str, str]] = None) -> Offer:
    """选卡：**走匹配卡**（`matchcard.rank_top5`，设计真源 §57），不再走旧的两层卡体系。"""
    from .matchcard import rank_top5
    offer = Offer()
    for h in rank_top5(recs_engine, k=topn, paths=paths):
        c = h['card']
        tp = plan_texture(c.stock, c.paper, long_side=1080, style=style)
        hit = _Hit(c, h['score'])
        offer.choices.append(Choice(hit=hit, texture=tp,
                                    note=' ｜ '.join(list(h['reason']) + list(h['cost']))))
    return offer


class _Hit:
    """`rank_top5` 的一条结果 → Choice 需要的形状（`.card` 就是匹配卡本身）。"""

    def __init__(self, card, score):
        self.card = card
        self.score = score
        self.contrib = {}


def describe(offer: Offer) -> str:
    """把 offer 说清楚（终端用）。**不推荐、不代选**：只列候选与代价。"""
    if not offer.choices:
        return '【选卡】没有候选（匹配卡为空？）'
    lines = ['【选卡】前 %d 个候选（看样例图定，别只看分数）：' % len(offer.choices)]
    for i, c in enumerate(offer.choices, 1):
        lines.append('%d) %-28s %-6.2f  %s + %s'
                     % (i, c.card.name, getattr(c.hit, 'score', 0.0),
                        c.stock, c.paper))
        if c.note:
            lines.append('     ' + c.note[:120])
    lines.append('')
    lines.append('选定就回：--card <名字>')
    return '\n'.join(lines)


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
    # ★ §20② / §48.2：引擎的两端边界**从卡来**（匹配卡的画像区间），不是卡里写死的 96/3。
    #   匹配卡自己没有 preset 名（它是一套风格），要映射到能渲的 preset —— 映射不上就抛。
    from .card_render import preset_for, safety_for
    saf = getattr(choice.card, 'safety', None)
    if not isinstance(saf, dict) or not saf:
        saf = safety_for(choice.card)
    preset_name = getattr(choice.card, 'preset', None) or preset_for(choice.card)
    p95_max = float(saf.get('p95_max', P95_ENGINE_SAFE) if p95_max is None else p95_max)
    black_max = float(saf.get('black_max', BLACK_MAX) if black_max is None else black_max)
    # ★ 白端要**记下来**：它决定这张底片的天花，也决定"余量"这个数可不可信
    try:
        import svFilm.presets as _presets
        white_level = float(_presets.load_raw(preset_name)['simulation']['scan_white_level'])
    except Exception:
        white_level = None
    if white_level is not None and white_level < 1.0:
        log('  ! 卡的白端 scan_white_level=%.2f < 1.0：天花会被切在 L*%.1f，'
            '换来的余量是零和（砍多少报多少）⇒ 出片后余量判为不可信'
            % (white_level, _code_to_L(white_level)))
    out = {}
    # ★ 质感这层要**真的进渲染**：`render_safe` 原来一个参数都不传，于是
    #   `plan_texture()` 算完就扔（对齐审计那类"算值不落地"）。现在整份 overrides 传下去。
    from .texture import to_overrides
    tex = choice.texture
    long_side = 2048
    try:
        from svFilm import presets as _ps
        long_side = int(getattr(_ps.C, 'MAX_SIDE', 2048) or 2048)
    except Exception:                                      # noqa: BLE001
        pass
    ov = to_overrides(tex, long_side=long_side)
    log('  质感 overrides：%d 根（%s）'
        % (len(ov), '、'.join(sorted(k.split('.')[-1] for k in ov))))
    jobs = engine_jobs(len(raf_paths))
    if jobs > 1:
        log('  并行渲：%d 路（长边 %d，内存预算 %d GB）'
            % (jobs, long_side, ENGINE_PER_JOB_GB * jobs))
        from multiprocessing import get_context
        argv = [(stem, raf, preset_name, out_dir, ov, p95_max, black_max,
                 white_level) for stem, raf in raf_paths.items()]
        with get_context('spawn').Pool(jobs) as pool:
            for stem, dst, hr, c0, c1, msgs in pool.imap_unordered(_render_one, argv):
                out[stem] = dst
                _write_headroom(out_dir, stem, hr)
                for m in msgs:
                    log(m)
        return out

    for stem, raf in raf_paths.items():
        stem, dst, hr, c0, c1, msgs = _render_one(
            (stem, raf, preset_name, out_dir, ov, p95_max, black_max, white_level))
        out[stem] = dst
        _write_headroom(out_dir, stem, hr)
        for m in msgs:
            log(m)
    return out


# ---------------------------------------------------------------- 单张渲染（串行/并行共用）

# ★★ 引擎渲染一律"最大限程 + 最快速度"（10-29 SV 定）。
#   实测本机 32 GB / 24 核 32 线程：2048 长边渲染峰值 2.3~3.1 GB/路 ⇒ 6 路 ≈ 17 GB，安全；
#   6264 全尺寸 ~8 GB/路 ⇒ 只有 1~2 路。**路数由内存算，不由核数算。**
ENGINE_PER_JOB_GB = 3.1
ENGINE_MEM_BUDGET_GB = 24.0      # 留 8 GB 给系统 / LR / 测量


def engine_jobs(n_images: int) -> int:
    """能开几路：内存预算是上限，图片数是不够就少开。"""
    import os
    want = int(os.environ.get('SV_ENGINE_JOBS', '0') or 0)
    if want > 0:
        return max(1, min(want, n_images))
    by_mem = int(ENGINE_MEM_BUDGET_GB // ENGINE_PER_JOB_GB)
    return max(1, min(by_mem, os.cpu_count() or 1, n_images))


def _render_one(args):
    """渲一张 + 量余量（**必须在模块级**：Windows 的 spawn 不能 pickle 闭包）。"""
    (stem, raf, card_name, out_dir, ov, p95_max, black_max, white_level) = args
    from svFilm import presets, io, lrbase       # 子进程里自己 import
    msgs = ['  渲 %s ← %s（卡 %s，p95_max=%.1f）'
            % (stem, os.path.basename(raf), card_name, p95_max)]
    lin = io.load_raw(raf).lin
    disp = presets.render_safe(lin, card_name, p95_max=p95_max,
                               black_max=black_max, overrides=ov)
    dst = os.path.join(out_dir, '%s_engine.tif' % stem)
    lrbase.save_tiff16(disp, dst)
    hr = headroom_report(disp, white_level=white_level)
    if hr['valid']:
        msgs.append('  可抬 %.2f 档（到 clip 0.5%% 为止）｜天花 L*%.1f'
                    % (hr['headroom_ev'], hr['ceiling_L']))
    else:
        msgs.append('  !!! 余量不可信：%s' % hr['why'])
    ev_plan = 1.0
    c0 = float((_lab_of_disp(disp) > 99).mean() * 100.0)
    c1 = headroom_clip(disp, ev_plan)
    msgs.append('  余量：当前 clip=%.2f%% ｜ +%.1fEV 后 clip=%.2f%%（限 0.5%%）'
                % (c0, ev_plan, c1))
    if c1 > 0.5:
        msgs.append('  ! 余量不足：抬 %.1f EV 就剪 %.2f%% ⇒ 引擎该再暗一点（调卡的 safety）'
                    % (ev_plan, c1))
    return stem, dst, hr, c0, c1, msgs


def _write_headroom(out_dir, stem, hr):
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


# ---------------------------------------------------------------- 引擎侧的边界（别再越界）

# ★★★ 2026-10-29 中午，SV 当面纠正过一次，记在这里免得再犯：
#   设计 §21 原文：「**保留** 引擎自带的中灰归一 / auto_exposure —— 它不是"我们的曝光旋钮"，
#   是**余量保证机制**」「**不要再** 在外面乘我们的 EV / PE（那就是手册说会振荡的那条路）」
#   设计 §20：引擎侧只剩三件事，都不逐图 —— ① 选卷/纸 ② T0 余量校验 ③ 白点按配对给定。
#   设计 §19：引擎判据只有 **T0 余量 · T3 白点**；曝光落点/明暗分布/颜色全是 LR 的。
#
#   所以：**不要在 flow 里做任何"按 L* 落点去二分 pe"的东西**（我做过一次 `render_p5_anchor`，
#   已删）。引擎若 T0 不过，按 §21 是**卷/纸或入口 WB 的问题**，不是"再乘一个系数"。
#   LR 提不动脸时，先做 Highlights 收白（§31 ③），**收不回来**才按 §32 回引擎要余量。


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
