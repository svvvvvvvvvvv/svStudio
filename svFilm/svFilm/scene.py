# -*- coding: utf-8 -*-
r"""**场景判据** —— 「这张图是什么场景」。

四根轴（`AXES`）＋ 第 5 个 token 光型（见 `lighttype.py`）：`exp` 整张中位 L50 ｜ `span` 跨度
L95−L5（都显示域，各三档）｜ `back` 主体 vs 它身后的背景 ＋ 最亮区位置 ｜ `overwhite` **线性域**里
超过白点的像素占比（唯一"必须在引擎里动作"的轴）。

★ 判据只收**能从 H×W 像素量出来**、且跟"参数该不该变"相关的量（不依赖 EXIF / 机型）。
★ **弃权（`None` ⇒ key 里写 `-`）是合法输出**：判不动就不判、下游什么都不改。"""
from __future__ import annotations

import numpy as np

from . import color
from . import config as C

VERSION = 8                          # ★ 换判据就要 +1（缓存键带它）
AXES = ('exp', 'span', 'back', 'overwhite')
#: **光型**（见 `lighttype.py`）也进 key（第 5 个 token），但 `AXES` 不动。
LIGHTTYPE_KEY = True

_EXP_ORDER = ('暗', '正常', '亮')
_SPAN_ORDER = ('平', '正常', '大')


def _b(names, edges, v):
    """按升序阈值 `edges` 把 `v` 分到 `names`（比 `edges` 多一档）。"""
    i = 0
    for e in edges:
        if v >= e:
            i += 1
    return names[i]


def token(axis, v):
    """把某一轴的值变成**写进 `_scene` 键里的那个词**（词形只由这里负责，否则**静默不命中**）。"""
    if v is None:
        return '-'
    if axis == 'back':
        return str(v)                     # 正逆光 / 侧逆光 / 顺平光（本身就是词，直接用）
    if axis == 'overwhite':
        return '过曝' if v else '正常'
    return str(v)


def _block_z(L, n):
    r"""**分块亮度场** `z = 块中位 L − 整张中位 L`（**全相对量** ⇒ 跨域可比）。"""
    h, w = L.shape
    g = float(np.median(L))
    z = np.zeros((n, n), np.float64)
    for i in range(n):
        for j in range(n):
            s = L[i * h // n:(i + 1) * h // n, j * w // n:(j + 1) * w // n]
            if s.size:
                z[i, j] = float(np.median(s)) - g
    return z


def _light_position(d, L, person, cfg):
    r"""**光位** —— 正逆光 / 侧逆光 / 侧光 / 面光顺平光；判不动 ⇒ `None`（**弃权**）。

    ```
    z(块) = 块中位 L − 整张中位 L      # 相对量
    E_lr / E_tb = 右列−左列 / 下行−上行（均值；负 = 上亮）
    E_span = max(z) − min(z)；E_bg = median(背景) − median(主体)
    spike  = z(最高块) − z(第二高块)   # ★ 光源 vs 渐变
    ```
    判决（**条件触发**）：① `E_bg ≥ TB` / `E_tb ≤ −TS` / 过曝块在主体之外 / 有突出光源 ⇒ **逆光族**
    （亮区偏中轴 ⇒ 正逆光，偏一侧 ⇒ 侧逆光）；② 否则 `|E_lr| ≥ TS` ⇒ **侧光**；③ 都弱 ⇒ **面光**。
    弃权 = `no_subject` / `extreme_luma` / `mostly_blown` / `conflict` / `marginal`。"""
    H, W = L.shape
    raw = dict(why=None, E_lr=None, E_tb=None, E_span=None, E_bg=None, spike=None,
               z_span=None, clip_pct=None, clip_blk_off=None, hot_blk_off=None,
               has_spike_src=False, hi_cx=None, sub_cx=None, cx_diff=None,
               person_pct=None, tb=None, ts=None)
    tb = float(getattr(cfg, 'SCENE_BACK_TB', 15.0))
    ts = float(getattr(cfg, 'SCENE_BACK_TS', 20.0))
    mg = float(getattr(cfg, 'SCENE_BACK_MARGIN', 0.25))
    n = max(int(getattr(cfg, 'SCENE_BACK_NBLK', 3) or 3), 2)
    raw['tb'], raw['ts'] = tb, ts

    # ---- ★ 兜底 0：没有"主体 / 背景" ⇒ 无从判 ----
    if person is None:
        raw['why'] = 'no_person(没人在哪)'
        return None, raw
    per = np.asarray(person, np.float64)
    if tuple(per.shape[:2]) != (H, W):
        raw['why'] = 'person_shape_mismatch'
        return None, raw
    sub = per > 0.5
    bak = ~sub
    raw['person_pct'] = float(sub.mean() * 100.0)
    if (int(sub.sum()) < int(getattr(cfg, 'SCENE_BACK_MIN_SUB_PX', 500))
            or int(bak.sum()) < 500):
        raw['why'] = 'no_subject'
        return None, raw

    # ---- ★ 兜底 1：图本身极端（几乎全白 / 黑）⇒ 判不动 ----
    _l50 = float(np.median(L))
    if (_l50 < float(getattr(cfg, 'SCENE_BACK_EXTREME_LO', 8.0))
            or _l50 > float(getattr(cfg, 'SCENE_BACK_EXTREME_HI', 92.0))):
        raw['why'] = 'extreme_luma(%.0f)' % _l50
        return None, raw
    rgb = np.clip(np.asarray(d, np.float64), 0.0, 1.0)
    cl = (rgb.max(axis=2) >= 254.0 / 255.0) if rgb.ndim == 3 else np.zeros((H, W), bool)
    raw['clip_pct'] = 100.0 * float(cl.mean())
    if raw['clip_pct'] > float(getattr(cfg, 'SCENE_BACK_BLOWN_ALL', 30.0)):
        raw['why'] = 'mostly_blown(%.1f%%)' % raw['clip_pct']
        return None, raw

    z = _block_z(L, n)
    raw['E_lr'] = float(np.mean(z[:, -1]) - np.mean(z[:, 0]))
    raw['E_tb'] = float(np.mean(z[-1]) - np.mean(z[0]))
    raw['E_span'] = float(z.max() - z.min())
    zf = np.sort(z.ravel())[::-1]
    span_z = float(zf[0] - zf[-1])
    spike = float(zf[0] - zf[1]) if zf.size > 1 else 0.0
    raw['spike'], raw['z_span'] = round(spike, 1), round(span_z, 1)

    cz = 0.0
    if cl.any():
        off = 0
        _cp = float(getattr(cfg, 'SCENE_BACK_CLIP_PCT', 1.0))
        for i in range(n):
            for j in range(n):
                sb = cl[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                if sb.size and sb.mean() * 100.0 >= _cp:
                    off += 1 if (sp.size and sp.mean() < 0.5) else 0
        cz = float(off) / float(n * n)
    raw['clip_blk_off'] = cz

    # ---- ★★ 光源 vs 渐变（最高块在不在主体外）----
    hot = z >= zf[0] - float(getattr(cfg, 'SCENE_BACK_HOT_REL', 0.10)) * max(span_z, 1e-6)
    hz = 0.0
    if hot.any():
        off = 0
        for i in range(n):
            for j in range(n):
                if not hot[i, j]:
                    continue
                sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
                if sp.size and sp.mean() < 0.5:
                    off += 1
        hz = float(off) / float(n * n)
    raw['hot_blk_off'] = hz
    raw['has_spike_src'] = bool(hz > 0.0 and spike >=
                                float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30)) * max(span_z, 1e-6))

    # ---- 主体 vs 它身后的**背景** ----
    raw['E_bg'] = float(np.median(L[bak]) - np.median(L[sub]))
    eb = raw['E_bg']

    # ---- ★ 兜底 2：证据矛盾 ⇒ 不猜 ----
    if (eb >= tb) and abs(raw['E_lr']) >= ts:
        raw['why'] = 'conflict: 背景亮 且 左右差也大'
        return None, raw

    # ---- ★ 兜底 3：每条证据各自过「贴边 ⇒ 弃权」（要"明确超过带宽"才算数）----
    _hard = (eb >= tb * (1 + mg)
             or raw['E_tb'] <= -ts * (1 + mg)
             or cz > float(getattr(cfg, 'SCENE_BACK_CLIP_OFF_MIN', 0.05))
             or (raw['has_spike_src']
                 and raw['E_span'] >= float(getattr(cfg, 'SCENE_BACK_SPAN_MIN', 20.0))))
    if not _hard:
        # ★★ 单向证据必须单向守卫：`E_bg` 只有正、`E_tb` 只有负支持逆光。
        _marg = ((tb * (1 - mg) <= eb <= tb * (1 + mg))                    # 只有正的 E_bg
                 or (-ts * (1 + mg) <= raw['E_tb'] <= -ts * (1 - mg))      # 只有负的 E_tb
                 or (ts * (1 - mg) <= abs(raw['E_lr']) <= ts * (1 + mg)))  # E_lr 双向
        if _marg:
            raw['why'] = ('marginal: E_bg=%.1f E_tb=%.1f E_lr=%.1f 贴 %.0f/%.0f'
                          % (eb, raw['E_tb'], raw['E_lr'], tb, ts))
            return None, raw

    if _hard:
        if cl.any():
            _yy, _xx = np.nonzero(cl)
            hi_cx = float(np.median(_xx)) / max(W - 1, 1)
        else:
            hi_cx = float(int(np.argmax(z[0] + z[1] + z[2])) + 0.5) / n
        _ys, _xs = np.nonzero(sub)
        sub_cx = float(np.median(_xs)) / max(W - 1, 1)
        raw['hi_cx'], raw['sub_cx'] = round(hi_cx, 3), round(sub_cx, 3)
        raw['cx_diff'] = round(abs(hi_cx - sub_cx), 3)
        _tol = float(getattr(cfg, 'SCENE_BACK_CX_TOL', 0.10))
        return ('正逆光' if abs(hi_cx - sub_cx) <= _tol else '侧逆光'), raw

    if abs(raw['E_lr']) >= ts:
        return '侧光', raw
    return '面光/顺平光', raw


# ---------------------------------------------------------------------------
# 并列的第二条光位实现 `light_evidence`（多线索 → 程度量）：**不进缓存键**、不改 `back`。
# ---------------------------------------------------------------------------

LIGHT_LABELS = ('正逆光', '侧逆光', '侧光', '面光/顺平光')

# ★★ **给"动作"用的连续程度量**（`{"by": …, "delta": Δ}` 按它插值）：分级是**阶跃**。
DEGREES = ('deg_back', 'deg_side', 'deg_front', 'deg_source', 'deg_reflect')


def _ramp(v, hi, lo=0.0):
    r"""把量 `v` 线性映射到 0~1；阈值只当**归一化的锚**（`hi = 2T` ⇒ 阈值处 ≈ 0.5）。"""
    hi, lo = float(hi), float(lo)
    if hi <= lo:
        return 0.0
    return float(min(max((float(v) - lo) / (hi - lo), 0.0), 1.0))


def _soft_or(pairs):
    r"""软或（noisy-OR）：`1 − Π(1 − wᵢ·eᵢ)` —— **多条弱线索能累加**。"""
    p = 1.0
    for w, e in pairs:
        p *= (1.0 - max(0.0, min(1.0, float(w) * float(e))))
    return 1.0 - p


def _block_fields(L, n):
    r"""n×n 块级亮度场 + **块间关系**：`coh` = |相邻块差的均值| ÷ 均绝对值（高 ⇒ 趋势一致）。"""
    h, w = L.shape
    g = float(np.median(L))
    z = np.zeros((n, n), np.float64)
    for i in range(n):
        for j in range(n):
            s = L[i * h // n:(i + 1) * h // n, j * w // n:(j + 1) * w // n]
            if s.size:
                z[i, j] = float(np.median(s)) - g
    zf = np.sort(z.ravel())[::-1]

    def _coh(dv):
        a = float(np.abs(dv).mean())
        return float(abs(dv.mean()) / a) if a > 1e-6 else 0.0

    # ★ **趋势量**：块列 / 行均值做最小二乘，取"首→末预测差"（比"最右列 − 最左列"稳健：一列亮就翻）。
    ci = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    den = float((ci ** 2).sum()) or 1.0
    E_lr_t = float(((z.mean(axis=0) * ci).sum() / den) * (n - 1))
    E_tb_t = float(((z.mean(axis=1) * ci).sum() / den) * (n - 1))

    return dict(z=z,
                E_lr=float(np.mean(z[:, -1]) - np.mean(z[:, 0])),
                E_tb=float(np.mean(z[-1]) - np.mean(z[0])),
                E_lr_t=E_lr_t, E_tb_t=E_tb_t,
                E_span=float(zf[0] - zf[-1]),
                spike=float(zf[0] - zf[1]) if zf.size > 1 else 0.0,
                coh_x=_coh(np.diff(z, axis=1)),
                coh_y=_coh(np.diff(z, axis=0)))


def _blk_off(mask, sub, n, thresh_pct, max_row=None):
    """`mask` 里达到 `thresh_pct` 的块中落在**主体之外**的占比（`max_row` 以下不算）。"""
    H, W = mask.shape
    off = tot = 0
    for i in range(n):
        if max_row is not None and i > max_row:
            continue
        for j in range(n):
            sb = mask[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
            sp = sub[i * H // n:(i + 1) * H // n, j * W // n:(j + 1) * W // n]
            if sb.size and sb.mean() * 100.0 >= thresh_pct:
                tot += 1
                if sp.size and sp.mean() < 0.5:
                    off += 1
    return (float(off) / float(n * n)), tot


def _sub_bottom_row(sub, n):
    """主体**最低**像素落在第几个块行；拿不到 ⇒ `None`。"""
    rr = np.nonzero(sub.any(axis=1))[0]
    if rr.size == 0:
        return None
    H = sub.shape[0]
    return min(int(rr.max()) * n // H, n - 1)


def _face_evidence(L, sub, face, cfg):
    r"""**脸上的受光** —— 脸是同一材质、连续表面 ⇒ 明暗差是光方向直接读数；脸太小 ⇒ `ok=False`。"""
    out = dict(ok=False, n_px=0, E_bg=None, E_lr=None, E_tb=None, E_span=None)
    if face is None:
        return out
    m = np.asarray(face, np.float64)
    if m.ndim != 2 or tuple(m.shape[:2]) != tuple(L.shape[:2]):
        return out
    fm = m > 0.5
    n_px = int(fm.sum())
    out['n_px'] = n_px
    if n_px < int(getattr(cfg, 'SCENE_LIGHT_FACE_MIN_PX', 300)):
        return out
    if n_px < float(getattr(cfg, 'SCENE_LIGHT_FACE_MIN_REL', 0.03)) * max(int(sub.sum()), 1):
        return out
    ys, xs = np.nonzero(fm)
    xm = int((int(xs.min()) + int(xs.max()) + 1) // 2)
    ym = int((int(ys.min()) + int(ys.max()) + 1) // 2)
    yy, xx = np.mgrid[0:L.shape[0], 0:L.shape[1]]
    Lf = float(np.median(L[fm]))
    out['E_bg'] = float(np.median(L[~sub]) - Lf)
    for key, half in (('E_lr', xx >= xm), ('E_tb', yy >= ym)):
        a, b = fm & half, fm & (~half)
        if int(a.sum()) >= 30 and int(b.sum()) >= 30:
            out[key] = float(np.median(L[a]) - np.median(L[b]))
    vs = L[fm]
    out['E_span'] = float(np.percentile(vs, 95) - np.percentile(vs, 5))
    out['ok'] = True
    return out


def light_evidence(d, L, person=None, face=None, cfg=C):
    r"""多线索光位证据 → **程度量** → 分级（**不改 `back`、不进缓存键**）：软或 + **降级路径**。"""
    H, W = L.shape
    tb = float(getattr(cfg, 'SCENE_BACK_TB', 15.0))
    ts = float(getattr(cfg, 'SCENE_BACK_TS', 20.0))
    n2 = max(int(getattr(cfg, 'SCENE_BACK_NBLK2', 8) or 8), 2)
    floor = float(getattr(cfg, 'SCENE_LIGHT_CONF_FLOOR', 0.35))
    grade = float(getattr(cfg, 'SCENE_LIGHT_GRADE', 0.50))
    t_off = float(getattr(cfg, 'SCENE_BACK_CLIP_OFF_MIN', 0.05))
    ev = {}
    out = dict(label=None, why=None, conf=0.0, deg_back=0.0, deg_side=0.0,
               degrade=None, ev=ev, H=H, W=W)

    # ---- ★ 主体：优先「人在哪」，拿不到 ⇒ 降级用中心区 ----
    sub = None
    if person is not None:
        per = np.asarray(person, np.float64)
        if per.ndim == 2 and tuple(per.shape[:2]) == (H, W):
            s = per > 0.5
            if (int(s.sum()) >= int(getattr(cfg, 'SCENE_BACK_MIN_SUB_PX', 500))
                    and int((~s).sum()) >= 500):
                sub = s
    if sub is None:
        sub = np.zeros((H, W), bool)
        sub[H // 3:2 * H // 3, W // 3:2 * W // 3] = True
        out['degrade'] = 'center_subject(人在哪不可用 ⇒ 中心区当主体)'
    bak = ~sub
    ev['person_pct'] = round(float(sub.mean() * 100.0), 2)

    # ---- 图本身极端 ⇒ 判不动（不是"证据弱"）----
    _l50 = float(np.median(L))
    ev['L50'] = round(_l50, 1)
    if (_l50 < float(getattr(cfg, 'SCENE_BACK_EXTREME_LO', 8.0))
            or _l50 > float(getattr(cfg, 'SCENE_BACK_EXTREME_HI', 92.0))):
        out['why'] = 'extreme_luma(%.0f)' % _l50
        return out
    rgb = np.clip(np.asarray(d, np.float64), 0.0, 1.0)
    cl = (rgb.max(axis=2) >= 254.0 / 255.0) if rgb.ndim == 3 else np.zeros((H, W), bool)
    ev['clip_pct'] = round(100.0 * float(cl.mean()), 3)
    if ev['clip_pct'] > float(getattr(cfg, 'SCENE_BACK_BLOWN_ALL', 30.0)):
        out['why'] = 'mostly_blown(%.1f%%)' % ev['clip_pct']
        return out

    bf = _block_fields(L, n2)
    e_lr, e_tb, e_span, spike = bf['E_lr'], bf['E_tb'], bf['E_span'], bf['spike']
    ev.update(E_lr=round(e_lr, 1), E_tb=round(e_tb, 1), E_span=round(e_span, 1),
              spike=round(spike, 1), coh_x=round(bf['coh_x'], 2), coh_y=round(bf['coh_y'], 2),
              E_lr_t=round(bf['E_lr_t'], 1), E_tb_t=round(bf['E_tb_t'], 1), nblk=n2)
    ev['E_bg'] = round(float(np.median(L[bak]) - np.median(L[sub])), 1)

    _bot = _sub_bottom_row(sub, n2)
    ev['sub_bot_blk'] = _bot
    cz, _n_clip = _blk_off(cl, sub, n2, float(getattr(cfg, 'SCENE_BACK_CLIP_PCT', 3.0)),
                           max_row=_bot)
    hz, _n_hot = _blk_off(bf['z'] >= bf['z'].max()
                          - float(getattr(cfg, 'SCENE_BACK_HOT_REL', 0.10)) * max(e_span, 1e-6),
                          sub, n2, 0.0, max_row=_bot)
    ev['clip_blk_off'] = round(cz, 3)
    ev['hot_blk_off'] = round(hz, 3)
    has_src = bool(hz > 0.0 and spike >= float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30))
                   * max(e_span, 1e-6))
    ev['has_spike_src'] = has_src

    fe = _face_evidence(L, sub, face, cfg)
    ev['face'] = dict(ok=fe['ok'], n_px=fe['n_px'],
                      **{k: (None if fe[k] is None else round(fe[k], 1))
                         for k in ('E_bg', 'E_lr', 'E_tb', 'E_span')})

    def _face(key, w=1.0, sign=1.0):
        v = fe.get(key)
        return (w, _ramp(sign * v, 2.0 * tb)) if (fe['ok'] and v is not None) else (0.0, 0.0)

    # ★★★ **因果证据必须是必要条件**：逆光 ⇒ 必然"主体（尤其脸）比它身后暗"；否则"上亮下暗"
    #   单独就能顶出逆光族。掩膜混衣服 ⇒ 以**脸**为主证据。
    if fe['ok'] and fe.get('E_bg') is not None:
        causal = _soft_or([
            (1.0, _ramp(fe['E_bg'], 2.0 * tb)),                       # ★ 脸比背景暗（主）
            (float(getattr(cfg, 'SCENE_LIGHT_EBG_W_FACE', 0.35)),
             _ramp(ev['E_bg'], 2.0 * tb)),                            # 主体比背景暗（弱支持）
        ])
    else:
        causal = _ramp(ev['E_bg'], 2.0 * tb)
    support = _soft_or([
        (1.0, _ramp(-e_tb, 2.0 * ts)),               # 上亮下暗
        (1.0, _ramp(cz, 2.0 * t_off)),               # 过曝区在主体之外（**底部已排除**）
        (0.6, 1.0 if has_src else 0.0),              # 突出光源（v3 教训：单独不够 ⇒ 权重低）
        _face('E_tb', 0.7, -1.0),                    # ★ 脸上「上亮下暗」
    ])

    deg_back = min(1.0, causal * (1.0 + 0.35 * support))
    ev['causal'], ev['support'] = round(causal, 3), round(support, 3)
    deg_side = _soft_or([
        (1.0, _ramp(abs(bf['E_lr_t']), 2.0 * ts)),   # ★ **趋势量**（不是边条）：抗"某一侧有亮物体"
        _face('E_lr', 0.7, +1.0),                    # ★ 脸上左右明暗差
    ])
    deg_front = _soft_or([                            # "明确顺光/面光"的**正面证据**
        (1.0, _ramp(-ev['E_bg'], 2.0 * tb)),          # 背景比主体暗 ⇒ 光从相机方向来
        _face('E_bg', 0.7, -1.0),                     # 脸比背景亮
    ])
    out['deg_back'], out['deg_side'] = round(deg_back, 3), round(deg_side, 3)
    out['conf'] = round(max(deg_back, deg_side, deg_front), 3)
    out['deg_front'] = round(deg_front, 3)

    # ---- ⑤ ★★ `deg_source`（有独立强光源的程度）· `deg_reflect`（反射光 / 底光）----
    _src_rel = spike / max(e_span, 1e-6)
    out['deg_source'] = round(_soft_or([
        (1.0, _ramp(_src_rel, 2.0 * float(getattr(cfg, 'SCENE_BACK_SPIKE_REL', 0.30)))),
        (0.5, _ramp(ev['clip_pct'], float(getattr(cfg, 'SCENE_LIGHT_SRC_CLIP_HI', 3.0)))),
    ]), 3)
    out['deg_reflect'] = round(_ramp(e_tb, 2.0 * ts), 3)
    ev['src_rel'] = round(_src_rel, 3)

    # ---- 判决：三量**取最大**；弃权只留给「best < floor 且有大结构」----
    struct_min = float(getattr(cfg, 'SCENE_LIGHT_STRUCT_MIN', 12.0))
    best = max(deg_back, deg_side, deg_front)
    if best >= floor:
        if best == deg_back and deg_back >= grade:
            # 亮区偏主体中轴 ⇒ 正逆光；偏一侧 ⇒ 侧逆光（沿用 v3 口径）
            if cl.any():
                _yy, _xx = np.nonzero(cl)
                hi_cx = float(np.median(_xx)) / max(W - 1, 1)
            else:
                hi_cx = float(int(np.argmax(bf['z'][0] + bf['z'][1] + bf['z'][2])) + 0.5) / n2
            _ys, _xs = np.nonzero(sub)
            cx = abs(hi_cx - float(np.median(_xs)) / max(W - 1, 1))
            ev['hi_cx'], ev['cx_diff'] = round(hi_cx, 3), round(cx, 3)
            out['label'] = ('正逆光' if cx <= float(getattr(cfg, 'SCENE_BACK_CX_TOL', 0.10))
                            else '侧逆光')
        elif best == deg_side:
            out['label'] = '侧光'
        else:
            out['label'] = '面光/顺平光'
    elif e_span < struct_min:
        # 画面**本来就没有明暗结构** ⇒ "面光/顺平光"是**有信息**的判断（不是弃权）
        out['label'] = '面光/顺平光'
    else:
        out['why'] = ('weak: best=%.2f deg_back=%.2f deg_side=%.2f deg_front=%.2f E_span=%.1f'
                      % (best, deg_back, deg_side, deg_front, e_span))
    return out


def classify(disp, cfg=C, lin=None, person=None, face=None):
    r"""量四根轴：`lin`（线性图）只给 `overwhite`；`person`（粗掩膜）只给 `back`（拿不到 ⇒ 弃权）；
    `face` 只给并列的 `light_evidence`。"""
    d = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    lab = color.to_lab(np.ascontiguousarray(d))
    L = lab[..., 0]
    p5, p50, p95 = (float(np.percentile(L, q)) for q in (5.0, 50.0, 95.0))
    span = p95 - p5
    out = dict(raw=dict(L5=p5, L50=p50, L95=p95, span=span))

    out['exp'] = _b(list(_EXP_ORDER),
                    [float(getattr(cfg, 'SCENE_EXP_DARK', 18.5)),
                     float(getattr(cfg, 'SCENE_EXP_BRIGHT', 25.2))], p50)
    out['span'] = _b(list(_SPAN_ORDER),
                     [float(getattr(cfg, 'SCENE_SPAN_FLAT', 47.0)),
                      float(getattr(cfg, 'SCENE_SPAN_WIDE', 70.0))], span)

    out['back'], _lp = _light_position(d, L, person, cfg)
    out['raw'].update(_lp)

    # ---- ★ 并列的第二条光位实现（不影响 `back` / `key` / 下游取参）----
    if bool(getattr(cfg, 'SCENE_LIGHT_EVIDENCE', False)):
        try:
            out['raw']['light'] = light_evidence(d, L, person=person, face=face, cfg=cfg)
            out['back2'] = out['raw']['light'].get('label')
        except Exception as _e:                             # noqa: BLE001
            out['raw']['light'] = dict(label=None, why='error: %s' % type(_e).__name__)
            out['back2'] = None

    # ---- 源头过曝：**必须在线性域判**（显示域已被重渲染压过）----
    ow_pct = None
    if lin is not None:
        a = np.asarray(lin, np.float64)
        lvl = float(getattr(cfg, 'SCENE_OVERWHITE_LEVEL', 1.0))
        if a.ndim == 3 and a.shape[-1] >= 3:
            ow_pct = float((a[..., :3].max(axis=-1) >= lvl).mean() * 100.0)
    out['raw']['overwhite_pct'] = ow_pct
    out['overwhite'] = (None if ow_pct is None
                        else bool(ow_pct >= float(getattr(cfg, 'SCENE_OVERWHITE_PCT', 0.5))))

    # ---- ★ 光型：复用 person 掩膜，只多取一次 FACE ----
    try:
        from . import lighttype as _lt
        out['lighttype'] = _lt.measure(disp, person_mask=person, cfg=cfg)
    except Exception as _e:                                                  # noqa: BLE001
        out['lighttype'] = {'type': '未知', 'why': 'lighttype_failed: %s' % _e}
    _lttype = (out.get('lighttype') or {}).get('type') or '-'
    out['key'] = 'v%d|%s|%s' % (VERSION,
                                 '|'.join(token(k, out.get(k)) for k in AXES), _lttype)
    return out


def label(scene):
    """给人看的一句话（别甩 `key`）。"""
    if not scene:
        return '（没判）'
    bits = [scene.get('exp') or '?', scene.get('span') or '?']
    if scene.get('back'):
        bits.append(scene['back'])
    if scene.get('overwhite'):
        bits.append('源头过曝')
    return ' · '.join(bits)
