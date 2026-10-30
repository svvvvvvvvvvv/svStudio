# -*- coding: utf-8 -*-
r"""_lib / selftest.py —— **用已有数据跑一遍，证明"能用"**（零渲染）。

```
① measure      复算 `颜色体检_1013\_m1_919.json`  => **max|差| 应 = 0.0000**
                （读 919 母版 2048 PNG/相机 JPG -> 900 域 -> 全量读数 -> 逐键比）
② verdict      复算 **靶带唯一真源** `_lib\bands.json`  => **逐位一致 / n 相同**
                （27 量 × 并集+鹿井+滨田 + 两位共识；数据 = 三份语料目录的 `_测量.json`）
③ fingerprint  读出三条预设的 **238/240 字段 SHA256**（+ targets 两层）
④ jsonsafe     读 -> 探测 -> 不改 的往返           => **逐字节相同**
⑤ render       路数预算（`per_shard_mb`，给死 avail）=> **6 路 x 8000 MB 应降到 2 路**（零渲染）
```

用法：
```
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
& <生产 python> -X utf8 E:\ds\photo\_lib\selftest.py            # 全跑
& <生产 python> -X utf8 E:\ds\photo\_lib\selftest.py --only 1   # 只跑第 1 条
& <生产 python> -X utf8 E:\ds\photo\_lib\selftest.py --stems 2  # measure 只跑前 2 张（快）
```
结果同时落 `_lib\_selftest.txt`。**本脚本只读，不写任何生产文件。**
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_PHOTO = os.path.dirname(HERE)
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402

OUT = []
FAIL = []
WARN = []
REF_CHEEK = 'circle'      # ★ `_m1_919.json` 落盘时的颊口径（圆 ∩ FACE）；别的口径 = SKIP
FINGERPRINT_NAMES = ('鹿井预设', '滨田预设', 'xhs预设')      # ★ 10-18：生产三条预设（实测清单）
EOL_PENDING = ()   # 10-18 晚：`xhs预设.json` 已统一为 CRLF（用户拍板），无例外


def log(s=''):
    print(s, flush=True)
    OUT.append(s)


def tag(ok, s):
    if not ok:
        FAIL.append(s)
    return ('PASS' if ok else '**FAIL**')


def warn(s):
    WARN.append(s)
    return s


# ---------------------------------------------------------------- ① measure

def check_measure(stems_limit=0, arms_limit=0):
    r"""复算 `_m1_919.json`（逐键比，含浮点的相对/绝对双判）。@returns {dict}"""
    import _lib.measure as M
    from PIL import Image, ImageOps
    ref_p = os.path.join(_PHOTO, '颜色体检_1013', '_m1_919.json')
    ref = json.load(io.open(ref_p, encoding='utf-8'))
    ROOT = os.path.join(_PHOTO, '效果测试919_三方对比', 'render')
    log('【① measure 复算 %s】' % ref_p)
    log('  基准文件 = m1_measure.py 的落盘读数（11 张 x 4 臂）')
    log('  口径 = 长边 900 -> to_lab；颊 = 圆 ∩ FACE；臂 = SKIN 去颊圆 x1.6；全量读数逐键比')
    # ★ 修（10-18）：`_m1_919.json` 是**按旧颊口径（圆 ∩ FACE）**落的盘，而 `measure` 的
    #   缺省口径 10-18 已改成 `SV_CHEEK=skin`（FACE ∩ 皮肤似像素）⇒ 两者必然不逐位相等。
    #   这不是 bug，是口径换代 ⇒ **明说 SKIP**，而不是留一个没有解释的红 FAIL。
    #   要复算基准，请 `$env:SV_CHEEK='circle'` 再跑。
    cur_mode = (os.environ.get('SV_CHEEK') or 'skin').strip().lower()
    if cur_mode != REF_CHEEK:
        msg = ('基准 `_m1_919.json` 是 `SV_CHEEK=circle` 口径落的盘，当前是 %r '
               '⇒ 不逐位比（要看基准请设 SV_CHEEK=circle）' % cur_mode)
        log('  SKIP：%s' % msg)
        return dict(ok=True, skipped=msg, max_abs=None, where=None, n_stem=0, n_arm=0)
    stems = sorted(ref)
    if stems_limit:
        stems = stems[:stems_limit]
    det = M.make_detector()
    t0 = time.time()
    worst = 0.0
    worst_where = ''
    nkey = 0
    narm = 0
    per_stem = []
    for i, stem in enumerate(stems, 1):
        # 掩膜与基准同口径：用**基线母版**（Ultramax400沉褐）出掩膜
        base_p = os.path.join(ROOT, 'Ultramax400沉褐', '_母版2048', stem + '.png')
        if not os.path.isfile(base_p):
            log('  !! 缺基线母版 %s' % base_p)
            continue
        r0 = M.read_image(base_p, det=det)
        d = ref[stem]
        ok_mask = (r0['cheek_n'] == d['cheek_n'] and r0['arm_n'] == d['arm_n']
                   and r0['nface'] == d['nface'])
        mx = 0.0
        for name, key in M.PRESETS:
            p = os.path.join(ROOT, name, '_母版2048', stem + '.png')
            if not os.path.isfile(p):
                continue
            im9 = M.to_domain(ImageOps.exif_transpose(Image.open(p)).convert('RGB'))
            lab = M.to_lab900(im9, do_exif=False)
            got = M.measure(lab, r0['cheekM'], r0['armM'])
            ref_arm = (d['arms'] or {}).get(key)
            if not ref_arm:
                continue
            narm += 1
            m, w = _cmp(got, ref_arm)
            nkey += 1
            if m > mx:
                mx = m
            if m > worst:
                worst, worst_where = m, '%s/%s %s' % (stem, key, w)
        cp = None
        for ext in ('.JPG', '.jpg', '.jpeg', '.JPEG'):
            pp = os.path.join(_lib.CAM_DIR, stem + ext)
            if os.path.isfile(pp):
                cp = pp
                break
        if cp and (d['arms'] or {}).get('cam'):
            im9 = M.to_domain(ImageOps.exif_transpose(Image.open(cp)).convert('RGB'))
            if im9.size == r0['im9'].size:
                lab = M.to_lab900(im9, do_exif=False)
                got = M.measure(lab, r0['cheekM'], r0['armM'])
                narm += 1
                m, w = _cmp(got, d['arms']['cam'])
                if m > mx:
                    mx = m
                if m > worst:
                    worst, worst_where = m, '%s/cam %s' % (stem, w)
        per_stem.append((stem, mx, ok_mask))
        log('  [%2d] %-9s 掩膜 %s 颊n=%-6d 臂n=%-6d 本张 max|差| = %.3e'
            % (i, stem, tag(ok_mask, 'mask'), r0['cheek_n'], r0['arm_n'], mx))
    log('  ---- %d 张 / %d 臂 ｜ max|差| = **%.10f**  @ %s ｜ 耗时 %.1f s'
        % (len(per_stem), narm, worst, worst_where, time.time() - t0))
    ok = (worst == 0.0 and narm > 0)
    log('  ① 判定 = %s（要求 max|差| = 0.0000）' % tag(ok, 'measure'))
    return dict(ok=ok, max_abs=worst, where=worst_where, n_stem=len(per_stem), n_arm=narm)


def _cmp(got, ref):
    r"""逐键比（递归到 float；返回 (max|差|, 最差键路径)）。@returns {tuple}"""
    worst = [0.0, '']

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in b:
                if k in ('_src',):
                    continue
                if k not in a:
                    worst[0] = max(worst[0], float('inf'))
                    worst[1] = path + '.' + k
                    continue
                walk(a[k], b[k], path + '.' + k)
            return
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, '%s[%d]' % (path, i))
            return
        if isinstance(b, bool) or b is None:
            if a != b:
                worst[0] = max(worst[0], float('inf'))
                worst[1] = path
            return
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            if str(a) != str(b):
                worst[0] = max(worst[0], float('inf'))
                worst[1] = path
            return
        if not (np.isfinite(fa) and np.isfinite(fb)):
            if np.isnan(fa) and np.isnan(fb):
                return
            worst[0] = max(worst[0], float('inf'))
            worst[1] = path
            return
        d = abs(fa - fb)
        if d > worst[0]:
            worst[0] = d
            worst[1] = path

    walk(got, ref, '')
    return worst[0], worst[1]


# ---------------------------------------------------------------- ② verdict

def check_verdict():
    r"""复算**靶带唯一真源** `_lib\bands.json`（27 量 × 3 套 + 两位共识）。@returns {dict}"""
    import _lib.verdict as V
    import _lib.bands as B
    log('【② verdict 复算靶带（唯一真源 = %s）】' % B.BANDS_JSON)
    log('  数据 = %s' % ' / '.join(B.MEASURE_PATHS.values()))
    log('  口径 = %s' % B.load()['_meta']['caliber'])
    log('  判据 = 真源写死的界 <-> 从大师**逐图读数复算**（逐位相同 + n 相同）')
    rows = B.check()
    bad = [r for r in rows if not r['ok']]
    for r in rows:
        log('  %-10s %-6s 复算 [%10.6f,%10.6f] 真源 [%8.3f,%8.3f] n=%-5d/%d %s'
            % (r['key'], r['which'], r['calc'][0], r['calc'][1],
               r['src'][0], r['src'][1], r['n'], r['n_src'],
               'OK' if r['ok'] else '**不一致**'))
    mx = max(r['d_lo'] + r['d_hi'] for r in rows)
    log('  ---- %d 条（27 量 × (并集+鹿井+滨田) = 81，两位共识 3）｜ 逐位一致 = %s ｜ 最大 |Δ| = %.6f'
        % (len(rows), tag(not bad, 'verdict-bands'), mx))
    # 附：AGENTS 那 8 条（A 组 + A8）逐条对真源
    log('  %-8s %-22s %-18s %10s %10s %6s %s'
        % ('键', '复算 [P25,P75]', '真源写死', 'd_lo', 'd_hi', 'n', '逐位'))
    rows8 = V.agents_bounds_check()
    bad8 = [r for r in rows8 if not r['digits_ok']]
    for r in rows8:
        log('  %-8s [%9.6f,%9.6f] [%8s,%8s] %+10.2e %+10.2e %6d %s'
            % (r['key'], r['calc'][0], r['calc'][1], r['written'][0], r['written'][1],
               r['d_lo'], r['d_hi'], r['n'], 'OK' if r['digits_ok'] else '**不一致**'))
    log('  ---- AGENTS 8 条逐位一致 = %s' % tag(not bad8, 'verdict-8'))
    # 活体检查：两条尺子 + 置换 + bootstrap 都出数（用大师自身的两半做假配对）
    m8 = json.load(io.open(_lib.M8, encoding='utf-8'))
    s = V.master_series(m8, 'A5')
    a, b = s['kurai'][:11], s['kurai'][11:22]
    row = V.judge('A5', a, b, m8, 'kurai', feat=None, boot_n=500, perm_n=500)
    log('  [活体] key=A5 假配对 n=%d  R=%.4f  R_pair=%.4f  置换p=%.4f  boot=[%+.3f,%+.3f]  同号 %d/%d'
        % (row['n'], row['r_abs'], row['r_pair'], row['perm_p'],
           row['boot'][1], row['boot'][2], row['sign']['k'], row['sign']['n']))
    log('         两套界 %s / %s ｜ 判定（并集）= %s'
        % (['%.3f' % x for x in row['band_union']], ['%.3f' % x for x in row['band_own']],
           row['verdict_union']))
    log('         强制项检查：assert_full_report() 通过（两套界 + 三样都报）')
    ok = (not bad) and (not bad8)
    log('  ② 判定 = %s' % tag(ok, 'verdict'))
    return dict(ok=ok, max_abs=mx, n_rows=len(rows), bad=bad,
                agents_bounds_ok=not bad8)


# ---------------------------------------------------------------- ③ fingerprint

def check_fingerprint():
    r"""三条预设的 238/240 字段 SHA256 + targets 两层 + 解析层读数。@returns {dict}"""
    import _lib.fingerprint as F
    log('【③ fingerprint 参数级全字段 SHA256】')
    log('  引擎路径 = %s（.pth 坑：先 bootstrap_engine 再 import）' % _lib.VENDOR)
    F.bootstrap_engine()                       # ★ 必须在 import spektrafilm 之前
    import spektrafilm
    where = os.path.dirname(os.path.dirname(spektrafilm.__file__))
    log('  实际 import 到 = %s  %s' % (where, 'OK（vendored）'
                                      if os.path.normcase(where) == os.path.normcase(_lib.VENDOR)
                                      else '**不是 vendored => .pth 抢走了**'))
    res = {}
    for n in FINGERPRINT_NAMES:
        d = F.param_digest(n)
        res[n] = d
        log('  %-16s nfields=%-4d sha=%s  文件 sha=%s  %s 尾换行=%s'
            % (n, d['nfields'], d['sha16'], d['file_sha256'][:16],
               'CRLF' if d['layout']['crlf'] else 'LF', d['layout']['trailing_nl']))
    L = F.scene_stock_layers()
    log('  targets 两层：`_scene_engine` 键 %d sha=%s ｜ `_stock_engine` 预设 %d 个 sha=%s'
        % (L['n_scene'], L['sha16_scene'], L['n_stock'], L['sha16_stock']))
    log('  %-16s %-46s %s' % ('预设', '运行时属性路径', '解析层值'))
    _rd = F.engine_readings()
    for n, r in _rd.items():
        for k, v in r.items():
            if v is not None:
                log('  %-16s %-46s %s' % (n, k, v))
    # ★★★ 10-14 新增：**"读得对"断言**（原来只验"同进程重算同值" = 只证"没变"，不证"读得对"）
    #   背景：`engine_readings` 原来只读 `presets._params_for()`（= 预设文件 + 轴层），
    #   而 10-12 起有 4 个键**下沉到 `targets.json` 的 `_stock_engine[预设名]`**
    #   ⇒ 对它们读出的**是轴层兜底值**（实测 lc 报 (0.7,1,2.2)，真值 [0.8,1,5.0]）。
    #   本断言**独立从 targets.json 读出真值**（不复用被验代码的路径）再对照。
    _SUNK = ('print_render.chemistry.active',
             'io.output_gamut_compress.lightness_compression',
             'film_render.grain.blur')
    _tj = None
    _bad = []
    try:
        _tp = F.targets_path()
        _tj = json.load(io.open(_tp, encoding='utf-8'))
        _se = _tj.get('_stock_engine') or {}
        for n in FINGERPRINT_NAMES:
            got = _rd.get(n) or {}
            want = _se.get(n) or {}
            for k in _SUNK:
                # ⚠ 只检查 `engine_readings` **实际返回**的键（`KEY_PATHS` 之外的不算"读错"）
                if k in want and k in got and got.get(k) != want[k]:
                    _bad.append('%s/%s: 读到 %r，targets 真值 %r' % (n, k.split('.')[-1], got.get(k), want[k]))
                    log('  ⚠ 读错层：%s / %s = %r（targets 真值 %r）' % (n, k, got.get(k), want[k]))
    except Exception as _e:                                # noqa: BLE001
        _bad.append('对照失败：%s: %s' % (type(_e).__name__, _e))
    read_ok = not _bad
    log('  读得对（4 个下沉键 vs targets.json 独立对照）= %s%s'
        % (read_ok, '' if read_ok else '  ← ' + ' ｜ '.join(_bad)))
    # 稳定性：同进程再算一次必须同值（缓存不会污染）
    again = F.param_digest(list(res)[0])['sha16']
    stable = (again == res[list(res)[0]]['sha16'])
    nf = {v['nfields'] for v in res.values()}
    # ★ 修（10-18）：旧断言写死「nfields ∈ (238, 240)」（那是 10-13 十条旧预设的快照）。
    #   现生产三条实测都是 236（`_params_for` 的叶子数随 schema / 可选键变）⇒ 旧断言恒红。
    #   改成**同族一致性**判据：三条的叶子数必须相同且 ≥ 200（防"压成 1 个叶子"那种假指纹）。
    nf_ok = (len(nf) == 1) and (min(nf) >= 200)
    ok = stable and read_ok and nf_ok
    log('  稳定性（同进程重算同值）= %s ｜ 字段数 %s（同族应相同且 ≥ 200）= %s'
        % (stable, sorted(nf), nf_ok))
    log('  ③ 判定 = %s' % tag(ok, 'fingerprint'))
    return dict(ok=ok, read_ok=read_ok, digests={k: v['sha16'] for k, v in res.items()},
                nfields={k: v['nfields'] for k, v in res.items()}, layers=L, stable=stable)


# ---------------------------------------------------------------- ④ jsonsafe

def check_jsonsafe():
    r"""读 -> 探测 -> 不改 的往返（逐字节相同）+ 排版断言。@returns {dict}"""
    import _lib.jsonsafe as J
    R = os.path.join(_lib.REPO, 'svFilm', 'data')
    files = [os.path.join(R, 'targets.json')] + \
            [os.path.join(R, 'presets', '%s.json' % n) for n in FINGERPRINT_NAMES]
    log('【④ jsonsafe 读 -> 探测 -> 不改 的往返】')
    log('  %-30s %-8s %-6s %-6s %-8s %-6s %s'
        % ('文件', 'indent', '换行', 'BOM', '尾换行', '字节', '往返逐字节相同'))
    bad = []
    lay = {}
    for p in files:
        d = J.roundtrip(p)
        pr = J.probe(p)
        lay[os.path.basename(p)] = dict(indent=pr['indent'], crlf=pr['crlf'],
                                       bom=pr['bom'], trailing_nl=pr['trailing_nl'])
        good = d['identical'] and d['json_ok'] and (d['sha256'] == d['sha256_file'])
        if not good:
            bad.append(p)
        log('  %-30s %-8s %-6s %-6s %-8s %-6d %s'
            % (os.path.basename(p), pr['indent'], 'CRLF' if pr['crlf'] else 'LF',
               pr['bom'], pr['trailing_nl'], pr['bytes'], tag(good, 'roundtrip')))
    # 排版断言（AGENTS §二.5 的实测口径）
    # ★ 修（10-18）：旧断言写死「预设条目数 = 1」（10-15 时确实只有一条）。
    #   现在生产有 3 条（鹿井 / 滨田 / xhs）⇒ 旧断言恒红。改成**对实测清单**断言。
    pre = [k for k in lay if k != 'targets.json']
    n1 = sum(1 for k in pre if lay[k]['indent'] == 1)
    n2 = sum(1 for k in pre if lay[k]['indent'] == 2)
    # ★ 已知例外（**不是放过，是记账**）：`xhs预设.json` 是 LF（10-18 01:07 新建时落的），
    #   另两份是 CRLF。改 EOL 会换掉预设全文 sha（作废全部 L3 缓存）且动生产文件
    #   ⇒ 等用户点头；这里把它**点名**而不是让整条断言一直红。
    eol_bad = [k for k in pre if not lay[k]['crlf']]
    unknown = [k for k in eol_bad if k not in EOL_PENDING]
    chk = [
        ('targets.json indent=2', lay['targets.json']['indent'] == 2),
        ('预设条目数 = %d 且全部 indent=2' % len(FINGERPRINT_NAMES),
         len(pre) == len(FINGERPRINT_NAMES) and n2 == len(pre) and n1 == 0),
        ('预设全部 CRLF（已知例外 %s）' % (', '.join(EOL_PENDING) or '无'), not unknown),
        ('全部无 BOM', all(not v['bom'] for v in lay.values())),
    ]
    for k in eol_bad:
        log('  ⚠ EOL 待拍板：%s 是 LF（另 %d 份是 CRLF）—— 改它会换预设全文 sha'
            % (k, len(pre) - len(eol_bad)))
    for nm, okk in chk:
        log('  断言 %-42s %s' % (nm, 'OK' if okk else '**不符（口径要更新）**'))
    # 写路径的活体检查：dry-run 一次插入（**不落盘**），要过 json.loads 硬门 + 无裸 LF
    tgt = os.path.join(R, 'targets.json')
    r = J.insert_key(tgt, '_selftest_probe_1013', {'x': 1, 'note': 'dry-run，不落盘'}, apply=False)
    log('  dry-run 插入（**不落盘**）：status=%s indent=%s json_ok=%s 裸LF=%d delta=%+d B'
        % (r['status'], r.get('indent'), r.get('json_ok'), len(r.get('bare_lf') or []),
           r.get('delta_bytes', 0)))
    after = J.probe(tgt)
    untouched = (after['sha256'] == r['sha_before'])
    log('  未改动核对：dry-run 后文件 sha 不变 = %s' % untouched)
    ok = (not bad) and all(o for _n, o in chk) and untouched
    log('  ④ 判定 = %s' % tag(ok, 'jsonsafe'))
    return dict(ok=ok, layout=lay, dry_run=r['status'], untouched=untouched)


# ---------------------------------------------------------------- ⑤ render 内存预算

def check_render_budget():
    r"""★ `render` 的**路数预算**：按「每路预估占用 x 路数」判（**零渲染、不起进程**）。

    判据 = `avail_mb() > n x per_shard_mb + reserve_mb`；不够就自动降路数。
    这里**给死 avail**（不读本机内存）=> 判据可复现。
    @returns {dict} ok / cases / table
    """
    import _lib.render as R
    log('【⑤ render 内存预算（per_shard_mb = 每路预估占用 MB）】')
    log('  判据 = avail_mb() > n x per_shard_mb + reserve_mb（默认 %d）；不够 -> 自动降路数'
        % R.MEM_RESERVE_MB)
    log('  口径 = 给死 avail（不读本机内存）=> 判定可复现；**不起任何进程**')
    cases = [
        ('6264 教训：6 路 x 8000 MB 只够 2 路', 6, 8000, 21096, 2, 'per_shard_mb=8000'),
        ('生产口径 2048：6 路 x 2800 MB 仍 6 路（默认值不砍）', 6, 2800, 22737, 6, '按 6 路启动'),
        ('内存充足：6 路 x 8000 MB 全上', 6, 8000, 100000, 6, '按 6 路启动'),
        ('内存见底：预算不足 1 路也至少起 1 路', 6, 8000, 5000, 1, '降为 1 路'),
        ('关预算（per_shard_mb=None）= 旧行为', 6, None, 22737, 6, '内存预算 = 关'),
        ('实测：6264 上按 4 路起 -> 降 2 路', 4, 8000, 22737, 2, '降为 2 路'),
    ]
    rows, bad = [], []
    for name, n, psm, av, want_n, want_txt in cases:
        pl = R.plan_parallel(n, per_shard_mb=psm, reserve_mb=R.MEM_RESERVE_MB, avail=av)
        line = R.budget_report(pl)
        ok = (pl['n_eff'] == want_n) and (want_txt in line)
        if not ok:
            bad.append(name)
        rows.append(dict(name=name, n_want=n, per_shard_mb=psm, avail_mb=av,
                         n_eff=pl['n_eff'], want=want_n, cut=pl['cut'], line=line))
        log('  %-4s %-48s -> %d 路（期望 %d ｜ 降 %d）'
            % (tag(ok, 'budget'), name, pl['n_eff'], want_n, pl['cut']))
        log('       %s' % line)
    # 对照表两端 = 实测锚点（拟合不许把锚点漂掉）+ 单调
    tbl = [(2048, 2800), (6264, 8000)]
    tabs = []
    for side, want_mb in tbl:
        got = R.per_shard_mb_for(side)
        ok = (got == want_mb)
        if not ok:
            bad.append('table_%d' % side)
        tabs.append(dict(side=side, got=got, want=want_mb, ok=ok))
        log('  对照表 长边 %-5d -> %5d MB/路（实测锚点 %d）%s'
            % (side, got, want_mb, 'OK' if ok else '**不符**'))
    mono = all(R.per_shard_mb_for(s) <= R.per_shard_mb_for(s * 2)
               for s in (1024, 2048, 4096))
    if not mono:
        bad.append('monotonic')
    dflt = (R.PER_SHARD_MB == 2800)
    if not dflt:
        bad.append('default_2800')
    log('  单调（长边翻倍 => 每路预算不降）= %s ｜ 默认每路预算 = %d MB（应为 2800）= %s'
        % (mono, R.PER_SHARD_MB, dflt))
    ok = (not bad)
    log('  ⑤ 判定 = %s（%d 条断言，失败 %d）' % (tag(ok, 'render-budget'), len(cases) + 4, len(bad)))
    return dict(ok=ok, cases=rows, table=tabs, monotonic=mono, default_ok=dflt, bad=bad)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', type=int, default=0, help='只跑某一条（1..5）')
    ap.add_argument('--stems', type=int, default=0, help='measure 只跑前 N 张（0 = 全部 11 张）')
    ap.add_argument('--no-write', action='store_true', help='不落 _selftest.txt')
    a = ap.parse_args(argv)
    t0 = time.time()
    log('=' * 108)
    log('_lib 自检（零渲染；只读生产文件，不写任何专题目录）  %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    log('库版本 = %s ｜ 引擎 python = %s' % (_lib.VERSION, _lib.PY))
    log('=' * 108)
    R = {}
    if a.only in (0, 1):
        R['measure'] = check_measure(stems_limit=a.stems)
    if a.only in (0, 2):
        R['verdict'] = check_verdict()
    if a.only in (0, 3):
        R['fingerprint'] = check_fingerprint()
    if a.only in (0, 4):
        R['jsonsafe'] = check_jsonsafe()
    if a.only in (0, 5):
        R['render_budget'] = check_render_budget()
    log('')
    log('=' * 108)
    log('【总计】耗时 %.1f s' % (time.time() - t0))
    for k, v in R.items():
        if v.get('skipped'):
            log('  %-12s **SKIP**（%s）' % (k, v['skipped']))
        else:
            log('  %-12s %s' % (k, tag(v.get('ok'), k)))
    log('  FAIL 项 = %d %s' % (len(FAIL), FAIL))
    log('  WARN 项 = %d' % len(WARN))
    for w in WARN:
        log('    - %s' % w)
    log('=' * 108)
    if not a.no_write:
        p = os.path.join(HERE, '_selftest.txt')
        tmp = p + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('\n'.join(OUT) + '\n')
        os.replace(tmp, p)
        print('-> %s' % p)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
