# -*- coding: utf-8 -*-
"""把 canary 的 20 张做成目检页：一行一张，带 ROI/取样点标记与读数。

用法：& $py -X utf8 build_canary_html.py <canary.json> [out.html]
"""
from __future__ import annotations

import json
import os
import sys

from PIL import Image, ImageDraw

SRC = sys.argv[1] if len(sys.argv) > 1 else r'E:\ds\photo\_corpus\kurai\full\_canary20f.json'
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(SRC), 'canary20.html')
PREV = os.path.join(os.path.dirname(OUT), 'canary_prev')

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:14px 14px 60px}
h1{font-size:19px;margin:6px 0 2px;font-weight:600}
.sub{color:#8f8b84;font-size:13px;margin:0 0 14px}
.row{border-top:1px solid #2e2d2b;padding:16px 0}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;margin:0 0 8px}
.name{font-weight:600;color:#fff}
.num{font-variant-numeric:tabular-nums;color:#9aa7b4}
.strict{color:#7fd08a}
.loose{color:#e8c08c}
.bad{color:#e08c8c}
.flex{display:flex;gap:10px;align-items:flex-start}
.big{flex:1 1 auto}
.zoom{flex:0 0 200px}
img{width:100%;height:auto;display:block;border-radius:8px;background:#000}
.cap{color:#8f8b84;font-size:12.5px;margin:6px 0 0}
.foot{color:#6f6b64;font-size:12px;margin:26px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
"""


def draw_one(rec, outdir):
    p = rec['file']
    im = Image.open(p).convert('RGB')
    dr = ImageDraw.Draw(im)
    fb = (rec.get('face') or {}).get('box')
    if fb:
        x, y, w, h = fb
        dr.rectangle([x, y, x + w, y + h], outline=(120, 220, 140), width=3)
    sk = rec.get('skin') or {}
    xy = sk.get('xy')
    if xy and fb:
        fw = fb[2]
        r = 0.13 * fw * 1.35
        dr.ellipse([xy[0] - r, xy[1] - r * 0.9, xy[0] + r, xy[1] + r * 0.9],
                   outline=(255, 210, 90), width=3)
        dr.ellipse([xy[0] - 5, xy[1] - 5, xy[0] + 5, xy[1] + 5], fill=(255, 60, 60))
    big = os.path.join(outdir, 'a_%s.jpg' % os.path.basename(os.path.dirname(p))[:18] + '_' + os.path.basename(p))
    im2 = im.copy()
    if im2.width > 900:
        im2 = im2.resize((900, round(im2.height * 900 / im2.width)), Image.LANCZOS)
    im2.save(big, quality=86)
    zoom = None
    if fb:
        x, y, w, h = fb
        m = 0.45 * max(w, h)
        box = (max(0, x - m), max(0, y - m), min(im.width, x + w + m), min(im.height, y + h + m))
        z = im.crop(box)
        z = z.resize((240, round(z.height * 240 / z.width)), Image.LANCZOS)
        zoom = os.path.join(outdir, 'z_%s.jpg' % os.path.basename(big))
        z.save(zoom, quality=88)
    return os.path.relpath(big, os.path.dirname(OUT)), (
        os.path.relpath(zoom, os.path.dirname(OUT)) if zoom else None)


def main() -> int:
    d = json.load(open(SRC, encoding='utf-8'))
    rows = d.get('per_image') or []
    os.makedirs(PREV, exist_ok=True)
    cal = (d.get('_meta') or {}).get('caliber') or {}
    n_ok = sum(1 for r in rows if r.get('skin'))
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>canary 20 · 肤色目检</title><style>%s</style></head><body><div class="wrap">' % CSS,
         '<h1>canary 20 · 肤色目检</h1>',
         '<p class="sub">%d 张，有值 %d ｜ 口径 %s ｜ 黄圈=ROI · 红点=取样点 · 绿框=脸检测框<br>'
         '看两件事：<b>点有没有落在脸上</b>、<b>这个 L/C 像不像这张脸</b>。'
         '</p>' % (len(rows), n_ok, cal.get('sha', ''))]
    for i, r in enumerate(rows, 1):
        sk = r.get('skin') or {}
        err = r.get('skin_err')
        big, zoom = draw_one(r, PREV)
        if sk:
            cls = 'strict' if sk.get('src') == 'strict' else 'loose'
            tag = 'strict' if sk.get('src') == 'strict' else 'loose（严格口径弃权:%s）' % (sk.get('strict_why') or '')
            num = 'L %.1f ｜ C %.1f ｜ h %.0f ｜ ITA %+.0f ｜ n %d' % (
                sk['L'], sk['C'], sk['h'], sk.get('ita') or 0, sk.get('n') or 0)
        else:
            cls, tag, num = 'bad', '弃权：%s' % err, '—'
        h.append('<div class="row">')
        h.append('<div class="hd"><span class="name">%d. %s</span>'
                 '<span class="num">%s</span></div>'
                 % (i, os.path.basename(os.path.dirname(r['file']))[:22], num))
        h.append('<div class="flex"><div class="big"><img loading="lazy" src="%s"></div>' % big)
        if zoom:
            h.append('<div class="zoom"><img loading="lazy" src="%s"></div>' % zoom)
        h.append('</div>')
        h.append('<p class="cap %s">%s ｜ %s</p>'
                 % (cls, tag, os.path.basename(r['file'])))
        h.append('</div>')
    h.append('<p class="foot">strict = 掩膜 + ROI + off_face 检查；loose = 严格口径弃权后的放宽值，'
             '通常会偏亮、彩度偏低（墙/白衣服/高光），**不能与 strict 混在一起算中位**。</p>')
    h.append('</div></body></html>')
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    print(OUT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
