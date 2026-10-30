# -*- coding: utf-8 -*-
r"""_lib / verdict.py —— 判据机器（**界表查询 · 分辨力 · 中位检验 · bootstrap · 配对统计**）。

来源 = `amount三链_1013\b0_bounds.py` + `b1_verdict.py` + `b4_pair.py`
     · `中性轴口径_1013\c3_verdict.py` · `B2核查_1013\z3_resid.py` · `B2p重解_1013\b1_master.py`

## ★ 两条硬要求（`AGENTS` §一.1）—— 本模块**强制**它们
```
① **两套界都列**：`union`（三位并集 [P25,P75]，= AGENTS §一.1 写死的，主判据）
                   `own`  （**该链自己大师**的逐图 [P25,P75]，签名分家的链用这套）
   ★ `assert_full_report()` 会因"缺一套界"直接报错。
② **三样都报**：
   · 分辨力  R      = 2*sqrt((大师逐图IQR/v12)^2 + (我们逐图IQR/vn)^2)   -> 判"缺陷"级（严）
   · 分辨力  R_pair = 2*sqrt((大师逐图IQR/v12)^2 + (配对Δ逐图IQR/vn)^2) -> 判"两档之差可辨"
   · 中位检验 p（置换，分布无关）-> 判"有系统差异"级（宽）
   · bootstrap 95% 区间（配对 Δ 重采样图）
   · 配对统计：Δ 中位 + **同号张数** k/n（符号检验 p）
   ⇒ ❌ 不许只报一个就下结论。
```

## 主函数（签名）
```
# —— 界
agents_bounds()                                    -> dict    AGENTS §一.1 写死的 8 条
agents_bounds_check(m8)                            -> list    用大师数据**复算** 8 条 + 逐位比对
bounds_from_series(vals)                           -> dict    {p25,p75,iqr,n,med}
bounds_union(m8, key)                              -> dict    三位并集界
bounds_own(m8, key, chain)                         -> dict    本链界
bounds_both(m8, key, chain)                        -> dict    {union, own}  ★两套
neutral_bounds(feat)                               -> dict    中性轴五档 × (a,b,C) 两套界

# —— 尺子
resolution(iqr_master, iqr_ours, n)                -> float   R（判"读数越界"）
resolution_pair(iqr_master, iqr_delta, n)          -> float   R_pair（判"两档之差"）
outside(m, lo, hi) / dist_out(v, lo, hi)           -> float   离界距离 / (距离, 带内朝向)

# —— 检验
perm_p(x, y, n=4000, seed=...)                     -> float   两样本中位差置换 p（双侧）
sign_test(d)                                       -> dict    {k,n,p} 配对 Δ 同号张数
median_t(dm, iqr_m, n_m, iqr_o, n_o)               -> float   中位检验 t
boot_ci(v, n=4000, seed=...)                       -> (med, lo95, hi95)
boot_ci_paired(ab, ac, n=4000, seed=...)           -> (med, lo95, hi95)
boot_dist_diff(band, ab, ac, n=4000, seed=...)     -> (med, lo95, hi95)

# —— 判定
jd_band(band, v0, v1, boot_dd)                     -> (判定, d0, d1)
judge(key=..., ours_base=..., ours_cand=..., m8=..., chain=..., ...) -> dict   ★一行全报
report_lines(rows)                                 -> list[str] 表格行（两套界 + 三样都列）
assert_full_report(row)                            -> None    缺项直接 raise

# —— 读数 -> 量（专题侧只传 measure 的 reading）
reading_value(reading, key)                        -> float
series_from_readings(doc, arm, key)                -> list     逐图序列（自动跳过缺的）
master_series(m8, key) / master_series_feat(feat, key) -> {group: [v]}
```

## 口径
* 界一律 `[P25,P75]`；**分辨力那一项第一项永远是大师逐图 IQR**（它代表"风格靶本身有多宽"）。
* `A5` 等幅度量用 F1 界；`R1`（黑端收窄比）在 `AGENTS` §一.5，本模块一并给。
* 缺 `p25/p75` 的键（样本 < 20）=> `nan`，判定自动跳过。
"""
from __future__ import annotations

import math
import os
import sys
from math import comb

import numpy as np

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402
import _lib.bands as _B                                                       # noqa: E402

# ---- 界表：**唯一真源 = `_lib\bands.json`**（新口径，10-18 重标）
#   ★ 这里**不再写死任何数字**：`AGENTS_BOUNDS` / `R1_BAND` / `NEUTRAL_SIG` /
#     `NEUTRAL_CONSENSUS` 全部由 `bands.py` 派生 ⇒ 别处也不许再抄一遍。
#   ★ A 组（A1/A1白端/R1/A4中·暗/A4中·亮/A5/A6/A7）= 并集界（逐位沿用 AGENTS 写死值）；
#     **A8 已按新颊口径重标** `[2.02,8.35] -> [2.325,9.575]`。
AGENTS_BOUNDS = _B.agents_bounds()
R1_BAND = _B.band('R1', use_own=False)
BOUNDS_TITLE = {'A1_blk': 'A1 黑端', 'A1_wht': 'A1 白端', 'A4_d': 'A4 中/暗', 'A4_b': 'A4 中/亮',
                'A5': 'A5 暗端', 'A6': 'A6 暖/冷', 'A7': 'A7 蓝/橙', 'A8': 'A8 颊-臂',
                'R1': 'R1 收窄比', 'B1': 'B1 颊C*', 'B2': 'B2 颊色相', 'B2p': 'B2′ 颊b*−a*',
                'B3': 'B3 颊/画面'}
# `AGENTS` §一.2 三位共识中位（新口径；只当叙事中心，不许当验收）
NEUTRAL_CONSENSUS = _B.neutral_consensus()
# B 组签名 = 三链 p50（新口径；B2 已退役，用 B2′）
NEUTRAL_SIG = _B.neutral_sig()
NA_BANDS = ('0_20', '20_40', '40_60', '60_80', '80_100')
GROUPS = _lib.CHAINS
MNAME = _lib.MNAME
# m8 里直接带读数、不用复算的键
M8_KEYS = ('A1_blk', 'A1_wht', 'R1', 'A4_d', 'A4_b', 'A5', 'A6', 'A7', 'B1', 'B2', 'B3', 'A8')
# "两套界 + 三样" 的必备字段
REQUIRED = ('band_union', 'band_own', 'r_abs', 'r_pair', 'perm_p', 'boot', 'sign', 'verdict_own',
            'verdict_union')


# ---------------------------------------------------------------- 基础统计

def fin(v):
    r"""过滤 None / nan / 非数。@returns {list[float]}"""
    return [float(x) for x in v if x is not None and isinstance(x, (int, float))
            and np.isfinite(x)]


def med(v):
    r"""中位（空 => nan）。@returns {float}"""
    v = fin(v)
    return float(np.median(v)) if v else float('nan')


def iqr(v):
    r"""P75-P25（样本 <= 3 => nan）。@returns {float}"""
    v = fin(v)
    return float(np.percentile(v, 75) - np.percentile(v, 25)) if len(v) > 3 else float('nan')


def p25p75(v, minn=20):
    r"""逐图 [P25,P75]（样本 < minn => (nan,nan)）。@returns {tuple}"""
    v = fin(v)
    if len(v) < minn:
        return float('nan'), float('nan')
    return float(np.percentile(v, 25)), float(np.percentile(v, 75))


def bounds_from_series(vals, minn=20):
    r"""一组逐图读数 -> 界。@returns {dict} {p25,p75,iqr,n,med}"""
    v = fin(vals)
    if len(v) < minn:
        return dict(p25=float('nan'), p75=float('nan'), iqr=float('nan'), n=len(v),
                    med=med(v))
    return dict(p25=float(np.percentile(v, 25)), p75=float(np.percentile(v, 75)),
                iqr=iqr(v), n=len(v), med=med(v))


# ---------------------------------------------------------------- 尺子

def resolution(iqr_master, iqr_ours, n):
    r"""分辨力 R = 2*sqrt((大师逐图IQR/v12)^2 + (我们逐图IQR/vn)^2)。

    ★ 判"缺陷"级（严）：**离界距离 > R 才许叫缺陷**。
    @returns {float} nan（输入不足时）
    """
    if not (np.isfinite(iqr_master) and np.isfinite(iqr_ours)) or not n or n < 2:
        return float('nan')
    return 2.0 * math.sqrt((iqr_master / math.sqrt(12.0)) ** 2
                           + (iqr_ours / math.sqrt(float(n))) ** 2)


def resolution_pair(iqr_master, iqr_delta, n):
    r"""配对分辨力 R_pair = 2*sqrt((大师逐图IQR/v12)^2 + (**配对Δ**逐图IQR/vn)^2)。

    ★ 判"两档之差能不能辨"。第一项仍是**大师的**（风格靶本身的宽度，配对消不掉）。
    @returns {float}
    """
    if not (np.isfinite(iqr_master) and np.isfinite(iqr_delta)) or not n or n < 2:
        return float('nan')
    return 2.0 * math.sqrt((iqr_master / math.sqrt(12.0)) ** 2
                           + (iqr_delta / math.sqrt(float(n))) ** 2)


def outside(m, lo, hi):
    r"""离界距离（带内 = 0，带外 = 到最近边的距离）。@returns {float}"""
    if not (np.isfinite(m) and np.isfinite(lo) and np.isfinite(hi)):
        return float('nan')
    return (lo - m) if m < lo else ((m - hi) if m > hi else 0.0)


def dist_out(v, lo, hi):
    r"""@returns {tuple} (离界距离, 带内朝向)；带内时第二项 = 到最近边的距离（正），带外为负。"""
    if not np.isfinite(v):
        return float('nan'), float('nan')
    if v < lo:
        return lo - v, -(lo - v)
    if v > hi:
        return v - hi, -(v - hi)
    return 0.0, min(v - lo, hi - v)


# ---------------------------------------------------------------- 检验

def perm_p(x, y, n=4000, seed=20251013):
    r"""两样本**中位差**的置换零分布（双侧，向量化）。

    问的是「**两组中位在统计上是否不同**」（宽口径 = "有系统差异"级）。
    @returns {float} p（含 +1 修正，下限 = 1/(n+1)）
    """
    x = np.asarray(fin(x), float)
    y = np.asarray(fin(y), float)
    if x.size < 3 or y.size < 3:
        return float('nan')
    rng = np.random.default_rng(seed)
    obs = abs(float(np.median(x) - np.median(y)))
    allv = np.concatenate([x, y])
    nx = x.size
    idx = rng.integers(0, allv.size, size=(n, allv.size))
    perm = allv[idx]
    dif = np.abs(np.median(perm[:, :nx], axis=1) - np.median(perm[:, nx:], axis=1))
    return float((int((dif >= obs - 1e-12).sum()) + 1.0) / (n + 1.0))


def sign_test(d):
    r"""配对 Δ 的**同号张数** + 符号检验 p（双侧精确二项）。

    @param d {list} 逐图 Δ
    @returns {dict} {k, n, p}；k = max(正号数, 负号数)，n = 非零 Δ 张数
    """
    d = [x for x in fin(d) if abs(x) > 0]
    n = len(d)
    if n == 0:
        return dict(k=0, n=0, p=1.0)
    k = sum(1 for x in d if x > 0)
    k = max(k, n - k)
    p = min(1.0, 2.0 * sum(comb(n, i) for i in range(k, n + 1)) / float(2 ** n))
    return dict(k=int(k), n=int(n), p=float(p))


def median_t(dm, iqr_master, n_master, iqr_ours, n_ours):
    r"""中位检验 t（IQR 转 SE 的近似，口径同 `中性轴口径_1013\c3_verdict.py`）。

    SE = (IQR/1.349)*1.253/sqrt(n)；t = |Δ中位| / sqrt(SE_m^2 + SE_o^2)。
    @returns {float}
    """
    if not (np.isfinite(dm) and np.isfinite(iqr_master) and np.isfinite(iqr_ours)):
        return float('nan')
    se_m = (iqr_master / 1.349) * 1.253 / math.sqrt(max(n_master, 1))
    se_o = (iqr_ours / 1.349) * 1.253 / math.sqrt(max(n_ours, 1))
    den = math.sqrt(se_m ** 2 + se_o ** 2)
    return float(abs(dm) / den) if den > 0 else float('nan')


def boot_ci(v, n=4000, seed=20251013):
    r"""中位的 bootstrap 95% 区间（有放回重采样样本）。

    @returns {tuple} (中位, lo95, hi95)
    """
    v = np.asarray(fin(v), float)
    if v.size < 3:
        return (float(np.median(v)) if v.size else float('nan'), float('nan'), float('nan'))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, v.size, size=(n, v.size))
    meds = np.median(v[idx], axis=1)
    return float(np.median(v)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def boot_ci_paired(ab, ac, n=4000, seed=20251013):
    r"""配对 Δ = cand - base 的中位 + 95% bootstrap 区间（**重采样图**，下标同步）。

    @returns {tuple} (Δ中位, lo95, hi95)
    """
    d = np.asarray(fin(ac), float) - np.asarray(fin(ab), float)
    m = d.size
    if m < 3:
        return (float(np.median(d)) if m else float('nan'), float('nan'), float('nan'))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, m, size=(n, m))
    meds = np.median(d[idx], axis=1)
    return float(np.median(d)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def boot_dist_diff(band, ab, ac, n=4000, seed=20251013):
    r"""到界距离变化 `d(cand) - d(base)` 的中位 + 95% 区间。**< 0 = 靠近界**。

    @returns {tuple} (Δ中位, lo95, hi95)
    """
    lo, hi = band
    ab = np.asarray(fin(ab), float)
    ac = np.asarray(fin(ac), float)
    m = ab.size
    if m < 3:
        return float('nan'), float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, m, size=(n, m))
    d0 = np.array([outside(float(np.median(ab[i])), lo, hi) for i in idx])
    d1 = np.array([outside(float(np.median(ac[i])), lo, hi) for i in idx])
    dd = d1 - d0
    return float(np.median(dd)), float(np.percentile(dd, 2.5)), float(np.percentile(dd, 97.5))


def jd_band(band, v0, v1, boot_dd):
    r"""带内/带外 的合法性判定（F1 界形态）。

    @param band {tuple} (lo, hi)
    @param v0,v1 {float} 现值 / 候选（整批中位）
    @param boot_dd {tuple} `boot_dist_diff` 的结果
    @returns {tuple} (判定字符串, d0, d1)
    """
    lo, hi = band
    d0, d1 = outside(v0, lo, hi), outside(v1, lo, hi)
    i0, i1 = (d0 == 0), (d1 == 0)
    dm, dl, dh = boot_dd
    if i0 and i1:
        return '都在带内', d0, d1
    if not i0 and i1:
        if dl < 0:
            return '★**改善**（走进界）', d0, d1
        return '名义进界 · 区间含 0 -> **判不了**', d0, d1
    if i0 and not i1:
        if dh > 0:
            return '★**弄坏**（走出界）', d0, d1
        return '名义出界 · 区间含 0 -> **判不了**', d0, d1
    if dh < 0:
        return '**改善**（离界 %.3f->%.3f）' % (d0, d1), d0, d1
    if dl > 0:
        return '**变差**（离界 %.3f->%.3f）' % (d0, d1), d0, d1
    return '%.3f->%.3f 区间含 0 -> **判不了**' % (d0, d1), d0, d1


# ---------------------------------------------------------------- 读数 -> 量

def reading_value(reading, key):
    r"""把 `measure.measure()` 的**一张图**读数取成一个标量（键语义 = 全项目统一）。

    键：`A1_blk` `A1_wht` `R1` `A4_d` `A4_b` `A5` `A6` `A7` `A8` `B1` `B2` `B2p` `B3`
        `imgL` `imgC` `NAa<档>` `NAb<档>` `NAC<档>`（档 = `0_20` … `80_100`）
    @returns {float} 取不到 => nan
    """
    # 实现已收敛到 `_lib.bands.value`（**同一件事只有一份**；这里保留旧名字）
    return _B.value(reading, key)


def series_from_readings(doc, arm, key):
    r"""`{stem: {'arms': {arm: reading}}}` -> 逐图序列（自动跳过缺的/取不到的）。

    @returns {list[float]}
    """
    out = []
    for stem in sorted(doc):
        a = (doc[stem].get('arms') or {}).get(arm)
        if not a:
            continue
        reading = a.get('reading') if isinstance(a, dict) and 'reading' in a else a
        out.append(reading_value(reading, key))
    return fin(out)


def master_series(m8, key):
    r"""大师逐图序列（`_m8_master_dist.json`）。@returns {dict} {group: [v]}"""
    if key in M8_KEYS:
        return {g: fin([r.get(key) for r in m8.get(g, [])]) for g in GROUPS}
    if key.startswith('NAC'):
        i = NA_BANDS.index(key[3:])
        return {g: fin([(r.get('NA_C') or [None] * 5)[i] for r in m8.get(g, [])]) for g in GROUPS}
    raise KeyError('m8 里没有这个键：%s（NAa/NAb 请用 master_series_feat）' % key)


def master_series_feat(feat, key):
    r"""中性轴 a*/b* 的逐图序列（`datapack_1011\\feat.json`，每张图自带 `neutral_axis`）。

    @param key {str} `NAa<档>` / `NAb<档>`
    @returns {dict} {group: [v]}
    """
    fld, b = ('a' if key[2] == 'a' else 'b'), key[3:]
    out = {g: [] for g in GROUPS}
    for r in feat.values():
        g = r.get('group')
        if g not in out:
            continue
        v = ((r.get('neutral_axis') or {}).get(b) or {}).get(fld)
        if isinstance(v, (int, float)) and np.isfinite(v):
            out[g].append(float(v))
    return out


def master_series_any(m8, key, feat=None):
    r"""统一入口：先查 m8，NAa/NAb 走 feat。@returns {dict} {group: [v]}"""
    if key.startswith('NAa') or key.startswith('NAb'):
        if feat is None:
            import json
            import io
            feat = json.load(io.open(_lib.FEAT, encoding='utf-8'))
        return master_series_feat(feat, key)
    return master_series(m8, key)


# ---------------------------------------------------------------- 界

def agents_bounds():
    r"""`AGENTS` §一.1 的 8 条（A 组并集 + A8 新值；+ §一.5 的 R1）。@returns {dict}"""
    d = dict(AGENTS_BOUNDS)
    d['R1'] = R1_BAND
    return d


def _canon(key, chain, pick_own):
    r"""真源条目 -> 与 `bounds_from_series` 同键的字典（多给 p50/p95）。@returns {dict}"""
    b = _B.own(key, chain) if pick_own else _B.union(key)
    return dict(p25=b['p25'], p75=b['p75'], iqr=b['iqr'], n=b['n'], med=b['p50'],
                p50=b['p50'], p95=b['p95'])


def bounds_union(m8, key, feat=None):
    r"""**三位并集** [P25,P75]（主判据 = `AGENTS` §一.1）。@returns {dict}

    ★ 键在真源里 => **直接读 `_lib\bands.json`**（唯一真源，不再从 m8 现算）；
      不在 => 回落到"从给的 m8 现算"（兼容旧专题数据）。
    """
    if key in _B.KEYS:
        return _canon(key, None, False)
    ms = master_series_any(m8, key, feat)
    return bounds_from_series([x for g in GROUPS for x in ms[g]], minn=50)


def bounds_own(m8, key, chain, feat=None):
    r"""**该链自己大师**的逐图 [P25,P75]（签名分家的链用这套）。

    ★ 成片语料只有鹿井 / 滨田；**増田无成片语料、也无预设** ⇒ `own.masuda` 不存在。
      这里返回 `n=0` 的空界（**不抛错**，免得打断遍历三条链的旧脚本）；判定侧会标"不可判"。
    @returns {dict}
    """
    if key in _B.KEYS:
        try:
            return _canon(key, chain, True)
        except KeyError:
            nan = float('nan')
            return dict(p25=nan, p75=nan, iqr=nan, n=0, med=nan, p50=nan, p95=nan,
                        missing_chain=chain)
    ms = master_series_any(m8, key, feat)
    return bounds_from_series(ms[chain], minn=20)


def bounds_both(m8, key, chain, feat=None):
    r"""★ 两套界（本模块**强制**两套都算）。@returns {dict} {'union':…, 'own':…}"""
    return dict(union=bounds_union(m8, key, feat), own=bounds_own(m8, key, chain, feat))


def agents_bounds_check(m8=None, rtol_digits=3):
    r"""★ 自检：用**大师原始读数复算** 8 条界，逐条比对真源写死的值。

    "逐位一致" = **在写死的精度上四舍五入后相同**（A5 两位，其余三位）。
    ★ 复算走 `bands.recompute()`（`重标_1018\_master_all.json`，新颊口径）；
      旧 `_m8_master_dist.json` 是圆口径 ⇒ **A8 不能拿它验**（这正是重标的原因）。
    @param m8 {dict|None} 保留旧签名；真源键一律走新口径复算，不看它
    @returns {list[dict]} 每行 {key, calc, written, d_lo, d_hi, n, digits_ok}
    """
    rows = []
    for k, (wlo, whi) in AGENTS_BOUNDS.items():
        r = _B.recompute(k)['union']
        clo, chi = r['p25'], r['p75']

        def _r(x, w):
            return round(x, len(str(w).split('.')[-1]))
        ok = (_r(clo, wlo) == wlo) and (_r(chi, whi) == whi)
        rows.append(dict(key=k, calc=(clo, chi), written=(wlo, whi), n=r['n'],
                         d_lo=clo - wlo, d_hi=chi - whi, digits_ok=bool(ok),
                         maxabs=max(abs(clo - wlo), abs(chi - whi))))
    return rows


def neutral_bounds(feat=None, m8=None):
    r"""中性轴五档 × (a,b,C) 的**两套界**（真源 = `_lib\bands.json`）。

    ★ `feat` / `m8` 只为兼容旧签名保留 —— 真源里已经含 15 个 NA 键的
      并集界 + **两位**（鹿井/滨田）本链界，**不再从旧 feat/m8 现算**（那是另一口径）。
    @returns {dict} {'union': {'a_0_20': {...}, ...}, 'own': {chain: {...}}}
    """
    out = dict(union={}, own={g: {} for g in _B.GROUPS})
    for f in ('a', 'b', 'C'):
        for b in NA_BANDS:
            k = 'NA%s%s' % (f, b)
            key = '%s_%s' % (f, b)
            out['union'][key] = _canon(k, None, False)
            for g in _B.GROUPS:
                out['own'][g][key] = _canon(k, g, True)
    return out


# ---------------------------------------------------------------- 判定

def judge(key, ours_base, ours_cand, m8, chain, use_own=None, feat=None,
          boot_n=4000, perm_n=4000, seed=20251013):
    r"""★ **一行全报**的判定：两套界 + 分辨力 R/R_pair + 中位检验 p + bootstrap + 配对统计。

    @param key {str} 见 `reading_value`
    @param ours_base {list} 现值**逐图**读数（配对的左臂）
    @param ours_cand {list} 候选**逐图**读数（配对的右臂，与左臂**同图同序**）
    @param m8 {dict} `_m8_master_dist.json`
    @param chain {str} `kurai` / `masuda` / `hamada`
    @param use_own {bool|None} 主判据用哪套界；None = 自动（A 组并集 / B 组本链）
    @returns {dict} 见模块头（含 `band_union` `band_own` `r_abs` `r_pair` `perm_p`
                    `boot` `sign` `median_p` `verdict_own` `verdict_union`）
    """
    if use_own is None:
        # A 组共同规律（A1/A1白端/R1/A4/A5/A6/A7）用并集界；
        # B 组个人签名 + A8（吃颊）+ 中性轴五档 用本链界（AGENTS §一.1；规则住在 bands.json）
        use_own = _B.use_own(key) if key in _B.KEYS else str(key).startswith('B')
    ab, ac = fin(ours_base), fin(ours_cand)
    bu = bounds_union(m8, key, feat)
    bo = bounds_own(m8, key, chain, feat)
    own_missing = (bo.get('n', 0) == 0)
    if use_own and own_missing:
        use_own = False          # 该链没有成片语料 => 用并集界兜底，判定里注明
    main = bo if use_own else bu
    lo, hi = main['p25'], main['p75']
    v0, v1 = med(ab), med(ac)
    n = min(len(ab), len(ac))
    d = [b - a for a, b in zip(ab, ac)]
    iq_o, iq_d = iqr(ac), iqr(d)
    r_abs = resolution(main['iqr'], iq_o, n)
    r_pair = resolution_pair(main['iqr'], iq_d, n)
    r_abs_u = resolution(bu['iqr'], iq_o, n)
    r_pair_u = resolution_pair(bu['iqr'], iq_d, n)
    bd = boot_ci_paired(ab, ac, boot_n, seed)
    bdc = boot_dist_diff((lo, hi), ab, ac, boot_n, seed)
    bdu = boot_dist_diff((bu['p25'], bu['p75']), ab, ac, boot_n, seed)
    jo, d0, d1 = jd_band((lo, hi), v0, v1, bdc)
    ju, u0, u1 = jd_band((bu['p25'], bu['p75']), v0, v1, bdu)
    # 单片置换（两组：图的左臂 vs 右臂）—— 分布无关的"中位是否不同"
    p_perm = perm_p(ab, ac, perm_n, seed)
    sg = sign_test(d)
    t = median_t(med(d), main['iqr'], main['n'], iq_o, n)
    row = dict(
        key=key, chain=chain, title=BOUNDS_TITLE.get(key, key), used_own=bool(use_own),
        own_missing=bool(own_missing),
        band_union=(bu['p25'], bu['p75']), band_own=(bo['p25'], bo['p75']),
        n_master_union=bu['n'], n_master_own=bo['n'],
        iqr_master_union=bu['iqr'], iqr_master_own=bo['iqr'],
        base=v0, cand=v1, dm=med(d), n=n,
        iqr_our_cand=iq_o, iqr_pair=iq_d,
        r_abs=r_abs, r_pair=r_pair, r_abs_union=r_abs_u, r_pair_union=r_pair_u,
        boot=bd, boot_dd=bdc, boot_dd_union=bdu,
        perm_p=p_perm, median_p=p_perm, t=t, sign=sg,
        dist0=(d0 if use_own else u0), dist1=(d1 if use_own else u1),
        dist0_own=d0, dist1_own=d1, dist0_union=u0, dist1_union=u1,
        verdict_own=jo, verdict_union=ju,
        verdict=(jo if use_own else ju),
    )
    # 分辨力 vs 离界距离 => "缺陷"级 / "判不了"
    dd = row['dist1'] if use_own else row['dist1_union']
    rr = row['r_abs'] if use_own else row['r_abs_union']
    if not np.isfinite(dd) or dd == 0:
        row['grade'] = '在带内'
    elif np.isfinite(rr) and dd > rr:
        row['grade'] = '★真越界（离界 %.3f > 分辨力 %.3f）' % (dd, rr)
    else:
        row['grade'] = '判不了（离界 %.3f <= 分辨力 %.3f）' % (dd, rr)
    if own_missing:
        row['grade'] += '（该链无成片语料，用并集界兜底）'
    # 配对口径：这个"变化"本身可辨吗
    row['pair_read'] = ('**可辨**' if (np.isfinite(r_pair) and abs(row['dm']) > r_pair
                                     and (sg['p'] <= 0.05 or t > 2))
                        else ('显著但<尺子' if (sg['p'] <= 0.05 or t > 2) else '噪声内'))
    assert_full_report(row)
    return row


def assert_full_report(row):
    r"""★ 强制「**两套界都列 + 三样都报**」（`AGENTS` §一.1）。

    缺一套界 / 缺分辨力 / 缺中位检验 / 缺 bootstrap => 直接 `AssertionError`。
    @returns {None}
    """
    for k in REQUIRED:
        if k not in row:
            raise AssertionError('判据报告缺字段 %r（AGENTS §一.1：两套界都要列 + 三样都要报）' % k)
    for k in ('band_union', 'band_own'):
        if not (len(row[k]) == 2):
            raise AssertionError('界 %r 不成对' % k)
    for k in ('r_abs', 'r_pair'):
        if row[k] is None:
            raise AssertionError('缺分辨力 %r' % k)
    if row['boot'] is None or len(row['boot']) != 3:
        raise AssertionError('缺 bootstrap 区间')
    if row['sign'] is None or 'k' not in row['sign']:
        raise AssertionError('缺配对同号张数')


def report_lines(rows, keys=('key', 'band_union', 'band_own', 'base', 'cand', 'dm',
                             'r_abs', 'r_pair', 'perm_p', 'boot', 'sign', 'grade')):
    r"""把 `judge()` 的行渲染成表格文本（两套界 + 三样都列）。

    @returns {list[str]}
    """
    out = ['  %-9s %-20s %-20s %10s %10s %+10s %8s %8s %8s %-22s %-6s %s'
           % ('量', '并集界', '本链界', '现值', '候选', '配对D中位', 'R', 'R_pair', '置换p',
              'boot95[lo,hi]', '同号', '判定'),
           '  ' + '-' * 168]
    for r in rows:
        bu, bo = r['band_union'], r['band_own']
        bs = '[%.3f,%.3f]' % (bu[0], bu[1])
        os_ = '[%.3f,%.3f]' % (bo[0], bo[1])
        bb = '[%+.3f,%+.3f]' % (r['boot'][1], r['boot'][2])
        out.append('  %-9s %-20s %-20s %10.4f %10.4f %+10.4f %8.4f %8.4f %8.4f %-22s %d/%-3d %s'
                   % (r['title'], bs, os_, r['base'], r['cand'], r['dm'],
                      r['r_abs'], r['r_pair'], r['perm_p'], bb,
                      r['sign']['k'], r['sign']['n'], r['grade']))
    return out
