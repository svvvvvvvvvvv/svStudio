# -*- coding: utf-8 -*-
"""③ 的**目检页**：只出片 + 摆测量 + 描述偏差，**不判过/不过**。

设计（`设计真源.md` §7 / `纪律.md`）：

    机器只做：出片 ③ + 目检页 + 测量（诊断）
    人做：目检；人说"过"才是过；人说"不过"就写哪一段不对

所以这里**没有** ok/bad 判定、没有硬门/软门、没有"物理底线过了就算交付"。
物理测量只用来定位事故（死白/p1/肤色崩），卡偏差只用来描述"哪一项差多少"。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


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
            im = im.resize((width, int(round(im.height * width / float(im.width)))),
                           Image.LANCZOS)
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
.diag{color:#8f8b84;font-size:12px;margin:5px 0 0}
details{margin:8px 0 0}
summary{color:#7f8b99;font-size:12.5px;cursor:pointer;outline:none}
details img{margin-top:8px}
.foot{color:#6f6b64;font-size:12px;margin:30px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
.herobox{border:1px solid #3a3126;background:#1b1815;border-radius:10px;padding:10px 12px;margin:0 0 22px}
.herobox img{width:100%;height:auto;display:block;border-radius:8px;background:#000;margin-top:8px}
"""


def contact_sheet(rows: List[dict], out_html: str, title: str, sub: str = '',
                  hero: Optional[dict] = None) -> str:
    """一页目检：一张图占满宽、竖着排；每张图下面跟它的账。

    rows 每项：dict(stem, final_jpg, engine_jpg, values, note, diag)
    """
    os.makedirs(os.path.dirname(out_html) or '.', exist_ok=True)
    out = ['<!DOCTYPE html>', '<html lang="zh-CN"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>%s</title><style>%s</style></head><body><div class="wrap">' % (title, _CSS),
           '<h1>%s</h1>' % title]
    if sub:
        out.append('<p class="sub">%s</p>' % sub)
    if hero and hero.get('jpg'):
        out.append('<div class="herobox"><div class="lab"><span class="name">hero · %s</span>'
                   '<span class="num">这一批的 look 参考（卡的样片，不参与算值）</span></div>'
                   '<img loading="lazy" src="%s" alt="hero"></div>'
                   % (hero.get('label') or 'look 参考', hero['jpg']))
    for r in rows:
        out.append('<figure>')
        out.append('<div class="lab"><span class="name">%s</span>'
                   '<span class="num">%s</span></div>'
                   % (r.get('stem', ''), r.get('values', '')))
        if r.get('final_jpg'):
            out.append('<img loading="lazy" src="%s" alt="%s">'
                       % (r['final_jpg'], r.get('stem', '')))
        if r.get('note'):
            out.append('<p class="cap">%s</p>' % r['note'])
        if r.get('diag'):
            out.append('<p class="diag">%s</p>' % r['diag'])
        if r.get('engine_jpg'):
            out.append('<details><summary>看 ① 引擎底片</summary>'
                       '<img loading="lazy" src="%s" alt="%s 底片"></details>'
                       % (r['engine_jpg'], r.get('stem', '')))
        out.append('</figure>')
    out.append('<p class="foot">③ 是 LR 成片；① 是引擎底片，只作对照。'
               '这里的数字只用来**定位**（死白/p1/肤色/卡偏差），'
               '不判过/不过 —— <b>人目检是唯一验收</b>。</p>')
    out.append('</div></body></html>')
    html = '\n'.join(out)
    with open(out_html, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(html)
    return out_html


# ---------------------------------------------------------------- 组装（不依赖 Batch）

def by_stem(recs: Dict[str, dict]) -> Dict[str, dict]:
    """把 {文件名: 记录} 归一成 {stem: 记录}。`_base.tif` 跳过。"""
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


def _hero_block(hero: str, prev_dir: str, log=print) -> Optional[dict]:
    """人选的 hero → 目检页顶部对照图 + 肤色读数（只做参考）。"""
    if not hero:
        return None
    if not os.path.isfile(hero):
        log('! hero 文件不存在，目检页不放参考图：%s' % hero)
        return None
    p = jpg_of(hero, os.path.join(prev_dir, 'hero.jpg'), width=1400)
    if not p:
        log('! hero 转 jpg 失败，目检页不放参考图：%s' % hero)
        return None
    label = os.path.basename(hero)
    try:
        from .measure_all import measure_one
        r = measure_one(hero)
        sk = r.get('skin') or {}
        L, C, h = sk.get('L'), sk.get('C'), sk.get('h')
        if L is not None:
            label += ' ｜ 脸 L %.1f · C %.1f · h %.1f' % (
                float(L), float(C or 0.0), float(h or 0.0))
    except Exception as e:                                      # noqa: BLE001
        log('! hero 肤色测量失败（只放图）：%s' % str(e)[:120])
    return dict(jpg='preview/hero.jpg', label=label)


def _g(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _plan_line(plan) -> str:
    """计划里真正落下去的键（一图一行）。"""
    wb = _g(plan, 'wb')
    cw = _g(wb, 'card_white', None) if wb is not None else None
    basic = []
    for key, label in (('exposure_ev', '曝'), ('contrast', '对'),
                       ('highlights', '高'), ('shadows', '阴'),
                       ('whites', '白'), ('blacks', '黑')):
        v = float(_g(plan, key, 0.0) or 0.0)
        if abs(v) > 1e-9:
            basic.append('%s%+.2f' % (label, v) if key == 'exposure_ev'
                         else '%s%+.0f' % (label, v))
    color = []
    pc = _g(plan, 'point_color')
    ss = float(_g(pc, 'sat_scale', 0.0) or 0.0) if pc is not None else 0.0
    if abs(ss) > 1e-9:
        color.append('点颜色 %+.2f' % ss)
    loc = _g(plan, 'local')
    lev = float(_g(loc, 'exposure_ev', 0.0) or 0.0) if loc is not None else 0.0
    if abs(lev) > 1e-9:
        color.append('局部 %+.2f档' % lev)
    parts = []
    if cw and len(cw) >= 3:
        parts.append('白点 %+.1f/%+.1f' % (float(cw[1]), float(cw[2])))
    if basic:
        parts.append(' '.join(basic))
    if color:
        parts.append(' ｜ '.join(color))
    return ' ｜ '.join(parts) or '不动'


def _meas_line(a: dict, b: dict) -> str:
    """①→③ 的测量（只描述，不判）。"""
    s1 = (a.get('skin') or {})
    s3 = (b.get('skin') or {})
    i1 = a.get('img') or {}
    i3 = b.get('img') or {}
    p1 = (a.get('person') or {})
    p3 = (b.get('person') or {})

    def _f(v, fmt):
        return '—' if v is None else fmt % float(v)

    return ('脸 L %s → %s ｜ 脸 C %s → %s ｜ 脸 h %s → %s ｜ p50 %s → %s ｜ '
            'p95 %s → %s ｜ 人 clip %s → %s ｜ 整幅 clip %s → %s'
            % (_f(s1.get('L'), '%.1f'), _f(s3.get('L'), '%.1f'),
               _f(s1.get('C'), '%.1f'), _f(s3.get('C'), '%.1f'),
               _f(s1.get('h'), '%.1f'), _f(s3.get('h'), '%.1f'),
               _f(i1.get('p50'), '%.1f'), _f(i3.get('p50'), '%.1f'),
               _f(i1.get('p95'), '%.1f'), _f(i3.get('p95'), '%.1f'),
               _f(p1.get('clip_pct'), '%.2f%%'), _f(p3.get('clip_pct'), '%.2f%%'),
               _f(i1.get('clip_pct'), '%.2f%%'), _f(i3.get('clip_pct'), '%.2f%%')))


def make_review(recs_1: Dict[str, dict], recs_3: Dict[str, dict],
                plans: Dict[str, object], out_dir: str,
                tif_1_dir: str = '', tif_3_dir: str = '',
                title: str = '成片目检', sub: str = '',
                url_map: Optional[Dict[str, str]] = None,
                hero: str = '', card_dev: Optional[Dict[str, dict]] = None,
                log=print) -> Dict[str, object]:
    """量完 ③ 之后出目检页。**只读，不判过/不过**。

    `url_map`：{本地 jpg 文件名: 托管直链}；传了手机端可看。
    """
    r1, r3 = by_stem(recs_1), by_stem(recs_3)
    prev_dir = os.path.join(out_dir, 'preview')
    rows = []
    for st, plan in (plans or {}).items():
        b = r3.get(st)
        if not b:
            continue
        a = r1.get(st) or {}
        dev = (card_dev or {}).get(st) or (_g(plan, 'card_dev') or {})
        diag = []
        for key, label in (('p50', 'p50'), ('p95', 'p95'),
                           ('face_L', '脸L'), ('face_C', '脸C'),
                           ('white_L50', '白L'), ('hair_L50', '发L'),
                           ('skin_L25', '皮暗')):
            v = (dev or {}).get(key)
            if isinstance(v, (int, float)):
                diag.append('%s 偏差 %+.1f' % (label, float(v)))
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
        rows.append(dict(stem=st, values=_plan_line(plan),
                         note=_meas_line(a, b), diag=' ｜ '.join(diag),
                         final_jpg=fj, engine_jpg=ej))
    html = contact_sheet(rows, os.path.join(out_dir, '目检.html'), title=title,
                         sub=sub or ('%d 张 ｜ 机器只出片+测量，过不过由你目检' % len(rows)),
                         hero=_hero_block(hero, prev_dir, log=log))
    log('目检页：%s' % html)
    return dict(rows=rows, n=len(rows), html=html)
