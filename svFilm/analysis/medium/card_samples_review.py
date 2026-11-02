# -*- coding: utf-8 -*-
"""Multi-sample card review: show up to 4 masters per card and flag heterogeneous cards."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

RECIPE_LABEL = {
    'P160': 'Portra 160 + Endura', 'P400': 'Portra 400 + Endura',
    'GOLD': 'Gold 200 + Endura', 'P800': 'Portra 800 + Endura',
    'PUSH1': 'Portra 800 push1 + Endura', 'PUSH2': 'Portra 800 push2 + Endura',
    'GREEN': 'Portra 400 + CAII', 'XTRA': 'Xtra 400 + CAII',
    'EKTAR': 'Ektar 100 + Endura',
}


def key_of(name):
    if name.startswith('xhs'):
        return 'xhs'
    if name.startswith('滨田'):
        return '滨田'
    return name[:3]


def stats(path):
    im = Image.open(path).convert('RGB')
    im.thumbnail((300, 300))
    a = np.asarray(im, np.float64) / 255.0
    lab = common.srgb_to_lab(a)
    L = lab[..., 0]
    return dict(p50=float(np.median(L)),
                contrast=float(np.percentile(L, 95) - np.percentile(L, 5)),
                C=float(np.mean(np.hypot(lab[..., 1], lab[..., 2]))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--max-samples', type=int, default=4)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    thumb_dir = os.path.join(out, 'review_samples')
    os.makedirs(thumb_dir, exist_ok=True)
    rows = []
    for f in sorted(glob.glob(os.path.join(str(common.CARDS), '*.json'))):
        c = json.load(open(f, encoding='utf-8'))
        key = key_of(c['name'])
        recipe = (c.get('medium') or {}).get('_lookbook', {}).get('recipe')
        samples = (c.get('samples') or [])[:args.max_samples]
        root = common.MASTER_ROOTS.get(c.get('corpus'), common.MASTER_ROOTS['鹿井'])
        imgs = []
        for i, s in enumerate(samples):
            src = root / s
            if not src.is_file():
                continue
            dst = os.path.join(thumb_dir, '%s_%d.jpg' % (key, i))
            im = Image.open(src).convert('RGB'); im.thumbnail((220, 280)); im.save(dst, quality=88)
            st = stats(src)
            imgs.append({'path': os.path.relpath(dst, out).replace('\\', '/'),
                         'name': s, **st})
        p50s = [x['p50'] for x in imgs]; ks = [x['contrast'] for x in imgs]
        spread = dict(p50=round(max(p50s) - min(p50s), 1) if p50s else 0,
                      contrast=round(max(ks) - min(ks), 1) if ks else 0)
        flag = spread['p50'] > 40 or spread['contrast'] > 35
        rows.append({'name': c['name'], 'key': key, 'recipe': recipe,
                     'label': RECIPE_LABEL.get(recipe, recipe or ''),
                     'samples': imgs, 'spread': spread, 'flag': flag})

    flagged = [r for r in rows if r['flag']]
    css = '''
    body{margin:0;background:#f7f7f5;color:#1f2937;font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
    .wrap{max-width:1500px;margin:0 auto;padding:24px}
    h1{font-size:23px;margin:0 0 6px} .sub{color:#6b7280;margin-bottom:14px}
    table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;table-layout:fixed}
    th,td{padding:7px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top;font-size:12px}
    th{background:#fafafa;text-align:left;position:sticky;top:0}
    th:nth-child(1),td:nth-child(1){width:250px}
    th:nth-child(2),td:nth-child(2){width:300px}
    th:nth-child(3),td:nth-child(3){width:580px}
    th:nth-child(4),td:nth-child(4){width:130px}
    th:nth-child(5),td:nth-child(5){width:90px}
    .sg{width:544px;table-layout:fixed;border-collapse:collapse}.sg td{border:0;padding:0 3px;width:130px;vertical-align:top}
    .sg img{width:130px;height:165px;object-fit:cover;border:1px solid #e5e7eb;border-radius:5px;display:block}
    .cap{font-size:10px;color:#6b7280;line-height:1.3}
    .s{width:130px}.s img{width:130px;height:165px;object-fit:cover;border:1px solid #e5e7eb;border-radius:5px;display:block}
    .s .cap{font-size:10px;color:#6b7280;line-height:1.3}
    .flag{background:#fff1f2}.pill{display:inline-block;padding:1px 7px;border-radius:999px;background:#eef2ff;color:#3730a3;font-size:10px}
    .bad{color:#b91c1c;font-weight:600}
    '''
    trs = []
    for r in rows:
        imgs = '<table class="sg"><tr>' + ''.join(
            f'<td><a href="{x["path"]}" target="_blank"><img src="{x["path"]}"></a>'
            f'<div class="cap">p50 {x["p50"]:.0f} · k {x["contrast"]:.0f} · C {x["C"]:.0f}</div></td>'
            for x in r['samples']) + '</tr></table>'
        trs.append(f'<tr class="{"flag" if r["flag"] else ""}"><td>{r["name"]}</td>'
                   f'<td><span class="pill">{r["label"]}</span></td><td>{imgs}</td>'
                   f'<td>p50 {r["spread"]["p50"]:.0f}<br>k {r["spread"]["contrast"]:.0f}</td>'
                   f'<td>{"<span class=bad>查/拆</span>" if r["flag"] else "ok"}</td></tr>')
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>64 卡多样片复核</title>
<style>{css}</style></head><body><div class="wrap">
<h1>64 卡多样片复核</h1>
<div class="sub">每卡最多 4 张样片；p50/contrast 是 sRGB D65 Lab 的简单统计，只用于发现极端曝光/内容跨度，不用于反推卷纸，也不自动拆卡。阈值：p50 差 &gt;40 或 contrast 差 &gt;35 标红。</div>
<div class="sub">标红 {len(flagged)} / {len(rows)}：{'、'.join(r['name'] for r in flagged) or '无'}</div>
<table><thead><tr><th>卡</th><th>当前配方</th><th>样片</th><th>卡内跨度</th><th>结论</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div></body></html>'''
    path = os.path.join(out, 'card_samples_review.html')
    open(path, 'w', encoding='utf-8').write(html)
    json.dump(rows, open(os.path.join(out, 'card_samples_review.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('cards', len(rows), 'flagged', len(flagged))
    for r in flagged:
        print('FLAG', r['name'], r['label'], r['spread'])
    print('wrote', path)


if __name__ == '__main__':
    main()
