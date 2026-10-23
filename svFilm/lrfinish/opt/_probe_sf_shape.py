# -*- coding: utf-8 -*-
r"""摸规律④：**SF 底片自己的形状分布** vs 鹿井 52 张精选。

为什么（SV：「要摸索出那个规律…不要为了对齐数值而打靶」）：
  · 拿我们 3 张底片比是"个案"，说不清"我们的底片天生如此"还是"这 3 张碰巧";
  · 这里把**引擎已经出过的 21 张底片**全量算一遍形状，跟 52 张大师比**分布**，
    比的是两条分布的形状差异 —— 不是把某一组的中点当靶子。

口径与 `_probe_52_shape.py` 完全一致：384 长边 → Lab → uint8 编码 → 五段中性轨迹。
  ⚠ 各轮底片来自引擎不同开发阶段（参数有差），所以先**按轮分组**看；
    如果各轮中位一致 ⇒ 说明这是引擎/胶片本身的性质，不是某一轮的参数。

产物：shape/sf_shape.json + 屏显
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

import _boot  # noqa: E402
import paths
paths.ensure_import_path()
from _probe_masters_shape import NAMES, traj, shape, pct    # noqa: E402
from _probe_52_shape import load_lab384, OUT               # noqa: E402

SF = str(paths.BATCH_ROOT)
# 优先级：我们正在调的那一轮排最前；同一张号只取第一个命中的轮次
RUNS = ('_b2048_1022', '_regress_1023', '_lrbase_919_2048_full',
        '_grain_1017_2048', '_v3run_2048', '_style4', '_lrbase_2048_full',
        '_lrbase_919_1024_full')
# 明确排除：性能/颗粒探针轮次（同一张图重复渲染，没有独立意义）
SKIP = ('_perf_', '_probe', '_grain_probe', '_test', '_native', '_cmp_', '_final')


def collect():
    seen, out = {}, []
    for run in RUNS:
        pat = os.path.join(SF, run, '*_svFilm_full.tif')
        for p in sorted(glob.glob(pat)):
            stem = os.path.basename(p).replace('_svFilm_full.tif', '')
            if stem in seen:
                continue
            seen[stem] = run
            out.append((stem, run, p))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    items = collect()
    print('SF 底片共 %d 张（不重复），来自 %d 轮\n'
          % (len(items), len(set(r for _, r, _ in items))))

    rows = []
    for stem, run, p in items:
        na, nb, share, npx = traj(load_lab384(p).astype(np.float64))
        r = dict(stem=stem, run=run, na=na, nb=nb, share=share)
        r.update(shape(na, nb))
        rows.append(r)
    good = [r for r in rows if r.get('n_ok', 0) >= 4]
    print('五段都够像素的 %d 张\n' % len(good))

    ms = json.load(open(os.path.join(OUT, 'masters52_shape.json'), encoding='utf-8'))['rows']
    msg = [r for r in ms if r.get('n_ok', 0) >= 4]

    print('%-12s %-22s %-22s' % ('关系量', 'SF 底片 P10/50/90', '鹿井 52 张 P10/50/90'))
    for key in ('a_rise', 'rough_a', 'rough_b', 'b_peak_h'):
        print('%-12s %-22s %-22s'
              % (key, pct([r[key] for r in good]), pct([r[key] for r in msg])))
    print('%-12s %-22s %-22s'
          % ('b 单峰占比',
             '%.0f%%' % (100 * np.mean([r['b_mid_high'] for r in good])),
             '%.0f%%' % (100 * np.mean([r['b_mid_high'] for r in msg]))))
    print()

    # 各轮分组：若各轮一致 ⇒ 是引擎性质，不是某轮参数
    print('== 按轮分组（各轮若一致 ⇒ 这是引擎/胶片自身的性质）')
    print('%-26s %2s %-18s %-18s %-18s' % ('轮次', 'n', 'a_rise', 'rough_a', 'rough_b'))
    by = {}
    for r in good:
        by.setdefault(r['run'], []).append(r)
    for run, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print('%-26s %2d %-18s %-18s %-18s'
              % (run[:26], len(v),
                 pct([r['a_rise'] for r in v]),
                 pct([r['rough_a'] for r in v]),
                 pct([r['rough_b'] for r in v])))
    print()

    # 逐张：谁把 a_rise 拉到 3 以上？谁在 1 以下？
    print('== 逐张 a_rise（384 域，看我们落在 52 张的哪一侧）')
    for r in sorted(good, key=lambda r: r['a_rise']):
        na = ['%+.1f' % x if np.isfinite(x) else '  .' for x in r['na']]
        print('  %-10s %6.2f  rough_a %4.2f   a 五段: %s'
              % (r['stem'], r['a_rise'], r['rough_a'], ' '.join(na)))
    print()

    json.dump(dict(rows=good), open(os.path.join(OUT, 'sf_shape.json'), 'w',
              encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print('-> %s' % os.path.join(OUT, 'sf_shape.json'))


if __name__ == '__main__':
    main()
