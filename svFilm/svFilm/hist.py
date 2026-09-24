# -*- coding: utf-8 -*-
r"""LR 风格的直方图工具 —— 以后汇报**看直方图，不看数字**。

## 为什么要它
数值（L5/L50/L95、a\*/b\*…）只有我自己看得懂；SV 要看的是「这张片子的明暗和颜色
分布长什么样、跟相机/大师比差在哪」。**直方图一眼就能看出来。**

## 跟 Lightroom 对齐的地方（规格来自 LR 官方文档 + 那几篇教程的实测描述）
1. **四条直方图叠在一张里**：最上面是**亮度**（灰），下面叠 **R / G / B** 三条彩色。
2. **加色混合**（不是 alpha 叠加）：R+G → 黄、G+B → 青、R+B → 品红、三条齐 → 灰白。
   实现就是三通道各自填充、逐像素相加后截断 —— 跟 LR 观感一致。
3. 横轴 = 显示域亮度 0~255（**不是 Lab 的 L\***，LR 用的是 RGB 亮度）。
4. 横轴分 **5 个区**：`Blacks / Shadows / Exposure / Highlights / Whites`。
   ⚠ LR 里这 5 个区是**软的重叠区间**（跟随滑块的作用曲线），不是硬边界；
      这里按等分 20% 画，是**画法上的近似**，够用。
5. **两端裁切三角**：左边 = 阴影裁切（亮**蓝**）、右边 = 高光裁切（亮**红**）。
6. 纵轴：用 99.5 分位当归一化基准 + 开方压缩 —— 纯线性的话，一个"纯黑"尖峰
   会把其他所有内容压成一条线（LR 也不这么干）。

## 用法
```bash
python -m svFilm.hist out.png 图1.jpg 图2.jpg ...        # 一张图里竖排多张的直方图
python -m svFilm.hist out.png --panel 图1.jpg            # 缩略图 + 直方图 一体
```
```python
from svFilm import hist
hist.draw(disp_array)            # -> PIL.Image（只画直方图）
hist.panel(path_or_array, title)  # -> PIL.Image（缩略图 + 直方图）
```
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---- 5 个区的名字与边界（等分 20%），跟 LR 的滑块同名 ----
ZONES = (
    ('Blacks', 0.00, 0.20),
    ('Shadows', 0.20, 0.40),
    ('Exposure', 0.40, 0.60),
    ('Highlights', 0.60, 0.80),
    ('Whites', 0.80, 1.00),
)

# ---- 配色：**照 SV 09-23 发来的那几张 Apple「照片」/ LR 直方图采样出来的** ----
# （09-24 他明确要求"对齐那个效果"，所以默认画法改成：浅灰实心填充＝亮度，
#   R/G/B 三条**彩色描边线**叠在上面 —— 不是加色填充。加色那版留成 `style='add'`。）
BG_OUT = (41, 41, 41)      # 最外面
BG = (56, 56, 56)          # 直方图面板底（= `bg`）
LUMA = (190, 190, 190)     # 亮度层：**浅灰实心填充**（参照图里它就是最亮的那块）
CH_R = (188, 58, 58)       # 采样自参照图 (184,37,37) ~ (178,47,47)
CH_G = (78, 122, 66)       # 采样自 (81,123,68)
CH_B = (32, 92, 168)       # 采样自 (26,88,164)
GRID = (74, 74, 74)        # 分区线（极淡，别抢戏）
TXT = (170, 170, 176)      # 轴标签
TXT_HI = (222, 222, 228)
TRI_OFF = (86, 86, 90)     # 裁切三角（未触发）
TRI_SH = (90, 165, 255)    # 阴影裁切 = 蓝
TRI_HI = (255, 90, 80)     # 高光裁切 = 红
LINE_W = 3                 # 彩色描边线宽（参照图按比例约这么多）
SMOOTH = 5                 # 彩色描边线的平滑窗口（只为好看，灰填充不动）

_FONT_PATHS = (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\msyhbd.ttc',
               r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\arial.ttf')


def _font(size):
    for p in _FONT_PATHS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:                                      # noqa: BLE001
                pass
    return ImageFont.load_default()


def _luma(rgb):
    """显示域 RGB → LR 意义上的亮度（Rec.709 权重），0~255。"""
    a = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0)
    return (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722) * 255.0


def channels(disp):
    """算出亮度 / R / G / B 四条直方图（各 256 bin，已按 99.5 分位归一 + 开方压缩）。

    @returns {(list[np.ndarray], float, float)} 四条 0~1 的高度曲线 + 阴影/高光裁切比例
    """
    a = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    ys = _luma(a).astype(np.int32).ravel()
    chans = [np.bincount(ys, minlength=256).astype(np.float64)]
    for i in range(3):
        v = (a[..., i] * 255.0).round().astype(np.int32).ravel()
        chans.append(np.bincount(v, minlength=256).astype(np.float64))
    hs = []
    for c in chans:
        ref = float(np.percentile(c[c > 0], 99.5)) if (c > 0).any() else 1.0
        ref = max(ref, 1.0)
        hs.append(np.clip(c / ref, 0.0, 1.0) ** 0.85)
    # 裁切：最暗/最亮那一档的占比（超过 0.05% 才亮三角）
    n = float(ys.size)
    sh_c = float((ys == 0).sum()) / n
    hi_c = float((ys == 255).sum()) / n
    return hs, sh_c, hi_c


def draw(disp=None, w=760, h=240, title=None, marks=None, curves=None, sat=None,
         style='lr', line_w=None, zones=True):
    """画一张直方图。

    `style`：`'lr'`（默认，对齐 SV 参照图：浅灰实心亮度 + R/G/B 描边线）
             `'add'`（加色填充那版：三通道各自填充后相加，LR 老版那种）
    `marks` = [(x01, 标签)] 可选，标出"某个分位落在哪"。
    `zones` = 要不要画 5 个区的刻度与名字。
    """
    if curves is not None:
        hs = list(curves)
        sh_c, hi_c = (sat or (0.0, 0.0))
    else:
        hs, sh_c, hi_c = channels(disp)
    PAD_L, PAD_R = 14, 14
    PAD_T = 30 if title else 10
    PAD_B = 30 if zones else 12
    gw, gh = w - PAD_L - PAD_R, h - PAD_T - PAD_B
    lw = int(line_w or LINE_W)

    img = np.zeros((h, w, 3), np.float64); img[:] = BG
    im = Image.fromarray(img.astype(np.uint8)); dr = ImageDraw.Draw(im)
    f9, f10 = _font(13), _font(14)

    xs = np.clip((np.arange(gw) / max(gw - 1, 1) * 255.0).round().astype(np.int32), 0, 255)

    # ---- 外框（参照图里直方图有一圈更深的底）----
    dr.rectangle([PAD_L - 2, PAD_T - 2, PAD_L + gw + 1, PAD_T + gh + 1], fill=BG_OUT)

    if style == 'add':
        acc = np.zeros((gh, gw, 3), np.float64)
        for ci, c in enumerate(hs):
            band = (np.arange(gh)[:, None] < (c[xs] * gh)[None, :]).astype(np.float64)
            if ci == 0:
                acc += band[..., None] * np.array([0.34, 0.34, 0.36])
            else:
                tint = np.zeros(3); tint[ci - 1] = 1.0
                acc += band[..., None] * tint
        img[PAD_T:PAD_T + gh, PAD_L:PAD_L + gw] = np.clip(acc, 0.0, 1.0) * 255.0
        im = Image.fromarray(img.astype(np.uint8)); dr = ImageDraw.Draw(im)
    else:
        # ★ 对齐参照图：亮度层 = **浅灰实心填充**（在最底下、最亮）
        y0 = PAD_T + gh - 1
        for x in range(gw):
            top = PAD_T + gh - int(round(float(hs[0][xs[x]]) * gh))
            dr.line([(PAD_L + x, top), (PAD_L + x, y0)], fill=LUMA)
        # ★ R/G/B = **三条彩色描边线**（叠在灰填充之上，不填充）
        #   ⚠ 彩色线**要平滑**：只有 256 个 bin 而画布 ~740px ⇒ 不平滑就是满屏台阶。
        #     参照图里那三条线也是平滑的（灰填充反而保留尖刺）—— 所以只平滑彩色线。
        for ci, col in ((1, CH_R), (2, CH_G), (3, CH_B)):
            v = np.asarray(hs[ci], np.float64)[xs]
            if SMOOTH > 1:
                k = np.ones(SMOOTH) / SMOOTH
                v = np.convolve(np.pad(v, SMOOTH // 2, mode='edge'), k, mode='valid')[:gw]
            pts = [(PAD_L + x, PAD_T + gh - float(v[x]) * gh) for x in range(gw)]
            dr.line(pts, fill=col, width=lw, joint='curve')

    # ---- 5 个区的刻度 + 名字（参照图没有，但 SV 要"黑色阴影高光白色"这套；做淡）----
    if zones:
        for _nm, a0, _a1 in ZONES[1:]:
            x = PAD_L + a0 * gw
            for yy in range(PAD_T + gh - 5, PAD_T + gh):
                dr.line([(x, yy), (x, yy)], fill=GRID, width=1)
        for nm, a0, a1 in ZONES:
            cx = PAD_L + (a0 + a1) / 2 * gw
            dr.text((cx, PAD_T + gh + 8), nm, font=f9, fill=TXT, anchor='mm')

    # ---- 两端裁切三角（放在**直方图区的左上 / 右上**，跟 LR 一个位置）----
    for left, cnt, col, nm in ((True, sh_c, TRI_SH, '阴影裁'), (False, hi_c, TRI_HI, '高光裁')):
        k = 18
        x0 = PAD_L if left else PAD_L + gw - 1
        dx = k if left else -k
        dr.polygon([(x0, PAD_T), (x0 + dx, PAD_T), (x0, PAD_T + k)],
                   fill=(col if cnt > 5e-4 else TRI_OFF))
        if cnt > 5e-4:
            dr.text((x0 + (k + 5 if left else -(k + 5)), PAD_T + 3),
                    '%s%.2f%%' % (nm, cnt * 100), font=f9, fill=col,
                    anchor='la' if left else 'ra')

    if title:
        dr.text((PAD_L + 2, 6), title, font=f10, fill=TXT_HI, anchor='la')
    for x01, lab in (marks or []):
        x = PAD_L + float(x01) * gw
        dr.line([(x, PAD_T), (x, PAD_T + gh)], fill=(255, 212, 120), width=1)
        dr.text((x, PAD_T + 4), lab, font=f9, fill=(255, 212, 120), anchor='la')
    return im


def thumb(disp, w):
    a = (np.clip(np.asarray(disp, np.float64), 0.0, 1.0) * 255).astype(np.uint8)
    im = Image.fromarray(a)
    im.thumbnail((w, w), Image.LANCZOS)
    return im


def panel(disp, w=760, title='', thumb_side=260, marks=None, bg=BG, curves=None, sat=None):
    """缩略图 + 直方图（竖排）—— 手机上看得清。

    `curves` 给了就画它（不画缩略图），用来画"一组片的平均直方图"当参照。
    """
    t = thumb(disp, thumb_side) if disp is not None else Image.new('RGB', (1, 0), bg)
    hist = draw(disp, w=w, h=240, title=title, marks=marks, curves=curves, sat=sat)
    H = t.height + (10 if t.height else 0) + hist.height
    out = Image.new('RGB', (w, H), bg)
    if t.height:
        out.paste(t, ((w - t.width) // 2, 0))
    out.paste(hist, (0, t.height + (10 if t.height else 0)))
    return out


def stack(items, w=760, gap=14, bg=BG):
    """把多张 panel 竖着拼成一张（`items` = [(disp, title), ...]）。"""
    ps = [panel(d, w=w, title=t, curves=c, sat=s) for d, t, c, s in
          [(it[0], it[1], it[2] if len(it) > 2 else None, it[3] if len(it) > 3 else None)
           for it in items]]
    H = sum(p.height for p in ps) + gap * (len(ps) - 1)
    out = Image.new('RGB', (w, H), bg)
    y = 0
    for p in ps:
        out.paste(p, (0, y)); y += p.height + gap
    return out


def load_disp(path, side=None):
    """读一张已有的成片（jpg/png）→ 显示域 float [0,1]。"""
    im = Image.open(path).convert('RGB')
    if side:
        im.thumbnail((side, side), Image.LANCZOS)
    return np.asarray(im, np.uint8).astype(np.float64) / 255.0


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='LR 风格直方图（亮度 + RGB 叠加 + 5 个区）')
    ap.add_argument('out', help='输出的 png')
    ap.add_argument('imgs', nargs='+', help='输入图片（jpg/png）')
    ap.add_argument('--side', type=int, default=None, help='先缩到这个长边再算')
    ap.add_argument('--w', type=int, default=760, help='画布宽度')
    a = ap.parse_args(argv)
    items = [(load_disp(p, a.side), os.path.basename(p)) for p in a.imgs]
    if len(items) == 1:
        panel(items[0][0], w=a.w, title=items[0][1]).save(a.out)
    else:
        stack(items, w=a.w).save(a.out)
    print('写下', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))
