# -*- coding: utf-8 -*-
"""引擎侧：**按卡渲一次 ①**（不匹配、不选卡、不管落点）。

设计（`引擎设计.md` §2/§4.4/§5）：
  · 只渲一次：人选卡之后，用这个目录的介质 + 该目录的 PE 做基准渲 ①；
  · 不再有 ①′ 基线；匹配 A 用机内 JPG（`contentmatch`）；
  · 落点：**auto_exposure=subject，用脸皮肤 mask 测光**；PE 只作基准；
  · 生产默认走 A：**按交付尺寸渲**（长边 1080，与大师同域）。

本模块只剩三件事：渲、报余量、写 `_render.json`（供 --reuse 校验）。
选卡/匹配/交付不在这里。
"""
from __future__ import annotations

import copy
import json
import os

import numpy as np
import time
from typing import Dict, Optional

# 余量只有在"上端没被砍"时才是一个可用的数（2026-10-29 实测）：
# `scan_white_level` 是上端硬天花，降它换来的余量是恒等式（砍多少报多少）。
CEILING_MIN_L = 99.5

# 交付域长边（`测量设计.md` §1.1 / `引擎设计.md` §4.4 A）
DELIVERY_LONG_SIDE = 1080

# 渲染并行：路数由**内存**算，不由核数算（2048 长边每路 2.3~3.1 GB）。
ENGINE_PER_JOB_GB = 3.1
ENGINE_MEM_BUDGET_GB = 24.0


# ---------------------------------------------------------------- 余量（产物，不是目标）

def headroom_clip(disp, ev: float = 1.0) -> float:
    """把已渲好的片子按线性域乘 2^ev，量"还会剪多少"。"""
    import numpy as _np
    u = _np.clip(_np.asarray(disp, _np.float64), 0.0, 1.0)
    lin = _np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    lin2 = _np.clip(lin * (2.0 ** ev), 0.0, 1.0)
    out = _np.where(lin2 <= 0.0031308, lin2 * 12.92,
                    1.055 * _np.power(lin2, 1.0 / 2.4) - 0.055)
    L2 = _lab_of_disp(out)
    return float((L2 > 99.0).mean() * 100.0)


def headroom_ev(disp, clip_max: float = 0.5, hi: float = 3.0) -> float:
    """这张底片还能抬几档（抬到 clip = clip_max 为止）。"""
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
    """这张底片的上端天花（全图最高 L*）。"""
    import numpy as _np
    return float(_np.max(_lab_of_disp(disp)))


def _code_to_L(code: float) -> float:
    """sRGB 显示码 → L*。"""
    c = max(0.0, min(1.0, float(code)))
    y = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 116.0 * (y ** (1.0 / 3.0)) - 16.0


def headroom_report(disp, clip_max: float = 0.5, white_level: float = None,
                    max_px: int = 1_200_000) -> dict:
    """余量 + 可不可信，一起报。

    `scan_white_level` < 1 时图被切在上端那条线，报的余量只是"砍掉的高度"。
    """
    import math as _math
    import numpy as _np
    top = ceiling_L(disp)
    wl_known = white_level is not None
    wl = 1.0 if white_level is None else float(white_level)
    expect = _code_to_L(wl)
    cut = expect < CEILING_MIN_L

    arr = _np.asarray(disp)
    n = int(arr[..., 0].size) if arr.ndim >= 2 else int(arr.size)
    step = 1
    while n // (step * step) > max_px:
        step += 1
    sub = arr[::step, ::step] if arr.ndim >= 2 else arr[::step]
    ev = headroom_ev(sub, clip_max=clip_max)

    y = ((top + 16.0) / 116.0) ** 3 if top > -16.0 else 0.0
    taut = float(-_math.log2(y)) if y > 0 else None
    if cut:
        why = ('白端 scan_white_level=%.2f（期望天花 L*%.1f，实测 L*%.1f）⇒ '
               '天花被那条线限住，报的 %.2f 档最多就是砍掉的高度，不是可用余量'
               % (wl, expect, top, ev))
    elif not wl_known:
        why = '天花 L*%.1f；没传白端值，按 1.0 处理' % top
    else:
        why = '天花 L*%.1f，白端 1.0 ⇒ 余量可信' % top
    return dict(headroom_ev=ev, ceiling_L=round(top, 2), valid=bool(not cut),
                ceiling_expect_L=round(expect, 2), white_level=wl,
                tautology_ev=(round(taut, 3) if taut is not None else None), why=why)


# ---------------------------------------------------------------- 渲一次

def engine_jobs(n_images: int) -> int:
    """能开几路：内存预算是上限，图片数不够就少开。"""
    want = int(os.environ.get('SV_ENGINE_JOBS', '0') or 0)
    if want > 0:
        return max(1, min(want, n_images))
    by_mem = int(ENGINE_MEM_BUDGET_GB // ENGINE_PER_JOB_GB)
    return max(1, min(by_mem, os.cpu_count() or 1, n_images))


def _filters_of(medium: dict):
    sim = medium.get('simulation') or {}
    return float(sim.get('print_y_filter_shift') or 0.0), \
        float(sim.get('print_m_filter_shift') or 0.0)


def render_card(card, raf_paths: Dict[str, str], out_dir: str,
                long_side: int = DELIVERY_LONG_SIDE, log=print,
                targets: Dict[str, float] = None, table: dict = None,
                stock: str = '', paper: str = '') -> Dict[str, str]:
    """按 peer target 渲一批：**不读脸**，出 16-bit TIF。

    `targets`：{stem: 目标}。目标可以是 dict（带 `peer_q` 直方图，按直方图对齐解 PE）
    或 float（只对齐 p50，旧口径）；`table`：色卡标定的曝光表。
    没有 target 时退回卡的 nominal pe（并记账），不建脸 mask。

    `stock` / `paper`：只跑介质对比时临时覆盖卡的卷/纸（不改卡 JSON）。
    """
    from .card_render import medium_of
    from .texture import plan_texture, to_overrides

    os.makedirs(out_dir, exist_ok=True)
    medium0 = medium_of(card)
    if stock or paper:
        medium0 = dict(medium0)
        _sim = dict(medium0.get('simulation') or {})
        if stock:
            _sim['film_stock'] = stock
        if paper:
            _sim['print_paper'] = paper
        medium0['simulation'] = _sim
        log('  介质覆盖：%s + %s' % (_sim.get('film_stock'), _sim.get('print_paper')))
    media_label = '%s + %s' % (medium0['simulation']['film_stock'],
                              medium0['simulation']['print_paper'])
    try:
        white_level = float(medium0['simulation']['scan_white_level'])
    except Exception:                                      # noqa: BLE001
        white_level = None
    if white_level is not None and white_level < 1.0:
        log('  ! 卡的白端 scan_white_level=%.2f < 1.0：天花会被切在 L*%.1f，'
            '余量判为不可信' % (white_level, _code_to_L(white_level)))
    tex = plan_texture(stock or _stock_of(card), paper or _paper_of(card),
                       long_side=long_side)
    ov = to_overrides(tex, long_side=long_side)
    # ★ 优先卡级 WB（一个目录一个值）；没有才用介质预设。不做逐批反解。
    wbc = (card.get('wb') if isinstance(card, dict)
           else getattr(card, 'wb', None)) or {}
    wbm = dict(medium0.get('wb') or {})
    if wbc.get('temperature') is not None and wbc.get('tint') is not None:
        temp, tint = wbc.get('temperature'), wbc.get('tint')
        why = 'card-wb'
    else:
        temp, tint = wbm.get('temperature'), wbm.get('tint')
        why = 'preset-wb'
    medium = medium0
    if temp is not None and tint is not None:
        wb = {'white_balance': 'custom',
              'temperature': float(temp), 'tint': float(tint)}
        wb_info = dict(calibrated=False, why=why,
                       y=_filters_of(medium0)[0], m=_filters_of(medium0)[1])
        log('  解码 WB：%s custom %.0fK / G×%.3f（整批共用，不逐帧）'
            % ('卡级' if why == 'card-wb' else '预设',
               float(temp), float(tint)))
    else:
        wb = {'white_balance': 'as_shot'}
        wb_info = dict(calibrated=False, why='no wb',
                       y=_filters_of(medium0)[0], m=_filters_of(medium0)[1])
        log('  解码 WB：没有卡/预设 wb ⇒ 相机 as-shot')
    log('  质感 overrides：%d 根（%s）' % (len(ov), '、'.join(sorted(
        k.split('.')[-1] for k in ov))))
    sim = dict(medium.get('simulation') or {})
    pe_nominal = float(sim.get('print_exposure') or 0.987)
    if targets and table is None:
        raise RuntimeError('缺少卡级曝光表（chart_calib.card_table_path）')
    jobs = engine_jobs(len(raf_paths))
    out: Dict[str, str] = {}
    skipped: list = []
    argv = [(stem, raf, medium, out_dir, ov, white_level, long_side, wb,
             (targets or {}).get(stem), table, pe_nominal)
            for stem, raf in raf_paths.items()]
    if jobs > 1:
        log('  并行渲：%d 路（长边 %d）' % (jobs, long_side))
        from multiprocessing import get_context
        with get_context('spawn').Pool(jobs) as pool:
            for stem, dst, hr, msgs in pool.imap_unordered(_render_one, argv):
                if dst is None:
                    skipped.append(stem)
                    for m in msgs:
                        log(m)
                    continue
                out[stem] = dst
                _write_headroom(out_dir, stem, hr)
                for m in msgs:
                    log(m)
    else:
        for a in argv:
            stem, dst, hr, msgs = _render_one(a)
            if dst is None:
                skipped.append(stem)
                for m in msgs:
                    log(m)
                continue
            out[stem] = dst
            _write_headroom(out_dir, stem, hr)
            for m in msgs:
                log(m)
    if skipped:
        raise RuntimeError('PE hit_bound，拒绝出片：%s（表够不到 target；换卡或重建表）'
                           % '、'.join(sorted(skipped)))
    # 整批再报一次"表不可信"：单张的警告容易被刷掉，而这一类是整批性的
    bad_model = []
    for stem in out:
        ep = os.path.join(out_dir, '%s_exposure.json' % stem)
        try:
            if json.load(open(ep, encoding='utf-8')).get('model_ok') is False:
                bad_model.append(stem)
        except Exception:                                  # noqa: BLE001
            pass
    if bad_model:
        log('  ! %d/%d 张的曝光表在**这张上**不可信（%s%s）：这些片子的 p50 会明显'
            '偏离 target，核对要看测量值，不要看 p50_pred'
            % (len(bad_model), len(out), '、'.join(sorted(bad_model)[:4]),
               ' 等' if len(bad_model) > 4 else ''))
    _write_render_meta(out_dir, card, media_label, out, wb, wb_info, medium,
                       targets=targets, table=table)
    return out


def _stock_of(card) -> str:
    return card.stock if hasattr(card, 'stock') else str(card.get('stock') or '')


def _paper_of(card) -> str:
    return card.paper if hasattr(card, 'paper') else str(card.get('paper') or '')


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# 整幅五分位的自然离散（746 张大师片；两端天生窄，所以更该像）
from .chart_calib import Q_W as _Q_W


def _hist_dist(rec: dict, q_peer: dict) -> Optional[float]:
    """**整幅**五分位的加权 L1 距离（w = 1/SD，见 chart_calib.Q_SD）。

    为什么是整幅、不是只背景（2026-11-04 实测）：
      · 只对背景 ⇒ 暗差中位 21.1，>25 的 9 张；
      · 先对背景跨度再对位置 ⇒ 21.8 / 9 张；
      · 整幅加权 L1 ⇒ **15.1 / 3 张**。
    原因：人被光照亮，人亮本身就是"场景亮"的信号；把整幅算进来，
      亮裙/亮脸会把我们的分位推高，求解为了对上 peer 必须压暗 —— 暗部因此更深。
      人虽然归 LR，但它在直方图里起的是"曝光压舱"的作用。
    """
    img = (rec or {}).get('img') or {}
    num = den = 0.0
    for k, key in ((5, 'p5'), (25, 'p25'), (50, 'p50'), (75, 'p75'), (95, 'p95')):
        a, b = _num(img.get(key)), _num((q_peer or {}).get(key))
        if a is None or b is None:
            continue
        w = _Q_W.get(k, 1.0)
        num += w * abs(a - b)
        den += w
    return (num / den) if den else None


def _solve_pe_hist(lin, medium, ov, q_peer: dict, base_pe: float,
                   stem: str, out_dir: str):
    """**整幅直方图求根**：三点对称试探，取 Σ|q_render − q_peer| 最小的一版。

    为什么对称：PE 是全局曝光，最优方向事先不知道（起点是表算的，可能已经偏），
    往两边各探 0.7 档、取最小 —— 三步之内一定落在最优附近，不需要抛物线猜。
    """
    evals = []

    def probe(pe):
        rec, disp = _render_measure_face(lin, medium, ov, pe, stem, out_dir)
        d = _hist_dist(rec, q_peer)
        evals.append(dict(pe=round(float(pe), 5),
                          dist=(None if d is None else round(d, 2))))
        return d, float(pe), rec, disp

    got = [probe(base_pe), probe(base_pe * (2.0 ** 0.7)), probe(base_pe * (2.0 ** -0.7))]
    ok = [g for g in got if g[0] is not None]
    if not ok:
        raise RuntimeError('量不到整幅分位')
    best = min(ok, key=lambda g: g[0])
    ds = [g[0] for g in ok]
    flat = (max(ds) - min(ds)) < 0.5
    why = 'unreachable' if flat else 'ok'
    return best, evals, why


def _render_measure_face(lin, medium, ov, pe, stem, out_dir):
    """按给定 PE 渲一版（与成品同尺寸），用同一套测量量它。→ (rec, disp)。

    控制量用 rec['face_p5']（脸的暗侧）：skin.L 是亮侧脸颊，顶在肩部后**不再随曝光变化**
    （实测两张改 0.5 档纹丝不动），不能当控制信号；face_p5 全程单调。
    """
    import numpy as _np
    from PIL import Image as _Im
    from svFilm import presets
    from .measure_all import measure_one
    disp = presets.render(lin, medium, overrides=ov, print_exposure=float(pe))
    d = _np.clip(_np.asarray(disp, _np.float64), 0.0, 1.0)
    p = os.path.join(out_dir, '_face_%s.png' % stem)
    _Im.fromarray((d * 255.0 + 0.5).astype(_np.uint8)).save(p)
    try:
        r = measure_one(p) or {}
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    return r, disp


def _solve_pe_face(lin, medium, ov, target, base_pe, stem, out_dir, max_probe=3):
    """**带有效域的双点求根**：让渲出来的脸中位落在目标上。

    三个通用规则，不针对任何一张图：
      ① 响应为零（两点差 <1 L*）⇒ ace_unreachable（目标在这条曲线上不存在）；
      ② 某次探测**检不到脸**（渲太暗，检测器失效）⇒ 这个 PE 在有效域之外，
         把区间收到它之前，往有效侧二分一次；
      ③ 每步都只保留「离目标最近」的那个已测点作为输出，所以失败不会把 PE 带跑。
    """
    import numpy as _np
    evals = []

    def probe(k):
        pe = float(2.0 ** k)
        rec, disp = _render_measure_face(lin, medium, ov, pe, stem, out_dir)
        p5, p95 = rec.get('face_p5'), rec.get('face_p95')
        s = ((float(p5) + float(p95)) / 2.0
             if (p5 is not None and p95 is not None) else None)
        evals.append(dict(pe=round(pe, 5), pe_ev=round(float(k), 2),
                          face_mid=(None if s is None else round(s, 1))))
        return s, rec, disp

    def better(best, k, s, disp, rec):
        if s is None:
            return best
        d = abs(s - target)
        return (d, k, s, disp, rec) if d < best[0] else best

    k0 = float(np.log2(base_pe))
    s0, r0, d0 = probe(k0)
    if s0 is None:
        # ★ 第二次机会（梯子上的通用一级）：起点不在检测器有效域里 ⇒ 往两边找有效域。
        #   找到就把它当新起点接着求根；两边都没有才真失败（再由调用方退直方图）。
        for dk in (0.7, -0.7, 1.4, -1.4, 2.1, -2.1):
            s, rec_, disp_ = probe(k0 + dk)
            if s is not None:
                k0, s0, r0, d0 = k0 + dk, s, rec_, disp_
                evals[-1]['note'] = '起点无效，改从 %+.1f 档起' % dk
                break
        else:
            raise RuntimeError('起点就量不到脸（±2.1 档都无效）')
    best = (abs(s0 - target), k0, s0, d0, r0)
    why = 'ok'
    if best[0] <= 1.0:
        return float(2.0 ** best[1]), best[3], best[4], evals, why

    def secant(ka, sa, kb, sb):
        if abs(sb - sa) < 1e-6:
            return None
        k = ka + (target - sa) * (kb - ka) / (sb - sa)
        return float(np.clip(k, min(ka, kb) - 0.8, max(ka, kb) + 0.8))

    # ① 往目标方向探一步：脸太亮 ⇒ 更暗（k↑）；太暗 ⇒ 更亮（k↓）
    k1 = k0 + (0.7 if s0 > target else -0.7)
    s1, r1, d1 = probe(k1)
    if s1 is None:
        # ② 探测失效 ⇒ 收到有效域内二分一次
        k2 = 0.5 * (k0 + k1)
        s2, r2, d2 = probe(k2)
        best = better(best, k2, s2, d2, r2)
        if s2 is not None and abs(s2 - s0) >= 1.0:
            k3 = secant(k0, s0, k2, s2)
            if k3 is not None and max_probe >= 3:
                s3, r3, d3 = probe(k3)
                best = better(best, k3, s3, d3, r3)
        else:
            why = 'face_detect_lost'
        return float(2.0 ** best[1]), best[3], best[4], evals, why

    best = better(best, k1, s1, d1, r1)
    if best[0] <= 1.0:
        return float(2.0 ** best[1]), best[3], best[4], evals, why
    if abs(s1 - s0) < 1.0:
        return float(2.0 ** best[1]), best[3], best[4], evals, 'face_unreachable'

    # ③ 割线
    k2 = secant(k0, s0, k1, s1)
    if k2 is None:
        return float(2.0 ** best[1]), best[3], best[4], evals, 'face_flat'
    s2, r2, d2 = probe(k2)
    if s2 is None:
        k3 = 0.5 * (k1 + k2)
        s3, r3, d3 = probe(k3)
        best = better(best, k3, s3, d3, r3)
        if s3 is None:
            why = 'face_detect_lost'
    else:
        best = better(best, k2, s2, d2, r2)
    return float(2.0 ** best[1]), best[3], best[4], evals, why


def _render_one(args):
    """渲一张 + 量余量（**必须在模块级**：Windows 的 spawn 不能 pickle 闭包）。"""
    (stem, raf, medium, out_dir, ov, white_level, long_side, wb,
     target, table, pe_nominal) = args
    from svFilm import io, lrbase, presets
    from . import chart_calib
    medium = dict(medium)
    sim = dict(medium.get('simulation') or {})
    sim['auto_exposure'] = False
    sim['auto_exposure_method'] = sim.get('auto_exposure_method') or 'matrix'
    medium['simulation'] = sim
    media_label = '%s + %s' % (sim['film_stock'], sim['print_paper'])
    msgs = ['  渲 %s ← %s（介质 %s，长边 %d）'
            % (stem, os.path.basename(raf), media_label, long_side)]
    lin = io.load_raw(raf, max_side=long_side, wb=wb).lin
    # ★ 锚脸（2026-11-04）：控制量用 **face_p5（脸的暗侧）**，不用 skin.L（亮侧脸颊，
    #   顶到肩部后不再随曝光变化 —— 实测两张改 0.5 档纹丝不动）。
    #   求根方式：量一次 → 往目标方向探 0.6 档再量 → 割线解 log2(PE*) → 复核一次。
    #   响应为 0 ⇒ face_unreachable（目标不可达），取更近的点；不针对任何一张图。
    _use_face = (os.environ.get('SV_PE_FACE') == '1')      # 锚脸路径已作废，默认关
    _disp = None
    if isinstance(target, dict) and (target.get('peer_q') or {}).get('p50') is not None:
        # ★ 2026-11-04 第二版：**整幅直方图对齐**（引擎管整体明暗；脸/服装归 LR）
        try:
            q_peer = target.get('peer_q') or {}
            base = chart_calib.solve_pe_q(lin, target.get('peer_q') or {}, table)
            best, evals, why = _solve_pe_hist(lin, medium, ov,
                                              target.get('peer_q') or {},
                                              float(base['pe']), stem, out_dir)
            dist, pe, rec1, _d1 = best
            img_q = (rec1.get('img') or {})
            f5, f95 = _num(rec1.get('face_p5')), _num(rec1.get('face_p95'))
            fmid = (None if (f5 is None or f95 is None) else (f5 + f95) / 2.0)
            _clip = _num(rec1.get('face_p99'))
            # ---- 人物提亮预算（给 LR 用，避免人/背景亮度差太大而割裂）----
            #   规则：人最多亮到「背景 × peer 的人/背景比」，再高就和背景切开了。
            _pl = _num((rec1.get('person') or {}).get('L'))
            _bg = _num((rec1.get('bg') or {}).get('L'))
            _rel = _num(target.get('peer_rel')) or 1.55
            _ceil = (max(_bg * _rel, _pl) if (_pl is not None and _bg) else None)
            _lift = (None if (_ceil is None or _pl is None or _pl <= 0)
                     else max(0.0, float(np.log2(_ceil / _pl))))
            sol = dict(stem=stem, pe=float(pe), status=why,
                       person_L=(None if _pl is None else round(_pl, 1)),
                       bg_L=(None if _bg is None else round(_bg, 1)),
                       person_rel=(None if (_pl is None or not _bg) else round(_pl / _bg, 2)),
                       person_ceil_L=(None if _ceil is None else round(_ceil, 1)),
                       face_lift_ev=(None if _lift is None else round(_lift, 2)),
                       hist_dist=(None if dist is None else round(float(dist), 2)),
                       bg_target={k: _num((target.get('bg_target') or {}).get(k))
                                  for k in ('p10', 'L', 'p90', 'p95')},
                       bg_render={k: (_num((rec1.get('bg') or {}).get(x))
                                      if _num((rec1.get('bg') or {}).get(x)) is None
                                      else round(_num((rec1.get('bg') or {}).get(x)), 1))
                                  for k, x in (('p10', 'L_p10'), ('L', 'L'),
                                               ('p90', 'L_p90'), ('p95', 'L_p95'))},
                       q_peer={k: _num(q_peer.get(k)) for k in
                               ('p5', 'p25', 'p50', 'p75', 'p95')},
                       q_render={k: (_num(img_q.get(k)) if _num(img_q.get(k)) is None
                                     else round(_num(img_q.get(k)), 1))
                                 for k in ('p5', 'p25', 'p50', 'p75', 'p95')},
                       face_mid=(None if fmid is None else round(fmid, 1)),
                       face_gap=(None if (fmid is None or _num(q_peer.get('p50')) is None)
                                 else round(fmid - _num(q_peer.get('p50')), 1)),
                       clip_skin=(None if _clip is None else round(_clip, 1)),
                       shadow_gap=(None if (_num(img_q.get('p5')) is None
                                            or _num(q_peer.get('p5')) is None)
                                   else round(_num(img_q.get('p5')) - _num(q_peer.get('p5')), 1)),
                       frame_q={k: (_num(img_q.get(k)) if _num(img_q.get(k)) is None
                                    else round(_num(img_q.get(k)), 1))
                                for k in ('p5', 'p25', 'p50', 'p75', 'p95')},
                       face_q=dict(p5=(None if f5 is None else round(f5, 1)),
                                   p95=(None if f95 is None else round(f95, 1))),
                       face_evals=evals, delta_ev=round(float(np.log2(pe / float(base['pe']))), 2),
                       target=round(float(_num(q_peer.get('p50')) or 0.0), 2),
                       hit_bound=False, chart_sha=(table or {}).get('chart_sha'),
                       model_ok=True, model_why='', q_pred={},
                       target_source=target.get('target_from'))
            if (target.get('target_from') or '') != 'peer' and why == 'ok':
                sol['status'] = 'constant'
            _disp = _d1
            msgs.append('  直方图对齐：%d 次渲染 %s ⇒ PE %.4f（L1 %.1f，%s）'
                        % (len(evals),
                           ' → '.join('%.1f' % (e['dist'] if e['dist'] is not None else -1)
                                      for e in evals),
                           pe, dist or -1, why))
        except Exception as _e:                            # noqa: BLE001
            msgs.append('  ! 直方图求解失败（%s）⇒ 退回 peer 直方图表解' % _e)
            sol = chart_calib.solve_pe_q(lin, target.get('peer_q') or {}, table)
            sol['status'] = 'fallback_hist'
            sol['target_source'] = (target.get('target_from') or '') + '+fail'
    elif _use_face and isinstance(target, dict) and target.get('face_mid_target') is not None:
        try:
            base = chart_calib.solve_pe_q(lin, target.get('peer_q') or {}, table)
            tgt_p5 = float(target['face_mid_target'])
            pe, d1, rec1, evals, why = _solve_pe_face(
                lin, medium, ov, tgt_p5, float(base['pe']), stem, out_dir)

            # ---- 交接契约：引擎只保证「脸放对了」，其余照实报给 LR ----
            img_q = (rec1.get('img') or {})
            peer_q = target.get('peer_q') or {}
            _p5r = _num(img_q.get('p5'))
            _p5p = _num(peer_q.get('p5'))
            _clip = _num(rec1.get('face_p99')) or _num((rec1.get('skin') or {}).get('p99'))
            _status = why
            if _clip is not None and _clip >= 99.0 and _status == 'ok':
                _status = 'clip_limited'
            if _status == 'ok' and (target.get('target_from') or '') != 'peer':
                _status = 'constant'      # 按语料常数解出来的（同光位 peer 一张都没有）
            sol = dict(stem=stem, pe=float(pe), status=_status,
                       shadow_gap=(None if (_p5r is None or _p5p is None)
                                   else round(_p5r - _p5p, 1)),
                       clip_skin=(None if _clip is None else round(_clip, 1)),
                       frame_q={k: (None if _num(img_q.get(k)) is None
                                    else round(_num(img_q.get(k)), 1))
                                for k in ('p5', 'p25', 'p50', 'p75', 'p95')},
                       face_mid=(None if (rec1.get('face_p5') is None
                                          or rec1.get('face_p95') is None)
                                 else round((float(rec1['face_p5'])
                                             + float(rec1['face_p95'])) / 2.0, 1)),
                       face_q=dict(p5=(None if _num(rec1.get('face_p5')) is None
                                       else round(_num(rec1.get('face_p5')), 1)),
                                   p95=(None if _num(rec1.get('face_p95')) is None
                                        else round(_num(rec1.get('face_p95')), 1))),
                       face_target=round(float(target.get('face_target_L') or 0.0), 1),
                       face_mid_target=round(tgt_p5, 1),
                       face_p5=(None if rec1.get('face_p5') is None
                                else round(float(rec1['face_p5']), 1)),
                       face_L=(None if (rec1.get('skin') or {}).get('L') is None
                               else round(float(rec1['skin']['L']), 1)),
                       face_evals=evals, face_why=why, face_base_pe=float(base['pe']),
                       delta_ev=round(float(np.log2(float(pe) / float(base['pe']))), 2),
                       target=round(tgt_p5, 2), hit_bound=False,
                       chart_sha=(table or {}).get('chart_sha'),
                       model_ok=True, model_why='', q_pred={},
                       target_source=target.get('target_from'))
            _disp = d1
            try:                                   # ① 的完整测量：目检页直接用，省一次重测
                rec1['stem'] = stem
                import json as _json
                with open(os.path.join(out_dir, '%s_rec1.json' % stem), 'w',
                          encoding='utf-8') as _f:
                    _json.dump(rec1, _f, ensure_ascii=False)
            except Exception:                      # noqa: BLE001
                pass
            msgs.append('  锚脸 mid %.1f：%d 次渲染 %s ⇒ PE %.4f（%s）'
                        % (tgt_p5, len(evals),
                           ' → '.join('%.1f' % (e['face_mid'] if e['face_mid'] is not None else -1)
                                      for e in evals),
                           pe, why))
        except Exception as _e:                            # noqa: BLE001
            msgs.append('  ! 锚脸失败（%s）⇒ 退回 peer 直方图' % _e)
            sol = chart_calib.solve_pe_q(lin, target.get('peer_q') or {}, table)
            sol['status'] = 'fallback_hist'
            sol['target_source'] = (target.get('target_from') or '') + '+face_fail'
    elif isinstance(target, dict) and target.get('person_target_L') is not None:
        # 设计（2026-11-03 实测）：**锚人** —— 把人放到 peer 的人所在的位置，
        # 暗部/高光由介质自己走（大师片里人 55~75 时 p5≈8、p95≈90）
        sol = chart_calib.solve_pe_person(lin, float(target['person_target_L']),
                                          float(target.get('person_gap') or 25.0),
                                          table)
        sol['target_source'] = target.get('target_from')
    elif isinstance(target, dict) and (target.get('peer_q') or {}).get('p50') is not None:
        sol = chart_calib.solve_pe_q(lin, target.get('peer_q') or {}, table)
        sol['target_source'] = target.get('target_from')
    elif target is not None:
        sol = chart_calib.solve_pe(lin, float(target), table)
    else:
        sol = dict(pe=float(pe_nominal), p50_pred=None, delta_ev=0.0,
                   hit_bound=False, target=None, pe_nominal=float(pe_nominal),
                   chart_sha=(table or {}).get('chart_sha'))
    if sol.get('hit_bound'):
        msgs.append('  ! PE hit_bound（表边界 PE %.4f）⇒ 不渲这张，避免超出表的假曝光'
                    % sol['pe'])
        return stem, None, {}, msgs
    disp = _disp if _disp is not None else presets.render(
        lin, medium, overrides=ov, print_exposure=float(sol['pe']))
    if sol.get('face_target') is not None:
        pass                       # 锚脸那行上面已经记过了（代理量脸）
    elif sol.get('person_target') is not None:
        msgs.append('  锚人 %.1f（人离高光 %.0f 级）→ PE %.4f（ΔEV %+.2f；'
                    '预测人 %.1f；整幅 %s）'
                    % (sol['person_target'], sol.get('person_gap') or 0,
                       sol['pe'], sol['delta_ev'], sol.get('person_pred') or -1,
                       sol.get('q_pred') or {}))
    elif sol.get('q_target'):
        msgs.append('  peer 直方图 %s → PE %.4f（ΔEV %+.2f；预测 %s；残差 %.1f）'
                    % (sol['q_target'], sol['pe'], sol['delta_ev'],
                       sol.get('q_pred') or {}, sol.get('dist') or 0.0))
    elif sol.get('target') is not None:
        msgs.append('  peer target %.1f → PE %.4f（ΔEV %+.2f；预测 p50 %.1f）'
                    % (sol['target'], sol['pe'], sol['delta_ev'],
                       sol.get('p50_pred') if sol.get('p50_pred') is not None else -1))
    if sol.get('target') is not None or sol.get('q_target') or sol.get('person_target') is not None or sol.get('face_target') is not None:
        if sol.get('model_ok') is False:
            msgs.append('  ! 曝光表在这张上不可信：%s ⇒ 渲出来的分布会明显偏离 '
                        'peer，验证要看实测，不要看预测值'
                        % (sol.get('model_why') or '模型有效性未知'))
    else:
        msgs.append('  ! 没有 peer target ⇒ 用 nominal PE %.4f；记账' % sol['pe'])
    dst = os.path.join(out_dir, '%s_engine.tif' % stem)
    lrbase.save_tiff16(disp, dst)
    with open(os.path.join(out_dir, '%s_exposure.json' % stem), 'w',
              encoding='utf-8') as fh:
        json.dump(sol, fh, ensure_ascii=False, indent=1)
    hr = headroom_report(disp, white_level=white_level)
    msgs.append('  余量 %s：可抬 %.2f 档（产物，不是目标）'
                % ('可信' if hr['valid'] else '不可信', hr['headroom_ev']))
    return stem, dst, hr, msgs


def _write_headroom(out_dir, stem, hr):
    with open(os.path.join(out_dir, '%s_headroom.json' % stem), 'w',
              encoding='utf-8') as fh:
        json.dump(dict(stem=stem, headroom_ev=hr['headroom_ev'],
                       ceiling_L=hr['ceiling_L'], valid=hr['valid'],
                       ceiling_expect_L=hr['ceiling_expect_L'],
                       white_level=hr['white_level'],
                       tautology_ev=hr['tautology_ev'], why=hr['why'],
                       note='引擎按 peer target 解 PE；只报告余量，不改曝光；脸归 LR'),
                  fh, ensure_ascii=False, indent=1)


def _write_render_meta(out_dir: str, card, media_label: str, out: dict,
                       wb=None, wb_info=None, medium=None,
                       targets=None, table=None) -> str:
    """把"这批 ① 是用什么渲的"写下来（供 --reuse 校验）。"""
    from .card_render import medium_of
    m = medium or medium_of(card)
    sim = m.get('simulation') or {}
    info = dict(wb_info or {})
    ex = {}
    for stem in out:
        ep = os.path.join(out_dir, '%s_exposure.json' % stem)
        if os.path.isfile(ep):
            try:
                ex[stem] = json.load(open(ep, encoding='utf-8'))
            except Exception:                              # noqa: BLE001
                pass
    p = os.path.join(out_dir, '_render.json')
    d = dict(card=getattr(card, 'name', '') if hasattr(card, 'name')
             else str((card or {}).get('name') or ''),
             medium=media_label,
             print_exposure=float(sim.get('print_exposure') or 0.0),
             auto_exposure=False,
             auto_exposure_method='none',
             peer_targets={k: v.get('target') for k, v in ex.items()},
             pe_solved={k: v.get('pe') for k, v in ex.items()},
             delta_ev={k: v.get('delta_ev') for k, v in ex.items()},
             hit_bound={k: bool(v.get('hit_bound')) for k, v in ex.items()},
             # ★ 2026-11-03：p50_pred 是**表算的**；model_ok=False 时它不可信
             #   （实测可差 20 L*）。落在 _render.json 里，--reuse / 目检都能查。
             model_ok={k: v.get('model_ok') for k, v in ex.items()},
             model_why={k: v.get('model_why') for k, v in ex.items()
                        if v.get('model_why')},
             chart_sha=(table or {}).get('chart_sha'),
             amount=float((m.get('couplers') or {}).get('amount') or 0.0),
             wb=(wb or {}).get('white_balance', 'as_shot'),
             wb_temperature=float((wb or {}).get('temperature') or 0.0),
             wb_tint=float((wb or {}).get('tint') or 1.0),
             wb_calibrated=bool(info.get('calibrated')),
             print_y_filter_shift=float(sim.get('print_y_filter_shift') or 0.0),
             print_m_filter_shift=float(sim.get('print_m_filter_shift') or 0.0),
             wb_target=info.get('target'),
             wb_base_white=info.get('base_white'),
             corpus=getattr(card, 'corpus', '') if hasattr(card, 'corpus')
             else str((card or {}).get('corpus') or ''),
             n=len(out), stems=sorted(out),
             ts=time.strftime('%Y-%m-%d %H:%M:%S'))
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def _lab_of_disp(disp):
    """显示域 → L*（0..100），与测量层同口径（浮点，不量化）。"""
    import numpy as np
    u = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lin = np.where(u <= 0.04045, u / 12.92, ((u + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T / np.array([0.95047, 1.0, 1.08883])
    d = 6.0 / 29.0
    f = np.where(xyz > d ** 3, np.cbrt(xyz), xyz / (3 * d * d) + 4.0 / 29.0)
    return 116.0 * f[..., 1] - 16.0
