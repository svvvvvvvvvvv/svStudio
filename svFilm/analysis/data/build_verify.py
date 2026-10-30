# -*- coding: utf-8 -*-
"""验收页（§56 步 7）：① vs ③ 一行一张，带"人的爆白"和关系读数。

用法：& $py -X utf8 build_verify.py
"""
from __future__ import annotations

import json
import os
import sys

OUT = r'E:\ds\photo\_run_1030_card004'
ROWS = os.path.join(OUT, 'verify_rows.json')
PREV = os.path.join(OUT, 'preview')
HTML = os.path.join(OUT, '验收.html')

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:14px 14px 60px}
h1{font-size:19px;margin:6px 0 2px;font-weight:600}
.sub{color:#8f8b84;font-size:13px;margin:0 0 14px}
.row{border-top:1px solid #2e2d2b;padding:16px 0}
.hd{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;margin:0 0 8px;flex-wrap:wrap}
.name{font-weight:600;color:#fff}
.num{color:#9aa7b4;font-variant-numeric:tabular-nums}
.ok{color:#7fd08a}.bad{color:#e08c8c}.warn{color:#e8c08c}
.pair{display:flex;gap:8px}
.pair figure{flex:1;margin:0}
.pair img{width:100%;display:block;border-radius:8px;background:#000}
figcaption{color:#8f8b84;font-size:12px;margin:4px 0 0}
.foot{color:#6f6b64;font-size:12px;margin:26px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
"""


def main() -> int:
    rows = json.load(open(ROWS, encoding='utf-8'))
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>卡004 · 验收</title><style>%s</style></head><body><div class="wrap">' % CSS,
         '<h1>卡 004 · 一次算的第一次执行</h1>',
         '<p class="sub">每行：左 ①（引擎底片）· 右 ③（LR 出片）。'
         '读数看三件：<b>脸 L</b>、<b>人的亮部 p99 / 爆白</b>、<b>人÷背景</b>。</p>']
    for r in rows:
        st = r['stem']
        d_face = (r['face3'] or 0) - (r['face1'] or 0)
        d_p99 = (r['p99_3'] or 0) - (r['p99_1'] or 0)
        blown = (r['clip3'] or 0) > max(0.5, (r['clip1'] or 0) + 0.3)
        cls = 'bad' if blown else ('warn' if d_p99 > 8 else 'ok')
        h.append('<div class="row"><div class="hd"><span class="name">%s</span>'
                 '<span class="num">脸 %.1f → <b>%.1f</b>（%+.1f）｜ 人 p99 %.1f → %.1f（%+.1f）'
                 '｜ 人爆白 %.2f%% → <b>%.2f%%</b> ｜ 脸 C %.1f → <b>%.1f</b></span></div>'
                 % (st, r['face1'] or 0, r['face3'] or 0, d_face,
                    r['p99_1'] or 0, r['p99_3'] or 0, d_p99,
                    r['clip1'] or 0, r['clip3'] or 0, r['c1'] or 0, r['c3'] or 0))
        h.append('<div class="pair">')
        for tag, lab in (('a', '① 引擎底片'), ('b', '③ LR 出片')):
            p = os.path.join(PREV, '%s_%s.jpg' % (st, tag))
            if os.path.isfile(p):
                h.append('<figure><img loading="lazy" src="preview/%s_%s.jpg"><figcaption>%s</figcaption></figure>'
                         % (st, tag, lab))
        h.append('</div>')
        if blown:
            h.append('<p class="num bad">人的亮部被推上去了（p99 %+.1f、爆白 %+.2f%%）—— '
                     '整人蒙版提亮的后果，局部层要加亮度范围。</p>' % (d_p99, (r['clip3'] or 0) - (r['clip1'] or 0)))
        h.append('</div>')
    h.append('<p class="foot">这一版只执行了 ③ 白端（Whites −8）与 ⑦ 局部（LocalExposure = EV/4）；'
             '⑥ 曲线本版没动。白端对 p95 几乎无影响，说明它不是一个能压整体白端的旋钮。</p>')
    h.append('</div></body></html>')
    with open(HTML, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    print(HTML)
    return 0


if __name__ == '__main__':
    sys.exit(main())
