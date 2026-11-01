# -*- coding: utf-8 -*-
"""统一尺子 v04：746 大师 + 19 机内 JPG，**1080 交付域** + 颗粒诊断。

v03 的尺子量的是原生尺寸（大师 1000–1600）；我们出片是 1080，两边不是同一工件。
v04 统一到「sRGB / 长边 1080 / PNG」再量（无损，避免 JPEG 误检），并加颗粒指标：

    amp       高通残差 σ（L*）——颗粒强度
    acf1      高通残差 lag-1 自相关
    coarse_px 0.25 / 频谱质心（cycles/px）
    ratio     σ(σ=1 高通) / σ(σ=3 高通)
    edge      边缘梯度 / 边缘对比

★ 颗粒指标只做**诊断/验证**，不从成片反解引擎参数（见 `sync_grain_from_stock.py`）。
★ 不覆盖 v03：默认写 `photos_tone_v04.json`。

跑：
    & $py -X utf8 -m analysis.corpus.kurai.build_photos_tone --jobs 6
或：
    & $py -X utf8 analysis/corpus/kurai/build_photos_tone.py --jobs 6
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai'
CARDS = os.path.join(BASE, 'cards_v5.json')
MASTER = r'E:\ds\大师成片'
OUT = os.path.join(BASE, 'photos_tone_v04.json')
OUR_JPG = r'E:\ds\photo\_run_1120_content\jpg1080'

if BASE not in sys.path:
    sys.path.insert(0, BASE)


def _one(args):
    path, meta = args
    try:
        if r'E:\svStudio\svFilm' not in sys.path:
            sys.path.insert(0, r'E:\svStudio\svFilm')
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        from design.batch import _delivery_copy
        from design.measure_all import measure_one
        import texture_probe as TP
        dp = _delivery_copy(path)
        rec = measure_one(dp)
        rec['grain'] = TP.metrics(dp)
        rec['file'] = path                      # 保留原图路径（peer_ids 要用）
        rec['delivery'] = dict(long=1080, file=os.path.basename(dp))
        rec.update(meta)
        return rec
    except Exception as e:                                # noqa: BLE001
        d = dict(meta)
        d.update(file=path, err=str(e)[:200])
        return d


def _targets():
    out, seen = [], set()

    def add(path, meta):
        p = os.path.normpath(path)
        if p in seen or not os.path.isfile(p):
            return
        seen.add(p)
        out.append((p, meta))

    cards = (json.load(open(CARDS, encoding='utf-8')).get('cards') or [])
    for c in cards:
        root = os.path.join(MASTER, c.get('corpus') or '')
        for rel in (c.get('samples') or []):
            add(os.path.join(root, rel),
                dict(card=c.get('name'), corpus=c.get('corpus'), role='master'))
    for p in sorted(glob.glob(os.path.join(OUR_JPG, '*.jpg'))):
        add(p, dict(card='', corpus='效果测试1027_2', role='ours',
                    stem=os.path.splitext(os.path.basename(p))[0]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args(argv)
    targets = _targets()
    if a.limit:
        targets = targets[:a.limit]
    print('目标 %d 张（大师 %d / 我方 %d）'
          % (len(targets),
             sum(1 for _, m in targets if m.get('role') == 'master'),
             sum(1 for _, m in targets if m.get('role') == 'ours')))
    t0 = time.time()
    rows = []
    if a.jobs > 1:
        from multiprocessing import get_context
        with get_context('spawn').Pool(a.jobs) as pool:
            for i, r in enumerate(pool.imap_unordered(_one, targets, chunksize=2), 1):
                rows.append(r)
                if i % 50 == 0:
                    print('  %d/%d  %.0fs' % (i, len(targets), time.time() - t0))
    else:
        for i, t in enumerate(targets, 1):
            rows.append(_one(t))
            if i % 50 == 0:
                print('  %d/%d  %.0fs' % (i, len(targets), time.time() - t0))
    bad = [r for r in rows if r.get('err')]
    good = [r for r in rows if not r.get('err')]
    doc = dict(_meta=dict(generated=time.strftime('%Y-%m-%d %H:%M:%S'),
                          n=len(rows), ok=len(good), bad=len(bad),
                          jobs=a.jobs, ruler='design.measure_all',
                          delivery_long=1080, delivery_format='png',
                          grain_metric='texture_probe.metrics/v1',
                          note='一把尺子 v04：746 大师 + 1027_2 机内 JPG，统一 1080 交付域'),
               per_image=rows,
               bad=[[r.get('file'), r.get('err')] for r in bad])
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    print('写出 %s ｜ ok %d / bad %d ｜ %.0f 秒'
          % (a.out, len(good), len(bad), time.time() - t0))
    if bad:
        for r in bad[:10]:
            print('  ! %s：%s' % (os.path.basename(r.get('file') or '?'), r.get('err')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
