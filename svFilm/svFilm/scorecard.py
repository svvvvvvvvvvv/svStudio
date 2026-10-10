# -*- coding: utf-8 -*-
r"""**评分卡** —— 每次改动都对着**同一套指标**打分，别只看"这次想改的那个数"。

## 为什么要有它（09-26）
就在 09-26 这一天里，我自己干了**两次**"只看一个数就下结论"：
  · 白位改到 0.92：只看"亮部对准大师的 92.0"，实际**把跨度和彩P90 都压穿了**（后改成 1.00）；
  · 说"脸的绝对亮度不必按场景分档"：只在**样本最多**的亮度段（整张 55~65）配平，
    而问题在暗段（整张 40~50，我们脸 53 vs 大师 67）—— **13.8 格被平均掉了**。
⇒ 冻结一套指标，**每改一次全量打分 + 留档**，"改一个坏一个"才会变成看得见的趋势线。

## 打分项 + 只看不打分
★ 只有**不变量**才配当"靶"（铁律 ①）。**落点是内容量，不设靶**，只报出来。

| 指标 | 为什么它可以当靶 |
|---|---|
| 跨度 L95−L5 | 09-24 证明它跨场景恒定（鹿井 514 张三组都是 82.6）|
| **局部对比 `lc`**（10-08 新增）| 不设单点靶，用**大师分布的 P25~P75 当带**（见下 §局部对比）|

## ★★★★★ §局部对比 —— 那把 1.10 / 3.33 的孤儿尺子（10-08 补）

**为什么补**：`diffusion_size_um` 60（我们）vs 20（作者默认）这个 3 倍的偏离，
当初（`3a0fe77` 09-29 01:04）就是**照"正中靶 1.10"定的**；19 分钟后又改了颗粒；
09-30 同一"定义"却测出 **3.33**（`84e7084`），自己也写「1.10 **无法复核**、
另一次会话另一把尺子」。⇒ **一把没有正式定义、也没进本文件的尺子，决定了 10 条预设的物理参数。**

**冻结的定义**（`LC_*` 常量；改定义 = 必须 bump `LC_VERSION` 并重算大师带）

1. **量什么**：**带限 RMS 对比度**（band-limited RMS contrast，**Peli 1990**,
   *Contrast in complex images*, JOSA A 7(10):2032 —— 复数图像里 contrast 的标准定义：
   先带通、再取 `σ/L̄`，**不是**逐像素梯度、**不是**局部标准差窗口）。
   ⇒ 实现 = **DoG 带通**（Difference of Gaussians，= 拉普拉斯金字塔的一层，Burt & Adelson 1983）。
2. **在哪量**：`measure()` 里**已经在算的那张 L\***（CIELAB 明度，0~100）——
   与卡片其它项同一个像素数组、同一把重采样，**不另开一条路**。
3. **尺度锚在哪**：★ **画面宽度**，不是像素。先把图归一到 `LC_SIDE_W = 1080`（**只缩不放**：
   大师 xhs 成片本来就 1080 宽，缩了就把人家的细节抹了）。
   频带写成**宽度的分数**：`周期 ∈ [W/64, W/16]`。
   ⇒ 为什么不用"µm / 35mm 片幅"：**大师的参照是 xhs JPG，片幅未知** ⇒ 锚在片幅上两边对不齐。
4. **频带为什么是这个**（`LC_HEAD = (64, 16)`）：
   · 大师源是 **1080 宽的小红书 JPG**（强压缩）—— 高频早被压掉了，
     所以**细于 W/64（≈17 px）的带两边不可比**（我们测得到、人家测不到）；
   · 粗于 W/16（≈68 px）就不是"局部"了，那是整张反差（已有 `span` 管）。
   ⇒ `[W/64, W/16]` 正好是摄影里说的「**局部对比 / 微反差 / clarity**」那一档，
     且**在两边都活得下来**。
5. **归一**：`lc = 100 × std(band) / mean(L*)`（Peli 的 `σ/L̄`，用整张均值当 `L̄`）。
   ⇒ 无量纲、可跨尺寸比。
6. **另外报形状**（4 个倍频程子带 `lc_b1..lc_b4`）—— "看分布、别看中间点"：
   两个 `lc` 相同的片子，细带 vs 粗带的分布可以完全不同。

⚠ **本项不打单点靶**（铁律：只有不变量才配当靶，而 `lc` 是内容量）。
   打分方式 = **离大师带（P25~P75）的距离**：带内 = 0；带外 = 到最近那条边的距离。
   大师带存在 `data/targets.json` 的 `_lc_band` 里（由 `_v1_lc_masters.py` 生成，1076 张）。

⚠ 09-29 新分支 `drop-tone-and-skin`：原来打分的「脸 L\* / 脸彩度 / 脸色相」三项
  随**肤色层 / 认人认脸**一起删除（那三项要靠 `face.py` 取脸掩膜）。

## 用法
  python -m svFilm.scorecard <成片目录> [--stock 卷名] [--tag 说明] [--log 文件.jsonl]
目录里放**出好的成片 JPG**。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as _st
import sys
import time

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter as _gauss

from . import color, targets

# 只报不打分的那一项（列在这里是为了让"不打分"这件事**显式**）
NO_TARGET = ('L50',)
SIDE = 1600

# ===========================================================================
# ★★★★★ 局部对比（带限 RMS 对比度 / Peli 1990）—— 定义见模块头 §局部对比
# ===========================================================================
LC_VERSION = 1          # ★ 改下面任何一个常量都必须 +1，并**重算大师带**
LC_SIDE_W = 1080        # 归一到「宽度 = 1080」（只缩不放）
LC_HEAD = (64, 16)      # 头条指标频带：周期 ∈ [W/64, W/16]
LC_OCTAVES = (128, 64, 32, 16)   # 倍频程子带（形状用），周期对为 (128,64)(64,32)(32,16)(16,8)


def _lc_bands(lab_L):
    """L*(0~100) → (lc, [4 个倍频程])。

    DoG：`band(P1,P2) = G(σ=P1/2π) − G(σ=P2/2π)`（σ 单位为像素；
    高斯 σ 与周期 P 的关系 σ = P/(2π)，见 §局部对比）。
    """
    L = np.asarray(lab_L, dtype=np.float64)
    W = float(L.shape[1])
    mean = float(L.mean())
    if mean <= 1e-6:
        return float('nan'), [float('nan')] * len(LC_OCTAVES)

    def blur(P):
        return _gauss(L, sigma=max(P / (2.0 * np.pi), 0.3), mode='nearest')

    def rms(P1, P2):
        return 100.0 * float(np.std(blur(P1) - blur(P2))) / mean

    head = rms(W / LC_HEAD[0], W / LC_HEAD[1])
    oct_ = [rms(W / n, W / (n / 2.0)) for n in LC_OCTAVES]
    return head, oct_


def _resize_wide(im, width=LC_SIDE_W):
    """★ 只缩不放（放大会把大师的细节凭空造出来）。"""
    if im.width > width:
        im = im.resize((width, max(1, int(round(im.height * width / float(im.width)))),
                        ), Image.LANCZOS)
    return im


def measure(path, side=SIDE):
    """一张成片的各项（都在**成片本身**上量 —— 跟眼睛看到的一致）。"""
    im0 = Image.open(path).convert('RGB')
    im = im0.copy()
    im.thumbnail((side, side), Image.LANCZOS)
    d = np.ascontiguousarray(np.asarray(im, np.uint8).astype(np.float64) / 255.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    Cc = np.hypot(a, b)
    Lm, bm = float(np.median(L)), float(np.median(b))
    mid = (L >= np.percentile(L, 25.0)) & (L <= np.percentile(L, 75.0))
    o = dict(L50=Lm, span=float(np.percentile(L, 95.0) - np.percentile(L, 5.0)),
             C50=float(np.median(Cc)), C90=float(np.percentile(Cc, 90.0)),
             mid_db=float(b[mid].mean() - bm),
             clip=float((d.min(axis=-1) >= 254.0 / 255.0).mean() * 100.0))
    # ---- 局部对比：**另起一条重采样路**（宽度 1080、只缩不放），只在 L* 上算 ----
    _lw = color.to_lab(np.ascontiguousarray(
        np.asarray(_resize_wide(im0), np.uint8).astype(np.float64) / 255.0))[..., 0]
    o['lc'], _oct = _lc_bands(_lw)
    for _i, _v in enumerate(_oct, 1):
        o['lc_b%d' % _i] = _v
    return o


# ★★★ 只给**有明确实测来源**的项打分；其它项一律只报不打分。
#   理由：打分要有一个**站得住的靶**，而"靶从哪来"正是这个项目反复翻车的地方
#   （口径不同 ⇒ 配平后差 5~10 格全是假的）。
#   ⚠ `targets.json` 里 鹿井 的 `mid_abs = [0.77, 4.81]` 与它的 `mid_note`（鹿井 −0.37/+0.45）
#     **对不上** ⇒ 中调这一项的靶**还没钉死**，所以这一版**不打分**（只报），
#     等把它弄清楚了再加进来（这是待办，不是可以糊过去的）。
C90_TARGET = 25.2   # 来源：`targets.json` 鹿井条目 note「彩度P90 25.2」
REPORT_ONLY = (
    ('L50', '落点 L50', '绑内容 + 绑曝光，**不是风格** ⇒ 永远不打分'),
    ('C50', '彩中位', '口径待钉（我们/大师两批的取法不同）'),
    ('C90', '彩P90', '靶 25.2（鹿井 note），但同口径的大师值没重量过 ⇒ 先只报'),
    ('mid_db', '中调 Δb*', '`targets.json` 的 `mid_abs` 与 `mid_note` 对不上 ⇒ 靶待钉'),
    ('clip', '糊死%', '越低越好；只有 0 与非 0 有意义，不是连续指标'),
    ('lc_b1', '局部对比 b1（最细）', '形状用 —— 与 b2..b4 一起看分布'),
    ('lc_b2', '局部对比 b2', '形状用'),
    ('lc_b3', '局部对比 b3', '形状用'),
    ('lc_b4', '局部对比 b4（最粗）', '形状用'),
)


def lc_band(stock=None):
    """大师 `lc` 的 P25~P75（**带**，不是靶）。缺失 ⇒ `(nan, nan)`，只报不打分。"""
    d = targets.load()
    e = (d.get('_lc_band') or {})
    head = e.get('head') or {}
    lo, hi = head.get('p25'), head.get('p75')
    if lo is None or hi is None:
        return float('nan'), float('nan')
    return float(lo), float(hi)


def targets_of(stock):
    """打分项 → (靶, 中文名, 权重)。靶**只从 `targets.json` 取**（跟引擎消费同一个源）。"""
    t = targets.for_stock(stock)

    def g(k):
        v = t.get(k)
        return float(v) if isinstance(v, (int, float)) else float('nan')

    return {
        'span': (g('span'), '跨度 L95−L5', 1.0),
    }


def scope_of(key):
    """该键的【内容关系】—— 决定它到底能不能当"靶"。

    见 `targets.json` 的 `_provenance` 与技能 §209/§210：
      cross = 跨内容（鹿井 747 张 vs 我们）⇒ **只能当线索**，不能宣布"做对了"；
      same  = 同内容（合成测试图 / 同片配对）⇒ 才可作**对照/验收**；
      phys  = 物理或上游依据。
    """
    try:
        pv = targets.load().get('_provenance') or {}
        return (pv.get(key) or {}).get('scope', '?')
    except Exception:                                        # noqa: BLE001
        return '?'


def main(argv=None):
    ap = argparse.ArgumentParser('svFilm.scorecard')
    ap.add_argument('folder')
    ap.add_argument('--stock', default='Ultramax400沉褐')
    ap.add_argument('--tag', default='')
    ap.add_argument('--log', default=r'E:/Debug_svStudio/_debug/评分卡.jsonl')
    ap.add_argument('--side', type=int, default=SIDE)
    a = ap.parse_args(argv)

    ps = sorted(glob.glob(os.path.join(a.folder, '*.jpg')))
    if not ps:
        print('这个目录里没有 jpg:', a.folder)
        return 1
    rows = []
    for i, p in enumerate(ps, 1):
        try:
            rows.append(measure(p, a.side))
        except Exception as e:                                       # noqa: BLE001
            print('  跳过 %s: %s' % (os.path.basename(p), str(e)[:50]))
        if i % 20 == 0:
            print('  %d/%d' % (i, len(ps)), flush=True)

    def med(k):
        v = [r[k] for r in rows if r.get(k) is not None]
        return float(_st.median(v)) if v else float('nan')

    tgt = targets_of(a.stock)
    print()
    print('评分卡 · %s · %d 张 · 卷 %s' % (a.folder, len(rows), a.stock))
    if a.tag:
        print('说明：%s' % a.tag)
    # ★★★ 10-10（SV 拍板 A：「把靶改成对照」）：**先说清这个表是什么**。
    #   本项目**当前没有"跨内容靶"**：下面每一个数都是【跨内容线索】（鹿井 747 张 vs 我们），
    #   按 §209 的文献结论，跨内容对齐**不成立**（Adobe US9857953：完全迁移统计会"产生伪影、
    #   值被拉伸得过于激进"；Reinhard 2001 的已知局限：无法建立空间/语义对应、内容差大时退化）。
    #   ⇒ 这个表的作用是**定位问题**，**不是**验收；验收在同内容的两条路上（合成幻影 / 同片配对）。
    print('★ 口径：下表全部是【跨内容线索】（大师语料 vs 我们），只用于**定位问题**；')
    print('   判据不在这个表里 —— 在 ① 合成测试图 ② 同片配对（同一张原片：我们 vs 用户 LR）③ 目检。')
    print()
    print('%-12s %-9s %-9s %-9s %s   %s' % ('指标', '实测', '线索值', '距离', '加权', '内容关系'))
    total = 0.0
    rec = {}
    _nscored, _notgt = 0, []          # ★ 10-08：真正打了分的项数 / 缺靶的项
    for k, (tv, zh, w) in tgt.items():
        mv = med(k)
        if not np.isfinite(mv) or not np.isfinite(tv):
            # ★ 10-08：**区分"样本不够"与"这条预设根本没写这个靶"**。
            #   老代码两种情况都打"（样本不够）"并 `continue` ⇒ 没靶时 `total` 停在 0.0，
            #   最后那行"离靶合计 0.0"**看起来完美**，其实是"一条靶都没有"（假绿）。
            if not np.isfinite(tv):
                _notgt.append(zh)
                print('%-12s %-9s %-9s %-9s %s' % (zh, '—', '无靶', '—',
                      '★ 这条预设没有这个靶（`_default` 也没兜住）'))
            else:
                print('%-12s %-9s %-9s %-9s %s' % (zh, '—', tv, '—', '（样本不够）'))
            continue
        dv = abs(mv - tv) * w
        total += dv
        rec[k] = mv
        _nscored += 1
        _sc = scope_of(k)
        _tag = {'cross': 'cross·线索', 'same': '★same·对照', 'phys': 'phys·依据'}.get(_sc, _sc)
        print('%-12s %-9.1f %-9.1f %+-9.1f %.1f   [%s]' % (zh, mv, tv, mv - tv, dv, _tag))
    # 只报不打分的（**显式列出来** —— 免得有人以为它们被忘了）
    _lclo, _lchi = lc_band(a.stock)
    for k, zh, why in REPORT_ONLY:
        print('%-14s %-9.2f %-9s %-9s %s' % (zh, med(k), '—', '—', why))
    print('-' * 56)
    # ---- ★ 局部对比：**带**（P25~P75）不是靶 ----
    _mv = med('lc')
    if np.isfinite(_lclo) and np.isfinite(_lchi) and np.isfinite(_mv):
        _dv = 0.0 if _lclo <= _mv <= _lchi else min(abs(_mv - _lclo), abs(_mv - _lchi))
        rec['lc'] = _mv
        total += _dv
        _nscored += 1
        print('%-14s %-9.2f %-9s %-9s %.2f   （大师带 P25~P75 = %.2f ~ %.2f，带内记 0）'
              % ('局部对比 lc', _mv, '%.2f~%.2f' % (_lclo, _lchi), '%+.2f' % _dv, _dv, _lclo, _lchi))
    else:
        print('%-14s %-9.2f %-9s %-9s %s' % ('局部对比 lc', med('lc'), '—', '—',
                                             '大师带缺失 ⇒ 先跑 `_v1_lc_masters.py` 生成 `_lc_band`'))
    print('-' * 56)
    # ★ 10-08：**别让"没靶"长得像"满分"**。老代码在没有可打分的靶时照样打 0.0。
    if _nscored == 0:
        print('离靶合计：**没有可打分的靶**（本预设缺 `span` / 大师带）'
              '—— 这不是"0.0 = 完美"，是"没靶可打"！')
    else:
        print('线索距离合计（**这不是分数、也不是"离靶"** —— 上表全是跨内容线索，'
              '只能当趋势/定位；要验收请走合成图或同片配对）：%.1f'
              '   （比了 %d 项%s）'
              % (total, _nscored,
                 ('；缺靶 ' + '/'.join(_notgt)) if _notgt else ''))
    try:
        with open(a.log, 'a', encoding='utf-8') as f:
            f.write(json.dumps(dict(t=time.strftime('%Y-%m-%d %H:%M'), folder=a.folder,
                                    stock=a.stock, tag=a.tag, n=len(rows),
                                    total=round(total, 2),
                                    m={k: round(v, 3) for k, v in rec.items()},
                                    L50=round(med('L50'), 2), clip=round(med('clip'), 3)),
                               ensure_ascii=False) + '\n')
        print('已留档 → %s' % a.log)
    except Exception as e:                                           # noqa: BLE001
        print('留档失败（不影响本次打分）:', e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
