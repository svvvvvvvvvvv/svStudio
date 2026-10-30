# -*- coding: utf-8 -*-
"""LR 侧求解：把**卡的带**接进一次算（§56 步 6 / §52）。

    from design.lr_solve import bands_of, plan_batch
    card = <cards_v5.json 里的一张>
    plans = plan_batch(recs_1, card)      # recs_1 = ① 的测量 {stem: rec}

三件事：
  1. `bands_of(card)` —— 从卡取出**可行域**（§63.4：只收关系/形状/色彩，
     绝对落点只作参考、不进可行域）
  2. `plan_batch` —— 逐图调用 `solver.solve_pass`（曝光/曲线/局部），并把 ③ 的白端
     （T1：p95 ≤ 93 · clip < 0.5%）作为**待执行的一步**写进计划
  3. `summary` —— 人读的一行一图

★ 为什么白端在这里：2026-10-30 定（选项 b）——① 的白端由"场景 + 扫描白端"决定，
  引擎不求解；白端是"这张片子该多亮"，归 LR 的 ③。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .curve_design import apply_lut, curve_from_quantiles
from .invariants import T1
from .photometry import ev_for_l, l_after_ev
from .solver import Plan, local_exposure_for, solve_pass

# ★ 2026-10-30：**所有阈值改从卡带来**（SV：求解必须照着匹配卡）。
#   原来这里的 P95_MAX=93 / CLIP_MAX=0.5 是我自己的常数，属于"乱搞"，
#   已删除；下面从卡带的上下沿取，取不到才退回物理线。
# ★ 2026-10-30 第二次收：`PHYS_P95=96` / `PHYS_CLIP=1.0` 也不是物理 ——
#   它们是上一版自己写的"保守上界"。真正的物理线只有两条：
#     · 白平台 L*=100（像素不可能更亮）；
#     · 爆白门用 T1（`invariants.T1['clip_max']`，一处真源）。
PHYS_P95 = 100.0
PHYS_CLIP = T1['clip_max']
# 一次收白最多压几档：政策（超过这个量级就不是单张图的问题，先查底片/场景）
MAX_WHITE_PULL_EV = 1.5
# ★ 2026-10-31 减法：没有公开传递函数的旋钮（Whites/局部饱和/局部阴影/冷暖）
#   一律"方向 + 一档步进 + 记账"，不再用 k(L)/二次式反解。
ONE_STEP_UI = 10.0


def _hi(band, default):
    return float(band[1]) if isinstance(band, (list, tuple)) and len(band) == 2 else float(default)


def _lo(band, default):
    return float(band[0]) if isinstance(band, (list, tuple)) and len(band) == 2 else float(default)


def bands_of(card: dict) -> Dict[str, object]:
    """卡 → 可行域（只收关系/形状；绝对落点只作参考，见 §63.4）。"""
    b = (card or {}).get('bands') or {}
    out: Dict[str, object] = {}
    # 形状（风格量，进可行域）
    shape = {}
    for k in ('toe', 'shoulder', 'contrast_win', 'skew'):
        v = b.get(k)
        if isinstance(v, list) and len(v) == 2:
            shape[k] = (float(v[0]), float(v[1]))
    if shape:
        out['shape'] = shape
    # 脸（生理 + 语料区间）
    if isinstance(b.get('face_L'), list):
        out['face_L'] = (float(b['face_L'][0]), float(b['face_L'][1]))
    if isinstance(b.get('face_over_bg'), list):
        out['face_over_bg'] = (float(b['face_over_bg'][0]), float(b['face_over_bg'][1]))
    # 关系（人÷背景中位）
    if isinstance(b.get('person_over_bg'), list):
        # 卡里存的是比值；求解器要的是"人的中位 L 区间"，所以这边只透传比值带
        out['person_over_bg'] = (float(b['person_over_bg'][0]), float(b['person_over_bg'][1]))
    # 绝对落点：**只作参考**，不进可行域
    out['_ref'] = {k: b.get(k) for k in ('p50', 'p95', 'p5', 'clip') if b.get(k)}
    return out


# 分离色调的色相轴（UI 约定，不是标定值）：暖端 45°、冷端 210°。
SPLIT_WARM_HUE = 45.0
SPLIT_COOL_HUE = 210.0


def grading_of(card: dict, rec: dict) -> Dict[str, float]:
    """⑧ 彩色：**能算的算，不能算的只给方向 + 一档**（§47 / §6D）。

    2026-10-31 减法：
      · 肤色饱和：没有公开传递函数，局部/全局还差 0.364× ⇒ 只给 ±10 UI。
      · 肤色色相：只记账，不写幅度（强染色，非微调）。
      · 冷暖：`warm_cool_b` 出带 ⇒ 两个轮子各 ±10 点、色相成对；
        不再用"带宽×2.5 / 0.18 b*/点"反解。
    """
    b = (card or {}).get('bands') or {}
    out = dict(saturation=0.0, vibrance=0.0,
               local_saturation=0.0, local_vibrance=0.0,
               local_toning_hue=0.0, local_toning_sat=0.0,
               split_highlight_hue=0.0, split_highlight_sat=0.0,
               split_shadow_hue=0.0, split_shadow_sat=0.0, split_balance=0.0)
    notes = []
    cb = b.get('face_C')
    sk = (rec.get('skin') or {}).get('C', rec.get('face_C'))
    if isinstance(cb, list) and sk is not None:
        lo, hi = float(cb[0]), float(cb[1])
        _dc = 0.0
        if sk < lo:
            _dc = lo - sk
        elif sk > hi:
            _dc = hi - sk
        if abs(_dc) > 0.05:
            # ★ 人像优先：饱和只加在**人身上**（LocalSaturation），背景不动。
            #   没有公开传递函数 ⇒ 只给方向 + 一档（10 UI），③ 验了再走。
            out['local_saturation'] = ONE_STEP_UI if _dc > 0 else -ONE_STEP_UI
            notes.append('脸 C %.1f 对卡带 [%.1f, %.1f] ⇒ ΔC%+.1f ⇒ 局部饱和 %+.0f UI'
                         '（方向 + 一档；③ 验）' % (sk, lo, hi, _dc, out['local_saturation']))
    hb = b.get('face_h')
    hk = (rec.get('skin') or {}).get('h', rec.get('face_h'))
    if isinstance(hb, list) and hk is not None:
        lo, hi = float(hb[0]), float(hb[1])
        _dh = 0.0
        if hk < lo:
            _dh = lo - hk
        elif hk > hi:
            _dh = hi - hk
        if abs(_dh) > 2.0:
            # 目标色相可以算（卡带给的是 Lab 色相角）；但把 Lab h 变成 LR 的
            # 局部染色刻度还需要一次验证（单位语义未验）⇒ 只记账，不写。
            notes.append('脸 h %.1f 对卡带 [%.1f, %.1f] ⇒ 目标 h %.1f（局部染色未接线：'
                         '单位语义未验，桥回读 nil，不写幅度）'
                         % (hk, lo, hi, lo if _dh > 0 else hi))
    wc = b.get('warm_cool_b')
    cur = rec.get('warm_cool_b')
    if isinstance(wc, list) and cur is not None:
        lo, hi = float(wc[0]), float(wc[1])
        _db = 0.0
        if cur < lo:
            _db = lo - float(cur)
        elif cur > hi:
            _db = hi - float(cur)
        if abs(_db) > 0.05:
            # 冷暖也没有公开传递函数：方向 + 一档；两个轮子成对。
            # SplitToning*Saturation 是 0..100，方向由 hue 交换表达，强度恒正。
            sat = ONE_STEP_UI
            warm = _db > 0                 # 需要"高光更暖 / 阴影更冷"
            out['split_highlight_hue'] = SPLIT_WARM_HUE if warm else SPLIT_COOL_HUE
            out['split_shadow_hue'] = SPLIT_COOL_HUE if warm else SPLIT_WARM_HUE
            out['split_highlight_sat'] = sat
            out['split_shadow_sat'] = sat
            notes.append('暖冷 %.2f 对卡带 [%.2f, %.2f] ⇒ Δb%+.2f ⇒ 分离色调 %+.0f 点'
                         '（方向 + 一档；高光 %s / 阴影 %s；③ 验）'
                         % (float(cur), lo, hi, _db, sat,
                            '暖' if warm else '冷', '冷' if warm else '暖'))
        else:
            notes.append('暖冷 %.2f 在卡带 [%.2f, %.2f] 内 ⇒ 不动' % (float(cur), lo, hi))
    elif isinstance(wc, list):
        notes.append('暖冷带 [%.2f, %.2f] 有，但这张图没测 warm_cool_b ⇒ 不动'
                     % (float(wc[0]), float(wc[1])))
    out['note'] = '；'.join(notes)
    return out


def _meas_of(rec: dict) -> Dict[str, object]:
    """① 的测录 → solve_pass 需要的形状。

    ★ 2026-10-30：同时吃**旧测量**（`skin/bg/person` 嵌套）与 **v7 测量**
    （`face_L / person_L / bg_L_p90` 扁平；`m_*_v7.json`）。v7 没有 `shape`，
    就从它自己的 `img` 分位算（toe=p25−p5 等），口径与旧版一致。
    """
    im = rec.get('img') or {}
    sh = rec.get('shape') or {}
    sk = rec.get('skin') or {}
    bgr = rec.get('bg') or {}
    per = rec.get('person') or {}
    face_L = sk.get('L', rec.get('face_L'))
    face_C = sk.get('C', rec.get('face_C'))
    face_h = sk.get('h', rec.get('face_h'))
    person_L = per.get('L', rec.get('person_L'))
    # 白端预算优先用**人**的 p99；v7 叫 person_p99/face_p99，旧版叫 person.clip_p95
    p99 = (per.get('clip_p95') or rec.get('person_p99')
           or rec.get('face_p99') or rec.get('p99'))
    bg_hi = bgr.get('L_p90', rec.get('bg_L_p90'))
    bg_med = bgr.get('L', rec.get('bg_L'))
    if not sh and im:
        p5, p25, p50, p75, p95 = (im.get('p5'), im.get('p25'), im.get('p50'),
                                  im.get('p75'), im.get('p95'))
        if None not in (p5, p25, p50, p75, p95):
            span = float(p95) - float(p5)
            sh = dict(toe=float(p25) - float(p5),
                      shoulder=float(p95) - float(p75),
                      contrast_win=float(p75) - float(p25),
                      skew=((float(p50) - float(p5)) / span) if span > 1e-6 else None)
    return dict(bg_hi=bg_hi, bg_med=bg_med, person_L=person_L, p99=p99,
                p50=im.get('p50'), p95=im.get('p95'), p5=im.get('p5'),
                face_L=face_L, face_C=face_C, face_h=face_h,
                shape=dict(toe=sh.get('toe'), shoulder=sh.get('shoulder'),
                           contrast_win=sh.get('contrast_win'), skew=sh.get('skew')))


def white_end_plan(rec: dict, card: Optional[dict] = None,
                   curve=None, ev: float = 0.0) -> Dict[str, object]:
    """③ 白端：判据只看**人身上的爆白**（人像优先级最高）。

    2026-10-30 定：整幅/天空的 clip 是**线索**，不是门。
    实测 919 那批：1231 整幅 clip 5.29% 但人 0.00%（全是天空）；
    1526 整幅 1.58% 而人 **9.53%**（白裙爆了）—— 该压的正是后者。

    Whites 半可算（无公开传递函数）⇒ 只给**方向与档级**，出片后一次验（§52.1）。
    """
    im = rec.get('img') or {}
    per = rec.get('person') or {}
    bgr = rec.get('bg') or {}
    p95 = im.get('p95')
    p_clip = per.get('clip_pct')
    b_clip = bgr.get('clip_pct')
    # ★ 门 = 卡的带（上沿），不是我的常数
    b = (card or {}).get('bands') or {}
    p95_gate = min(PHYS_P95, _hi(b.get('p95'), PHYS_P95))
    clip_gate = min(PHYS_CLIP, max(_hi(b.get('clip'), PHYS_CLIP), 0.3))
    # ★ ③ 的判据是**曝光 + 曲线之后**的残差（§6B.2）：
    #   上一版只看曲线、不看已经给的全局曝光 ⇒ 曝光已经压下去了还报"要收白"。
    from .curve_design import apply_lut
    p95_ev = l_after_ev(float(p95), float(ev)) if p95 is not None else None
    p95_res = apply_lut(curve, float(p95_ev)) if (curve and p95_ev is not None) else p95_ev
    need = None
    if p95_res is not None and p95_res > p95_gate:
        need = 'down'
    if p_clip is not None and p_clip >= clip_gate:
        need = 'down'
    # ★ 2026-10-31 减法：Whites 没有公开传递函数，**不做模型反解**。
    #   方向 + 一档（−10 UI），③ 验了再说；残余欠账写进 note。
    whites_ui = 0.0
    note = ''
    if need == 'down' and p95_res is not None:
        whites_ui = -ONE_STEP_UI
        note = ('方向 + 一档 %.0f UI（残差 %.1f L*）；没有公开传递函数，不反解，③ 验'
                % (whites_ui, float(p95_res) - float(p95_gate)))
    return dict(need=need, amount=round(whites_ui, 1), whites_ui=round(whites_ui, 1),
                amount_basis='one_step', amount_note=note,
                p95=p95, p95_after_ev=p95_ev, p95_after_curve=p95_res,
                clip=p_clip, person_clip=p_clip,
                bg_clip=b_clip, frame_clip=im.get('clip_pct'),
                gate=dict(p95_max=round(p95_gate, 1), person_clip_max=round(clip_gate, 2),
                          from_card=bool(b)))


def plan_batch(recs: Dict[str, dict], card: dict,
               batch: Optional[dict] = None) -> Dict[str, dict]:
    """逐图出计划：曝光（整幅）· 曲线（形状）· 局部（人）· 白端（③）。

    `batch` = `batch_grade.build(recs, card)`：A 段（曲线/颜色）**全批同参**。
    不传时退回"逐图造曲线/颜色"（只给诊断用；生产必须传，见设计真源 §2）。
    """
    bands = bands_of(card)
    shape_band = bands.get('shape') or {}
    face_band = bands.get('face_L')
    out: Dict[str, dict] = {}
    # ⑥ 曲线：由卡的形状带反推（可算，不跑 LR）
    _shape = bands.get('shape') or {}
    _cap = min(PHYS_P95, _hi((card or {}).get('bands', {}).get('p95'), PHYS_P95))
    def _curve_for(rec):
        q = rec.get('img') or {}
        return curve_from_quantiles(q, _shape, p95_cap=_cap)
    _cap_hi = min(PHYS_P95, _hi((card or {}).get('bands', {}).get('p95'), PHYS_P95))
    face_rel = bands.get('face_over_bg')
    person_rel = bands.get('person_over_bg')
    for stem, rec in recs.items():
        meas = _meas_of(rec)
        card_in = dict(shape=shape_band)
        # ★ 目标 = 卡的**关系带** × **这张图自己的背景**（§63：只用关系，不用绝对落点）
        if face_rel and meas.get('bg_hi'):
            card_in['face_L'] = (face_rel[0] * meas['bg_hi'], face_rel[1] * meas['bg_hi'])
        elif face_band:
            card_in['face_L'] = face_band
        if person_rel and meas.get('bg_med'):
            card_in['person_med'] = (person_rel[0] * meas['bg_med'], person_rel[1] * meas['bg_med'])

        # ★ 一起动起来：⑥ 曲线是全局的，它会改脸的亮度 ⇒ ⑦ 的起点要换成**曲线之后**的值。
        #   否则曲线压暗多少，脸就白丢多少（实测 1772 脸 65.5→51.4）。
        # ★ A 段：给了 batch 就用**全批那一条**曲线；逐图曲线只作诊断兜底。
        curve = (batch.get('curve') if (batch and batch.get('curve'))
                 else _curve_for(rec))
        if curve:
            for _k in ('face_L', 'person_L'):
                if meas.get(_k) is not None:
                    meas[_k] = apply_lut(curve, float(meas[_k]))
            if meas.get('p99') is not None:
                meas['p99'] = apply_lut(curve, float(meas['p99']))
        # ★ 白端：曲线之后仍超卡的肩 ⇒ 用**曝光**把它压下来（EV 有文档单位）
        # ★ 人像优先：白端只为**人**服务 —— 整幅 p95 高（天空）不压，人 p99 高才压。
        _p95_res = apply_lut(curve, float(meas.get('p99') or meas.get('p95') or 0.0)) if curve else (meas.get('p99') or meas.get('p95'))
        _ev_white = 0.0
        if _p95_res is not None and float(_p95_res) > _cap_hi:
            # 2026-10-30：原来的 `(cap−L)/斜率` 是线性近似；改成精确反解。
            # 一步最多压 1.5 档是**政策**（再大就不是这张图的问题，是底片/场景）。
            _ev_white = ev_for_l(float(_p95_res), float(_cap_hi))
            _ev_white = max(_ev_white, -MAX_WHITE_PULL_EV)
            for _k in ('face_L', 'person_L', 'p99'):
                if meas.get(_k) is not None:
                    meas[_k] = l_after_ev(float(meas[_k]), _ev_white)

        # ★★ 2026-10-30 废掉"拟合式求解"（网格 + 罚项 = 区间靶）。
        #    改为**残差闭式**：目标来自卡的关系带，幅度由 CIELAB 的 Y 比反解，
        #    上限由白端物理量（主体 p99 → 100）给出。不搜索、不迭代。
        from .formula import ev_for_face, R_DEFAULT
        p: Plan = solve_pass(meas, card_in)
        _face = meas.get('face_L')
        _bghi = (rec.get('bg') or {}).get('L_p90')
        # 曲线是全局的，背景亮部也会被它改；目标比值要用**同一态**的背景。
        if _bghi is not None and curve:
            _bghi = apply_lut(curve, float(_bghi))
        # ★ 2026-10-30：白端上限只能看**人**的 p99，不能看整幅 p99
        #   （整幅 p99 常被天空/窗光顶到 99.9 ⇒ 脸永远不许提，实测这批全被截断）。
        #   `_meas_of` 取的正是 person.clip_p95；没人读数时**不设上限**并记账。
        _p99 = meas.get('p99')
        if _face is not None and _bghi:
            # ★ 2026-10-30：R 不再写死 0.85。卡的 `face_over_bg` 带就是"脸该是
            #   背景亮部的多少"，按 P1/P3：出带才动，动到**最近的那个带边**；
            #   在带内就 R = 当前比值（EV=0）。没有带才退回 R_DEFAULT。
            _R = float(R_DEFAULT)
            _cur_r = None
            if face_rel:
                _cur_r = float(_face) / max(float(_bghi), 1e-6)
                if _cur_r < float(face_rel[0]):
                    _R = float(face_rel[0])
                elif _cur_r > float(face_rel[1]):
                    _R = float(face_rel[1])
                else:
                    _R = _cur_r
            _res = ev_for_face(_face, _bghi, _R, p99=_p99)
            p.exposure_ev = round(float(_ev_white), 3)
            p.local_ev = _res['ev']
            p.local_exposure = round(local_exposure_for(_res['ev']), 5)
            # 预测用精确式；局部旋钮的实际效率实测 1.06~1.16（n=2 台账），不折进剂量。
            p.pred_face = round(l_after_ev(float(_face), float(_res['ev'])), 2)
            p.fit_dist = None
            p.ledger = list(getattr(p, 'ledger', []) or []) + [
                'face_L %.1f ｜ 目标 %.1f（R=%.3f×bg_hi %.1f，%s）｜ EV %+.3f%s'
                % (_face, _res['target'], _R, _bghi,
                   ('卡带内' if (_cur_r is not None and _R == _cur_r)
                    else ('卡带边' if face_rel else '无卡带·R_DEFAULT')),
                   _res['ev'],
                   '（白端截断）' if _res.get('capped_by') else '')]
            if _p99 is None:
                p.ledger = list(p.ledger) + [
                    '没人 p99 读数 ⇒ 局部提亮不设白端上限（记账，③ 出片必须验人 clip）']
        else:
            p.exposure_ev = round(float(_ev_white), 3)
        # §6E：局部高光**未接线**（桥回读 nil）。旧版在"要提亮/贴近肩"时给 −30，
        #   那是没有出处也没有落地的常数 —— 2026-10-30 删除，只记账。
        _p99m = meas.get('p99')
        _need_lift = float(p.local_ev or 0.0) > 0.05 or float(p.local_shadows or 0.0) > 0.5
        p.light_weight = 1.0
        p.local_highlights = 0.0
        if _need_lift or (_p99m is not None and float(_p99m) > _cap_hi):
            p.ledger = list(getattr(p, 'ledger', []) or []) + [
                '局部高光未接线（桥回读 nil）⇒ 不写；不用 −30 这个无出处常数']
        _lr_val = meas.get('light_ratio')
        if _lr_val is not None:
            p.ledger = list(getattr(p, 'ledger', []) or []) + [
                '光比 %.2f（不参与剂量：逆光的"多补"已由 R×bg_hi 表达，'
                '旧版再乘 mid_b/ratio 是重复计权）' % float(_lr_val)]
        _plan_d = dict(p.as_dict(), light_weight=round(float(getattr(p, 'light_weight', 1.0)), 2),
                      fit_dist=getattr(p, 'fit_dist', None), pred_face=getattr(p, 'pred_face', None))
        out[stem] = dict(plan=_plan_d,
                         white=white_end_plan(rec, card, curve, ev=float(_ev_white)),
                         meas=meas, ledger=[str(e) for e in p.ledger],
                         curve=curve,
                         grading=(batch.get('grading') if batch else grading_of(card, rec)))
    return out


def summary(plans: Dict[str, dict], card_name: str = '') -> str:
    lines = ['【LR 计划】卡 %s' % card_name]
    lines.append('%-10s %8s %8s  %-22s %-10s %s' % ('stem', '曝光EV', '局部EV', '曲线', '白端', '人clip/天空clip'))
    for stem, p in sorted(plans.items()):
        pl = p['plan']
        w = p['white']
        lines.append('%-10s %+8.2f %+8.2f  %-22s %-10s %.2f%% / %.2f%% (p95 %.1f)'
                     % (stem, pl['exposure_ev'], pl['local_ev'],
                        ','.join('%s%+.1f' % (k, v) for k, v in (pl.get('curve') or {}).items()) or '—',
                        w['need'] or '不动', w.get('person_clip') or 0, w.get('bg_clip') or 0,
                        w['p95'] or 0))
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--recs', required=True)
    ap.add_argument('--card', default='004_鹿井小渔_n6ab1e755')
    # 2026-10-30：匹配层已改读 v5（sky/person 齐），诊断入口跟同一份。
    ap.add_argument('--cards', default=r'E:\svStudio\svFilm\analysis\corpus\kurai\cards_v5.json')
    a = ap.parse_args(argv)
    d = json.load(open(a.cards, encoding='utf-8'))
    card = next((c for c in d['cards'] if c['name'] == a.card), None)
    if card is None:
        print('没有这张卡：%s' % a.card)
        return 2
    recs = json.load(open(a.recs, encoding='utf-8'))['per_image']
    if isinstance(recs, list):
        recs = {os.path.basename(r['file']).replace('_engine.tif', '').replace('.tif', ''): r
                for r in recs}
    print(summary(plan_batch(recs, card), a.card))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())


def refine(plans: Dict[str, dict], meas3: Dict[str, dict], card: dict) -> Dict[str, dict]:
    """**一次验后修正**：用实测 ③ 的残差，重算第二轮的量（只用可算/已标定量）。

    为什么需要：`compose` 是**开环预测**，而 LR 的实际传递（蒙版羽化、
    曲线与曝光的交互）比线性模型复杂。我们已经在量 ③ 了，就没有理由不做闭环。

    规则（只在残差超过门槛时才动，避免来回抖）：
      · 脸 L 差 > 2 L*      ⇒ 用局部曝光补（精确反解 EV，受 p99 白端约束）
      · 脸 C 差 > 1.5 C*    ⇒ 局部饱和 ±一档（10 UI）
      · 人 p99 > 卡的肩     ⇒ 局部曝光回撤

    2026-10-31 减法：局部饱和没有公开传递函数，只给一档；不再用 k(L)/二次式。
    """
    bands = (card or {}).get('bands') or {}
    out: Dict[str, dict] = {}
    for stem, p in plans.items():
        m3 = meas3.get(stem) or {}
        sk = m3.get('skin') or {}
        per = m3.get('person') or {}
        cur = dict(p.get('plan') or {})
        g = dict(p.get('grading') or {})
        face = sk.get('L')
        C = sk.get('C')
        p99 = per.get('clip_p95')
        cap = min(PHYS_P95, _hi((bands.get('p95') or [0, PHYS_P95]), PHYS_P95))
        dL = 0.0
        dC = 0.0
        fb = (p.get('meas') or {}).get('face_target')
        if face is not None and fb:
            dL = float(fb) - float(face)
        cb = bands.get('face_C')
        if C is not None and isinstance(cb, list):
            lo, hi = float(cb[0]), float(cb[1])
            if C < lo:
                dC = lo - C
            elif C > hi:
                dC = hi - C
        add_fill = 0.0
        if abs(dL) > 2.0:
            add_fill = ev_for_l(float(face or 50.0), float(face or 50.0) + float(dL))
            if p99 is not None and add_fill > 0.0:
                ev_cap = max(0.0, ev_for_l(float(p99), float(cap)))
                add_fill = min(add_fill, ev_cap)
        add_sat = 0.0
        if abs(dC) > 1.5:
            add_sat = ONE_STEP_UI if dC > 0 else -ONE_STEP_UI
        cur['local_ev'] = round(float(cur.get('local_ev') or 0.0) + add_fill, 3)
        cur['local_exposure'] = round(cur['local_ev'] / 4.0, 5)
        g['local_saturation'] = round(float(g.get('local_saturation') or 0.0) + add_sat, 1)
        out[stem] = dict(p, plan=cur, grading=g,
                         refine=dict(dL=round(dL, 1), dC=round(dC, 1),
                                     add_fill=round(add_fill, 2), add_sat=round(add_sat, 1)))
    return out
