# -*- coding: utf-8 -*-
"""分离色调 split_b：阴影 / 高光各自的中位色偏（绝对阈值，交付域口径）。

口径（`设计真源.md` / `测量设计.md` §8.2）：
  阴影：L* < 20，像素 ≥ 0.5% 画面
  高光：L* > 80，像素 ≥ 0.5% 画面
  各取 b* 中位 + C* 中位 ⇒ split_b = {shadow_b, high_b, shadow_C, high_C}
  像素不足 ⇒ None（unavailable，不许给 0）

域：交付域 sRGB / 长边 1080。

用法：
  & $py -X utf8 split_b.py --n 20 --out _split_b_pilot.json
  & $py -X utf8 split_b.py --all --out split_b.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, r'E:\ds\photo')
from _lib.measure import open_rgb, to_lab900  # noqa: E402

M = r'E:\ds\photo\_corpus\kurai\full\m_%s_v4.json'
BASE = {'鹿井': r'E:\ds\大师成片\鹿井', '滨田': r'E:\ds\大师成片\滨田', 'xhs': r'E:\ds\大师成片\xhs'}

L_SHADOW, L_HIGH = 20.0, 80.0
MIN_SHARE = 0.005


def split_of(lab: np.ndarray) -> dict:
    L = lab[..., 0]
    b = lab[..., 2]
    C = np.hypot(lab[..., 1], lab[..., 2])
    out = dict(n_px=int(L.size))
    for name, m in (('shadow', L < L_SHADOW), ('high', L > L_HIGH)):
        share = float(m.mean())
        out[name + '_share'] = round(share, 5)
        if share >= MIN_SHARE and int(m.sum()) >= 300:
            out[name + '_b'] = round(float(np.median(b[m])), 3)
            out[name + '_C'] = round(float(np.median(C[m])), 3)
        else:
            out[name + '_b'] = None
            out[name + '_C'] = None
    out['ok'] = (out['shadow_b'] is not None) and (out['high_b'] is not None)
    if out['ok']:
        out['split'] = round(out['high_b'] - out['shadow_b'], 3)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', default='鹿井')
    ap.add_argument('--n', type=int, default=20, help='只跑前 n 张（试点）；0 = 全部')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out', default='')
    a = ap.parse_args(argv)

    d = json.load(open(M % a.corpus, encoding='utf-8'))
    rows = [r for r in d.get('per_image') or [] if r.get('file')]
    if not a.all and a.n > 0:
        rows = rows[:a.n]

    base = BASE[a.corpus]
    out, bad = [], 0
    for r in rows:
        p = os.path.join(base, r['file'].replace('/', os.sep))
        try:
            lab = to_lab900(open_rgb(p), side=1080)
            s = split_of(lab)
        except Exception as e:                                # noqa: BLE001
            bad += 1
            s = dict(ok=False, err=str(e)[:80])
        s['file'] = r['file']
        out.append(s)

    ok = sum(1 for s in out if s.get('ok'))
    print('%s：%d 张，可用 %d（%.0f%%），坏 %d' % (a.corpus, len(out), ok, 100.0 * ok / max(len(out), 1), bad))
    for k in ('shadow_share', 'high_share'):
        v = [s[k] for s in out if s.get(k) is not None]
        if v:
            print('  %s 中位 %.3f%% / 最小 %.3f%% / 最大 %.3f%%'
                  % (k, 100 * np.median(v), 100 * min(v), 100 * max(v)))
    sp = [s['split'] for s in out if s.get('split') is not None]
    if sp:
        print('  split（高光 b − 阴影 b）：中位 %.2f / IQR %.2f' % (np.median(sp), np.subtract(*np.percentile(sp, [75, 25]))))
    no_sh = sum(1 for s in out if s.get('shadow_b') is None)
    no_hi = sum(1 for s in out if s.get('high_b') is None)
    print('  阴影不足 %d 张 · 高光不足 %d 张' % (no_sh, no_hi))

    if a.out:
        json.dump(dict(corpus=a.corpus, n=len(out), ok=ok, per_image=out),
                  open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('写出 %s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
