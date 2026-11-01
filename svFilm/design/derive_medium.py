# -*- coding: utf-8 -*-
"""给每个目录写出它自己的介质（卷/纸/amount），按源码里的物理量推，不要人拍板。

规则（**没有"选一个预设"这回事**）：

1) **卷**：卡的暗部色偏 CC → "暖度分位"（CC 越负越暖）；卷的暖度 = sepR − sepB
   （`design/stocks.py` 从 profile 读）→ 分位最接近的一支。
   只用 `use == 'still'` 且**有颜色**的负片（黑白卷 sepR=sepB=0，排除）。
   为什么用分位：sepR/sepB 是 log 灵敏度、卡 CC 是密度，单位不能直接比；分位无量纲。

2) **纸**：黑端要求 Dmax ≥ −log10(Y(p5))；卷的设计配纸够就用它，不够才换更厚的纸。
   p5 ≈ 0（被扫描切平）⇒ 没有信息，直接用设计配纸。只用 `use == 'still'` 的 print profile。

3) **amount**：**按卷（film_stock）定，不按目录/不按卡**。
   引擎源码 `couplers.py`：`couplers_matrix *= dir_couplers.amount`，
   它缩放的是 DIR coupler 的抑制矩阵，影响**饱和度/对比度/局部对比**；
   不是“肤色 C 的旋钮”。引擎默认 `DirCouplersParams.amount=1.0`，
   profile 里没有 amount 字段，作者给的标定方式是 0/0.5/1/1.5/2 ramp。
   所以默认取 **1.0**；以后按卷做参考色卡标定，写进 STOCK_AMOUNT，
   绝不拿卡的脸 C/整幅 C 反推。

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
_CARDS = os.path.join(_ROOT, 'cards_v6.json')
_BASE = r'E:\svStudio\svFilm\analysis\corpus\kurai\medium_base'

# 每个语料的**基线介质**（已从旧 preset 一次性冻结成 medium_base/<语料>.json）。
# 这里没有 preset 这个名字，也没有"从若干预设里挑一个"这一步。
CORPUS_BASE = {'鹿井': '鹿井', '滨田': '滨田', 'xhs': 'xhs'}

# 引擎 DIR couplers 默认值（`DirCouplersParams.amount=1.0`；测试也钉 1.0）。
# 以后按卷做 0/0.5/1/1.5/2 参考色卡标定，再把结果写进这里；不要按卡推。
STOCK_AMOUNT = {}


def amount_for_film(film: str) -> float:
    return float(STOCK_AMOUNT.get(film, 1.0))


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
    p5 = (card.get('points') or {}).get('p5')
    if not isinstance(p5, (int, float)):
        p5 = ((card.get('bands') or {}).get('p5') or [None, None])[0]
    L = p5
    if L is None or L <= 1.0:          # 0~1 L* 已经被扫描切平 ⇒ 不约束纸
        return None
    y = (L + 16.0) / 116.0
    return round(-math.log10(y ** 3), 3)


def _paper_shape() -> dict:
    """{纸名: (σ_fast/σ_mid, σ_slow/σ_mid)} —— 从 print profile 的
    `density_curves_model` 读；层序按 **mu（阈值）** 排：mu 最小 = fast（负片的趾部层）。

    这两个比值描述"纸的曲线形状"：fast 控制趾部、slow 控制肩部。
    """
    out = {}
    for p in glob.glob(os.path.join(r'E:\spektrafilm\src\spektrafilm\data\profiles', '*.json')):
        d = json.load(open(p, encoding='utf-8'))
        info = d.get('info') or {}
        if info.get('stage') != 'printing' or info.get('use') != 'still':
            continue
        model = (d.get('data') or {}).get('density_curves_model') or {}
        mu = (model.get('centers') or [[]])[0]
        sig = (model.get('sigmas') or [[]])[0]
        if len(mu) < 3 or len(sig) < 3:
            continue
        o = sorted(range(3), key=lambda i: mu[i])
        sf, sm, ss = float(sig[o[0]]), float(sig[o[1]]), float(sig[o[2]])
        if sm:
            out[info['stock']] = (round(sf / sm, 3), round(ss / sm, 3))
    return out


def _card_shape_ratio(card: dict):
    """卡的形状比 (toe/contrast_win, shoulder/contrast_win) —— 用点值。"""
    b = card.get('bands') or {}
    pt = card.get('points') or {}
    def _pt(key):
        v = pt.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        band = b.get(key)
        return 0.5 * (float(band[0]) + float(band[1])) if band else None
    toe, sh, win = _pt('toe'), _pt('shoulder'), _pt('contrast_win')
    if None in (toe, sh, win):
        return None
    return (toe / win, sh / win) if win else None


def _pct(xs, v):
    if not xs:
        return 0.5
    lo, hi = min(xs), max(xs)
    return (v - lo) / (hi - lo) if hi > lo else 0.5


def pick_paper(film, card: dict, all_cards=None) -> tuple:
    """纸：先满足黑端 Dmax，再按**形状分位**挑最像的那张。

    · 硬约束：纸的 Dmax ≥ −log10(Y(p5))（黑端够不够印）
    · 软匹配：卡的 (toe/win, shoulder/win) 与纸的 (σf/σm, σs/σm) 各按**分位**对齐
      —— 两边单位不可通约（L* 间距 vs logE 层宽），所以只比"谁相对更宽"，
      不比绝对值。这也是为什么**不能**拿形状去直接算 morph 的 γ（实测出来
      1.2~4.2，全撞边界）。
    """
    papers = _papers()
    shapes = _paper_shape()
    need = _required_D(card)
    cands = [k for k, v in papers.items() if need is None or v + 1e-9 >= need]
    if not cands:
        best = max(papers.items(), key=lambda kv: kv[1])
        return best[0], '黑端需 D≥%s，没有纸够 ⇒ 用最厚的 %s（%.2f）' % (need, best[0], best[1])
    cr = _card_shape_ratio(card)
    if cr is None or not shapes:
        return film.target_print if film.target_print in cands else cands[0], \
            '缺形状带 ⇒ 用设计配纸/第一张够厚的'
    cards = all_cards or [card]
    ratios = [r for r in (_card_shape_ratio(c) for c in cards) if r]
    ct, cs = _pct([r[0] for r in ratios], cr[0]), _pct([r[1] for r in ratios], cr[1])
    cand_shapes = {k: shapes[k] for k in cands if k in shapes}
    if not cand_shapes:
        return cands[0], '候选中没有形状数据 ⇒ 取第一张'
    ft = [v[0] for v in cand_shapes.values()]
    fs = [v[1] for v in cand_shapes.values()]
    best, best_d = None, 9e9
    for k, (sf, ss) in cand_shapes.items():
        d = abs(_pct(ft, sf) - ct) + abs(_pct(fs, ss) - cs)
        if d < best_d:
            best, best_d = k, d
    return best, ('黑端需 D≥%s；形状 (toe/win %.2f, sh/win %.2f) 分位 (%.2f, %.2f) '
                  '⇒ 最接近的纸 %s（σf/σm %.2f, σs/σm %.2f）'
                  % (need if need is not None else '无', cr[0], cr[1], ct, cs,
                     best, cand_shapes[best][0], cand_shapes[best][1]))

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


_PROFILES = r'E:\spektrafilm\src\spektrafilm\data\profiles'


def _apply_halation(block: dict, film_name: str) -> str:
    """按**卷**写 halation 的低层物理量（来自引擎自己的 `_HALATION_PRESETS`）。

    为什么这么做：`svFilm/config.py` 里 `PRESET_APPLY_STOCK_SPECIFICS = False`，
    所以引擎那套"按 profile 的 use/antihalation 自动套"**在我们的路径上不跑**；
    介质块里那份值是卡38 的（75 µm / 0.025），所有卷一个样。

    表本身是引擎的（`params_builder._HALATION_PRESETS`），按 (use, antihalation) 给：
       still + strong → sigma_h 65 µm，strength (0.015, 0.005, 0.0)
       still + weak   → sigma_h 65 µm，strength (0.08,  0.02,  0.0)
       still + no     → sigma_h 65 µm，strength (0.30,  0.10,  0.015)
    ⚠ 介质块里这两个字段是**百分数**（`presets._apply` 会 /100），所以要 ×100 写回。
    """
    path = os.path.join(_PROFILES, film_name + '.json')
    if not os.path.isfile(path):
        return '没有这支卷的 profile'
    info = json.load(open(path, encoding='utf-8')).get('info') or {}
    key = (info.get('use'), info.get('antihalation'))
    from spektrafilm.runtime.params_builder import _HALATION_PRESETS
    preset = _HALATION_PRESETS.get(key)
    if not preset:
        return 'profile 没标 use/antihalation（%s）⇒ 保持原值' % (key,)
    h = dict(block.get('halation') or {})
    h['halation_first_sigma_um'] = [round(float(v), 1) for v in preset['sigma_h']]
    h['halation_strength'] = [round(float(v) * 100.0, 3) for v in preset['strength']]
    block['halation'] = h
    return ('%s/%s ⇒ sigma_h %.0f µm，strength %s（写百分数 %s）'
            % (key[0], key[1], preset['sigma_h'][0], preset['strength'],
               h['halation_strength']))


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
        paper, paper_why = pick_paper(film, c, cards)
        block['simulation']['film_stock'] = film.name
        block['simulation']['print_paper'] = paper
        hal_why = _apply_halation(block, film.name)
        amount = amount_for_film(film.name)
        why = ('卷 %s 的 DIR couplers 默认 %.2f（源码 DirCouplersParams.amount；'
               'profile 无此字段，不按卡推）' % (film.name, amount))
        block['couplers'] = dict(block.get('couplers') or {}, amount=amount)
        save(name, block, note='卷按暗部色偏、纸按黑端密度、amount 按脸彩度；端点见 _target')
        apply_targets(name)
        b = load(name)
        b['_derived'] = dict(corpus=corpus, base=base_name, film=film.name, paper=paper,
                             film_reason=film_why, paper_reason=paper_why,
                             amount=amount, amount_reason=why, halation_reason=hal_why,
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
