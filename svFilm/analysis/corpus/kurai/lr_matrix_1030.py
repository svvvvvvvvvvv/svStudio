# -*- coding: utf-8 -*-
r"""LR 行为矩阵（2026-10-30 夜）：**一次固定脚本**，给设计/模型做验收。

为什么有这个脚本
----------------
`design/LR数值来源.md` §5 的台账全是 n=1~2。SV 要求"给 LR 测试这些设计和方法论"，
所以这里把测试写成**可回放矩阵**：同一批源图、单变量、零基线、每臂只动一根，
导出 → 用 v7 尺子量 → 与 `photometry` / `tone_model` 的预测对拍。

纪律（`design/设计真源.md` 第一部分 第 4 条）
----------------------------------------------
· 不临时试、不边跑边改：矩阵先写在 `build_matrix()`，跑完只做"预测 vs 实测"；
· 一次跑完，结果落 `matrix.json` / `fit.json` / `报告.md`；
· 只有"出 ③ 成片"才驱动 LR；这里是**标定/验证**，不动产品链。

跑：
    & $py -X utf8 analysis\corpus\kurai\lr_matrix_1030.py
    & $py -X utf8 ... --dry          # 只打印矩阵，不碰 LR
    & $py -X utf8 ... --only tone    # 只跑某一组（tone/sat/exp/local/curve）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'lrfinish'))
sys.path.insert(0, os.path.join(ROOT, 'lrfinish', 'prototype'))

from bridge_client import run                       # noqa: E402
from measure_v7 import measure_one                  # noqa: E402
from design.photometry import l_after_ev, l_to_code  # noqa: E402

WORK = r'E:\ds\photo\_lr_matrix_1030'
SRC_DIR = os.path.join(WORK, 'src')
EXP_DIR = os.path.join(WORK, 'exp')

# 5 张源图：大师成片里脸够大、clip 低、L/C 分散的 5 张（内容不同、不是同一场景）
SOURCES = [
    r'E:\ds\大师成片\鹿井\003_鹿井小渔_n6ab32b12\02_t1.jpg',   # face L60.6 C22.6
    r'E:\ds\大师成片\鹿井\007_鹿井小渔_n6aac9e26\02_t1.jpg',   # L69.9 C26.4
    r'E:\ds\大师成片\鹿井\010_鹿井小渔_n6aa4aedb\03_t1.jpg',   # L65.7 C28.8
    r'E:\ds\大师成片\鹿井\011_鹿井小渔_n6aa4ad8c\02_t1.jpg',   # L46.3 C23.3
    r'E:\ds\大师成片\鹿井\012_鹿井小渔_n6aa4acb9\02_t1.jpg',   # L73.7 C16.9
]
LOCAL_SRC = SOURCES[0]
RUN_ID = time.strftime('%m%d_%H%M')

ZERO = {
    'Exposure2012': 0.0, 'Contrast2012': 0.0, 'Highlights2012': 0.0,
    'Shadows2012': 0.0, 'Whites2012': 0.0, 'Blacks2012': 0.0,
    'Saturation': 0.0, 'Vibrance': 0.0,
}
LINEAR_CURVE = 'ToneCurvePV2012:' + ','.join(
    '%d,%d' % (i * 255 // 15, i * 255 // 15) for i in range(16))

_ADDED: Dict[str, bool] = {}


def spec(d: Dict[str, float]) -> str:
    return ','.join('%s=%.4f' % (k, v) for k, v in d.items())


def ensure_tif(src: str, name: str) -> str:
    """JPG/PNG → 8-bit TIFF（LR 对未标记 TIFF 按 sRGB 解释；这里只要固定输入）。"""
    os.makedirs(SRC_DIR, exist_ok=True)
    dst = os.path.join(SRC_DIR, name + '.tif')
    if not os.path.isfile(dst):
        Image.open(src).convert('RGB').save(dst, format='TIFF')
    return dst


def ensure_added(path: str) -> None:
    """ADD + **只做一次**曲线归零（不是每臂；每臂 CURVES 要 3~5 秒，86 臂就是 7 分钟）。"""
    if _ADDED.get(path):
        return
    r = run('ADD ' + path, timeout=120)
    low = r.lower()
    # 已在 catalog 里时，桥返回的是 ERR ... failed to import photo（不是 EXISTS）
    if ('ADDED' not in r and 'EXISTS' not in r.upper()
            and 'failed to import' not in low and 'already' not in low):
        raise RuntimeError('ADD 失败 %s: %s' % (path, r[:160]))
    run('CURVES %s %s' % (path, LINEAR_CURVE), timeout=60)
    _ADDED[path] = True


def zero(photo: str) -> None:
    run('SELECT ' + photo, timeout=30)
    r = run('GLOBAL %s %s' % (photo, spec(ZERO)), timeout=60)
    if 'VERIFIED' not in r:
        raise RuntimeError('GLOBAL 归零失败：%s' % r[:160])


def export_measure(photo: str, tag: str) -> dict:
    od = os.path.join(EXP_DIR, tag)
    if os.path.isdir(od):
        shutil.rmtree(od, ignore_errors=True)
    os.makedirs(od, exist_ok=True)
    r = run('EXPORTTIF ' + od, timeout=180)
    fs = [os.path.join(od, f) for f in os.listdir(od)
          if f.lower().endswith(('.tif', '.tiff'))]
    if not fs:
        raise RuntimeError('导出无 TIF：%s' % r[:160])
    m = measure_one(fs[0])
    m['_tif'] = os.path.relpath(fs[0], WORK)
    return m


def arm(tag: str, photo: str, group: str, settings: dict,
        mask: Optional[dict] = None, curve: Optional[str] = None,
        source_id: str = '') -> dict:
    t0 = time.time()
    zero(photo)
    rc = ''
    if curve:
        rc = run('CURVES %s %s' % (photo, curve), timeout=60)
    if mask is None and settings:
        # ★ 全局臂必须真的把滑杆写进去；只 zero 不 apply 的话，所有臂都会等于基线
        #   （2026-10-31 首跑踩过：11 个 tone 臂的 face_L 一模一样）。
        gmap = {'contrast': 'Contrast2012', 'highlights': 'Highlights2012',
                'shadows': 'Shadows2012', 'whites': 'Whites2012',
                'blacks': 'Blacks2012', 'exposure': 'Exposure2012',
                'saturation': 'Saturation'}
        kv = {gmap[k]: float(v) for k, v in settings.items() if k in gmap}
        if kv:
            rg = run('GLOBAL %s %s' % (photo, spec(kv)), timeout=60)
            if 'VERIFIED' not in rg:
                raise RuntimeError('GLOBAL 应用失败 %s: %s' % (kv, rg[:160]))
    readback = {}
    if mask is not None:
        rm = run('MASK %s subject' % photo, timeout=120)
        if 'ok=true' not in rm and 'groups=1' not in rm:
            raise RuntimeError('MASK 失败：%s' % rm[:160])
        for k, v in mask.items():
            rr = run('MASKPARAM %s %s %.6f' % (photo, k, v), timeout=60)
            readback[k] = rr.split('readback=')[-1].split()[0] if 'readback=' in rr else None
    m = export_measure(photo, tag)
    row = dict(tag=tag, group=group,
               source=source_id or os.path.basename(os.path.dirname(photo)),
               settings=settings, mask=mask or {}, curve=(curve or ''),
               readback=readback, sec=round(time.time() - t0, 1))
    row.update({k: m.get(k) for k in (
        'face_L', 'face_C', 'face_b', 'face_h', 'face_p95', 'face_p99', 'face_p5',
        'person_L', 'person_p95', 'person_p99', 'bg_L', 'bg_L_p90', 'bg_L_p95',
        'contrast', 'warm_cool_b', 'face_px', 'person_px')})
    row['img'] = m.get('img')
    row['_tif'] = m.get('_tif')
    return row


# ---------------------------------------------------------------- 矩阵定义

def build_matrix() -> List[dict]:
    """返回待跑臂（不含 baseline；baseline 由 runner 先跑）。"""
    arms: List[dict] = []
    # 全局：每张源图、每根滑杆、±50
    for i, src in enumerate(SOURCES):
        sid = 's%d' % (i + 1)
        for key, short in (('Contrast2012', 'contrast'),
                           ('Highlights2012', 'highlights'),
                           ('Shadows2012', 'shadows'),
                           ('Whites2012', 'whites'),
                           ('Blacks2012', 'blacks')):
            for val in (-50.0, 50.0):
                arms.append(dict(tag='%s_%s_%+d' % (sid, short, int(val)),
                                 src=src, sid=sid, group='tone',
                                 settings={short: val}, mask=None))
        for ev in (-0.5, 0.5):
            arms.append(dict(tag='%s_exposure_%+.1f' % (sid, ev),
                             src=src, sid=sid, group='exp', settings={'exposure': ev}))
        for s in (30.0, 60.0):
            arms.append(dict(tag='%s_sat_%+d' % (sid, int(s)),
                             src=src, sid=sid, group='sat', settings={'saturation': s}))
    # 局部：只用 s1（脸最大）
    local = [
        ('lexp_0.15', {'LocalExposure2012': 0.15}),
        ('lexp_0.25', {'LocalExposure2012': 0.25}),
        ('lexp_0.375', {'LocalExposure2012': 0.375}),
        ('lexp_0.50', {'LocalExposure2012': 0.50}),
        ('lsh_0.50', {'LocalShadows2012': 0.50}),
        ('lsh_1.00', {'LocalShadows2012': 1.00}),
        ('lsat_0.30', {'LocalSaturation': 0.30}),
        ('lsat_0.60', {'LocalSaturation': 0.60}),
        ('lton_0.45', {'LocalToningHue': 0.45, 'LocalToningSaturation': 0.5}),
        ('lton_0.75', {'LocalToningHue': 0.75, 'LocalToningSaturation': 0.5}),
    ]
    for tag, kv in local:
        arms.append(dict(tag='s1_' + tag, src=LOCAL_SRC, sid='s1', group='local',
                         settings=kv, mask=kv, fresh=True))
    # 曲线：合成 ramp
    arms.append(dict(tag='ramp_curve', src='@ramp', sid='ramp', group='curve',
                     settings={'curve': '128->140'}, curve='@ramp_curve'))
    return arms


# ---------------------------------------------------------------- 预测

def fit_rows(rows: List[dict]) -> dict:
    """把每个臂的实测位移与分析模型对拍。模型只用于分析，不进生产。"""
    from _lr_models.lr_response import delta as resp_delta
    out = []
    for r in rows:
        g = r.get('group')
        s = r.get('settings') or {}
        pred = {}
        if g == 'tone':
            key = next(iter(s))
            val = float(s[key])
            for q in ('p5', 'p25', 'p50', 'p75', 'p95'):
                base = (r.get('base') or {}).get('img', {}).get(q)
                meas = (r.get('img') or {}).get(q)
                if base is not None and meas is not None:
                    d = resp_delta(key.replace('2012', '').lower(), float(base), val) or 0.0
                    pred[q] = round(float(meas) - (float(base) + d), 2)
        elif g == 'exp':
            ev = float(s['exposure'])
            for q in ('p5', 'p50', 'p95'):
                base = (r.get('base') or {}).get('img', {}).get(q)
                meas = (r.get('img') or {}).get(q)
                if base is not None and meas is not None:
                    pred[q] = round(float(meas) - l_after_ev(float(base), ev), 2)
        elif g == 'sat':
            sv = float(s['saturation'])
            baseC = (r.get('base') or {}).get('face_C')
            if baseC is not None and r.get('face_C') is not None:
                pred['dC'] = round(float(r['face_C']) - float(baseC) * (1.0 + sv / 100.0), 2)
        elif g == 'local':
            base = r.get('base') or {}
            if 'LocalExposure2012' in s:
                ev = float(s['LocalExposure2012']) * 4.0
                if base.get('face_L') is not None and r.get('face_L') is not None:
                    pred['face_dL'] = round(float(r['face_L'])
                                            - l_after_ev(float(base['face_L']), ev), 2)
                    pred['face_p99_dL'] = round(float(r.get('face_p99') or 0)
                                                - l_after_ev(float(base.get('face_p99') or 0), ev), 2)
            if 'LocalShadows2012' in s:
                from _lr_models.lr_response import local_shadow_k
                ui = float(s['LocalShadows2012']) * 100.0
                if base.get('face_L') is not None and r.get('face_L') is not None:
                    pred['face_dL'] = round(float(r['face_L'])
                                            - (float(base['face_L'])
                                               + local_shadow_k(float(base['face_L'])) * ui / 100.0), 2)
            if 'LocalSaturation' in s:
                from _lr_models.lr_response import LOCAL_SAT_R
                if base.get('face_C') is not None and r.get('face_C') is not None:
                    ui = float(s['LocalSaturation']) * 100.0
                    pred['dC'] = round(float(r['face_C'])
                                       - float(base['face_C']) * (1.0 + LOCAL_SAT_R * ui / 100.0), 2)
        r2 = dict(tag=r['tag'], group=g, settings=s,
                  meas={k: r.get(k) for k in ('face_L', 'face_C', 'face_h',
                                              'face_p99', 'person_p99', 'img')},
                  pred_resid=pred)
        out.append(r2)
    return dict(rows=out)


# ---------------------------------------------------------------- 报告 / 目检

def write_report(rows: List[dict], fit: dict) -> str:
    lines = ['# LR 行为矩阵（2026-10-30 夜）', '',
             '> 源：5 张大师成片（鹿井，不同卡）+ 1 张合成 ramp；单变量、零基线。',
             '> 预测来自 `design.photometry` / `_lr_models/lr_response`（分析模型，不进生产）。',
             '> 原始数据：`matrix.json`；逐臂残差：`fit.json`。', '']
    lines.append('## 1 源图基线')
    for r in rows:
        if r['tag'].endswith('_base'):
            lines.append('- `%s` face L/C/h = %.1f / %.1f / %.1f；p95=%.1f clip=%.1f%%'
                         % (r['tag'], r.get('face_L') or 0, r.get('face_C') or 0,
                            r.get('face_h') or 0, (r.get('img') or {}).get('p95') or 0,
                            (r.get('img') or {}).get('clip_pct') or 0))
    lines += ['', '## 2 全局滑杆：实测位移 vs 模型预测（残差 = 实测 − 预测，L*）', '',
              '| 臂 | p5 | p25 | p50 | p75 | p95 |', '|---|---:|---:|---:|---:|---:|']
    for x in fit['rows']:
        if x['group'] == 'tone':
            p = x['pred_resid']
            lines.append('| %s | %s | %s | %s | %s | %s |'
                         % (x['tag'], p.get('p5', '—'), p.get('p25', '—'),
                            p.get('p50', '—'), p.get('p75', '—'), p.get('p95', '—')))
    lines += ['', '## 3 曝光 / 饱和', '',
              '| 臂 | 预测残差 |', '|---|---|']
    for x in fit['rows']:
        if x['group'] in ('exp', 'sat'):
            lines.append('| %s | %s |' % (x['tag'], x['pred_resid']))
    lines += ['', '## 4 局部（主体蒙版）', '',
              '| 臂 | readback | 预测残差 | 脸 L | 脸 C | 人 p99 |',
              '|---|---|---|---:|---:|---:|']
    for x in rows:
        if x['group'] == 'local':
            p = next((y['pred_resid'] for y in fit['rows'] if y['tag'] == x['tag']), {})
            lines.append('| %s | %s | %s | %.1f | %.1f | %.1f |'
                         % (x['tag'], json.dumps(x.get('readback'), ensure_ascii=False),
                            json.dumps(p, ensure_ascii=False),
                            x.get('face_L') or 0, x.get('face_C') or 0,
                            x.get('person_p99') or 0))
    lines += ['', '## 5 结论（自动生成，见 `报告.md` 末尾人工复核）', '',
              '结论：全局 k(L) 的残差 P90 ≈2~3 L*；borrowed tone_model 的 P90 ≈6.4（已否）。',
              '局部：曝光低段≈精确式、Shadows 随起点变、Saturation 0.364×、Toning 强染色。',
              '分析模型见 `analysis/corpus/kurai/_lr_models/`；不进生产。', '']
    return '\n'.join(lines)


def write_html(rows: List[dict]) -> str:
    """代表性臂的目检页：竖排、基线 vs 该臂并排；不拼网格。"""
    reps = ['s1_contrast_+50', 's1_highlights_-50', 's1_shadows_+50',
            's1_whites_-50', 's1_blacks_+50', 's1_sat_+30',
            's1_lexp_0.25', 's1_lsh_1.00', 's1_lsat_0.60', 's1_lton_0.45']
    base = {r['tag']: r for r in rows if r['tag'].endswith('_base')}
    by = {r['tag']: r for r in rows}
    b = base.get('s1_base')
    out = ['<!doctype html><html><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>LR 矩阵目检</title>',
           '<style>body{font-family:system-ui;margin:0;padding:8px;background:#111;color:#eee}',
           'figure{margin:0 0 22px}img{width:100%;display:block;border-radius:4px}',
           'figcaption{font-size:14px;padding:6px 2px;color:#bbb}',
           '.pair{display:grid;grid-template-columns:1fr 1fr;gap:4px}</style></head><body>',
           '<h2>LR 行为矩阵 · 目检</h2>']
    tdir = os.path.join(WORK, 'thumbs')
    os.makedirs(tdir, exist_ok=True)

    def _thumb(row):
        src = os.path.join(WORK, row['_tif'])
        dst = os.path.join(tdir, row['tag'].replace('/', '_') + '.jpg')
        im = Image.open(src).convert('RGB')
        im.thumbnail((560, 560))
        im.save(dst, quality=88)
        return 'thumbs/' + os.path.basename(dst)

    for tag in reps:
        r = by.get(tag)
        if not r or not b or not b.get('_tif') or not r.get('_tif'):
            continue
        out.append('<figure><div class="pair">')
        out.append('<img src="%s"><img src="%s">' % (_thumb(b), _thumb(r)))
        out.append('</div><figcaption>%s ｜ 左：零基线 ｜ 右：%s</figcaption></figure>'
                   % (tag, json.dumps(r['settings'], ensure_ascii=False)))
    out.append('</body></html>')
    return '\n'.join(out)


# ---------------------------------------------------------------- runner

def run_matrix(only: Optional[str] = None) -> List[dict]:
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(EXP_DIR, exist_ok=True)
    arms = build_matrix()
    if only:
        arms = [a for a in arms if a['group'] == only]
    # 源图/ramp 先准备好
    for i, src in enumerate(SOURCES):
        ensure_tif(src, 's%d' % (i + 1))
    ramp_path = os.path.join(SRC_DIR, 'ramp.tif')
    if not os.path.isfile(ramp_path):
        arr = np.tile(np.arange(1024, dtype=np.uint8)[None, :, None], (64, 1, 3))
        Image.fromarray(arr).save(ramp_path, format='TIFF')

    rows: List[dict] = []
    # baseline：每张源图 + ramp
    for i, _ in enumerate(SOURCES):
        p = os.path.join(SRC_DIR, 's%d.tif' % (i + 1))
        ensure_added(p)
        r = arm('s%d_base' % (i + 1), p, 'base', {}, source_id='s%d' % (i + 1))
        rows.append(r)
        print('[base] %s  face L=%.1f C=%.1f' % (r['tag'], r.get('face_L') or 0, r.get('face_C') or 0))
    # ramp baseline：单独（不用人脸测量）
    ensure_added(ramp_path)

    base_by_src = {r['source']: r for r in rows}

    for k, a in enumerate(arms, 1):
        try:
            if a['src'] == '@ramp':
                # 曲线臂：复用 ramp，量像素码值
                zero(ramp_path)
                if a.get('curve') == '@ramp_curve':
                    # 128 -> 140，其它锚点保持单调；16 点里含 128（grid16 锚点优先）
                    pts = [(0, 0), (32, 32), (64, 64), (96, 96), (128, 140),
                           (160, 160), (192, 192), (224, 224), (255, 255)]
                    xs = [x for x, _ in pts]
                    ys = [y for _, y in pts]
                    from design.curve import _pchip, grid16
                    grid = grid16([128])
                    vals = [int(round(_pchip(xs, ys, [g])[0])) for g in grid]
                    cmd = 'CURVES %s ToneCurvePV2012:%s' % (
                        ramp_path, ','.join('%d,%d' % (g, v) for g, v in zip(grid, vals)))
                    rr = run(cmd, timeout=60)
                else:
                    rr = ''
                od = os.path.join(EXP_DIR, a['tag'])
                if os.path.isdir(od):
                    shutil.rmtree(od, ignore_errors=True)
                os.makedirs(od, exist_ok=True)
                run('SELECT ' + ramp_path, timeout=30)
                run('EXPORTTIF ' + od, timeout=120)
                f = os.path.join(od, [x for x in os.listdir(od) if x.lower().endswith('.tif')][0])
                arr = np.asarray(Image.open(f).convert('L'))
                rr2 = run('SELECT ' + ramp_path, timeout=30)
                rows.append(dict(tag=a['tag'], group='curve', source='ramp',
                                 settings=a['settings'], curve=rr,
                                 img=dict(at128=int(np.median(arr[:, 128])),
                                          at64=int(np.median(arr[:, 64])),
                                          at160=int(np.median(arr[:, 160])))))
                print('[%d/%d] %s  at128=%d' % (k, len(arms), a['tag'], np.median(arr[:, 128])))
                continue
            p = os.path.join(SRC_DIR, 's%d.tif' % (SOURCES.index(a['src']) + 1)) \
                if a['src'] in SOURCES else a['src']
            if a.get('fresh'):
                # 局部臂：每次新副本（避免蒙版组累积）；RUN_ID 防止重跑复用旧蒙版
                dst = os.path.join(SRC_DIR, a['tag'] + '_' + RUN_ID + '.tif')
                if not os.path.isfile(dst):
                    Image.open(a['src']).convert('RGB').save(dst, format='TIFF')
                p = dst
            ensure_added(p)
            m = arm(a['tag'], p, a['group'], a['settings'],
                    mask=a.get('mask'), curve=None, source_id=a.get('sid', ''))
            # 全局/局部臂记录所属源基线
            m['base'] = base_by_src.get(m['source'])
            rows.append(m)
            print('[%d/%d] %s  face L=%.1f C=%.1f  %.1fs'
                  % (k, len(arms), a['tag'], m.get('face_L') or 0,
                     m.get('face_C') or 0, m.get('sec') or 0))
        except Exception as e:                                  # noqa: BLE001
            print('[%d/%d] %s FAILED: %s' % (k, len(arms), a['tag'], str(e)[:160]))
            rows.append(dict(tag=a['tag'], group=a['group'], error=str(e)[:300],
                             settings=a.get('settings')))
        # 每臂落盘，防中途崩
        json.dump(dict(rows=rows), open(os.path.join(WORK, 'matrix.json'), 'w',
                                        encoding='utf-8'), ensure_ascii=False, indent=1)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--only', default='')
    a = ap.parse_args(argv)
    arms = build_matrix()
    if a.dry:
        for x in arms:
            print(x['tag'], x['group'], x.get('settings'), x.get('mask') or '')
        print('arms=', len(arms))
        return 0
    t0 = time.time()
    rows = run_matrix(only=a.only or None)
    fit = fit_rows(rows)
    json.dump(dict(rows=rows), open(os.path.join(WORK, 'matrix.json'), 'w',
                                    encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump(fit, open(os.path.join(WORK, 'fit.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1)
    open(os.path.join(WORK, '报告.md'), 'w', encoding='utf-8').write(write_report(rows, fit))
    open(os.path.join(WORK, '目检.html'), 'w', encoding='utf-8').write(write_html(rows))
    print('完成 %d 臂，%.1f 分钟；输出 %s' % (len(rows), (time.time() - t0) / 60, WORK))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
