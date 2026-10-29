# -*- coding: utf-8 -*-
"""匹配页：把 §57 的 5 个候选做成**给人拍板**的一页（设计真源 §56 步 3）。

这页只做三件事：**排序 · 给理由 · 给代价**。
· 每张候选带**这套大师的成片样例**（人靠看图判"像不像"，不靠分数）
· 分数是两条通道合成的（分面标签 κ 加权 + 数值画像 IQR 归一），并写明每条的出处
· 不许系统代选；页面上不给"推荐"，只给 `--card <名字>`

用法：
    from design.matchpage import make_match_page
    make_match_page(recs, out_dir)      # recs = ① 的测量
"""
from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional

from .matchcard import (MAX_ABS_IQR, NUM_KEYS_RANK, NUM_KEYS_REPORT,
                        SELFREF_KEYS, batch_profile, batch_tags, rank_top5)

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:14px 14px 60px}
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
.lab{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px;margin:0 0 6px}
.lab .name{font-weight:600;color:#e8e6e1}
.lab .num{color:#7f8b99;font-size:12px;font-variant-numeric:tabular-nums}
.cap{color:#8f8b84;font-size:12.5px;margin:4px 0 0}
.pick{background:#1b241c;border:1px solid #2c3d2e;border-radius:9px;padding:10px 12px;margin:12px 0 0;font-size:13px;color:#b9d3bd;word-break:break-all}
.warn{color:#e8c08c}
.foot{color:#6f6b64;font-size:12px;margin:30px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
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


def make_match_page(recs: Dict[str, dict], out_dir: str,
                    paths: Optional[Dict[str, str]] = None,
                    k: int = 5, title: str = '匹配 · 挑一张卡',
                    log=print) -> str:
    """出匹配页。返回 html 路径。"""
    hits = rank_top5(recs, k=k, paths=paths)
    batch = batch_profile(recs)
    btags = batch_tags(recs, paths)
    prev = os.path.join(out_dir, 'match', 'preview')
    os.makedirs(prev, exist_ok=True)

    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>%s</title><style>%s</style></head><body><div class="wrap">' % (title, CSS),
         '<h1>%s</h1>' % title,
         '<p class="sub">%d 张 ① 的测量 → 两条通道合成 → 前 %d 个。<b>看样例图定</b>，别只看分数。</p>'
         % (len(recs), k)]

    # ---- 这批长什么样
    h.append('<div class="box"><h3>这批（从 ① 量出来）</h3>')
    h.append('<div class="row"><div class="k">场景侧</div><div class="v">%s</div></div>'
             % '、'.join('%s=%.2f' % (k2, batch[k2]) for k2 in ('sky', 'span') if k2 in batch))
    h.append('<div class="row"><div class="k">分面标签</div><div class="v">%s</div></div>'
             % ('、'.join('%s=%s' % kv for kv in btags.items()) or '（判不出）'))
    h.append('<div class="row"><div class="k">另五维只报不比</div><div class="v">%s'
             '（它们是<b>成片量</b>，① 还没过 LR，不同态 ⇒ 不参与排序）</div></div>'
             % '、'.join('%s=%.1f' % (k2, batch[k2]) for k2 in NUM_KEYS_REPORT if k2 in batch))
    h.append('</div>')

    # ---- 判据说明（谁都能复核）
    h.append('<div class="box"><h3>怎么算的（可复核）</h3>')
    for a, b in [
        ('数值通道', '只用场景侧 %s；每维用 64 张卡自己的 IQR 归一，**单维最多算 %s 个 IQR**（防近零离散度吃掉权重）'
         % ('/'.join(NUM_KEYS_RANK), MAX_ABS_IQR)),
        ('分面通道', '只用**有 κ** 的分面（背景/服装/色彩/构图）；权重 = κ。光分面没有 κ ⇒ 只做轻微降权'),
        ('禁用维（自指）', '%s —— 它们是"当前基线卡"造出来的，比了是自指（§57.2）' % '/'.join(SELFREF_KEYS)),
        ('不做的事', '不推荐、不代选；分数只用来排序，**好看不好看由你拍**'),
    ]:
        h.append('<div class="row"><div class="k">%s</div><div class="v">%s</div></div>' % (a, b))
    h.append('</div>')

    # ---- 候选
    for i, hit in enumerate(hits, 1):
        c = hit['card']
        prof = c.profile
        h.append('<h2>%d) %s</h2>' % (i, c.name))
        h.append('<p class="sub">分数 %.2f ｜ %s + %s ｜ n=%d 张 ｜ 分面：%s</p>'
                 % (hit['score'], c.stock, c.paper, c.n,
                    '、'.join('%s' % v for v in c.tags.values()) or '（无标签）'))
        for s in c.samples[:3]:
            dst = os.path.join(prev, '%s_%d.jpg' % (os.path.basename(os.path.dirname(s)), len(h)))
            if _thumb(s, dst):
                h.append('<figure><img loading="lazy" src="preview/%s" alt="%s"></figure>'
                         % (os.path.basename(dst), os.path.basename(s)))
        h.append('<p class="cap">它的画像：脸 L %.1f ｜ C %.1f ｜ p50 %.1f ｜ p95 %.1f ｜ clip %.2f%%</p>'
                 % (prof.get('L') or 0, prof.get('C') or 0, prof.get('p50') or 0,
                    prof.get('p95') or 0, prof.get('clip') or 0))
        if hit['reason']:
            h.append('<p class="cap">为什么：%s</p>' % ' ｜ '.join(hit['reason']))
        if hit['cost']:
            h.append('<p class="cap warn">代价：%s</p>' % '；'.join(hit['cost']))
        h.append('<p class="pick">选定它就回我：<b>--card %s</b></p>' % c.name)

    h.append('<p class="foot">这一页不替你选。5 个都不像 ⇒ 直说"都不是这个方向"，我按最接近的两张去做新卡。</p>')
    h.append('</div></body></html>')

    os.makedirs(os.path.join(out_dir, 'match'), exist_ok=True)
    out = os.path.join(out_dir, 'match', '匹配页.html')
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    log('匹配页：%s（%d 个候选）' % (out, len(hits)))
    return out
