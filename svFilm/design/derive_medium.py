# -*- coding: utf-8 -*-
"""给每个目录写出它自己的介质（卷/纸/amount），按源码里的物理量推，不要人拍板。

规则（**没有"选一个预设"这回事**）：

1) **卷**：卡的暗部色偏 CC → "暖度分位"（CC 越负越暖）；卷的暖度 = sepR − sepB
   （`design/stocks.py` 从 profile 读）→ 分位最接近的一支。
   只用 `use == 'still'` 且**有颜色**的负片（黑白卷 sepR=sepB=0，排除）。
   为什么用分位：sepR/sepB 是 log 灵敏度、卡 CC 是密度，单位不能直接比；分位无量纲。

2) **纸**：黑端要求 Dmax ≥ −log10(Y(p5))；卷的设计配纸够就用它，不够才换更厚的纸。
   p5 ≈ 0（被扫描切平）⇒ 没有信息，直接用设计配纸。只用 `use == 'still'` 的 print profile。

3) **amount**：同语料内按分色强度（暗部 CC − 高光 CC）排百分位，在基线值上下 ±15% 缩放
   （**这条是有界工程选择，不是物理定律**）。

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
def pick_film(shadow_cc, all_cc=None) -> tuple:
    """按卡的**暗部色偏**匹配卷：用**分位**对齐，不掺量纲、不取极端。

    为什么用分位：sepR/sepB 是 log 灵敏度（无量纲），卡的 CC 是密度（无量纲），
    两个单位不能直接比大小；但"这批卡里谁最暖、20 支卷里谁最暖"是可比的。
      卡暖度 = −CC（CC 负 = 偏黄 = 暖）
      卷暖度 = sepR − sepB（大红灵敏度、小蓝灵敏度 = 暖）
    取"卷暖度的分位"最接近"卡暖度的分位"的那支。

    ★ 黑白卷（sepR=sepB=0，如 kodak_doublex）排除：它给不出颜色。
    ★ 纸不再作为筛选条件：引擎的 `neutral_print_filters.json` 覆盖 8 纸 × 20 卷，
      换纸/换卷都会自动套对应的中性滤片（`digest_params` 干这事）。
    """
    from design.stocks import registry
    reg = registry()
    cands = [s for s in reg.stocks.values()
             if s.kind == 'negative' and s.use == 'still'
             and (abs(s.sepR) > 1e-6 or abs(s.sepB) > 1e-6)]
    if not cands:
        return None, '没有彩色负片卷'
    if shadow_cc is None:
        return min(cands, key=lambda s: abs(s.sepR - s.sepB)), '没有暗部 Lab ⇒ 取最中性'
    warmth = [s.sepR - s.sepB for s in cands]
    lo, hi = min(warmth), max(warmth)
    cc_all = list(all_cc or [shadow_cc])
    c_lo, c_hi = min(cc_all), max(cc_all)
    card_warm_pct = (c_hi - shadow_cc) / (c_hi - c_lo) if c_hi > c_lo else 0.5   # 越暖 → 1
    best, best_d = None, 9e9
    for s, w in zip(cands, warmth):
        pct = (w - lo) / (hi - lo) if hi > lo else 0.5
        d = abs(pct - card_warm_pct)
        if d < best_d:
            best, best_d = s, d
    return best, ('暗部 %+.1f CC ⇒ 暖度分位 %.2f；取暖度最接近的卷 %s（sepR %+.2f / sepB %+.2f）'
                  % (shadow_cc, card_warm_pct, best.name, best.sepR, best.sepB))


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
        split = _split_of(name)
        base_amt = float(block['couplers']['amount'])
        amount, why = _amount_from_split(base_amt, name, split)
        block['couplers'] = dict(block.get('couplers') or {}, amount=amount)
        save(name, block, note='卷按暗部色偏、纸按黑端密度、amount 按分色强度；端点见 _target')
        apply_targets(name)
        b = load(name)
        b['_derived'] = dict(corpus=corpus, base=base_name, film=film.name, paper=paper,
                             film_reason=film_why, paper_reason=paper_why,
                             amount=amount, amount_reason=why,
                             split=split, shadow_cc=shadow_cc,
                             neutral_filters='engine database (neutral_print_filters.json)')
        save(name, b, note='卷按暗部色偏、纸按黑端密度、amount 按分色强度；中性滤片由引擎数据库给')
        out[name] = dict(ok=True, base=base_name, film=film.name, paper=paper,
                         amount=amount, film_reason=film_why, paper_reason=paper_why,
                         amount_reason=why)
    tmp = os.path.join(_ROOT, 'card_medium.json') + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(_ROOT, 'card_medium.json'))
    return out


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


def _amount_from_split(base: float, name: str, split) -> tuple:
    """amount 由分色强度定：在**同语料**里排百分位，映射到 base 的 ±15%。"""
    if split is None:
        return base, '没有暗部/高光 Lab ⇒ 沿用 %.2f' % base
    cards = json.load(open(_CARDS, encoding='utf-8'))['cards']
    corpus = next((c.get('corpus') for c in cards if c['name'] == name), '')
    xs = []
    for c in cards:
        if c.get('corpus') == corpus:
            s = _split_of(c['name'])
            if s is not None:
                xs.append(s)
    if len(xs) < 5:
        return base, '同语料样本不足（n=%d）⇒ 沿用 %.2f' % (len(xs), base)
    pct = sum(1 for x in xs if x <= split) / float(len(xs))
    amount = round(base * (0.85 + 0.30 * pct), 2)
    return amount, ('分色强度 %.1f 在同语料排 %.0f%% ⇒ amount %.2f×(0.85+0.30×%.2f)=%.2f'
                    % (split, 100 * pct, base, pct, amount))
