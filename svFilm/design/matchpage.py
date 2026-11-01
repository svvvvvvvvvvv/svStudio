# -*- coding: utf-8 -*-
"""内容匹配页：把**机内 JPG 内容匹配**的候选做成给人拍板的一页。

只做五件事：这批长什么样 · 候选卡全部作品 · 为什么像/不像 ·
全部卡（按风格另选）· 人拍板。
**没有 ①/①′**；选完卡才渲一次 ①。

页面不替你选：只给 `--card`/`--hero` 命令。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:14px 14px 130px}
h1{font-size:20px;margin:6px 0 4px;font-weight:600}
h2{font-size:17px;margin:30px 0 4px;color:#fff;border-top:1px solid #2e2d2b;padding-top:16px}
.sub{color:#8f8b84;font-size:13px;margin:0 0 10px}
.box{border:1px solid #2e2d2b;border-radius:10px;padding:12px 14px;margin:14px 0;background:#191919}
.row{display:flex;gap:10px;align-items:baseline;padding:4px 0;font-size:13px;border-top:1px solid #232221}
.row:first-child{border-top:0}
.row .k{flex:0 0 120px;color:#cfcbc4}
.row .v{flex:1;color:#b9b5ae;font-variant-numeric:tabular-nums}
figure{margin:0 0 10px}
figure img{width:100%;height:auto;display:block;border-radius:8px;background:#000}
.grid{columns:2;column-gap:8px;margin:8px 0 4px}
.grid img{width:100%;height:auto;display:block;border-radius:8px;background:#000}
.grid figure{margin:0 0 10px;break-inside:avoid}
.grid a{display:block;margin:0 0 8px;break-inside:avoid}
@media(max-width:600px){.grid{columns:1}}
.cap{color:#8f8b84;font-size:12.5px;margin:4px 0 0}
.pick{background:#1b241c;border:1px solid #2c3d2e;border-radius:9px;padding:10px 12px;margin:12px 0 0;font-size:13px;color:#b9d3bd;word-break:break-all}
.warn{color:#e8c08c}
.foot{color:#6f6b64;font-size:12px;margin:30px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
.pickbar{position:fixed;left:0;right:0;bottom:0;display:none;background:#11221a;border-top:1px solid #2c3d2e;padding:10px 14px 12px;z-index:9}
.pickbar .who{color:#cfe9d6;font-size:13px}
.pickbar input{width:100%;box-sizing:border-box;font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;background:#0d1512;color:#cfe9d6;border:1px solid #2c3d2e;border-radius:8px;padding:9px;margin-top:6px}
.pickbtn{display:inline-block;background:#1d2b22;color:#cfe9d6;border:1px solid #35513c;border-radius:8px;padding:9px 14px;font-size:14px;cursor:pointer;margin:10px 0 0}
.pickbtn.on{background:#2f543c;border-color:#6aa87c;color:#fff}
.pickbtn.alt{background:#221d1d;border-color:#4a3434;color:#e3c8c8;margin-left:8px}
.shot{position:relative;margin:0 0 8px;break-inside:avoid;cursor:pointer}
.shot img{width:100%;height:auto;display:block;border-radius:8px;background:#000}
.shot .hint{position:absolute;left:6px;bottom:6px;display:none;background:#000a;color:#e8dcc0;font-size:11px;padding:3px 6px;border-radius:6px}
.shot:hover .hint{display:block}
.shot.on img{outline:3px solid #e0b34a;outline-offset:-3px}
.shot.on .hint{display:block;background:#e0b34a;color:#1a1408}
.shot .big{position:absolute;right:6px;top:6px;background:#000a;color:#cfcbc4;font-size:11px;padding:3px 6px;border-radius:6px;text-decoration:none}
.allgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin:10px 0}
.allcard{border:1px solid #2e2d2b;border-radius:9px;padding:6px;background:#191919}
.allcard img{width:100%;height:auto;display:block;border-radius:6px;background:#000}
.allcard .nm{font-size:12px;color:#cfcbc4;margin:6px 0 0;word-break:break-all}
.allcard .pickbtn{width:100%;margin:6px 0 0;padding:6px 8px;font-size:12px}
"""

JS = """
<script>
var CURCARD = '', CURHERO = '';
function showBar(){
  var v = '--card ' + CURCARD;
  if (CURHERO) { v += ' --hero ' + CURHERO; }
  document.getElementById('pickwho').textContent =
    '选的是：' + CURCARD + (CURHERO ? ' ｜ hero：' + CURHERO.split('/').pop() : ' ｜ 还没选 hero');
  document.getElementById('pickcmd').value = v;
  document.getElementById('pickbar').style.display = 'block';
}
function markCard(name){
  CURCARD = name || '';
  document.querySelectorAll('.pickbtn[data-card]').forEach(function(x){
    x.classList.toggle('on', x.getAttribute('data-card') === CURCARD);
  });
}
function markHero(el){
  document.querySelectorAll('.shot.on').forEach(function(x){ x.classList.remove('on'); });
  if (el) { el.classList.add('on'); }
}
function pick(b){ CURHERO = ''; markCard(b.getAttribute('data-card')); markHero(null); showBar(); }
function pickHero(el){
  CURHERO = el.getAttribute('data-hero');
  markCard(el.getAttribute('data-card'));
  markHero(el);
  showBar();
}
function pickNone(){
  CURCARD = ''; CURHERO = ''; markCard(''); markHero(null);
  document.getElementById('pickwho').textContent = '都不要 ⇒ 换候选集';
  document.getElementById('pickcmd').value = '都不是这个方向，换候选集';
  document.getElementById('pickbar').style.display = 'block';
}
function copycmd(){
  var e = document.getElementById('pickcmd');
  e.select();
  try { navigator.clipboard.writeText(e.value); } catch (err) { document.execCommand('copy'); }
}
</script>
"""


def _thumb(src: str, dst: str, width: int = 760, quality: int = 82) -> Optional[str]:
    try:
        from PIL import Image
        im = Image.open(src)
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        im.convert('RGB').save(dst, quality=quality, optimize=True)
        return dst
    except Exception:                                      # noqa: BLE001
        return None


def make_content_page(batch: dict, hits: List[dict], jpg_paths: Dict[str, str],
                      out_dir: str, title: str = '内容匹配 · 机内 JPG',
                      all_cards: Optional[List[str]] = None,
                      log=print) -> str:
    """机内 JPG 内容匹配页：**没有 ①，也没有 ①′**。"""
    from .matchcard import card_samples

    prev = os.path.join(out_dir, 'match', 'preview')
    os.makedirs(prev, exist_ok=True)
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>%s</title><style>%s</style></head><body><div class="wrap">' % (title, CSS),
         '<h1>%s</h1>' % title,
         '<p class="sub">只读机内 JPG 做内容匹配，<b>没有跑 ①′</b>；'
         '选完卡才渲一次 ①。分数只用于排序，<b>看样片定</b>。</p>']

    cl = (batch.get('clothing') or {}) if isinstance(batch, dict) else {}
    h.append('<div class="box"><h3>这批（从机内 JPG 读出来的内容）</h3>')
    rows = [
        ('场景', batch.get('scene')),
        ('主体', batch.get('subject')),
        ('服装类型', cl.get('type')),
        ('服装色', cl.get('palette')),
        ('图案', cl.get('pattern')),
        ('轮廓', cl.get('silhouette')),
        ('环境色', batch.get('palette')),
        ('背景元素', '、'.join(batch.get('elements') or [])),
        ('光位/光向量（仅诊断）', batch.get('light_note') or batch.get('light')),
        ('置信/来源', '%s / %s' % (batch.get('confidence'), batch.get('source'))),
    ]
    for k, v in rows:
        h.append('<div class="row"><div class="k">%s</div><div class="v">%s</div></div>'
                 % (k, v if v not in (None, '', []) else '—（unknown，不进分数）'))
    if batch.get('evidence'):
        h.append('<div class="row"><div class="k">证据</div><div class="v">%s</div></div>'
                 % batch['evidence'])
    h.append('</div>')

    # ---- 这批机内 JPG 原样
    h.append('<div class="box"><h3>这批机内 JPG（%d 张，点开看大图）</h3>' % len(jpg_paths))
    h.append('<div class="grid">')
    shown = 0
    for stem, src in sorted(jpg_paths.items()):
        if not os.path.isfile(src):
            continue
        dst = os.path.join(prev, 'jpg_%s.jpg' % stem)
        big = os.path.join(prev, 'jpg_%s_1200.jpg' % stem)
        if _thumb(src, dst, width=480) and _thumb(src, big, width=1200):
            shown += 1
            h.append('<figure><a href="preview/%s" target="_blank"><img loading="lazy" '
                     'src="preview/%s" alt="%s"></a>'
                     '<figcaption class="cap">%s</figcaption></figure>'
                     % (os.path.basename(big), os.path.basename(dst), stem, stem))
    h.append('</div>')
    if not shown:
        h.append('<p class="cap warn">这批 JPG 一张都没读到（路径问题）</p>')
    h.append('</div>')

    # ---- 候选
    for i, hit in enumerate(hits, 1):
        c = hit.get('content') or {}
        cl = c.get('clothing') or {}
        h.append('<h2>%d) %s</h2>' % (i, hit['card']))
        h.append('<p class="sub">内容不一致度 %.2f ｜ %s / %s ｜ 置信 %s ｜ 来源 %s%s</p>'
                 % (hit.get('score', 0), c.get('scene'), c.get('palette'),
                    hit.get('confidence'), hit.get('source'),
                    (' ｜ variant：%s' % hit['variant']) if hit.get('variant') else ''))
        h.append('<div class="box">')
        for k, v in [('场景', c.get('scene')), ('主体', c.get('subject')),
                     ('服装', '%s / %s / %s' % (cl.get('type'), cl.get('palette'),
                                                cl.get('pattern'))),
                     ('环境色', c.get('palette')),
                     ('背景元素', '、'.join(c.get('elements') or [])),
                     ('光位/光向量（仅诊断）', c.get('light_note') or c.get('light'))]:
            h.append('<div class="row"><div class="k">%s</div><div class="v">%s</div></div>'
                     % (k, v if v not in (None, '', []) else '—'))
        if c.get('evidence'):
            h.append('<div class="row"><div class="k">证据</div><div class="v">%s</div></div>'
                     % c['evidence'])
        h.append('</div>')
        if hit.get('reason'):
            h.append('<p class="cap">为什么像：%s</p>' % ' ｜ '.join(hit['reason']))
        if hit.get('cost'):
            h.append('<p class="cap warn">代价：%s</p>' % '；'.join(hit['cost']))
        h.append('<p class="cap">点任意一张样片 = <b>选定这张卡</b>并把它当 <b>hero</b>；'
                 '只看不选就点右上「大图」。</p>')
        h.append('<div class="grid">')
        shown = 0
        for s in card_samples(hit['card']):
            if not os.path.isfile(s):
                continue
            dst = os.path.join(prev, '%02d_%s' % (i, os.path.basename(s)))
            if _thumb(s, dst, width=480):
                shown += 1
                href = 'file:///' + s.replace('\\', '/')
                h.append('<div class="shot" data-card="%s" data-hero="%s" onclick="pickHero(this)">'
                         '<img loading="lazy" src="preview/%s" alt="%s">'
                         '<span class="hint">选它当 hero</span>'
                         '<a class="big" href="%s" target="_blank" onclick="event.stopPropagation()">大图</a>'
                         '</div>'
                         % (hit['card'], s.replace('\\', '/'), os.path.basename(dst),
                            os.path.basename(s), href))
        h.append('</div>')
        if not shown:
            h.append('<p class="cap warn">这张卡的样片一张都没读到（路径/格式问题）</p>')
        h.append('<button class="pickbtn" data-card="%s" onclick="pick(this)">'
                 '选这张 → %s</button>' % (hit['card'], hit['card']))

    if all_cards:
        h.append('<h2>全部 %d 张卡（按风格另选）</h2>' % len(all_cards))
        h.append('<p class="sub">内容候选之外，想按风格选也可以；'
                 '点缩略图看大图，点「选这张」= 选卡（hero 可在下一步指定）。'
                 'xhs/滨田是 mixed 卡，点开样片后再定 variant。</p>')
        h.append('<div class="allgrid">')
        for j, name in enumerate(all_cards):
            samples = [s for s in card_samples(name) if os.path.isfile(s)]
            if not samples:
                continue
            dst = os.path.join(prev, 'all_%02d.jpg' % j)
            if not _thumb(samples[0], dst, width=360):
                continue
            href = 'file:///' + samples[0].replace('\\', '/')
            h.append('<div class="allcard">'
                     '<a href="%s" target="_blank"><img loading="lazy" src="preview/%s" alt="%s"></a>'
                     '<div class="nm">%s</div>'
                     '<button class="pickbtn" data-card="%s" onclick="pick(this)">选这张</button>'
                     '</div>' % (href, os.path.basename(dst), name, name, name))
        h.append('</div>')

    h.append('<p class="foot">这一页不替你选。都不像 ⇒ 点下面「都不是」，换候选集/拆批；'
             '不许硬选。选完卡才渲一次 ①，不再跑 ①′。</p>')
    h.append('<button class="pickbtn alt" onclick="pickNone()">都不是 · 换候选集</button>')
    h.append('<div class="pickbar" id="pickbar">'
             '<div class="who" id="pickwho"></div>'
             '<input id="pickcmd" readonly onclick="this.select()">'
             '<button class="pickbtn" onclick="copycmd()">复制</button></div>')
    h.append(JS)
    h.append('</div></body></html>')

    os.makedirs(os.path.join(out_dir, 'match'), exist_ok=True)
    out = os.path.join(out_dir, 'match', '内容匹配.html')
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    log('内容匹配页：%s（%d 个候选）' % (out, len(hits)))
    return out
