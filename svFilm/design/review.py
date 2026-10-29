# -*- coding: utf-8 -*-
"""完整流程的收尾三件事：**量 ③ · 验收 · 目检页**。

为什么单独一个文件（对齐审计 G1/G2/G3）：流程原来断在"导出成片"之后 ——
没有人量它、没有东西判它、也没有一页能看的图。这三件事都只做"读"和"排版"，
不改任何参数（参数在 lr.py 里算，这里只判结果）。

三条规矩（纪律.md）：
  · **脸优先**：硬门只看脸和"有没有把脸改坏"；背景爆是软门，只报不拦。
  · 量 ③ 是为了**定位**问题，不是宣布做对。
  · 目检页一图一行、占满宽、竖排；**不拼图、不用 <table>**。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .expose import eval_slope
from .invariants import T1, T2, T3, T4, T5, S1, S3

# ---------------------------------------------------------------- 验收

def verify(rec_1: dict, rec_3: dict, plan=None, ev_known: bool = True) -> Dict[str, object]:
    """③ 的验收：**硬门（脸 + 没把脸改坏）+ 软门（背景）**。

    为什么不像 check_S 那样直接比 ①→③：②曝光本来就会改脸的亮度，
    拿"绝对位移"判 S1 会把正当的曝光算成违规。
    这里比的是**残差**：③ 的脸位移 − 曝光该带来的位移。
    残差大 ⇒ 收白/曲线/点颜色这几步偷偷改了明暗（那才是要抓的）。

    返回 dict(hard=[(名, ok, 值, 限, 说明)], soft=[...], ok=bool)
    """
    hard, soft = [], []
    img1 = rec_1.get('img') or {}
    img3 = rec_3.get('img') or {}
    s1 = rec_1.get('skin') or {}
    s3 = rec_3.get('skin') or {}

    # ---- 硬门：脸
    L3 = s3.get('L')
    if L3 is not None:
        hard.append(('T4 脸L', T4['L_lo'] <= L3 <= T4['L_hi'], round(float(L3), 1),
                     '%g~%g' % (T4['L_lo'], T4['L_hi']), '脸在合理曝光区'))
    C3 = s3.get('C')
    if C3 is not None:
        hard.append(('T5 脸C', (C3 or 0) >= T5['C_min'], round(float(C3), 1),
                     T5['C_min'], '脸不灰'))

    # ---- 硬门：脸的明暗残差（曝光之外，别的步骤不许动脸）
    ev = float(getattr(plan, 'exposure_ev', 0.0) or 0.0) if plan is not None else 0.0
    L1 = s1.get('L')
    if L1 is not None and L3 is not None:
        expect = L1 + ev * eval_slope(L1)
        resid = (float(L3) - float(L1)) - (expect - float(L1))
        item = ('S1 脸残差', abs(resid) <= S1['dL_max'], round(resid, 2),
                S1['dL_max'], '③ 的脸位移 − 曝光该带来的位移')
        if ev_known:
            hard.append(item)
        else:
            # 手里的"曝光档数"是重算的、不是实际落下去的那个 ⇒ 这条只能当信息
            soft.append(item[:4] + (item[4] + '（参数是重算值，不是实际值 ⇒ 只报不算）',))

    # ---- 硬门：黑端没堵
    # ① 本身就堵黑（p1 已经低于 T2）⇒ 只要求 ③ 不比 ① 更差。
    # 拿绝对门去罚 ③，就是让 LR 背引擎的账 —— 和"回引擎"是同一个病。
    p1_1, p1_3 = img1.get('p1'), img3.get('p1')
    if p1_3 is not None:
        if p1_1 is not None and float(p1_1) < T2['p1_min']:
            hard.append(('T2 p1', float(p1_3) >= float(p1_1) - 0.2, round(float(p1_3), 2),
                         '≥ ①-0.2（%.2f）' % (float(p1_1) - 0.2),
                         '① 本身就没到 %.1f ⇒ 只要求不比 ① 更差' % T2['p1_min']))
        else:
            hard.append(('T2 p1', float(p1_3) >= T2['p1_min'], round(float(p1_3), 2),
                         T2['p1_min'], '黑端不堵'))

    # ---- 硬门：曲线预算
    if plan is not None and getattr(plan, 'curve', None):
        from .curve import displacement
        Ls = [img1.get('p%d' % k) for k in (5, 25, 50, 75, 95)]
        ds = displacement(plan.curve, [v for v in Ls if isinstance(v, (int, float))])
        if ds:
            w = max(abs(x) for x in ds)
            hard.append(('S3 曲线预算', w <= S3['anchor_max'], round(w, 2),
                         S3['anchor_max'], '每锚点位移（L*）'))

    # ---- 硬门：白点**不许被 LR 改坏**（相对 ①）；绝对值只进软门（那是卷/纸的账）
    wp1 = rec_1.get('white_point') or None
    wp3 = rec_3.get('white_point') or None
    if wp3 and wp1:
        d = max(abs(wp3.get('a', 0) - wp1.get('a', 0)),
                abs(wp3.get('b', 0) - wp1.get('b', 0)))
        hard.append(('T3 白点位移', d <= T3['ab_max'], round(d, 2), T3['ab_max'],
                     '白点相对 ① 的位移（绝对值是卷/纸的账，只看软门）'))
    if wp3:
        worst = max(abs(wp3.get('a', 0.0)), abs(wp3.get('b', 0.0)))
        soft.append(('T3 白点绝对', worst <= T3['ab_max'], round(worst, 2), T3['ab_max'],
                     '白点绝对值（卷/纸定的，只报告）'))

    # ---- 软门：背景爆（脸优先 ⇒ 只报不拦）
    clip = img3.get('clip_pct')
    if clip is not None:
        soft.append(('T1 clip', float(clip) < T1['clip_max'], round(float(clip), 2),
                     T1['clip_max'], '终片不爆白（背景可爆，此项只报告）'))

    # ---- 软门：背景 a/b 位移（要 background 掩膜才量得到）
    bb, ba = rec_1.get('bg') or {}, rec_3.get('bg') or {}
    if bb and ba:
        d = max(abs(ba.get('a', 0) - bb.get('a', 0)), abs(ba.get('b', 0) - bb.get('b', 0)))
        soft.append(('S2 背景', d <= 2.0, round(d, 2), 2.0, '背景 a/b 位移（软门）'))

    return dict(hard=hard, soft=soft, ok=all(x[1] for x in hard))


def verify_line(v: Dict[str, object]) -> str:
    """一行说清验收结果（给终端/报告用）。"""
    bad_h = [x for x in v['hard'] if not x[1]]
    bad_s = [x for x in v['soft'] if not x[1]]
    if not bad_h and not bad_s:
        return '✓ 全过'
    parts = []
    if bad_h:
        parts.append('硬门破 %d：%s' % (len(bad_h), '、'.join(x[0] for x in bad_h)))
    if bad_s:
        parts.append('软门 %d（背景，可接受）：%s' % (len(bad_s), '、'.join(x[0] for x in bad_s)))
    return ' ｜ '.join(parts)


# ---------------------------------------------------------------- 目检页

def jpg_of(tif_path: str, dst_path: str, width: int = 1400, quality: int = 88) -> Optional[str]:
    """TIF → JPG（目检页不能直接显示 TIFF）。"""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(tif_path).convert('RGB')
        if im.width > width:
            im = im.resize((width, int(round(im.height * width / float(im.width)))), Image.LANCZOS)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        im.save(dst_path, 'JPEG', quality=quality, optimize=True)
        return dst_path
    except Exception:
        return None


_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:14px 14px 60px}
h1{font-size:19px;font-weight:600;margin:6px 0 4px;letter-spacing:.3px}
.sub{color:#8f8b84;font-size:13px;margin:0 0 18px}
figure{margin:0 0 26px}
.lab{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:13px;margin:0 0 6px}
.lab .name{color:#cfcbc4;font-weight:600}
.lab .num{color:#7f8b99;font-size:12px;font-variant-numeric:tabular-nums}
figure img{width:100%;height:auto;display:block;border-radius:10px;background:#000}
.cap{color:#8f8b84;font-size:12.5px;margin:7px 0 0}
.cap b{color:#dcd9d3;font-weight:600}
.ok{color:#8fce9d}
.bad{color:#e0928c}
details{margin:8px 0 0}
summary{color:#7f8b99;font-size:12.5px;cursor:pointer;outline:none}
details img{margin-top:8px}
.foot{color:#6f6b64;font-size:12px;margin:30px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
"""


def contact_sheet(rows: List[dict], out_html: str, title: str, sub: str = '') -> str:
    """一页目检：**一张图占满宽、竖着排**，每张图下面跟它的账。

    rows 每项：
        dict(stem, final_jpg, engine_jpg, verdict(ok|bad 文本),
             values='曝光 +0.3 ｜ 收白 -50 ｜ 点颜色 0.6',
             note='③ 的脸 L 62.1（① 61.8）｜ clip 1.2%（① 5.4%）')
    """
    os.makedirs(os.path.dirname(out_html), exist_ok=True)
    out = ['<!DOCTYPE html>', '<html lang="zh-CN"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>%s</title><style>%s</style></head><body><div class="wrap">' % (title, _CSS),
           '<h1>%s</h1>' % title]
    if sub:
        out.append('<p class="sub">%s</p>' % sub)
    for r in rows:
        out.append('<figure>')
        out.append('<div class="lab"><span class="name">%s</span>'
                   '<span class="num">%s</span></div>' % (r.get('stem', ''), r.get('values', '')))
        if r.get('final_jpg'):
            out.append('<img src="%s" alt="%s">' % (r['final_jpg'], r.get('stem', '')))
        v = r.get('verdict') or ''
        cls = 'ok' if v.startswith('✓') else 'bad'
        out.append('<p class="cap">成片 · %s<span class="%s">%s</span></p>' % (r.get('note', ''),
                                                                              cls, v))
        if r.get('engine_jpg'):
            out.append('<details><summary>看① 引擎底片</summary><img src="%s" alt="%s 底片"></details>'
                       % (r['engine_jpg'], r.get('stem', '')))
        out.append('</figure>')
    out.append('<p class="foot">③ 是 LR 出的成片（曝光/收白/曲线/点颜色都在里面）；'
               '① 是引擎底片，只作对照。验收硬门只看脸和"有没有把脸改坏"，'
               '背景爆属软门，只报不拦。</p>')
    out.append('</div></body></html>')
    html = '\n'.join(out)
    with open(out_html, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    return out_html


# ---------------------------------------------------------------- 组装（不依赖 Batch）

def by_stem(recs: Dict[str, dict]) -> Dict[str, dict]:
    """把 {文件名: 记录} 归一成 {stem: 记录}（`DSCF2453_xmp.tif` 和 `DSCF2453` 都算同一个）。

    `_base.tif` 直接跳过：它是"不加 XMP 的对照"，不是 ③，混进来会把没改过的图当成品。
    """
    out = {}
    for name, rec in (recs or {}).items():
        st = os.path.basename(str(name))
        if st.endswith('_base.tif'):
            continue
        for cut in ('_engine.tif', '_xmp.tif', '_final.tif', '_sim.png', '.tif', '.png'):
            if st.endswith(cut):
                st = st[:-len(cut)]
                break
        if '_' in st:
            st = st.split('_')[0]
        out.setdefault(st, rec)
    return out


def _find_tif(d: str, stem: str, allow_engine: bool = False) -> Optional[str]:
    """在一堆 tif 里找这张图（后缀不定：_xmp / _final / _engine / 裸名）。"""
    if not d or not os.path.isdir(d):
        return None
    import glob
    hits = sorted(glob.glob(os.path.join(d, stem + '_*.tif'))) or \
        sorted(glob.glob(os.path.join(d, stem + '.tif')))
    bad = ['_base.tif']
    if not allow_engine:
        bad.append('_engine.tif')
    hits = [p for p in hits if not p.endswith(tuple(bad))]
    return hits[0] if hits else None


def make_review(recs_1: Dict[str, dict], recs_3: Dict[str, dict], plans: Dict[str, object],
                out_dir: str, tif_1_dir: str = '', tif_3_dir: str = '',
                title: str = '成片目检', sub: str = '', ev_known: bool = True,
                log=print) -> Dict[str, object]:
    """量完 ③ 之后：**验收 + 出一页目检**。只读，不改参数。

    这是 G1/G2/G3 的落地：以前流程走到"导出"就断了，现在导出之后必须过这里。
    """
    r1, r3 = by_stem(recs_1), by_stem(recs_3)
    prev_dir = os.path.join(out_dir, 'preview')
    rows, nok = [], 0

    for st, plan in (plans or {}).items():
        b = r3.get(st)
        if not b:
            continue
        a = r1.get(st) or {}
        v = verify(a, b, plan, ev_known=ev_known)
        nok += 1 if v['ok'] else 0

        s1 = (a.get('skin') or {}).get('L')
        s3 = (b.get('skin') or {}).get('L')
        c1 = (a.get('img') or {}).get('clip_pct')
        c3 = (b.get('img') or {}).get('clip_pct')
        vals = '曝光 %+.2f ｜ 收白 %+.0f ｜ 饱和 %+.0f' % (
            float(getattr(plan, 'exposure_ev', 0.0) or 0.0),
            float(getattr(plan, 'highlights', 0.0) or 0.0),
            float(getattr(plan, 'saturation', 0.0) or 0.0))
        if getattr(plan, 'point_color', None):
            vals += ' ｜ 点颜色 %+.2f' % float(plan.point_color.get('scale', 0.0))
        note = '脸 L %s → %s ｜ clip %s → %s' % (
            '—' if s1 is None else '%.1f' % s1,
            '—' if s3 is None else '%.1f' % s3,
            '—' if c1 is None else '%.2f%%' % c1,
            '—' if c3 is None else '%.2f%%' % c3)

        fin = _find_tif(tif_3_dir, st)
        eng = _find_tif(tif_1_dir, st, allow_engine=True) if tif_1_dir else None
        fj = ej = None
        if fin:
            p = jpg_of(fin, os.path.join(prev_dir, st + '_3.jpg'))
            fj = 'preview/%s_3.jpg' % st if p else None
        if eng:
            p = jpg_of(eng, os.path.join(prev_dir, st + '_1.jpg'))
            ej = 'preview/%s_1.jpg' % st if p else None

        rows.append(dict(stem=st, values=vals + ' ｜ ', note=note, verdict=verify_line(v),
                         final_jpg=fj, engine_jpg=ej, _v=v))

    html = contact_sheet(rows, os.path.join(out_dir, '目检.html'), title=title,
                         sub=sub or ('%d 张 ｜ 硬门（脸）过 %d 张 ｜ 背景爆只报不拦'
                                     % (len(rows), nok)))
    bad = [r for r in rows if not r['verdict'].startswith('✓')]
    log('目检页：%s' % html)
    for r in bad:
        log('  ! %s ｜ %s' % (r['stem'], r['verdict']))
    return dict(rows=rows, ok=nok, n=len(rows), html=html)
