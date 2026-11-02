# -*- coding: utf-8 -*-
"""Build the 64-card visual assignment page and CSV from assignments.json.

Usage:
  python analysis/medium/assign.py
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

RECIPES = {
    'P160': dict(label='Portra 160 + Endura', img='lookbook/kodak_portra_160__kodak_portra_endura.png',
                 note='C 11.4（最低）· 高-key 柔'),
    'P400': dict(label='Portra 400 + Endura', img='lookbook/kodak_portra_400__kodak_portra_endura.png',
                 note='C 12.8 · 中性柔'),
    'GOLD': dict(label='Gold 200 + Endura', img='lookbook/kodak_gold_200__kodak_portra_endura.png',
                 note='hue 40.7 · 暖黄'),
    'P800': dict(label='Portra 800 + Endura', img='lookbook/kodak_portra_800__kodak_portra_endura.png',
                 note='hue 42.6 · 暖、柔'),
    'PUSH1': dict(label='Portra 800 push1 + Endura', img='lookbook/kodak_portra_800_push1__kodak_portra_endura.png',
                  note='contrast 62.2 · C 15.1'),
    'PUSH2': dict(label='Portra 800 push2 + Endura', img='lookbook/kodak_portra_800_push2__kodak_portra_endura.png',
                  note='contrast 64.9 · C 17.6'),
    'GREEN': dict(label='Portra 400 + CAII（绿纸）', img='cross/kodak_portra_400__fujifilm_crystal_archive_typeii.png',
                  note='leaf a −10.4 · CAII 加绿'),
    'XTRA': dict(label='Xtra 400 + CAII', img='lookbook/fujifilm_xtra_400__fujifilm_crystal_archive_typeii.png',
                 note='contrast 67.1 · C 17.3'),
    'EKTAR': dict(label='Ektar 100 + Endura', img='lookbook/kodak_ektar_100__kodak_portra_endura.png',
                  note='hue 38.2 · 红/暖'),
}


def card_key(name: str) -> str:
    if name.startswith('xhs'):
        return 'xhs'
    if name.startswith('滨田'):
        return '滨田'
    return name[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(common.OUT_DEFAULT))
    ap.add_argument('--cards', default=str(common.CARDS))
    ap.add_argument('--assignments', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assignments.json'))
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    thumbs = os.path.join(out, 'thumbs')
    os.makedirs(thumbs, exist_ok=True)
    assign = json.load(open(args.assignments, encoding='utf-8'))

    cards = []
    for f in sorted(os.listdir(args.cards)):
        if not f.endswith('.json'):
            continue
        c = json.load(open(os.path.join(args.cards, f), encoding='utf-8'))
        samples = c.get('samples') or []
        if not samples:
            continue
        root = common.MASTER_ROOTS.get(c.get('corpus'), common.MASTER_ROOTS['鹿井'])
        src = root / samples[0]
        if not src.is_file():
            print('missing sample', src)
            continue
        key = card_key(c['name'])
        rec = assign.get(key, 'P400')
        if rec not in RECIPES:
            print('unknown recipe', key, rec, '-> P400')
            rec = 'P400'
        tp = os.path.join(thumbs, key + '.jpg')
        im = Image.open(src).convert('RGB')
        im.thumbnail((220, 280))
        im.save(tp, quality=88)
        cards.append({'name': c['name'], 'key': key, 'recipe': rec,
                      'thumb': os.path.relpath(tp, out).replace('\\', '/')})

    counts = Counter(c['recipe'] for c in cards)
    palette = []
    for k, r in RECIPES.items():
        palette.append(f'''
        <div class="recipe">
          <a href="{r['img']}" target="_blank"><img src="{r['img']}" alt="{k}"></a>
          <div class="rlabel"><b>{r['label']}</b><br><span class="muted">{r['note']}</span></div>
          <div class="count">{counts.get(k, 0)} 张</div>
        </div>''')

    rows = []
    for c in cards:
        r = RECIPES[c['recipe']]
        rows.append(f'''
        <tr data-recipe="{c['recipe']}" data-name="{c['name']}">
          <td>{c['name']}</td>
          <td><img class="thumb" src="{c['thumb']}" alt="master"></td>
          <td><span class="pill">{r['label']}</span></td>
          <td><a href="{r['img']}" target="_blank"><img class="look" src="{r['img']}" alt="look"></a></td>
          <td class="muted">{r['note']}</td>
        </tr>''')

    css = '''
    body{margin:0;background:#f7f7f5;color:#1f2937;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
    .wrap{max-width:1500px;margin:0 auto;padding:26px 20px 60px}
    h1{font-size:25px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px;border-bottom:1px solid #e5e7eb;padding-bottom:5px}
    .sub,.muted{color:#6b7280}
    .note{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:13px 15px;margin:12px 0}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
    .recipe{position:relative;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
    .recipe img{width:100%;height:220px;object-fit:cover;display:block}
    .rlabel{padding:7px 9px;font-size:11px;line-height:1.35}
    .count{position:absolute;top:7px;right:7px;background:#111827;color:#fff;font-size:10px;padding:1px 6px;border-radius:999px}
    .controls{display:flex;gap:10px;align-items:center;margin:10px 0}
    input,select{padding:6px 8px;border:1px solid #d1d5db;border-radius:7px;background:#fff;font:13px inherit}
    table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden}
    th,td{padding:7px 9px;border-bottom:1px solid #e5e7eb;vertical-align:middle;font-size:12px}
    th{background:#fafafa;text-align:left;font-size:11px;color:#374151}
    tr:last-child td{border-bottom:0}
    .thumb{width:80px;height:100px;object-fit:cover;border-radius:5px;border:1px solid #e5e7eb}
    .look{width:100px;height:125px;object-fit:cover;border-radius:5px;border:1px solid #e5e7eb}
    .pill{display:inline-block;background:#eef2ff;color:#3730a3;border:1px solid #c7d2fe;border-radius:999px;padding:2px 8px;font-size:10.5px}
    .hint{font-size:11px;color:#6b7280}
    @media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}}
    '''

    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>64 卡视觉配方</title><style>{css}</style></head><body><div class="wrap">
<h1>64 卡视觉配方</h1>
<p class="sub">look book 是尺子，大师样片是目标；每张卡一个视觉提案，不是自动匹配结论。</p>
<div class="note">
<b>口径：</b>同一张作者测试图、同一 auto exposure/PE、grain/halation 开；纸按 target_print 或明确跨配对（GREEN 用 CAII）。<br>
<b>没有用：</b>旧预设/卡38/xhs、旧 amount、旧卡统计、现实胶卷资料。<br>
<b>注意：</b>Kodak 单支卷之间差异很细；这里是族/配方级提案，低置信的卡可以只认“族”，不认单支。
</div>

<h2>配方 palette</h2>
<div class="grid">{''.join(palette)}</div>

<h2>64 卡</h2>
<div class="controls">
  <input id="q" placeholder="搜卡名，如 001 / 101" oninput="filter()">
  <select id="r" onchange="filter()"><option value="">全部配方</option>{''.join(f'<option value="{k}">{v["label"]}</option>' for k, v in RECIPES.items())}</select>
  <span class="hint" id="count"></span>
</div>
<table><thead><tr><th>卡</th><th>大师样片</th><th>视觉配方</th><th>候选 look</th><th>测量依据</th></tr></thead>
<tbody id="tbody">{''.join(rows)}</tbody></table>

<h2>说明</h2>
<div class="note">
这是视觉提案：每张卡看第一张样片 + look book 选的配方。逐卡风格仍由 amount/PE/WB 承担；卷/纸只负责整体质感，不追求 64 个唯一。
</div>
</div>
<script>
function filter(){{
  const q=document.getElementById('q').value.trim();
  const r=document.getElementById('r').value;
  let n=0;
  document.querySelectorAll('#tbody tr').forEach(tr=>{{
    const ok=(!q||tr.dataset.name.includes(q))&&(!r||tr.dataset.recipe===r);
    tr.style.display=ok?'':'none'; if(ok)n++;
  }});
  document.getElementById('count').textContent=n+' / {len(cards)}';
}}
filter();
</script></body></html>'''
    html_path = os.path.join(out, 'all_cards_assignment.html')
    open(html_path, 'w', encoding='utf-8').write(html)
    print('wrote', html_path, 'cards', len(cards))

    csv_path = os.path.join(out, 'card_recipes.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['name', 'key', 'recipe', 'label'])
        w.writeheader()
        for c in cards:
            w.writerow({'name': c['name'], 'key': c['key'], 'recipe': c['recipe'],
                        'label': RECIPES[c['recipe']]['label']})
    print('wrote', csv_path)
    print('counts', dict(counts))


if __name__ == '__main__':
    main()
