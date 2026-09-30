# -*- coding: utf-8 -*-
r"""LR 风格的直方图工具 —— 以后汇报**看直方图，不看数字**。

## 为什么要它
数值（L5/L50/L95、a\*/b\*…）只有我自己看得懂；要看的是「这张片子的明暗和颜色
分布长什么样、跟相机/大师比差在哪」。**直方图一眼就能看出来。**

## 跟 Lightroom 对齐的地方（规格来自 LR 官方文档 + 那几篇教程的实测描述）
1. **四条直方图叠在一张里**：最上面是**亮度**（灰），下面叠 **R / G / B** 三条彩色。
2. **加色混合**（不是 alpha 叠加）：R+G → 黄、G+B → 青、R+B → 品红、三条齐 → 灰白。
   实现就是三通道各自填充、逐像素相加后截断 —— 跟 LR 观感一致。
3. 横轴 = 显示域亮度 0~255（**不是 Lab 的 L\***，LR 用的是 RGB 亮度）；
   ★ LR 官方手册原话：为给直方图提供有用信息，它"假定 gamma 约 2.2、用类似 sRGB 的响应曲线"。
4. 横轴分 **5 个区**：`Blacks / Shadows / Exposure / Highlights / Whites`。
   ⚠ LR 里这 5 个区是**软的重叠区间**（跟随滑块的作用曲线），不是硬边界；
      这里按等分 20% 画，是**画法上的近似**（图上已注明）。
5. **两端裁切三角**，判据 = **任一通道到达端点**（与 LR 一致）：
   左上 = 阴影、右上 = 高光；**颜色随"被裁的是哪个通道"变** ——
   单通道 → 该通道色 · R+G → 黄 · G+B → 青 · R+B → 品红 · 三通道 → 白。
   （09-30 之前用的是 `luma == 255`，那实际在数"**纯白像素**"、不是"高光被裁"，
     漏掉了"只有蓝通道爆"这类情形 —— 详见 `clip_stats` 的注释。）
6. **纵轴固定口径**：满格 = 单档占画面 `YMAX_RATIO`（7%），再开 `Y_GAMMA`（0.45 次幂）压缩。
   ⚠ **刻意不走 LR 那条"自适应"路**（LR 是"最高那点顶满"，两张图没法比高矮），
     因为"和大师比"正是我们的用途。代价：**高度不是线性比例**（图上"半高"实际约 1.5%），
     图上已注明。
7. `waveform()`：横轴 = 画面左右、纵轴 = 亮度、点亮 = 该处像素多。
   直方图只答"**有多少**"，它答"**在哪里**"；顶部那条彩线 = 该位置有通道到端点。
8. `clip_stats()`：逐通道端点统计，`'which'` 直接告诉你 R / G / B 谁到端点。

## 用法
```bash
python -m svFilm.hist out.png 图1.jpg 图2.jpg ...        # 竖排多张的直方图
python -m svFilm.hist out.png --mode wave 图1.jpg        # 波形图（看"哪里亮"）
python -m svFilm.hist out.png --clip 图1.jpg             # 顺便打印逐通道端点统计
```
```python
from svFilm import hist
hist.draw(disp_array)             # -> PIL.Image（只画直方图）
hist.panel(path_or_array, title)  # -> PIL.Image（缩略图 + 直方图）
hist.waveform(disp_array)         # -> PIL.Image（波形图）
hist.clip_stats(disp_array)       # -> dict（逐通道端点占比）
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

# ---- 配色：**照那几张 Apple「照片」/ LR 直方图采样出来的** ----
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
GUIDE = (78, 78, 82)       # 纵轴参考线
GUIDE_TXT = (140, 140, 146)
TRI_OFF = (86, 86, 90)     # 裁切三角（未触发）
# ★★ 09-30：三角颜色改成**逐通道混色**（跟 LR 一致）——
#   只红 → 红 · 只绿 → 绿 · 只蓝 → 蓝 · R+G → 黄 · G+B → 青 · R+B → 品红 · 三个 → 白。
#   用"加色混合"算，所以这里取**纯原色**（老版固定"阴影=蓝 / 高光=红"，
#   那对"只有蓝通道爆"的实况是错的，见 `clip_stats` 的注释）。
TRI_RGB = ((235, 62, 55), (72, 196, 96), (58, 132, 235))
LINE_W = 3                 # 彩色描边线宽（参照图按比例约这么多）
SMOOTH = 5                 # 彩色描边线的平滑窗口（只为好看，灰填充不动）
# ★★ 纵轴上限：**满格 = 某一档占全画面这么多像素**（固定值 ⇒ **跨图可比**）。
#   为什么必须固定：按每张图自己的最大值归一的话，每张的纵轴刻度都不一样，
#   "我的 vs 机内 vs 大师"根本没法比 —— 每张都有个峰顶满，看不出高矮。
#   ★★⚠ 必须在**原尺寸**上量/算（见 `load_disp` 的注释）：缩放会显著改变直方图 ——
#     同一张有颗粒的片子，缩到 400px 单档峰值 8.7%、原尺寸只有 3.3%（**2.6 倍**）。
#   09-24 在**原尺寸**量了 242 张（我们 10 + 机内 120 + 鹿井 32 + 小红书 80）：
#     P50 2.2% · P75 3.4% · P90 6.7% · P95 11.6% · P99 21.6% · 最大 41.5%
#   取 **7%（≈ P90）** ⇒ 约 **90% 的照片不会被削平**。
#   超出的削平 —— 削平本身就是"这一档堆了很多"的信号（LR 也削）。
YMAX_RATIO = 0.07
# ★ 幂压缩（**所有图用同一个指数** ⇒ 仍然完全可比）：满格 8% 对普通片子太宽松 ——
#   大部分片子单档只占 2%，线性画出来只有 1/4 高、全挤在底下没法看形状。
#   开 0.45 次方之后：8% → 满格、2% → 约半高、0.5% → 约三成，形状和可比性都保住。
#   （相机 / LR 内部也是某种压缩；关键是**口径统一**。）
Y_GAMMA = 0.45
# 纵轴上画几条横向参考线（标出"这个高度 = 占画面百分之几"）——⚠ 要按压缩后的高度画
Y_GUIDES = (0.02, 0.05)

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


def _empty_clip():
    """没有端点信息时的占位（只喂 `curves` 画参照曲线时用）。"""
    z = {k: 0.0 for k in ('r', 'g', 'b', 'any', 'all')}
    z['which'] = ''
    return {'n': 0, 'hi': dict(z), 'lo': dict(z)}


def _clip_tint(which):
    """被裁通道串 → 三角颜色（**加色混合**，跟 LR 一致）。"""
    col = np.zeros(3)
    for nm, c in zip('RGB', TRI_RGB):
        if nm in (which or ''):
            col += np.asarray(c, np.float64)
    return TRI_OFF if not col.any() else tuple(int(min(v, 255)) for v in col)


def _codes(x):
    """0~1 显示值 → 0~255 档位（**四舍五入**，与 `color.display_to_u8` 同口径）。

    ⚠ 别用 `.astype(int)`（截断）：那样"喂浮点数组"和"喂 8bit 成片"会落进不同的档，
      同一张图两条路给出不同直方图。成片的档位是四舍五入来的。
    """
    return np.clip(np.rint(np.asarray(x, np.float64) * 255.0), 0.0, 255.0).astype(np.int32)


def _luma(rgb):
    """显示域 RGB → LR 意义上的亮度（Rec.709 权重），0~255。"""
    a = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0)
    return (a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722) * 255.0


def clip_stats(disp):
    """**逐通道**的端点统计 —— 判据与业界一致：**任一通道**到达端点就算。

    ★★ 09-30 修正：老实现数的是 `luma == 255`，而 `luma` 只有 R=G=B=255 时才等于
      255 ⇒ 它实际在数"**纯白像素**"，**不是"高光被裁"**。
      实测代价：鹿井参照的**蓝通道（天空）裁了 0.227%**，老判据报 **0.000%**，
      三角根本不亮 —— 等于看不见，而我们正拿它比高光。
      Lightroom 的判据是"任一通道到端点"，且会告诉你**是哪个通道**：
      Adobe 官网 / CAN Photo 原话 —— "the clipping icon will turn the colour of an
      individual channel that is being clipped … white when all detail is blown-out"。

    @returns {dict}
      `'hi'` / `'lo'` 各是 dict：`'r'|'g'|'b'` 单通道占比 · `'any'` 任一通道 ·
      `'all'` **同一个像素**三通道同时到端点（真·纯白）；另有 `'n'` = 像素数，占比都是 0~1。
      `'which'` = 到达端点的通道串（`'B'` / `'R+G'` / `'R+G+B'` / `''`）——
      ⚠ 它只表示"**这些通道里都有**到端点的像素"，**不要求落在同一个像素上**；
      要"真·纯白"请看 `'all'`。
    """
    a = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    n = float(a.shape[0] * a.shape[1])
    codes = [_codes(a[..., i]) for i in range(3)]
    out = {'n': int(n)}
    for key, hit in (('hi', 255), ('lo', 0)):
        m = [c == hit for c in codes]
        d = {}
        for i, nm in enumerate('rgb'):
            d[nm] = float(m[i].sum()) / n
        d['any'] = float((m[0] | m[1] | m[2]).sum()) / n
        d['all'] = float((m[0] & m[1] & m[2]).sum()) / n
        d['which'] = '+'.join(nm.upper() for i, nm in enumerate('rgb') if m[i].any())
        out[key] = d
    return out


def channels(disp, ymax=None):
    """算出亮度 / R / G / B 四条直方图（各 256 bin）+ 逐通道端点统计。

    ★ 纵轴归一用的是**固定上限** `YMAX_RATIO`（满格 = 某档占全画面这么多），
      **不是**按每张图自己的最大值 —— 那样每张刻度都不一样、跨图不可比。

    @returns {(list[np.ndarray], dict)} 四条 0~1 的高度曲线 + `clip_stats()` 的结果
    """
    a = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    ys = _codes(_luma(a) / 255.0).ravel()
    chans = [np.bincount(ys, minlength=256).astype(np.float64)]
    for i in range(3):
        chans.append(np.bincount(_codes(a[..., i]).ravel(), minlength=256).astype(np.float64))
    ref = max(float(ymax if ymax is not None else YMAX_RATIO), 1e-6)
    hs = [np.clip(c / (float(c.sum()) * ref), 0.0, 1.0) ** Y_GAMMA for c in chans]
    return hs, clip_stats(a)


def draw(disp=None, w=760, h=240, title=None, marks=None, curves=None, clip=None,
         style='lr', line_w=None, zones=True, guides=True):
    """画一张直方图。

    `style`：`'lr'`（默认，对齐参照图：浅灰实心亮度 + R/G/B 描边线）
             `'add'`（加色填充那版：三通道各自填充后相加，LR 老版那种）
    `marks` = [(x01, 标签)] 可选，标出"某个分位落在哪"。
    `zones` = 要不要画 5 个区的刻度与名字。
    `clip`  = `clip_stats()` 的结果（不传就现算）；只喂 `curves` 时用它带端点信息。

    ★ 图上写死两句口径说明（防误读）：
      ① 高度已开 `Y_GAMMA` 次幂压缩 ⇒ **不是线性比例**（"半高"实际只有约 1.5%）；
      ② 5 个区是**等分近似**，Lightroom 里是跟随滑块的软区间。
    """
    if curves is not None:
        hs = list(curves)
        clip = clip or _empty_clip()
    else:
        hs, clip = channels(disp)
    PAD_L, PAD_R = 14, 14
    PAD_T = 30 if title else 10
    PAD_B = 44 if zones else 12      # ★ 多留一行给"5 区是近似"的说明（防误读）
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

    # ---- 纵向参考线（标出"这个高度 = 占画面百分之几"，配合固定上限用）----
    if guides:
        for gy in Y_GUIDES:
            yy = PAD_T + gh - (min(gy / YMAX_RATIO, 1.0) ** Y_GAMMA) * gh
            if not (PAD_T < yy < PAD_T + gh):
                continue
            for xx in range(PAD_L, PAD_L + gw, 7):
                dr.line([(xx, yy), (xx + 3, yy)], fill=GUIDE, width=1)
            dr.text((PAD_L + gw - 3, yy - 2), '%.0f%%' % (gy * 100), font=f9,
                    fill=GUIDE_TXT, anchor='rs')

    # ---- 5 个区的刻度 + 名字（参照图没有，但要"黑色阴影高光白色"这套；做淡）----
    if zones:
        for _nm, a0, _a1 in ZONES[1:]:
            x = PAD_L + a0 * gw
            for yy in range(PAD_T + gh - 5, PAD_T + gh):
                dr.line([(x, yy), (x, yy)], fill=GRID, width=1)
        for nm, a0, a1 in ZONES:
            cx = PAD_L + (a0 + a1) / 2 * gw
            dr.text((cx, PAD_T + gh + 8), nm, font=f9, fill=TXT, anchor='mm')
        # ★ 防误读：LR 里这 5 个区是**软区间**（跟随滑块的作用曲线），这里是等分近似
        dr.text((PAD_L + 2, PAD_T + gh + 26),
                '5 区为等分近似（Lightroom 里是跟随滑块的软区间）'
                '　·　端点占比 <0.05% 视为噪声（不亮三角）',
                font=f9, fill=GUIDE_TXT, anchor='la')

    # ---- 两端裁切三角（放在**直方图区的左上 / 右上**，跟 LR 一个位置）----
    # ★★ 判据 = **任一通道到端点**（业界口径），三角颜色 = **被裁通道的混色**：
    #    只红→红 · 只蓝→蓝 · R+G→黄 · G+B→青 · R+B→品红 · 三个→白。跟 LR 一致。
    for left, key, nm in ((True, 'lo', '阴影裁'), (False, 'hi', '高光裁')):
        d = (clip or _empty_clip()).get(key) or _empty_clip()[key]
        cnt, which = float(d['any']), str(d['which'])
        on = cnt > 5e-4
        col = _clip_tint(which) if on else TRI_OFF
        k = 18
        x0 = PAD_L if left else PAD_L + gw - 1
        dx = k if left else -k
        dr.polygon([(x0, PAD_T), (x0 + dx, PAD_T), (x0, PAD_T + k)], fill=col)
        if on:
            # ⚠ `which` 是"**有哪些**通道到端点"（各通道可以落在**不同**像素上），
            #   不是"同一个像素三通道都爆" ⇒ `R+G+B` 会被读成"纯白"，所以有真·三通道
            #   同白的像素时，把那个数**单独写出来**（`all`），免得歧义。
            txt = '%s %s %.2f%%' % (nm, which, cnt * 100)
            if float(d['all']) > 5e-4:
                txt += '（其中三通道同白 %.2f%%）' % (d['all'] * 100)
            dr.text((x0 + (k + 5 if left else -(k + 5)), PAD_T + 3),
                    txt, font=f9, fill=col, anchor='la' if left else 'ra')

    if title:
        dr.text((PAD_L + 2, 6), title, font=f10, fill=TXT_HI, anchor='la')
        # ★ 防误读：高度是幂压缩过的，**不是线性比例**（图上"半高"实际只有约 1.5%）
        dr.text((PAD_L + gw, 7),
                '满格＝单档 %.0f%%　·　高度已开 %.2g 次幂压缩（非线性）'
                % (YMAX_RATIO * 100, Y_GAMMA),
                font=f9, fill=GUIDE_TXT, anchor='ra')
    for x01, lab in (marks or []):
        x = PAD_L + float(x01) * gw
        dr.line([(x, PAD_T), (x, PAD_T + gh)], fill=(255, 212, 120), width=1)
        dr.text((x, PAD_T + 4), lab, font=f9, fill=(255, 212, 120), anchor='la')
    return im


def waveform(disp=None, w=760, h=240, title=None, clip=None, ref_lines=(0.0, 0.5, 1.0),
             gamma=0.5):
    """波形图（waveform）：**横轴 = 画面左右，纵轴 = 亮度**，点亮程度 = 该处像素数。

    ★★ 和直方图的区别（这正是要它的原因）：
      直方图只回答"**有多少**"，waveform 多一维"**在哪里**"。
      「天空多亮 / 脸多亮 / 暗部沉不沉」全是**空间**问题，直方图答不了
      —— 以前只能靠手写「人在哪」掩膜去补。
      出处：darktable 官方手册（waveform 与 histogram / RGB parade / vectorscope 并列）、
      视频工业标准（Premiere / Resolve / 专业监视器的 waveform + IRE 刻度）。

    ★ 顶部额外画一条**裁切带**：某一列有通道到端点就标出该列，颜色 = 被裁通道的混色
      ⇒ "天空在**哪里**爆的"一眼看得出。

    `gamma` 只影响"计数 → 显示亮度"的压缩（0.5 ≈ 开方），**不改数据**。
    """
    a = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    H, W = a.shape[:2]
    PAD_L, PAD_R = 14, 14
    PAD_T = 52 if title else 30          # 顶部：标题 + 一行提示 + 裁切带
    PAD_B = 30
    gw, gh = w - PAD_L - PAD_R, h - PAD_T - PAD_B

    li = _codes(_luma(a) / 255.0)                                   # (H,W) 0~255
    ci = (np.arange(W, dtype=np.int64) * gw // max(W, 1))           # (W,) 显示列
    idx = li.ravel().astype(np.int64) * gw + np.tile(ci, H)
    cnt = np.bincount(idx, minlength=256 * gw).reshape(256, gw).astype(np.float64)
    val = np.clip(cnt / max(float(cnt.max()), 1.0), 0.0, 1.0) ** gamma
    # 256 档 → gh 行（分段取 max，避免丢档）
    edges = (np.arange(gh + 1) * 256 // gh)
    band = np.empty((gh, gw), np.float64)
    for r in range(gh):
        lo = int(edges[r]); hi = max(int(edges[r + 1]), lo + 1)
        band[r] = val[lo:hi].max(axis=0)
    band = band[::-1]                                               # 亮度高在上

    img = np.zeros((h, w, 3), np.float64); img[:] = BG
    shade = band[..., None] * np.array([150.0, 185.0, 235.0])       # 加色叠加（跟直方图一致）
    img[PAD_T:PAD_T + gh, PAD_L:PAD_L + gw] = np.clip(np.asarray(BG, np.float64) + shade, 0, 255)
    im = Image.fromarray(img.astype(np.uint8)); dr = ImageDraw.Draw(im)
    f9, f10 = _font(13), _font(14)

    # 0 / 50 / 100% 参考线（darktable：顶=100%、中=50%、底=0%）
    for gv in ref_lines:
        yy = PAD_T + gh - float(gv) * gh
        if not (PAD_T <= yy <= PAD_T + gh):
            continue
        for xx in range(PAD_L, PAD_L + gw, 7):
            dr.line([(xx, yy), (xx + 3, yy)], fill=GUIDE, width=1)
        if float(gv) >= 1.0:
            dr.text((PAD_L + gw - 3, yy + 2), '%.0f%%' % (gv * 100), font=f9,
                    fill=GUIDE_TXT, anchor='rt')
        else:
            dr.text((PAD_L + gw - 3, yy - 2), '%.0f%%' % (gv * 100), font=f9,
                    fill=GUIDE_TXT, anchor='rs')

    # 顶部裁切带：哪些显示列有通道到端点，颜色 = 被裁通道的混色
    st = clip or clip_stats(a)
    hits = []
    for i in range(3):
        colany = (_codes(a[..., i]) >= 255).any(axis=0)             # (W,)
        hits.append(np.bincount(ci[colany], minlength=gw) > 0)
    anyhit = hits[0] | hits[1] | hits[2]
    for x in range(gw):
        if anyhit[x]:
            col = np.zeros(3)
            for i, c in enumerate(TRI_RGB):
                if hits[i][x]:
                    col += np.asarray(c, np.float64)
            dr.rectangle([PAD_L + x, PAD_T - 5, PAD_L + x, PAD_T - 1],
                         fill=tuple(int(min(v, 255)) for v in col))
    if bool(anyhit.any()):
        dr.text((PAD_L + 2, PAD_T - 22), '↑ 彩线 = 该位置有通道到端点（颜色 = 被裁的通道）',
                font=f9, fill=GUIDE_TXT, anchor='la')

    if title:
        dr.text((PAD_L + 2, 6), title, font=f10, fill=TXT_HI, anchor='la')
        dr.text((PAD_L + gw, 7), '横轴＝画面左右　纵轴＝亮度（点亮＝该处像素多）',
                font=f9, fill=GUIDE_TXT, anchor='ra')
    dr.text((PAD_L + 2, PAD_T + gh + 8), '← 画面左　　画面右 →', font=f9, fill=TXT, anchor='la')
    def _one(dd, nm):
        if dd['any'] <= 5e-4:
            return '%s 无' % nm
        s = '%s %s %.2f%%' % (nm, dd['which'] or '—', dd['any'] * 100)
        if float(dd['all']) > 5e-4:                     # 真·三通道同白，单独标出来
            s += '（其中三通道同白 %.2f%%）' % (dd['all'] * 100)
        return s
    dr.text((PAD_L + gw, PAD_T + gh + 8),
            _one(st['hi'], '高光端点') + '　·　' + _one(st['lo'], '阴影端点'),
            font=f9, fill=TXT_HI, anchor='ra')
    return im


def thumb(disp, w):
    """缩略图 —— ★ **宽度撑满 w**（跟直方图同宽），高度按原比例。

    ⚠ 不要用 `thumbnail((w, w))` —— 那是"长边不超过 w"，竖图变窄、横图也变窄，
      结果缩略图比直方图窄一圈，页面上左右不齐。
    """
    a = (np.clip(np.asarray(disp, np.float64), 0.0, 1.0) * 255).astype(np.uint8)
    im = Image.fromarray(a)
    h = max(1, int(round(im.height * w / max(im.width, 1))))
    return im.resize((w, h), Image.LANCZOS)


def panel(disp, w=760, title='', thumb_side=260, marks=None, bg=BG, curves=None,
          clip=None, mode='hist'):
    """缩略图 + 直方图（竖排）—— 手机上看得清。

    `curves` 给了就画它（不画缩略图），用来画"一组片的平均直方图"当参照。
    `mode` = `'hist'`（默认，直方图）或 `'wave'`（下面那张换成 waveform）。
    """
    t = thumb(disp, w) if disp is not None else Image.new('RGB', (1, 0), bg)
    if mode == 'wave':
        g = waveform(disp, w=w, h=240, title=title, clip=clip)
    else:
        g = draw(disp, w=w, h=240, title=title, marks=marks, curves=curves, clip=clip)
    H = t.height + (10 if t.height else 0) + g.height
    out = Image.new('RGB', (w, H), bg)
    if t.height:
        out.paste(t, (0, 0))                      # ★ 左对齐（宽已 = w，跟直方图同宽）
    out.paste(g, (0, t.height + (10 if t.height else 0)))
    return out


def stack(items, w=760, gap=14, bg=BG, mode='hist'):
    """把多张 panel 竖着拼成一张（`items` = [(disp, title[, curves, clip]), ...]）。"""
    ps = [panel(d, w=w, title=t, curves=c, clip=s, mode=mode) for d, t, c, s in
          [(it[0], it[1], it[2] if len(it) > 2 else None, it[3] if len(it) > 3 else None)
           for it in items]]
    H = sum(p.height for p in ps) + gap * (len(ps) - 1)
    out = Image.new('RGB', (w, H), bg)
    y = 0
    for p in ps:
        out.paste(p, (0, y)); y += p.height + gap
    return out


def load_disp(path, side=None):
    """读一张已有的成片（jpg/png）→ 显示域 float [0,1]。

    ★★⚠ **`side` 默认 None = 不缩放，这是刻意的 —— 别为了快就把图缩小再算。**
      09-24 实测（同一张有颗粒的成片）：
        缩到 400px 单档峰值 8.68% · 缩到 900px 5.79% · **原尺寸 3.34%**
      降采样会把颗粒/细节抹平、像素挤到少数几档 ⇒ **直方图被改**。
      而且**有颗粒的图受影响远大于平滑的图**（同一批：机内 JPG 只从 4.41% 变到 3.37%）
      ⇒ 在同一 `side` 下比也是**不公平**的（我方看着高，其实是颗粒被抹的结果）。
      要跨图比就必须**都用原尺寸**。

    ★ 16 位文件**不再静默降位**（09-30）：PIL 的 `.convert('RGB')` 会把 16 位砍成 8 位，
      那样"高光有没有到端点"这种 0.0x% 量级的判断直接失效。
      现在 8 位按 /255、16 位按 /65535，各自回到 [0,1]。
      （16 位分支**忽略 `side`** —— 它本来就是"要精确量"的场景，不该缩放。）
    """
    im = Image.open(path)
    a = np.asarray(im)
    if a.dtype == np.uint16 or (a.dtype in (np.int32, np.int16) and a.size and a.max() > 255):
        a = a[..., :3] if a.ndim == 3 else a
        return np.asarray(a, np.float64) / 65535.0
    im = im.convert('RGB')
    if side:
        im.thumbnail((side, side), Image.LANCZOS)
    return np.asarray(im, np.uint8).astype(np.float64) / 255.0


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='LR 风格直方图（亮度 + RGB 叠加 + 5 个区）')
    ap.add_argument('out', help='输出的 png')
    ap.add_argument('imgs', nargs='+', help='输入图片（jpg/png/tif）')
    ap.add_argument('--side', type=int, default=None, help='先缩到这个长边再算')
    ap.add_argument('--w', type=int, default=760, help='画布宽度')
    ap.add_argument('--mode', choices=('hist', 'wave'), default='hist',
                    help='hist=直方图（默认）· wave=波形图（横轴＝画面左右）')
    ap.add_argument('--clip', action='store_true', help='顺便打印逐通道端点统计')
    a = ap.parse_args(argv)
    items = [(load_disp(p, a.side), os.path.basename(p)) for p in a.imgs]
    if a.clip:
        for d, t in items:
            st = clip_stats(d)
            print('%-28s 高光端点 %-5s %.3f%%   阴影端点 %-5s %.3f%%'
                  % (t, st['hi']['which'] or '—', st['hi']['any'] * 100,
                     st['lo']['which'] or '—', st['lo']['any'] * 100))
    if len(items) == 1:
        panel(items[0][0], w=a.w, title=items[0][1], mode=a.mode).save(a.out)
    else:
        stack(items, w=a.w, mode=a.mode).save(a.out)
    print('写下', a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))
