# -*- coding: utf-8 -*-
r"""_lib / sbs.py —— 并排图（整幅两联 · 脸 1:1 · 颊 1:1 · `index.html` 含"看什么"清单）。

来源 = `gamma3元组_1013\a8_page.py` + `分色续研_1013\x7_page.py` + `amount三链_1013\a6_page.py`
       （零渲染：只读已落盘的 2048 成片，只裁图）。

## 纪律（`AGENTS` §三）
```
★ **看图必须给：目录路径 + 直接打开 + 看什么**
★ 并排两侧**同显示长边**，并标注双方原始像素（不标 = 欺骗性对比）
★ 判肤色看**颊 1:1（放大）**；判整体看**整幅**；判"发灰/发绿/发闷"看**脸 1:1**
```

## 主函数（签名）
```
font(sz=16)                              -> ImageFont
fit(im, w=1000)                          -> Image   等比缩到宽 w
detect_face(im, det=None)                -> dict|None  {cx, cy, face_w, eye_y}  （YuNet）
face_box(im, info)                       -> tuple    脸窗（按脸宽归一）
cheek_box(im, info)                      -> tuple    左颊窗
pair_image(a, b, la, lb, title, box=None, zoom=1, gap=8, bar=34) -> Image
make_pair(a_path, b_path, out_dir, tag, det=None, zoom=3, width=1000, show=True) -> dict
build_index(out_dir, rows, title=..., what_to_look=..., css=True) -> str
WHAT_DEFAULT                             -> list[str]  默认"看什么"清单
```

## 用法
```python
import _lib.sbs as S
det = S.make_detector()
rows = S.make_pair(pa, pb, PAGE, 'sbs_kurai_DSCF1307', det=det, show=True,
                   labels=('现值 0.99', '候选 3 元组'))
S.build_index(PAGE, rows, title='knee 并排图')
```
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402
import _lib.label as _L                                                       # noqa: E402
import _lib.measure as _M                                                     # noqa: E402

MODEL = _lib.YUNET
WIDTH = 1000
BG = (18, 18, 18)
FG = (240, 240, 240)
WHAT_DEFAULT = [
    '① <b>颊 1:1（放大）</b> —— 血色/黄橙味变了没有？是不是朝那位大师的方向？',
    '② <b>脸 1:1</b> —— 有没有发灰 / 发绿 / 发闷？',
    '③ <b>整幅</b> —— 中性物（白墙/灰地/白衣）有没有被染色？整体还自然吗？',
]


def font(sz=16):
    r"""旧名字（已合并 -> `label.font`）；保留为 1 行转发 + 一次性 `DeprecationWarning`。

    ★ 原来这里自己抄一份字体候选表，拿不到就**静默** `ImageFont.load_default()`
      （无汉字，乱码会被烧进成片）。唯一实现 = `_lib.label.font`。
    @returns {ImageFont}
    """
    _lib.deprecation('sbs.font', 'label.font')
    return _L.font(sz)


F = _L.font(16)


def open_rgb(path):
    r"""旧名字（已合并 -> `measure.open_rgb`）。@returns {Image}"""
    _lib.deprecation('sbs.open_rgb', 'measure.open_rgb')
    return _M.open_rgb(path)


def fit(im, w=WIDTH):
    r"""等比缩到宽 `w`（LANCZOS）。@returns {Image}"""
    k = w / float(im.width)
    return im.resize((w, max(1, int(round(im.height * k)))), Image.LANCZOS)


def make_detector(model=MODEL):
    r"""旧名字（已合并 -> `measure.make_detector`）。@returns {cv2.FaceDetectorYN}"""
    _lib.deprecation('sbs.make_detector', 'measure.make_detector')
    return _M.make_detector(model)


def detect_face(im, det=None):
    r"""YuNet 出最大脸 -> 颊圆参数（**与 `measure.roi_masks` 同口径**）。

    @returns {dict|None} {cx, cy, face_w, eye_y, nface}
    """
    import cv2
    det = det or _M.make_detector()
    Wp, Hp = im.size
    bgr = cv2.cvtColor(np.asarray(im.convert('RGB'), np.uint8), cv2.COLOR_RGB2BGR)
    det.setInputSize((Wp, Hp))
    _, faces = det.detect(bgr)
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda z: z[2] * z[3])
    w = float(f[2])
    le, re = f[6:8], f[4:6]
    lm, rm = f[12:14], f[10:12]
    ey = 0.5 * (float(le[1]) + float(re[1]))
    my = 0.5 * (float(lm[1]) + float(rm[1]))
    cy = ey + 0.55 * (my - ey)
    cx = 0.5 * (float(le[0]) + float(re[0]))
    return dict(cx=cx, cy=cy, face_w=w, eye_y=ey, nface=int(len(faces)))


def face_box(im, info, k=1.75):
    r"""脸窗（以脸宽归一，**两臂同一块像素**）。@returns {tuple} (x0,y0,x1,y1)"""
    side = int(info['face_w'] * k)
    x0 = int(max(0, min(im.width - 1, info['cx'] - side / 2)))
    x1 = int(min(im.width, x0 + side))
    y0 = int(max(0, min(im.height - 1, info['eye_y'] - side * 0.55)))
    y1 = int(min(im.height, y0 + side))
    return (x0, y0, x1, y1)


def cheek_box(im, info, k=0.30, off=0.26):
    r"""左颊窗（`center_x - off*face_w`，边长 `k*face_w`）。@returns {tuple} (x0,y0,x1,y1)"""
    cw = int(max(24, k * info['face_w']))
    cxp = int(max(0, min(im.width - 1, info['cx'] - off * info['face_w'])))
    cyq = int(max(0, min(im.height - 1, info['cy'])))
    x0 = max(0, cxp - cw // 2)
    y0 = max(0, cyq - cw // 2)
    return (x0, y0, min(im.width, x0 + cw), min(im.height, y0 + cw))


def pair_image(a, b, la='', lb='', title='', box=None, zoom=1, gap=8, bar=34,
               width=None, nearest=False):
    r"""两联并排（可指定裁切窗与放大倍数）。

    @param a,b {Image} 左 / 右
    @param la,lb {str} 左右标签（**必须写清各自是什么**）
    @param title {str} 顶栏文字
    @param box {tuple|None} 同一裁切窗；None = 整幅（等比缩到 `width`）
    @param zoom {int} 放大倍数（NEAREST = 1:1 真像素）
    @returns {Image}
    """
    if box:
        a = a.crop(box)
        b = b.crop(box)
    elif width:
        a, b = fit(a, width), fit(b, width)
    if zoom and zoom != 1:
        rs = Image.NEAREST
        a = a.resize((a.width * zoom, a.height * zoom), rs)
        b = b.resize((b.width * zoom, b.height * zoom), rs)
    cv = Image.new('RGB', (a.width + b.width + gap, a.height + bar), BG)
    cv.paste(a, (0, bar))
    cv.paste(b, (a.width + gap, bar))
    d = ImageDraw.Draw(cv)
    d.text((6, 8), title, font=F, fill=FG)
    d.text((6, bar - 16), la, font=_L.font(13), fill=(150, 210, 250))
    d.text((a.width + gap + 6, bar - 16), lb, font=_L.font(13), fill=(250, 200, 150))
    d.line([(a.width + gap // 2, 0), (a.width + gap // 2, cv.height)], fill=(90, 90, 90))
    return cv


def side_note(im):
    r"""标注双方原始像素（不标 = 欺骗性对比）。@returns {str}"""
    return '原始 %dx%d' % (im.width, im.height)


def make_pair(a_path, b_path, out_dir, tag, det=None, labels=('左：A', '右：B'),
              title='', zoom=3, width=WIDTH, show=True, quality=93, make_face=True,
              make_cheek=True):
    r"""一组三张：`sbs_<tag>.jpg` 整幅 · `face1to1_<tag>.jpg` 脸 1:1 · `cheek1to1_<tag>.jpg` 左颊 1:1（放大）。

    @returns {dict} {tag, made: [文件名], box, cheek, zoom}
    """
    os.makedirs(out_dir, exist_ok=True)
    A, B = _M.open_rgb(a_path), _M.open_rgb(b_path)
    made = []
    if show:
        p = 'sbs_%s.jpg' % tag
        pair_image(A, B, labels[0], labels[1],
                   title or '%s   |   %s  %s' % (tag, side_note(A), side_note(B)),
                   width=width).save(os.path.join(out_dir, p), quality=quality)
        made.append(p)
    info = detect_face(A, det)
    if info and (make_face or make_cheek):
        if make_face:
            bb = face_box(A, info)
            fa, fb = A.crop(bb), B.crop(bb)
            if fa.width > 8 and fa.height > 8:
                p = 'face1to1_%s.jpg' % tag
                pair_image(fa, fb, labels[0], labels[1], '%s 脸 1:1' % tag,
                           zoom=1).save(os.path.join(out_dir, p), quality=95)
                made.append(p)
        if make_cheek:
            cb = cheek_box(A, info)
            ca, cbb = A.crop(cb), B.crop(cb)
            if ca.width > 8 and ca.height > 8:
                z = zoom if ca.width * zoom <= 900 else 1
                p = 'cheek1to1_%s.jpg' % tag
                pair_image(ca, cbb, labels[0], labels[1],
                           '%s 左颊 1:1 x%d' % (tag, z), zoom=z).save(
                    os.path.join(out_dir, p), quality=95)
                made.append(p)
    return dict(tag=tag, made=made, box=info, zoom=zoom,
                cheek=cheek_box(A, info) if info else None)


def strip(paths, out_path, labels=None, title='', width=560, gap=8, bar=34, quality=93):
    r"""**N 连图**（同一张照片的多个臂并排；比 `pair_image` 多臂时用）。

    ★ 为什么加：三方对账（参照批 / 今日生产 / 候选）一次要看三张，
      用两次 `pair_image` 串起来会套两层边栏、标签也乱。

    @param paths {list[str]} 图片路径（顺序 = 从左到右）
    @param labels {list[str]|None} 每格标签
    @param width {int} **每格**宽度（总宽 = n*width + (n-1)*gap）
    @returns {str} 写出的文件路径
    """
    ims = [fit(_M.open_rgb(p), width) for p in paths]
    h = max(im.height for im in ims)
    W = sum(im.width for im in ims) + gap * (len(ims) - 1)
    canvas = Image.new('RGB', (W, h + bar), (18, 18, 18))
    f = _L.font(18)
    d = ImageDraw.Draw(canvas)
    x = 0
    for i, im in enumerate(ims):
        canvas.paste(im, (x, bar))
        lab = (labels[i] if labels and i < len(labels) else '')
        if lab:
            d.text((x + 8, 8), lab, fill=(255, 255, 255), font=f)
        x += im.width + gap
    if title:
        d.text((W - 12 - d.textlength(title, font=f), 8), title, fill=(200, 200, 200), font=f)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    canvas.save(out_path, quality=quality)
    return out_path


def build_index(out_dir, rows, title='并排图', subtitle='', what_to_look=None,
                groups=None, fname='index.html'):
    r"""生成 `index.html`（**含"看什么"清单**）。

    @param out_dir {str} 图片所在目录
    @param rows {list[dict]} `make_pair` 的返回（或 {'tag':…, 'caption':…, 'files':[…]})
    @param groups {dict|None} {组标题: [row, …]}（按链/按臂分组时用）
    @param what_to_look {list[str]|None} 默认 = `WHAT_DEFAULT`
    @returns {str} index.html 路径
    """
    wl = what_to_look if what_to_look is not None else WHAT_DEFAULT
    h = ['<!doctype html><meta charset="utf-8"><title>%s</title>' % title,
         '<style>body{background:#111;color:#eee;font:14px/1.6 "Microsoft YaHei",sans-serif;'
         'margin:18px} h1{font-size:20px} h2{margin:22px 0 4px;color:#ffd479;font-size:17px}',
         'p.k{color:#9ad;margin:2px 0 10px} figure{margin:0 0 16px}',
         'img{max-width:100%;border:1px solid #333;display:block}',
         'figcaption{color:#aaa;font-size:12px;padding:3px 0} code{color:#ffd479}</style>',
         '<h1>%s</h1>' % title]
    if subtitle:
        h.append('<p class="k">%s</p>' % subtitle)
    h.append('<p class="k">★ <b>看什么</b>：<br>' + '<br>'.join(wl) + '</p>')

    def _emit(rs):
        for r in rs:
            files = r.get('files') or r.get('made') or []
            for f in files:
                if not os.path.isfile(os.path.join(out_dir, f)):
                    continue
                lab = {'sbs': '整幅两联', 'face1to1': '脸 1:1', 'cheek1to1': '★左颊 1:1（放大）'}
                kind = f.split('_')[0]
                h.append('<figure><img src="%s"><figcaption>%s · %s</figcaption></figure>'
                         % (f, r.get('caption', r.get('tag', '')), lab.get(kind, kind)))
    if groups:
        for g, rs in groups.items():
            h.append('<h2>%s</h2>' % g)
            _emit(rs)
    else:
        _emit(rows)
    p = os.path.join(out_dir, fname)
    tmp = p + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    os.replace(tmp, p)
    return p


def main(argv=None):
    r"""命令行：两个目录/两组成片 -> 一套并排图 + index.html。

    用法：
      python -m _lib.sbs --a <目录A> --b <目录B> --out <看图目录> [--tag 前缀]
                          [--ext .jpg] [--zoom 3] [--stems A,B,C]
    @returns {int}
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--tag', default='')
    ap.add_argument('--ext', default='.jpg')
    ap.add_argument('--zoom', type=int, default=3)
    ap.add_argument('--stems', default='')
    ap.add_argument('--la', default='左：A')
    ap.add_argument('--lb', default='右：B')
    a = ap.parse_args(argv)
    stems = ([s for s in a.stems.split(',') if s] if a.stems else
             sorted(os.path.splitext(f)[0] for f in os.listdir(a.a) if f.endswith(a.ext)))
    det = _M.make_detector()
    rows = []
    for st in stems:
        pa = os.path.join(a.a, st + a.ext)
        pb = os.path.join(a.b, st + a.ext)
        if not (os.path.isfile(pa) and os.path.isfile(pb)):
            print('  缺 %s' % st)
            continue
        tag = ('%s_%s' % (a.tag, st)) if a.tag else st
        r = make_pair(pa, pb, a.out, tag, det=det, labels=(a.la, a.lb), zoom=a.zoom)
        r['caption'] = '%s ｜ %s / %s' % (st, a.la, a.lb)
        rows.append(r)
        print('  ok %-28s %d 张' % (tag, len(r['made'])))
    p = build_index(a.out, rows, title='并排图（%s vs %s）' % (a.la, a.lb))
    print('入口： %s' % p)
    return 0


if __name__ == '__main__':
    sys.exit(main())
