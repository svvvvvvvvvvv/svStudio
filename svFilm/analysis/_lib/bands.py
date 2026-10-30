# -*- coding: utf-8 -*-
r"""_lib / bands.py —— **靶带的唯一真源（新口径）**：界表读取 / 复算 / 落盘。

```
数据 = `_lib\bands.json`（**唯一真源**；由 `重标_1018\gen_bands.py` 从大师逐图读数生成）
        ↑ 本模块只读它；`verdict.py` 的 `AGENTS_BOUNDS` / `R1_BAND` / `NEUTRAL_SIG` /
          `NEUTRAL_CONSENSUS` 全部由它派生 ⇒ **别处不许再抄一遍数字**
```

## 口径（不可违反）
```
· 界 = 大师**逐图** [P25,P75]（不是"组中位的范围"）
· A1 黑端 / A1 白端 / R1 / A4 中·暗 / A4 中·亮 / A5 / A6 / A7  -> 并集界（A 组共同规律）
· A8 / B1 颊 C* / B2′ 颊 b*−a* / B3 颊/画面 / 中性轴五档             -> **本链界**（并集并列）
· 吃颊的量只收 `cheek_n >= 300 px`（900 域）的图
```

## 主函数（签名）
```
load()                      -> dict   整个 bands.json（按 mtime 缓存）
entry(key)                  -> dict   {title,use_own,union:{p25,p75,p50,p95,iqr,n},own:{链:…}}
union(key) / own(key,chain) -> dict   一套界的字典
band(key, chain=None, use_own=None) -> (lo,hi)
use_own(key)                -> bool   这一格该用本链界吗（A8/B*/NA*）
value(reading, key)         -> float  读数 -> 标量（**全项目唯一的键语义**）
recompute(key)              -> dict   从大师原始读数**复算**一套界（自检用，不是第二真源）
check(tol=1e-9)             -> list   真源 vs 复算逐条比（FAIL=0 才算通过）
master_rows()               -> dict   `_master_成片.json` 的 {链: [逐图读数]}
agents_bounds()             -> dict   {键: (lo,hi)} = `AGENTS` 的 8 条（A8 已是新值）
neutral_consensus()         -> dict   {a/b/C: 五档**两位**共识中位}（鹿井/滨田；只当叙事中心）
neutral_sig()               -> dict   {B1/B2p/B3: {链: p50}}（只有鹿井/滨田）
add_deprecation(...)        -> None   旧名字转发时的**一次性** `DeprecationWarning`
```

用法：
```
python -m _lib.bands show              # 打三张表（A 组 / 皮肤 / 中性轴）
python -m _lib.bands check             # 真源 <-> 大师原始读数复算（逐条）
python -m _lib.bands emit <out.json>   # 把真源落成专题 JSON（**由脚本生成，不手抄**）
```
"""
from __future__ import annotations

import io
import json
import os
import sys

import numpy as np

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402

BANDS_JSON = os.path.join(_lib.ROOT, 'bands.json')
MASTER_ALL = os.path.join(_lib.PHOTO, '重标_1018', '_master_成片.json')
# ★ 成片语料跟着图走：每份语料目录里一个 `_测量.json`（逐图 raw + derived + summary）。
#   成片只有两位：鹿井 52 / 滨田 22。増田无成片语料 ⇒ 并集 = 鹿井+滨田；増田无本链界。
#   xhs 也有 `_测量.json`，但它是**独立语料**，不进这个并集。
CORPUS_DIRS = {'kurai': os.path.join(r'E:\ds\大师成片', '鹿井'),
               'hamada': os.path.join(r'E:\ds\大师成片', '滨田')}
XHS_DIR = os.path.join(r'E:\ds\大师成片', 'xhs', '_单图')
MEASURE_NAME = '_测量.json'
MEASURE_PATHS = {g: os.path.join(CORPUS_DIRS[g], MEASURE_NAME) for g in ('kurai', 'hamada')}
XHS_MEASURE = os.path.join(XHS_DIR, MEASURE_NAME)
# ★ 成片语料只有两位：鹿井 52 / 滨田 22。**増田无成片语料**（大师作品\\増田彩来 是抓取库，
#   不作靶）⇒ 并集 = 鹿井+滨田；増田没有本链界。
GROUPS = ('kurai', 'hamada')
NA_BANDS = ('0_20', '20_40', '40_60', '60_80', '80_100')
A_KEYS = ('A1_blk', 'A1_wht', 'R1', 'A4_d', 'A4_b', 'A5', 'A6', 'A7')
SKIN_KEYS = ('A8', 'B1', 'B2p', 'B3')
NA_KEYS = tuple('NA%s%s' % (f, b) for f in ('a', 'b', 'C') for b in NA_BANDS)
KEYS = A_KEYS + SKIN_KEYS + NA_KEYS
CHEEK_KEYS = ('A8', 'B1', 'B2p', 'B3')       # 吃颊掩膜 => 要过 >=300 px 门
MIN_CHEEK = 300
_CACHE = {'t': None, 'doc': None, 'rows': None}


# ---------------------------------------------------------------- 读真源

def load(path=None):
    r"""读 `bands.json`（默认真源；按 mtime 缓存）。@returns {dict}"""
    p = path or BANDS_JSON
    t = os.stat(p).st_mtime_ns
    if _CACHE['t'] != t or _CACHE['doc'] is None:
        _CACHE['doc'] = json.load(io.open(p, encoding='utf-8'))
        _CACHE['t'] = t
    return _CACHE['doc']


def entry(key):
    r"""一个量的真源条目。@returns {dict}"""
    d = load()['keys']
    if key not in d:
        raise KeyError('靶带里没有这个量：%r（有：%s）' % (key, ', '.join(KEYS)))
    return d[key]


def union(key):
    r"""并集界（三位小数 + p50/p95/iqr/n；并集 = 鹿井+滨田）。@returns {dict}"""
    return dict(entry(key)['union'])


def own(key, chain):
    r"""本链界。@returns {dict}"""
    e = entry(key)
    if chain not in e['own']:
        raise KeyError('链名 %r 不在 %r' % (chain, tuple(e['own'])))
    return dict(e['own'][chain])


def use_own(key):
    r"""这一格该用**本链界**吗（A8/B*/NA* = 吃颊或已证分家）。@returns {bool}"""
    return bool(entry(key)['use_own'])


def band(key, chain=None, use_own=None):
    r"""取一套界。`use_own=None` => 按真源规则（A8/B*/NA* 用本链界）。@returns {tuple}"""
    pick = globals()['use_own'](key) if use_own is None else bool(use_own)
    if pick:
        if chain is None:
            raise ValueError('%s 用本链界，必须给 chain' % key)
        b = own(key, chain)
    else:
        b = union(key)
    return float(b['p25']), float(b['p75'])


def agents_bounds():
    r"""`AGENTS §一.1` 的 8 条并集界（A8 已按新口径重标）。@returns {dict} {键: (lo,hi)}"""
    return {k: band(k, use_own=False) for k in A_KEYS + ('A8',)}


def neutral_consensus():
    r"""两位共识中位（鹿井/滨田；**只当叙事中心，不许当验收**）。@returns {dict}"""
    c = load()['neutral_consensus']
    return {k: tuple(float(x) for x in c[k]) for k in ('a', 'b', 'C')}


def neutral_sig():
    r"""B 组签名 = 各链 p50（只有鹿井/滨田）。@returns {dict} {B1/B2p/B3: {链: p50}}"""
    out = {}
    for k in ('B1', 'B2p', 'B3'):
        e = entry(k)
        out[k] = {g: float(e['own'][g]['p50']) for g in GROUPS}
    return out


def provenance(key):
    r"""报告用：{n_union, n_own, caliber, use_own, title}。@returns {dict}"""
    e = entry(key)
    m = load()['_meta']
    return dict(title=e['title'], use_own=e['use_own'], caliber=m['caliber'],
                n_union=e['union']['n'],
                n_own={g: e['own'][g]['n'] for g in GROUPS},
                master=m['master'])


# ---------------------------------------------------------------- 读数 -> 量

def value(reading, key):
    r"""★ **全项目唯一的键语义**：`measure.measure()` 的一张图读数 -> 标量。

    键：`A1_blk` `A1_wht` `R1` `A4_d` `A4_b` `A5` `A6` `A7` `A8` `B1` `B2` `B2p` `B3`
        `imgL` `imgC` `NAa<档>` `NAb<档>` `NAC<档>`（档 = `0_20` … `80_100`）
    @returns {float} 取不到 => nan
    """
    if not reading:
        return float('nan')
    na = reading.get('neutral_axis') or {}
    c5 = [na.get(b, {}).get('C', float('nan')) for b in NA_BANDS]
    a5 = [na.get(b, {}).get('a', float('nan')) for b in NA_BANDS]
    b5 = [na.get(b, {}).get('b', float('nan')) for b in NA_BANDS]
    d1 = reading.get('D1_midC') or [float('nan')] * 3
    rel = reading.get('rel') or {}
    ck, am = reading.get('cheek'), reading.get('arm')
    img = reading.get('img') or {}
    mid3 = np.nanmax(c5[1:4]) if np.any(np.isfinite(c5[1:4])) else float('nan')
    if key == 'A1_blk':
        return c5[0] / mid3 if mid3 else float('nan')
    if key == 'A1_wht':
        return c5[4] / mid3 if mid3 else float('nan')
    if key == 'R1':
        return c5[0] / c5[1] if c5[1] else float('nan')
    if key == 'A4_d':
        return d1[1] / d1[0] if d1[0] else float('nan')
    if key == 'A4_b':
        return d1[1] / d1[2] if d1[2] else float('nan')
    if key == 'A5':
        return d1[0]
    if key == 'A6':
        return rel.get('warmC_over_coolC', float('nan'))
    if key == 'A7':
        return rel.get('blue_over_orange', float('nan'))
    if key == 'A8':
        return abs(ck['C'] - am['C']) if (ck and am) else float('nan')
    if key == 'B1':
        return ck['C'] if ck else float('nan')
    if key == 'B2':
        return ck['h'] if ck else float('nan')          # 已退役（`AGENTS §一.1 B2`）
    if key == 'B2p':
        return (ck['b'] - ck['a']) if ck else float('nan')
    if key == 'B3':
        return (ck['C'] / img['C']) if (ck and img.get('C')) else float('nan')
    if key == 'imgL':
        return img.get('L', float('nan'))
    if key == 'imgC':
        return img.get('C', float('nan'))
    if key.startswith('NAC'):
        return c5[NA_BANDS.index(key[3:])]
    if key.startswith('NAa'):
        return a5[NA_BANDS.index(key[3:])]
    if key.startswith('NAb'):
        return b5[NA_BANDS.index(key[3:])]
    raise KeyError(key)


# ---------------------------------------------------------------- 复算（自检，不是第二真源）

def measure_path(corpus):
    r"""语料名 -> `_测量.json` 路径（`xhs` 是独立语料）。@returns {str}"""
    return XHS_MEASURE if corpus == 'xhs' else MEASURE_PATHS[corpus]


def load_measure(corpus):
    r"""读一份 `_测量.json`；**口径版本不一致 => 直接抛错**（宁可停下，不混两代口径）。@returns {dict}"""
    p = measure_path(corpus)
    if not os.path.isfile(p):
        raise FileNotFoundError('缺 %s（先在语料目录跑 `重标_1018\\emit_measure.py`）' % p)
    doc = json.load(io.open(p, encoding='utf-8'))
    ver = (doc.get('_meta') or {}).get('measure_version')
    if ver != getattr(_lib, 'MEASURE_VERSION', None):
        raise RuntimeError('`%s` 的口径版本 = %r，而当前 `_lib.MEASURE_VERSION` = %r '
                           '⇒ 拒绝出界（先重跑 emit_measure.py）'
                           % (p, ver, getattr(_lib, 'MEASURE_VERSION', None)))
    return doc


def master_rows(corpus=None):
    r"""从语料目录的 `_测量.json` 读 {链: [逐图]}（按各文件 mtime 缓存）。

    ★ `_测量.json` 的 `per_image[*].raw` 是 `measure.read_image()['reading']` 的**完整原样**，
      所以这里能还原出 `bands.value()` 需要的一切。
    @returns {dict}
    """
    groups = (corpus,) if corpus else GROUPS
    stamp = tuple(os.stat(measure_path(g)).st_mtime_ns for g in groups)
    c = _CACHE.get('rows')
    if not c or c.get('t') != stamp or c.get('groups') != groups:
        rows = {g: [] for g in groups}
        for g in groups:
            doc = load_measure(g)
            for name, e in (doc.get('per_image') or {}).items():
                d = e.get('derived') or {}
                rows[g].append(dict(
                    path=name,
                    cheek_n=int((d.get('cheek') or {}).get('n') or 0),
                    arm_n=int((d.get('arm') or {}).get('n') or 0),
                    reading=e.get('raw') or {}))
        c = dict(t=stamp, groups=groups, rows=rows)
        _CACHE['rows'] = c
    return c['rows']


def series(key, rows=None):
    r"""逐图序列 {链: [v]}（吃颊的量先过 >=300 px 门）。@returns {dict}"""
    rows = rows if rows is not None else master_rows()
    need = key in CHEEK_KEYS
    out = {g: [] for g in GROUPS}
    for g in GROUPS:
        for r in rows.get(g) or []:
            if need and int(r.get('cheek_n') or 0) < MIN_CHEEK:
                continue
            v = value(r.get('reading'), key)
            if isinstance(v, (int, float)) and np.isfinite(v):
                out[g].append(float(v))
    return out


def _bstat(v):
    r"""逐图 [P25,P75]/p50/p95/iqr/n（与 `gen_bands.py` / `s_score.py` 同一算式）。"""
    v = np.asarray(v, float)
    if v.size < 1:
        return dict(p25=float('nan'), p75=float('nan'), p50=float('nan'),
                    p95=float('nan'), iqr=float('nan'), n=0)
    q = np.percentile(v, [25, 50, 75, 95])
    return dict(p25=float(q[0]), p75=float(q[2]), p50=float(q[1]), p95=float(q[3]),
                iqr=float(q[2] - q[0]), n=int(v.size))


def recompute(key):
    r"""从大师原始读数**复算**一套界（与真源独立的一侧）。@returns {dict}"""
    s = series(key)
    allv = [x for g in GROUPS for x in s[g]]
    return dict(union=_bstat(allv), own={g: _bstat(s[g]) for g in GROUPS})


def check(tol=None):
    r"""真源 vs 复算：逐量逐套比（**在真源写死的精度上逐位相同** + n 相同）。@returns {list}"""
    rows = []
    for k in KEYS:
        e = entry(k)
        r = recompute(k)
        digits = 2 if k == 'A5' else 3          # A5 原本就是两位小数（AGENTS 写死值）
        for lab, got, want in (('union', r['union'], e['union']),) + \
                tuple((g, r['own'][g], e['own'][g]) for g in GROUPS):
            d_lo = abs(got['p25'] - want['p25'])
            d_hi = abs(got['p75'] - want['p75'])
            same = (round(got['p25'], digits) == round(want['p25'], digits)
                    and round(got['p75'], digits) == round(want['p75'], digits))
            ok = same and (got['n'] == want['n'])
            if tol is not None:
                ok = ok and (d_lo <= tol and d_hi <= tol)
            rows.append(dict(key=k, which=lab, digits=digits, n=got['n'], n_src=want['n'],
                             calc=(got['p25'], got['p75']),
                             src=(want['p25'], want['p75']),
                             d_lo=d_lo, d_hi=d_hi, ok=ok))
    # 两位共识中位也要对得上
    cons = load()['neutral_consensus']
    for f in ('a', 'b', 'C'):
        got = []
        for b in NA_BANDS:
            vals = [entry('NA%s%s' % (f, b))['own'][g]['p50'] for g in GROUPS]
            got.append(round(sum(vals) / float(len(vals)), 3))
        rows.append(dict(key='consensus_' + f, which='median2', n=len(GROUPS),
                         n_src=len(GROUPS),
                         calc=tuple(got), src=tuple(cons[f]),
                         d_lo=max(abs(got[i] - cons[f][i]) for i in range(5)),
                         d_hi=0.0, ok=(got == [round(float(x), 3) for x in cons[f]])))
    return rows


# ---------------------------------------------------------------- 命令行

def _fmt(b):
    return '[%.3f, %.3f]' % (b['p25'], b['p75'])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else 'show'
    if cmd == 'emit':
        if len(argv) < 2:
            raise SystemExit('用法：python -m _lib.bands emit <out.json>')
        doc = load()
        p = argv[1]
        d = os.path.dirname(os.path.abspath(p))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, indent=1))
        os.replace(tmp, p)
        print('-> %s（由 %s 生成）' % (p, BANDS_JSON))
        return 0
    if cmd == 'check':
        rows = check()
        bad = [r for r in rows if not r['ok']]
        for r in rows:
            if not r['ok']:
                print('  **FAIL** %-10s %-6s 复算 %s 真源 %s n=%d/%d'
                      % (r['key'], r['which'], r['calc'], r['src'], r['n'], r['n_src']))
        print('  真源 <-> 复算：%d 条 ｜ FAIL %d ｜ 最大 |Δ| = %.3g'
              % (len(rows), len(bad), max(r['d_lo'] + r['d_hi'] for r in rows)))
        return 1 if bad else 0
    m = load()['_meta']
    print('靶带真源 = %s' % BANDS_JSON)
    print('  口径 = %s' % m['caliber'])
    print('  成片 n = %s ｜ 过 300px 门 = %s ｜ 并集 n = %d'
          % (m['n_master'], m['n_gate'], m['n_union']))
    print('  链 = %s（**増田无成片语料 ⇒ 不在 bands 里**）' % (list(GROUPS),))
    print('')
    print('## A 组（并集界 = 鹿井+滨田；A 组共同规律）')
    print('  %-9s %-20s %8s %8s %8s %6s' % ('量', '并集 [P25,P75]', 'p50', 'p95', 'IQR', 'n'))
    for k in A_KEYS:
        b = union(k)
        print('  %-9s %-20s %8.3f %8.3f %8.3f %6d'
              % (k, _fmt(b), b['p50'], b['p95'], b['iqr'], b['n']))
    print('')
    print('## 皮肤 / A8（本链界；并集并列）')
    for k in SKIN_KEYS:
        u = union(k)
        print('  %-5s 并集 %s n=%d' % (k, _fmt(u), u['n']))
        for g in GROUPS:
            b = own(k, g)
            print('        %-3s %s p50=%.3f n=%d' % (_lib.MNAME[g], _fmt(b), b['p50'], b['n']))
    print('')
    print('## 中性轴五档（本链界；并集并列）')
    for f in ('a', 'b', 'C'):
        print('  %s*  %-14s %-14s %-14s' % (f, '鹿井', '滨田', '并集'))
        for b_ in NA_BANDS:
            k = 'NA%s%s' % (f, b_)
            cells = [_fmt(own(k, g)) for g in GROUPS] + [_fmt(union(k))]
            print('  %-5s %-14s %-14s %-14s'
                  % (b_.replace('_', '–'), cells[0], cells[1], cells[2]))
    print('')
    print('两位共识中位（鹿井/滨田，只当叙事中心）：%s' % neutral_consensus())
    return 0


if __name__ == '__main__':
    sys.exit(main())
