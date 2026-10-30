# -*- coding: utf-8 -*-
r"""**中文标签工具**（修一类 bug：PIL 默认字体没有汉字 ⇒ 画出来的中文变乱码并烧进成片）。

## 病灶
```
`ImageDraw.text((x,y), '现状')` 用 PIL 的**默认位图字体**（只有 ASCII）⇒ 中文变方块/乱码，
而且**烧进导出的 JPG**（成片就带乱码了）。本项目多个出页脚本都这么写过 ⇒ 反复犯。
```

## 修法（机制级）
```
所有画字的地方**必须走这里**：`_lib.label.text(draw, xy, s, size=..)`
 · 优先 `C:\Windows\Fonts\msyh.ttc`（微软雅黑）→ `simhei.ttf` → `arial.ttf`
 · 全部拿不到 ⇒ **抛错**（宁可炸，不许再输出乱码）
```
用法：
```
from _lib.label import text, font
text(draw, (8, 6), '档3 去锐化', size=18)
```
重建阶梯页（不重渲，直接用已渲好的 r*/）：
```
python -m _lib.label ladder
```
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import ImageFont                                                   # noqa: E402

_CAND = [
    r'C:\Windows\Fonts\msyh.ttc',
    r'C:\Windows\Fonts\msyhbd.ttc',
    r'C:\Windows\Fonts\simhei.ttf',
    r'C:\Windows\Fonts\simsun.ttc',
    r'C:\Windows\Fonts\arial.ttf',
]
_cache = {}


def font(size=18):
    """→ ImageFont（必须能画中文；全拿不到就抛错）。"""
    if size in _cache:
        return _cache[size]
    for p in _CAND:
        if os.path.isfile(p):
            try:
                f = ImageFont.truetype(p, size)
                _cache[size] = f
                return f
            except Exception:                                            # noqa: BLE001
                continue
    raise RuntimeError('找不到可用字体（含中文）: %s' % _CAND)


def text(draw, xy, s, size=18, fill=(240, 230, 150)):
    """画中文标签（唯一合法入口）。"""
    return draw.text(xy, str(s), font=font(size), fill=fill)


def rebuild_ladder(root=None, stems=('DSCF2807', 'DSCF2891')):
    """用已渲好的各档图重建"竖排 5 档（r0..r5）+ 整页"，**不重新渲染**。"""
    import io
    from PIL import Image, ImageDraw
    root = root or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'xhs_1018', 'render', 'ladder')
    RUNGS = [('r0_现状', 'r0 现状：锐化0.4 / 无虚焦 / 柔光1/4'),
             ('r1_去锐化', 'r1 去数码锐：unsharp 0'),
             ('r2_+虚焦0.6', 'r2 + 扫描端虚焦 0.6 px'),
             ('r3_+柔光1/2', 'r3 + 柔光 1/2 黑柔')]
    W, bar = 1080, 30
    made = []
    for stem in stems:
        ims = []
        for tag, lab in RUNGS:
            p = os.path.join(root, tag, stem + '.jpg')
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert('RGB')
            im = im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)
            if tag.startswith('r3'):
                ims.append(('r4 = r3 再缩 1080 + JPEG 82（模拟XHS二次压缩）', im.copy()))
            ims.append((lab, im))
        p5 = os.path.join(root, 'r5_更狠', stem + '.jpg')
        if os.path.isfile(p5):
            im = Image.open(p5).convert('RGB')
            ims.append(('r5 更狠：虚焦 1.2 px + 柔光1/2 + 缩1080', im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)))
        if not ims:
            continue
        H = ims[0][1].height
        tot = Image.new('RGB', (W, (H + bar) * len(ims)), (14, 14, 14))
        dr = ImageDraw.Draw(tot)
        for i, (lab, im) in enumerate(ims):
            tot.paste(im, (0, i * (H + bar) + bar))
            text(dr, (8, i * (H + bar) + 6), '%s ｜ %s' % (stem, lab), size=19)
        fp = os.path.join(root, 'ladder_%s.jpg' % stem)
        tot.save(fp, quality=92)
        made.append(fp)
        print('  出图 %s' % fp)
    h = ['<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>柔度阶梯</title><style>',
         'body{background:#0f0f12;color:#e9e9ea;font:14px/1.7 "Microsoft YaHei",sans-serif;margin:0;padding:16px}',
         'h1{font-size:18px}h2{font-size:15px;color:#ffd479;margin:20px 0 6px}',
         'img{display:block;border:1px solid #333;max-width:100%}.sub{color:#9a9a9a}',
         '</style></head><body>',
         '<h1>柔度阶梯 ｜ 每档只动一根已知量（同显示宽 1080 · 标签已用系统中文字体重画）</h1>',
         '<p class=sub>竖排：r0 现状 → r1 去数码锐 → r2 +虚焦0.6 → r3 +柔光1/2 → r4 = r3 缩1080+JPG82 → r5 更狠(虚焦1.2)。'
         '只挑"最像他那种氛围感"的档，不用管数字。</p>']
    for fp in made:
        h.append('<h2>%s</h2><img src="%s">' % (os.path.basename(fp)[:-4], os.path.basename(fp)))
    h.append('</body></html>')
    hp = os.path.join(root, '柔度阶梯.html')
    with io.open(hp, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(h))
    print('  页面: %s' % hp)
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'ladder':
        raise SystemExit(rebuild_ladder())
    print('font ok:', font(18).getname())
