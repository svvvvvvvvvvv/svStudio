# -*- coding: utf-8 -*-
"""给每个目录写出它自己的介质（卷/纸/amount），按源码里的物理量推，不要人拍板。

规则（**没有"选一个预设"这回事**）：

1) **卷**：卡的暗部色偏 CC → "暖度分位"（CC 越负越暖）；卷的暖度 = sepR − sepB
   （`design/stocks.py` 从 profile 读）→ 分位最接近的一支。
   只用 `use == 'still'` 且**有颜色**的负片（黑白卷 sepR=sepB=0，排除）。
   为什么用分位：sepR/sepB 是 log 灵敏度、卡 CC 是密度，单位不能直接比；分位无量纲。

2) **纸**：黑端要求 Dmax ≥ −log10(Y(p5))；卷的设计配纸够就用它，不够才换更厚的纸。
   p5 ≈ 0（被扫描切平）⇒ 没有信息，直接用设计配纸。只用 `use == 'still'` 的 print profile。

3) **amount**：按**颜色饱和度**——本卡脸 C 带中点 ÷ 同语料中位，等比缩放到语料标定值，
   夹在 [0.6, 1.6]。依据：源码里 amount 是耦合矩阵的标量倍率（`couplers.py:126`），
   正比于耦合强度；颜色浓度是它最直接的后果。用比值只需要一个锚点，
   不需要"每单位 amount 值多少 C"那种没出处的斜率（手册那条 2.3 C/单位 已被否：
   反解出来是 −1.8~7.5，69% 撞边界）。

4) **中性滤片（WB）不用我们定**：引擎自带 `data/filters/neutral_print_filters.json`
   （8 纸 × 20 卷 × 照明），`digest_params` 按 (卷, 纸) 自动套 C/M/Y 中性值。

产物：`analysis/corpus/kurai/media/<卡名>.json` + 汇总 `card_medium.json`
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics as st

_ROOT = r'E:\svStudio\svFilm\analysis\corpus\kurai'
_CARDS = os.path.join(_ROOT, 'cards_v5.json')
_BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai\medium_base'

# 每个语料的**基线介质**（已从旧 preset 一次性冻结成 medium_base/<语料>.json）。
# 这里没有 preset 这个名字，也没有"从若干预设里挑一个"这一步。
CORPUS_BASE = {'鹿井': '鹿井', '滨田': '滨田', 'xhs': 'xhs'}

# 引擎手册：couplers.amount +1 ⇒ 颊 C* +2.3（台账级，n=2~3 卷）
C_PER_AMOUNT = 2.3
AMOUNT_MIN, AMOUNT_MAX = 0.6, 1.6


def _block(corpus: str) -> dict:
    return json.load(open(os.path.join(_BASE, corpus + '.json'), encoding='utf-8'))


def _papers() -> dict:
    """{纸名: Dmax} —— 从 print profile 的密度曲线读。"""
    out = {}
    for p in glob.glob(os.path.join(r'E:\spektrafilm\src\spektrafilm\data\profiles', '*.json')):
        d = json.load(open(p, encoding='utf-8'))
        info = d.get('info') or {}
        if info.get('stage') != 'printing' or info.get('use') != 'still':
            continue
        dens = (d.get('data') or {}).get('density_curves') or []
        if dens:
            out[info['stock']] = round(max(max(r) for r in dens), 3)
    return out


def _required_D(card: dict):
    """卡的黑端要求多少纸密度（Dmax）。黑端被扫到 0 = 没信息，返回 None。"""
    p5 = (card.get('bands') or {}).get('p5') or [None, None]
    L = p5[0]
    if L is None or L <= 1.0:          # 0~1 L* 已经被扫描切平 ⇒ 不约束纸
        return None
    y = (L + 16.0) / 116.0
    return round(-math.log10(y ** 3), 3)


def pick_paper(film, card: dict) -> tuple:
    """纸：先用卷的**设计配纸**；只有当这张卡的黑端密度超出它的 Dmax 时才换。

    ★ 依据：纸的 Dmax 是硬上限（`density_curves` 的最大密度）；
      黑端被扫到 0 的卡（p5≈0）没有信息，不据此换纸。
    """
    papers = _papers()
    need = _required_D(card)
    designed = film.target_print
    if need is None:
        return designed, '黑端无信息（p5≈0）⇒ 用设计配纸 %s' % designed
    if designed in papers and papers[designed] + 1e-9 >= need:
        return designed, ('黑端需 D≥%.2f，设计配纸 %s Dmax=%.2f 够用'
                          % (need, designed, papers[designed]))
    ok = [(v, k) for k, v in papers.items() if v + 1e-9 >= need]
    if not ok:
        best = max(papers.items(), key=lambda kv: kv[1])
        return best[0], ('黑端需 D≥%.2f，没有纸够（最高 %s %.2f）⇒ 用它' % (need, best[0], best[1]))
    v, k = min(ok)
    return k, '黑端需 D≥%.2f，设计配纸不够 ⇒ 换 %s（Dmax %.2f）' % (need, k, v)
_DB = r'E:\spektrafilm\src\spektrafilm\data\filters\neutral_print_filters.json'
_REF_PAPER = 'kodak_portra_endura'
_REF_ILLUM = 'TH-KG3'


def _neutral_y() -> dict:
    """{卷名: 中性黄滤片 Y} —— 引擎自带数据库（作者按 log_sensitivity 算出来的色平衡）。

    Y 越大 = 这支卷越"蓝富"（要更多黄滤片才能中性）；
    Y 越小 = 越"蓝穷"（偏黄/暖）。
    这比 sepR−sepB 更接近"色偏"这件事：它是**中和这支卷需要多少黄**。
    """
    d = json.load(open(_DB, encoding='utf-8'))
    return {k: float(v[2]) for k, v in (d.get(_REF_PAPER, {}).get(_REF_ILLUM) or {}).items()}


def pick_film(shadow_cc, all_cc=None) -> tuple:
    """按卡的**暗部偏蓝程度**匹配卷：用中性黄滤片 Y 做分位对齐。

    卡：暗部 CC 正 = 偏蓝。
    卷：中性黄滤片 Y 大 = 蓝富（暗部更容易偏蓝）。
    两边都按"蓝的程度"升序排，取分位最接近的一支。
    ★ 只用 use=still、有颜色、且在中性滤片库里的负片。
    """
    from design.stocks import registry
    reg = registry()
    ys = _neutral_y()
    cands = [s for s in reg.stocks.values()
             if s.kind == 'negative' and s.use == 'still'
             and (abs(s.sepR) > 1e-6 or abs(s.sepB) > 1e-6)
             and s.name in ys]
    if not cands:
        return None, '没有可用负片卷'
    if shadow_cc is None:
        return min(cands, key=lambda s: abs(ys[s.name] - 50.0)), '没有暗部 Lab ⇒ 取最中性'
    vals = sorted(ys[s.name] for s in cands)
    lo, hi = vals[0], vals[-1]
    cc_all = list(all_cc or [shadow_cc])
    c_lo, c_hi = min(cc_all), max(cc_all)
    card_pct = (shadow_cc - c_lo) / (c_hi - c_lo) if c_hi > c_lo else 0.5
    best, best_d = None, 9e9
    for s in cands:
        pct = (ys[s.name] - lo) / (hi - lo) if hi > lo else 0.5
        d = abs(pct - card_pct)
        if d < best_d:
            best, best_d = s, d
    return best, ('暗部 %+.1f CC ⇒ 蓝度分位 %.2f；取中性黄滤片 Y=%.1f（分位 %.2f）的卷 %s'
                  % (shadow_cc, card_pct, ys[best.name],
                     (ys[best.name] - lo) / (hi - lo) if hi > lo else 0.5, best.name))


def apply_all() -> dict:
    from design.medium import apply_targets, load, save
    cards = json.load(open(_CARDS, encoding='utf-8'))['cards']
    out = {}
    all_cc = [x for x in (_shadow_cc(c['name']) for c in cards) if x is not None]
    for c in cards:
        name = c['name']
        corpus = c.get('corpus') or ''
        base_name = CORPUS_BASE.get(corpus)
        if not base_name:
            out[name] = dict(ok=False, why='语料「%s」没有基线介质' % corpus)
            continue
        block = _block(base_name)
        shadow_cc = _shadow_cc(name)
        film, film_why = pick_film(shadow_cc, all_cc)
        if film is None:
            out[name] = dict(ok=False, why=film_why)
            continue
        paper, paper_why = pick_paper(film, c)
        block['simulation']['film_stock'] = film.name
        block['simulation']['print_paper'] = paper
        base_amt = float(block['couplers']['amount'])
        amount, why = _amount_from_saturation(base_amt, name, cards)
        block['couplers'] = dict(block.get('couplers') or {}, amount=amount)
        save(name, block, note='卷按暗部色偏、纸按黑端密度、amount 按脸彩度；端点见 _target')
        apply_targets(name)
        b = load(name)
        b['_derived'] = dict(corpus=corpus, base=base_name, film=film.name, paper=paper,
                             film_reason=film_why, paper_reason=paper_why,
                             amount=amount, amount_reason=why,
                             split=_split_of(name), shadow_cc=shadow_cc,
                             # ★ 诚实标记：这是**按成片统计逐目录拟合**出来的，
                             #   不是"这个摄影师真的用了这支卷"。场景差异与介质差异
                             #   从打印端分不开（62 张同门卡会被分到 11 支卷）。
                             scene_fit=True,
                             neutral_filters='engine database (neutral_print_filters.json)')
        save(name, b, note='卷按暗部色偏、纸按黑端密度、amount 按分色强度；中性滤片由引擎数据库给')
        out[name] = dict(ok=True, base=base_name, film=film.name, paper=paper,
                         amount=amount, film_reason=film_why, paper_reason=paper_why,
                         amount_reason=why)
    tmp = os.path.join(_ROOT, 'card_medium.json') + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(_ROOT, 'card_medium.json'))
    _report(out)
    return out


def _report(out: dict) -> str:
    """出一张人看的表：每行 = 目录 → 卷+纸、amount、凭哪个量推的。"""
    p = os.path.join(_ROOT, 'card_medium.md')
    rows = ['# 每目录介质（按成片统计拟合，`scene_fit`）', '',
            '> 卷/纸/amount 都是**逐目录**算的；但打印端分不开"场景"与"介质"，',
            '> 所以这是"这张成片像哪套介质"，不是"摄影师真的用了这支卷"。', '',
            '| 目录 | 卷 | 纸 | amount | 依据 |', '|---|---|---|---|---|']
    for name, v in sorted(out.items()):
        if not v.get('ok'):
            continue
        rows.append('| %s | %s | %s | %.2f | %s ｜ %s |'
                    % (name, v['film'], v['paper'], v['amount'],
                       v['film_reason'].replace('|', '/'),
                       v['paper_reason'].replace('|', '/')))
    with open(p, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(rows) + '\n')
    return p


_TONE = os.path.join(_ROOT, 'card_tone.json')


def _shadow_cc(name: str):
    """这个目录暗部偏蓝/偏黄多少 CC（card_tone 的 Lab → 定义式）。"""
    tones = json.load(open(_TONE, encoding='utf-8'))
    lab = ((tones.get(name) or {}).get('shadow') or {}).get('lab')
    if not lab:
        return None
    L, a, b = lab
    fY = (L + 16.0) / 116.0
    Z = (fY - b / 200.0) ** 3
    Y = fY ** 3
    return round(math.log10(Z / Y) * 100.0, 1)


def _split_of(name: str):
    """分色强度 = |暗部 CC − 高光 CC|（蓝/黄）。全部来自 card_tone 的 Lab。"""
    tones = json.load(open(_TONE, encoding='utf-8'))
    t = tones.get(name) or {}
    sh = (t.get('shadow') or {}).get('lab')
    hi = (t.get('highlight') or {}).get('lab')
    if not sh or not hi:
        return None
    def cc(lab):
        L, a, b = lab
        fY = (L + 16.0) / 116.0
        Z = (fY - b / 200.0) ** 3
        Y = fY ** 3
        return math.log10(Z / Y) * 100.0
    return round(abs(cc(sh) - cc(hi)), 2)


def _face_c_mid(name: str, cards=None):
    """这张卡的脸彩度带中点（= 它的"颜色饱和度"目标）。"""
    cards = cards or json.load(open(_CARDS, encoding='utf-8'))['cards']
    c = next((x for x in cards if x['name'] == name), None)
    b = ((c or {}).get('bands') or {}).get('face_C')
    return 0.5 * (float(b[0]) + float(b[1])) if b else None


def _amount_from_saturation(base: float, name: str, cards=None) -> tuple:
    """amount 由**颜色饱和度**定：以语料标定值为锚，按"本卡脸 C / 同语料中位"等比缩放。

    为什么用比值而不是绝对值：`amount` 在源码里是耦合矩阵的标量倍率
    （`couplers.py:126 couplers_matrix *= amount`），正比于耦合强度；
    颜色浓度是它最直接的可观测后果。用比值只需要一个锚点（语料已标定值），
    不需要"每单位 amount 值多少 C"这种没出处的斜率。

    ⚠ 仍分不开"场景本来就艳"和"耦合剂强"——所以介质里标了 scene_fit。
    """
    cards = cards or json.load(open(_CARDS, encoding='utf-8'))['cards']
    corpus = next((c.get('corpus') for c in cards if c['name'] == name), '')
    mine = _face_c_mid(name, cards)
    if mine is None:
        return base, '没有脸彩度带 ⇒ 沿用 %.2f' % base
    xs = [x for x in (_face_c_mid(c['name'], cards) for c in cards
                      if c.get('corpus') == corpus) if x is not None]
    if len(xs) < 5:
        return base, '同语料样本不足（n=%d）⇒ 沿用 %.2f' % (len(xs), base)
    med = st.median(xs)
    ratio = mine / med if med else 1.0
    amount = round(max(0.6, min(1.6, base * ratio)), 2)
    return amount, ('脸 C %.2f / 同语料中位 %.2f = %.3f ⇒ amount %.2f×%.3f = %.2f（夹 0.6~1.6）'
                    % (mine, med, ratio, base, ratio, amount))
