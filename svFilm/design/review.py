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
from .invariants import T1, T2, T3, T4, T4_BACKLIT, T5, S1, S3

# ---------------------------------------------------------------- 验收

def verify(rec_1: dict, rec_3: dict, plan=None, ev_known: bool = True,
           light: Optional[str] = None) -> Dict[str, object]:
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
    # ★ ① 本身就不在区里/就发灰 ⇒ 只要求 ③ 不比 ① 更差（否则就是拿引擎的账罚 LR）
    L1, C1 = s1.get('L'), s1.get('C')
    L3, C3 = s3.get('L'), s3.get('C')
    # ★ 脸亮区**分光位**（2026-10-29，目录卡实测 n=43）：逆光 45~70，顺光/未知 55~85
    zlo, zhi = (T4_BACKLIT['L_lo'], T4_BACKLIT['L_hi']) if light == 'backlit' \
        else (T4['L_lo'], T4['L_hi'])
    if L3 is not None:
        if L1 is not None and not (zlo <= float(L1) <= zhi):
            okv = abs(float(L3) - zhi) <= abs(float(L1) - zhi) + 0.2 or zlo <= float(L3) <= zhi
            hard.append(('T4 脸L', okv, round(float(L3), 1), '比 ①(%.1f) 更靠近区' % float(L1),
                         '① 本身出区 ⇒ 只要求别更远'))
        else:
            hard.append(('T4 脸L', zlo <= float(L3) <= zhi, round(float(L3), 1),
                         '%g~%g' % (zlo, zhi), '脸在合理曝光区（%s）' % (light or 'unknown')))
    if C3 is not None:
        if C1 is not None and float(C1) < T5['C_min']:
            hard.append(('T5 脸C', float(C3) >= float(C1) - 0.2, round(float(C3), 1),
                         '≥ ①(%.1f)-0.2' % float(C1), '① 本身就发灰 ⇒ 只要求不比 ① 更差'))
        else:
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
        # ★ 这条只能当信息：曝光对 L* 的**实际**响应与模型差得多
        #   （10-29 真跑：模型斜率 13~15 L*/EV，实测 11.4~18.5，散 ±30%）
        #   ⇒ |残差| ≤ 1 这种门在暗脸上必然误报，不能当硬门
        # 点颜色的亮度也会抬脸，而它没有可用的传递函数 ⇒ 有 lum 时这条只能当信息
        _lum = float((getattr(plan, 'point_color', None) or {}).get('lum', 0.0) or 0.0) \
            if plan is not None else 0.0
        if ev_known and abs(expect - float(L1)) <= 1e-9 and abs(_lum) < 1e-9:
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
            out.append('<img loading="lazy" src="%s" alt="%s">'
                       % (r['final_jpg'], r.get('stem', '')))
        v = r.get('verdict') or ''
        cls = 'ok' if v.startswith('✓') else 'bad'
        out.append('<p class="cap">成片 · %s<span class="%s">%s</span></p>' % (r.get('note', ''),
                                                                              cls, v))
        if r.get('engine_jpg'):
            out.append('<details><summary>看① 引擎底片</summary>'
                       '<img loading="lazy" src="%s" alt="%s 底片"></details>'
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
                light: Optional[str] = None,
                url_map: Optional[Dict[str, str]] = None,
                log=print) -> Dict[str, object]:
    """量完 ③ 之后：**验收 + 出一页目检**。只读，不改参数。

    这是 G1/G2/G3 的落地：以前流程走到"导出"就断了，现在导出之后必须过这里。

    `url_map`：`{本地jpg文件名: 托管直链}`。传了就用直链（**手机端看资料库必须用这个**），
    没传就写相对路径（本地桌面看）。直链来自 `manage/upload_image.py`。
    """
    r1, r3 = by_stem(recs_1), by_stem(recs_3)
    prev_dir = os.path.join(out_dir, 'preview')
    rows, nok = [], 0

    for st, plan in (plans or {}).items():
        b = r3.get(st)
        if not b:
            continue
        a = r1.get(st) or {}
        v = verify(a, b, plan, ev_known=ev_known, light=light)
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
        um = url_map or {}
        if fin:
            p = jpg_of(fin, os.path.join(prev_dir, st + '_3.jpg'))
            fj = 'preview/%s_3.jpg' % st if p else None
            fj = um.get(st + '_3.jpg') or um.get(st + '_3') or fj
        if eng:
            p = jpg_of(eng, os.path.join(prev_dir, st + '_1.jpg'))
            ej = 'preview/%s_1.jpg' % st if p else None
            ej = um.get(st + '_1.jpg') or um.get(st + '_1') or ej

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


# ---------------------------------------------------------------- 选卡页（人定，不是自动挑）

_MATCH_CSS = _CSS + """
.sec{font-size:13px;color:#8f8b84;margin:26px 0 10px;letter-spacing:.5px;scroll-margin-top:12px}
a{text-decoration:none}
.lead{font-size:14.5px;margin:0 0 10px;color:#dcd9d3}
.lead b{color:#fff}
.row{display:flex;gap:10px;align-items:baseline;padding:9px 0;border-top:1px solid #2e2d2b;font-size:13.5px}
.row:first-of-type{border-top:0}
.row .k{flex:0 0 96px;color:#cfcbc4}
.row .v{flex:1;color:#b9b5ae;font-variant-numeric:tabular-nums}
.card{border-top:1px solid #2e2d2b;padding:14px 0 4px}
.card:first-of-type{border-top:0}
.card h2{font-size:15.5px;margin:0 0 4px;color:#fff;font-weight:600}
.card .meta{color:#8f8b84;font-size:12.5px;margin:0 0 8px;font-variant-numeric:tabular-nums}
.mono{color:#7f8b99;font-size:12.5px;line-height:1.6;white-space:pre-wrap}
.pick{background:#1b241c;border:1px solid #2c3d2e;border-radius:9px;padding:10px 12px;margin:14px 0 0;font-size:13px;color:#b9d3bd}
"""


def _card_diffs(a, b) -> List[str]:
    """两张同卷同纸的卡，真正不同的档位（这是人做决定要看的东西）。"""
    def rms(c):
        v = c.texture.get('rms_granularity') or ()
        return ('%.1f' % (sum(v) / len(v))) if v else None
    rows = [('颗粒 rms', rms(a), rms(b)),
            ('彩度 amount', a.style.get('amount'), b.style.get('amount')),
            ('柔光', a.style.get('diffusion'), b.style.get('diffusion')),
            ('画幅', a.style.get('film_format_mm'), b.style.get('film_format_mm')),
            ('余量 p95≤', a.safety.get('p95_max'), b.safety.get('p95_max'))]
    return ['%s %s→%s' % (k, x, y) for k, x, y in rows if str(x) != str(y)]


def match_sheet(batch_name: str, profile: Dict[str, float], refs: List[object],
                offer, gap_rows=None, gap_verdict: str = '', out_html: str = '',
                samples: Optional[Dict[str, str]] = None, n_img: int = 0,
                caliber: str = '', master_sets: Optional[List[dict]] = None,
                log=print) -> str:
    """选卡页：**给人挑**，不给结论。

    页面上必须有三件事，缺一件人就挑不了：
      ① 这批像哪一套（画像级，不是靶）；② 最近那套的缺口关不关得掉；
      ③ 现有卡各自的代价（卷/纸/质感档）。有样片就放样片，没有就明说"未渲"。
      `master_sets`：匹配到的几套**大师成片**（每套给全部图），排在图区里给人看"像的那套长什么样"。
    """
    samples = samples or {}
    out = ['<!DOCTYPE html>', '<html lang="zh-CN"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>选卡 · %s</title><style>%s</style></head><body><div class="wrap">'
           % (batch_name, _MATCH_CSS),
           '<h1>选卡 · %s</h1>' % batch_name]
    sub = '%d 张试点 ｜ 口径 %s' % (n_img, caliber or '—')
    out.append('<p class="sub">%s</p>' % sub)

    out.append('<p class="lead">这批画像：<b>脸 L %.1f ｜ 脸 C %.1f ｜ 脸 h %.1f ｜ '
               'p50 %.1f ｜ p95 %.1f</b></p>'
               % (profile.get('L', 0), profile.get('C', 0), profile.get('h', 0),
                  profile.get('p50', 0), profile.get('p95', 0)))
    out.append('<p class="cap">匹配只回答"最像哪一套"，不是要求做到那套的数；'
               '参照卡只有画像、没有配方。这里的数拿的是<b>引擎底片①</b>，'
               '还没有过 LR，所以差里混着"我们还没调"那一截。</p>')

    out.append('<div class="sec">① 这批最像哪一套</div>')
    for h in refs:
        why = ' · '.join('%s%+.1f' % (k, v) for k, v in list(h.diff.items())[:3]) or '很接近'
        out.append('<div class="row"><span class="k">%.2f</span>'
                   '<span class="v">%s　<span class="mono">n=%d ｜ %s</span></span></div>'
                   % (h.score, h.ref.label(), h.ref.n, why))

    if gap_rows:
        out.append('<div class="sec">② 最近那套的缺口，关得掉吗</div>')
        for r in gap_rows:
            out.append('<div class="mono">%s</div>' % str(r))
        if gap_verdict:
            out.append('<p class="cap">%s</p>' % gap_verdict.replace('\n', '<br>'))

    out.append('<div class="sec">③ 现有卡（你定用哪张）</div>')
    choices = list(offer.choices) if offer else []
    # 候选太少、还有几张卷纸完全相同时，分数**没有分辨力** —— 必须写明，否则等于骗人
    pairs = [(c.card.stock, c.card.paper) for c in choices]
    dup = len(pairs) != len(set(pairs))
    if dup:
        out.append('<p class="cap">⚠ 下面的卡里有<b>同卷同纸</b>的（只差质感档）⇒ '
                   '分数在这几张之间几乎没有分辨力，请看"和别的卡差在哪"那几行，'
                   '或者直接看样片。</p>')
    for ch in choices:
        c = ch.card
        rms = c.texture.get('rms_granularity') or ()
        rms_s = ('%.1f' % (sum(rms) / len(rms))) if rms else '—'
        out.append('<div class="card">')
        out.append('<h2>%s</h2>' % c.label())
        out.append('<p class="meta">匹配分 %.2f ｜ rms %s ｜ 画幅 %s ｜ 柔光 %s ｜ 彩度 %s'
                   ' ｜ 余量设定 p95≤%s</p>'
                   % (ch.hit.score, rms_s, c.style.get('film_format_mid')
                      or c.style.get('film_format_mm', '—'),
                      c.style.get('diffusion', 'off'), c.style.get('amount', '—'),
                      c.safety.get('p95_max', '—')))
        if getattr(ch.hit, 'contrib', None):
            top = ch.hit.top(2)
            out.append('<p class="mono">主要差在：%s</p>'
                       % ' · '.join('%s%+.2f' % (k, v) for k, v in top))
        # 同卷同纸的卡：把真正的差别摆出来（分数帮不上忙，质感档才帮得上）
        for other in choices:
            if other is ch:
                continue
            oc = other.card
            if (oc.stock, oc.paper) != (c.stock, c.paper):
                continue
            diffs = _card_diffs(c, oc)
            out.append('<p class="mono">和「%s」同卷同纸，差别在：%s</p>'
                       % (oc.name, ' · '.join(diffs) if diffs else '几乎一样'))
            break
        s = samples.get(c.name)
        if s:
            out.append('<figure style="margin:10px 0 0"><img src="%s" alt="%s"></figure>'
                       % (s, c.name))
        else:
            out.append('<p class="mono">样片：未渲（要图的话给这张卡渲一张试点）</p>')
        out.append('</div>')

    if offer and offer.new_card_candidates:
        out.append('<div class="sec">想换风格的话（要做新卡，不是改一个键）</div>')
        out.append('<p class="mono">值得做新卡的卷：%s</p>' % '、'.join(offer.new_card_candidates))
        out.append('<p class="cap">卷/纸在卡构建时就传进 params，overrides 改不了；'
                   '换卷 = 做一张新卡。</p>')

    if master_sets:
        out.append('<div class="sec">④ 这几套大师的成片（点名字跳过去）</div>')
        for i, m in enumerate(master_sets, 1):
            out.append('<div class="row"><span class="k">%.2f</span>'
                       '<span class="v"><a href="#m%d" style="color:#cfe3d2">%s</a>'
                       '<span class="mono">　%d 张 ｜ %s</span></span></div>'
                       % (m.get('score', 0), i, m.get('label', ''), m.get('n', 0),
                          m.get('diff', '')))
        for i, m in enumerate(master_sets, 1):
            out.append('<div class="sec" id="m%d">第 %d 套 · %s　'
                       '<span class="mono">%.2f ｜ %s ｜ %d 张</span></div>'
                       % (i, i, m.get('label', ''), m.get('score', 0),
                          m.get('diff', ''), m.get('n', 0)))
            out.append('<p class="cap">同一个拍点如果有两个版本，只展开一张，'
                       '另一版折在下面（不重复占屏）。</p>')
            # 按拍点分组：第一张直接显示，同拍点的其余版本折进 <details>
            groups, order = {}, []
            for item in (m.get('imgs') or []):
                k, u = (item if isinstance(item, (tuple, list)) else ('', item))
                if k not in groups:
                    groups[k] = []
                    order.append(k)
                groups[k].append(u)
            for k in order:
                us = groups[k]
                out.append('<figure><img loading="lazy" src="%s" alt="%s"></figure>'
                           % (us[0], m.get('label', '')))
                if len(us) > 1:
                    inner = ''.join('<img loading="lazy" src="%s" alt="%s">' % (x, k)
                                    for x in us[1:])
                    out.append('<details><summary>同拍点另一版（%d）</summary>%s</details>'
                               % (len(us) - 1, inner))

    out.append('<div class="pick">选好之后这样告诉流程：<br>'
               '<code>--card &lt;卡名&gt;</code>（卡名就是上面每个小标题括号前的那个，'
               '如 <code>%s</code>）——不给就会按匹配分自动取第一张。</div>'
               % (offer.choices[0].card.name if (offer and offer.choices) else '卡名'))
    out.append('</div></body></html>')

    html = '\n'.join(out)
    if out_html:
        os.makedirs(os.path.dirname(out_html) or '.', exist_ok=True)
        with open(out_html, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(html)
        log('选卡页：%s' % out_html)
    return html
