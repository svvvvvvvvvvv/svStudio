# -*- coding: utf-8 -*-
"""Card-level WB + amount review page. No parameter changes."""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
from card_samples_review import RECIPE_LABEL, key_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    cards = []
    for f in sorted(glob.glob(os.path.join(str(common.CARDS), '*.json'))):
        c = json.load(open(f, encoding='utf-8'))
        key = key_of(c['name'])
        wb = c.get('wb') or {}
        if wb.get('temperature') is None:
            wb = dict((c.get('medium') or {}).get('wb') or {})
        thumb = os.path.join(out, 'review_samples', key + '_0.jpg')
        recipe = (c.get('medium') or {}).get('_lookbook', {}).get('recipe')
        cards.append({'name': c['name'], 'key': key, 'recipe': recipe,
                      'label': RECIPE_LABEL.get(recipe, recipe or ''),
                      'temperature': wb.get('temperature'), 'tint': wb.get('tint'),
                      'thumb': os.path.relpath(thumb, out).replace('\\', '/') if os.path.isfile(thumb) else None,
                      'amount': c.get('amount')})

    trs = []
    for c in cards:
        im = f'<img src="{c["thumb"]}" width="70" height="88" style="object-fit:cover;border-radius:4px">' if c['thumb'] else ''
        trs.append(f'<tr><td>{c["name"]}</td><td><span class="pill">{c["label"]}</span></td>'
                   f'<td>{c["temperature"]:.0f} K</td><td>{c["tint"]:.3f}</td>'
                   f'<td>{c["amount"]}</td><td>{im}</td></tr>')

    ramps = []
    for key, label in RECIPE_LABEL.items():
        imgs = []
        for a in ['0p0', '0p5', '1p0', '1p5', '2p0']:
            p = os.path.join(out, 'amount_ramp', '%s_%s.png' % (key, a))
            if os.path.isfile(p):
                imgs.append(f'<div class="a"><a href="{os.path.relpath(p, out)}"><img src="{os.path.relpath(p, out)}"></a>'
                            f'<div class="cap">amount {a.replace("p", ".")}</div></div>')
        ramps.append(f'<div class="r"><div class="rlabel">{label}</div><div class="aimgs">{"".join(imgs)}</div></div>')

    css = '''
    body{margin:0;background:#f7f7f5;color:#1f2937;font:13px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
    .wrap{max-width:1500px;margin:0 auto;padding:24px}
    h1{font-size:23px;margin:0 0 6px} h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:5px}
    .sub{color:#6b7280;margin:6px 0 14px}
    table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb}
    th,td{padding:5px 7px;border-bottom:1px solid #e5e7eb;font-size:11px;vertical-align:middle}
    th{background:#fafafa;text-align:left}
    .pill{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:999px;padding:1px 7px;font-size:10px}
    .r{background:#fff;border:1px solid #e5e7eb;border-radius:9px;padding:8px;margin:8px 0}
    .rlabel{font-size:12px;font-weight:600;margin-bottom:5px}
    .aimgs{display:flex;gap:5px}
    .a{width:180px}.a img{width:180px;height:120px;object-fit:cover;border:1px solid #e5e7eb;border-radius:4px;display:block}
    .cap{font-size:10px;color:#6b7280}
    '''
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>WB / amount 卡级复核</title>
<style>{css}</style></head><body><div class="wrap">
<h1>WB / amount 卡级复核</h1>
<div class="sub">WB 是卡级 RAW 参数；这里只列 64 卡的当前值，不做逐图反解。amount 是配方级；当前全部 1.0，下面是 9 配方 ramp（作者测试人像）。</div>

<h2>WB（64 卡，卡级）</h2>
<table><thead><tr><th>卡</th><th>配方</th><th>temperature</th><th>tint</th><th>amount</th><th>样片</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>

<h2>amount ramp（9 配方 × 0/0.5/1/1.5/2）</h2>
<div class="sub">1.5/2.0 出现 face C/contrast 崩；当前保持 1.0。</div>
{''.join(ramps)}

</div></body></html>'''
    path = os.path.join(out, 'wb_amount_review.html')
    open(path, 'w', encoding='utf-8').write(html)
    print('wrote', path, 'cards', len(cards))


if __name__ == '__main__':
    main()
