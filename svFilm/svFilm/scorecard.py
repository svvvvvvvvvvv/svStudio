# -*- coding: utf-8 -*-
r"""**评分卡** —— 每次改动都对着**同一套指标**打分，别只看"这次想改的那个数"。

## 为什么要有它（09-26 SV 的质问：「为什么改了一个地方，另一个地方又出问题？」）
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

from . import color, targets

# 只报不打分的那一项（列在这里是为了让"不打分"这件事**显式**）
NO_TARGET = ('L50',)
SIDE = 1600


def measure(path, side=SIDE):
    """一张成片的六项（都在**成片本身**上量 —— 跟眼睛看到的一致）。"""
    im = Image.open(path).convert('RGB')
    im.thumbnail((side, side), Image.LANCZOS)
    d = np.ascontiguousarray(np.asarray(im, np.uint8).astype(np.float64) / 255.0)
    lab = color.to_lab(d)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    Cc = np.hypot(a, b)
    Lm, bm = float(np.median(L)), float(np.median(b))
    mid = (L >= np.percentile(L, 25.0)) & (L <= np.percentile(L, 75.0))
    return dict(L50=Lm, span=float(np.percentile(L, 95.0) - np.percentile(L, 5.0)),
                C50=float(np.median(Cc)), C90=float(np.percentile(Cc, 90.0)),
                mid_db=float(b[mid].mean() - bm),
                clip=float((d.min(axis=-1) >= 254.0 / 255.0).mean() * 100.0))


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
)


def targets_of(stock):
    """打分项 → (靶, 中文名, 权重)。靶**只从 `targets.json` 取**（跟引擎消费同一个源）。"""
    t = targets.for_stock(stock)

    def g(k):
        v = t.get(k)
        return float(v) if isinstance(v, (int, float)) else float('nan')

    return {
        'span': (g('span'), '跨度 L95−L5', 1.0),
    }


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
    print('%-12s %-9s %-9s %-9s %s' % ('指标', '实测', '靶', '离靶', '加权'))
    total = 0.0
    rec = {}
    for k, (tv, zh, w) in tgt.items():
        mv = med(k)
        if not np.isfinite(mv) or not np.isfinite(tv):
            print('%-12s %-9s %-9s %-9s %s' % (zh, '—', tv, '—', '（样本不够）'))
            continue
        dv = abs(mv - tv) * w
        total += dv
        rec[k] = mv
        print('%-12s %-9.1f %-9.1f %+-9.1f %.1f' % (zh, mv, tv, mv - tv, dv))
    # 只报不打分的（**显式列出来** —— 免得有人以为它们被忘了）
    for k, zh, why in REPORT_ONLY:
        print('%-12s %-9.2f %-9s %-9s %s' % (zh, med(k), '—', '—', why))
    print('-' * 56)
    print('离靶合计（**只打分那几项**；越小越好，只当趋势看、别当分数去刷）：%.1f' % total)
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
