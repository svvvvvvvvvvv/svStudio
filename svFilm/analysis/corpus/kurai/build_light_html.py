# -*- coding: utf-8 -*-
"""光向量 300 张 → 目检页。"""
from __future__ import annotations

import json
import os
import sys

BASE = r'E:\ds\photo\_corpus\kurai\full'
OUT = r'E:\ds\photo\_light300'
for p in (BASE, r'E:\svStudio\svFilm'):
    if p not in sys.path:
        sys.path.insert(0, p)

from PIL import Image                        # noqa: E402

ORDER = [('back', '逆光'), ('back_side', '侧逆光'), ('side', '侧光'),
         ('front', '顺光/面光'), ('dappled', '斑驳/缝隙光'),
         ('unknown', '判不清（弃权）')]

CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141414;color:#e8e6e1;font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:14px 14px 60px}
h1{font-size:20px;margin:6px 0 4px;font-weight:600}
h2{font-size:17px;margin:36px 0 4px;color:#fff;border-top:1px solid #2e2d2b;padding-top:16px}
.sub{color:#8f8b84;font-size:13px;margin:0 0 10px}
.box{border:1px solid #2e2d2b;border-radius:10px;padding:12px 14px;margin:14px 0;background:#191919}
.box h3{margin:0 0 8px;font-size:14px;color:#dcd9d3;font-weight:600}
.row{display:flex;gap:10px;align-items:baseline;padding:4px 0;font-size:13px;border-top:1px solid #232221}
.row:first-child{border-top:0}
.row .k{flex:0 0 128px;color:#cfcbc4}
.row .v{flex:1;color:#b9b5ae;font-variant-numeric:tabular-nums}
figure{margin:0 0 24px}
.lab{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px;margin:0 0 6px}
.lab .name{font-weight:600}
.lab .num{color:#7f8b99;font-size:12px;font-variant-numeric:tabular-nums}
figure img{width:100%;height:auto;display:block;border-radius:10px;background:#000}
.cap{color:#8f8b84;font-size:12.5px;margin:6px 0 0}
.foot{color:#6f6b64;font-size:12px;margin:30px 0 0;border-top:1px solid #2e2d2b;padding-top:12px}
.tag{display:inline-block;padding:1px 7px;border-radius:5px;font-size:12px;margin-right:6px}
.k-back{background:#3a2020;color:#e8a8a0}
.k-back_side{background:#3a2c1c;color:#e8c08c}
.k-side{background:#1e2c3a;color:#9dc0e8}
.k-top{background:#2a2436;color:#bda8e8}
.k-front{background:#1e3326;color:#9de8b0}
.k-dappled{background:#332a1e;color:#e8d49d}
.k-unknown{background:#262626;color:#9a9a9a}
"""


def fmt(v, n=1):
    return '—' if v is None else ('%.' + str(n) + 'f') % v


def main():
    d = json.load(open(os.path.join(BASE, 'light_300.json'), encoding='utf-8'))
    rows = [r for r in d['rows'] if r.get('kind')]
    prev = os.path.join(OUT, 'preview')
    os.makedirs(prev, exist_ok=True)

    import collections
    cnt = collections.Counter(r['kind'] for r in rows)

    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<title>光向量判定 · 300 张</title><style>%s</style></head><body><div class="wrap">' % CSS,
         '<h1>光向量判定 · %d 张大师成片</h1>' % len(rows),
         '<p class="sub">判的是**光从哪边来、属于哪一类**。每张下面给了它凭什么这么判，'
         '不同意就按你自己的判 —— 这一页的目的是让判据可以被你证伪。</p>']

    h.append('<div class="box"><h3>判据（都是可算的，没有一条靠"看着像"）</h3>')
    for k, v in [
        ('① 脸上明暗梯度', '脸局部坐标（眼线 = x，眼→嘴 = y）左右半脸 L 中位差 <code>dLR_img</code>。'
                        '正 ⇒ 画面右侧亮。这是"光在像平面上的投影"的第一阶估计。'),
        ('② 方位 az', '<code>dLR_img / 20</code> 截到 ±1。−1 = 光来自画面左，+1 = 右。'
                   '**只给左右，不给前后** —— 单图没有深度，前后靠③④。'),
        ('③ 近邻背景比', '<code>rn90</code> = 脸中位 ÷ 近邻背景 p90（近邻 = 外扩 0.9 个脸宽）。'
                     '逆光时脸明显低于身边背景。**不用全幅 p90**（会被远处太阳骗，1231 已证）。'),
        ('④ 发丝 vs 脸', '<code>hvf</code> = 发丝 p90 − 脸中位。逆光时头发接直射光 ⇒ 明显为正。'),
        ('⑤ 斑驳 clump', '脸内 L 减掉一阶斜坡后的残差 std ÷ 脸反差。缝隙光的直接证据。'),
        ('做不到的：顶光', '上下半脸的差 <code>dTB</code> 被眼/眉/嘴污染（上半脸含眼睛眉毛 = 暗），'
                     '语料 p50 就是 −7.6（下比上亮）⇒ 那是测量 artifact，不是光。'
                     '**本版不判顶光**；要判得量鼻影，需要 3D 模型或 landmark 级分析。'),
        ('做不到的：前后分量', '单图没有深度 ⇒ 只能给"左右 + 类型"。前后（光在身后多远）'
                        '靠③④间接推，给不了角度。'),
        ('阈值（初始，待你校准）',
         'clump ≥ 0.53 → 斑驳；rn90 < 0.78 且 |dLR| < 8 → 逆光；'
         'rn90 < 0.78 且 |dLR| ≥ 8 → 侧逆；|dLR| ≥ 8 → 侧光；'
         '否则脸≥整幅 → 顺光；其余弃权。'),
    ]:
        h.append('<div class="row"><div class="k">%s</div><div class="v">%s</div></div>' % (k, v))
    h.append('</div>')

    h.append('<div class="box"><h3>分布</h3>')
    for key, name in ORDER:
        if cnt.get(key):
            h.append('<div class="row"><div class="k"><span class="tag k-%s">%s</span></div>'
                     '<div class="v">%d 张（%.0f%%）</div></div>'
                     % (key, name, cnt[key], 100.0 * cnt[key] / max(len(rows), 1)))
    h.append('</div>')

    h.append('<div class="box"><h3>我自己看出来的三个限制（先写前面，别当它是准的）</h3>')
    for k, v in [
        ('① 光源在画面里 ≠ 光从背后来了',
         '滨田 x104_03：姑娘**侧脸冲着窗**，窗在她头后面 ⇒ 近邻背景亮 ⇒ 被判成"侧逆"。'
         '肉眼是**窗光侧光**。⇒ "光在画面里"和"光照在脸上"是两件事，本版只量了前者。'),
        ('② 转头（yaw）会冒充光的方向',
         '脸转过去时可见的半个脸本来就更大更亮 ⇒ dLR 出现梯度，和"光从那边来"长得一模一样。'
         '本版没有 yaw 估计（landmark 只有眼线/嘴角，没鼻尖）⇒ 侧脸片的方位要打折看。'),
        ('③ 顶光判不了',
         '上下半脸差被眼/眉/嘴污染（语料 p50 = −7.6，下比上亮）⇒ 那是 artifact，不是光。'),
    ]:
        h.append('<div class="row"><div class="k">%s</div><div class="v">%s</div></div>' % (k, v))
    h.append('</div>')

    for key, name in ORDER:
        sub = [r for r in rows if r['kind'] == key]
        if not sub:
            continue
        sub.sort(key=lambda r: -(r.get('conf') or 0))
        h.append('<h2>%s · %d 张</h2>' % (name, len(sub)))
        h.append('<p class="sub">按可信度从高到低；每组只放前 %d 张，其余在数据里。</p>'
                 % min(len(sub), 24))
        for r in sub[:24]:
            src = r.get('file') or ''
            import hashlib
            stem = hashlib.md5((r.get('_path') or src).encode('utf-8')).hexdigest()[:10]
            jpg = os.path.join(prev, stem + '.jpg')
            p = r.get('_path') or ''
            ok = False
            if p and os.path.isfile(p):
                try:
                    im = Image.open(p)
                    w = 1000
                    if im.width > w:
                        im = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS)
                    im.convert('RGB').save(jpg, quality=82, optimize=True)
                    ok = True
                except Exception:                          # noqa: BLE001
                    ok = False
            az = r.get('azimuth')
            arrow = '—' if az is None else ('←' if az < -0.15 else ('→' if az > 0.15 else '·'))
            h.append('<figure><div class="lab"><span class="name">'
                     '<span class="tag k-%s">%s</span>%s %s</span>'
                     '<span class="num">clump %s ｜ rn90 %s ｜ hvf %s</span></div>'
                     % (key, name, arrow, os.path.basename(src)[:28],
                        fmt(r.get('clump'), 2), fmt(r.get('ratio_near90'), 2),
                        fmt((r.get('hair') or {}).get('vs_face'))))
            if ok:
                h.append('<img loading="lazy" src="preview/%s" alt="%s">'
                         % (os.path.basename(jpg), os.path.basename(src)[:28]))
            h.append('<p class="cap">%s ｜ dLR_img %s ｜ dTB %s ｜ 脸 L %s ｜ 脸/整幅 %s</p></figure>'
                     % (r.get('kind_why') or '', fmt(r.get('d_lr_img')),
                        fmt(r.get('d_tb')), fmt((r.get('face') or {}).get('L_med')),
                        fmt(r.get('ratio_frame'), 2)))

    h.append('<p class="foot">这张表是我判的，不是真理：阈值的出处是"零"，只有 4 张靠肉眼看过的锚。'
             '你要做的是挑出错的那几张 —— 错的那几张比对的 300 张值钱。</p>')
    h.append('</div></body></html>')

    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, '光向量_300.html')
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(h))
    print(out)


if __name__ == '__main__':
    main()
