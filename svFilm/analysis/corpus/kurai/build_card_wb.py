# -*- coding: utf-8 -*-
"""给每张卡补**卡级 look-white 目标**（`LR设计.md` §2）。

输入：
  · `photos_tone_v03.json`（旧尺子，765 张；v6 由 `build_cards_v6.py` 从 v04 重建）
  · `cards_v5.json`（64 张卡的原样数据；v5 只做回滚）

输出：
  · 原地写回 `cards_v5.json` 的每张卡 `wb` 字段：
        { neutral_axis, white_point, cct_look, duv, n, policy, source, role }

口径（设计：中性轴/白点只取中性面，不拿整体暖色）：
  · 白点 = 每张大师片里"背景高光 ∧ 低彩度"的那块（measure_all 的 `white_point`），
    只收 C* ≤ 8.0、n ≥ 300 的样本；
  · 卡白点 = 各片白点的中位（a*, b*、L_thr、像素数）；
  · 中性轴 = 5 个亮度带里"低彩度（C ≤ 6）中位"的中位，至少 3 片才算；
  · `cct_look` = 白点色度的 McCamy CCT，**仅描述**（D65≈6504）；
    主片已调色，白点≈D65，**不是场景 CCT，也不是解码温度**；
  · tint 没有公开刻度 ⇒ 只存 Δuv（记账），不编 LR tint。
  · 白点样本 < 3 的卡：`white_point=None`，不做目标（设计 §8 失效清单）。

用法：
    & $py -X utf8 analysis/corpus/kurai/build_card_wb.py
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

_CORPUS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_CORPUS)))   # .../svFilm
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from design.color_math import cct_from_lab, duv_from_lab  # noqa: E402

TONE = os.path.join(_CORPUS, 'photos_tone_v03.json')
CARDS = os.path.join(_CORPUS, 'cards_v5.json')

WHITE_C_MAX = 8.0        # 白点只收低彩度的（中性面/白裙/白墙）
WHITE_N_MIN = 300        # 有效像素
NEUTRAL_C_MAX = 6.0      # 中性轴每个亮度带的中位彩度门
NEUTRAL_MIN_N = 3        # 至少 3 片


def _med(xs):
    v = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    return float(st.median(v)) if v else None


def card_wb(rows) -> dict:
    # ---- 白点
    wps = []
    for r in rows:
        wp = r.get('white_point') or {}
        a, b = wp.get('a'), wp.get('b')
        n = wp.get('n') or 0
        C = wp.get('C')
        if a is None or b is None or n < WHITE_N_MIN:
            continue
        if C is None:
            C = math.hypot(a, b)
        if C > WHITE_C_MAX:
            continue
        wps.append(dict(L=wp.get('L_thr'), a=float(a), b=float(b),
                        C=float(C), n=int(n)))
    wb = dict(policy='card-fixed', role='look-white', source='white_point(bg ∧ C*≤%.0f, n≥%d)'
              % (WHITE_C_MAX, WHITE_N_MIN), n=len(wps),
              white_point=None, neutral_axis=None,
              cct_look=None, duv=None)
    if len(wps) >= 3:
        a = _med([w['a'] for w in wps])
        b = _med([w['b'] for w in wps])
        L = _med([w['L'] for w in wps]) or 95.0
        wp = dict(L=round(L, 2), a=round(a, 2), b=round(b, 2),
                  C=round(math.hypot(a, b), 2), n=int(sum(w['n'] for w in wps)))
        wb['white_point'] = wp
        wb['cct_look'] = round(float(cct_from_lab([L, a, b])), 0)
        wb['duv'] = round(float(duv_from_lab([L, a, b])), 5)
    # ---- 中性轴（5 个亮度带；只收低彩度，至少 3 片）
    bands = []
    for i in range(5):
        vals = []
        for r in rows:
            na = r.get('neutral_axis') or []
            if i >= len(na):
                continue
            a, b = na[i].get('a'), na[i].get('b')
            if a is None or b is None:
                continue
            if math.hypot(float(a), float(b)) > NEUTRAL_C_MAX:
                continue
            vals.append((float(a), float(b)))
        if len(vals) >= NEUTRAL_MIN_N:
            bands.append(dict(band=i, n=len(vals),
                              a=round(_med([v[0] for v in vals]), 2),
                              b=round(_med([v[1] for v in vals]), 2)))
    wb['neutral_axis'] = bands or None
    return wb


def main() -> int:
    tone = json.load(open(TONE, encoding='utf-8'))
    rows = [r for r in (tone.get('per_image') or [])
            if r.get('role') == 'master' and not r.get('err')]
    by = {}
    for r in rows:
        by.setdefault(r.get('card'), []).append(r)

    doc = json.load(open(CARDS, encoding='utf-8'))
    n_ok = 0
    for c in doc.get('cards') or []:
        wb = card_wb(by.get(c['name']) or [])
        c['wb'] = wb
        # 旧的顶层 neutral_axis 全是 null（旧尺子没测中性面）⇒ 清掉，真源在 wb.neutral_axis
        na = c.get('neutral_axis')
        if na and all((v or {}).get('a') is None for v in na):
            c.pop('neutral_axis', None)
        if wb['white_point']:
            n_ok += 1
        print('%-32s n=%-3d 白点 %s ｜ T %sK ｜ 中性轴 %s 带'
              % (c['name'], wb['n'],
                 ('a%+.1f b%+.1f' % (wb['white_point']['a'], wb['white_point']['b']))
                 if wb['white_point'] else '—',
                 ('%.0f' % wb['cct_look']) if wb['cct_look'] else '—',
                 len(wb['neutral_axis'] or [])))
    meta = dict(doc.get('_meta') or {})
    meta['wb'] = dict(source='photos_tone_v03.json（design.measure_all）',
                      rule='white_point C*≤%.0f ∧ n≥%d；中性轴 C*≤%.0f、≥%d 片/带'
                           % (WHITE_C_MAX, WHITE_N_MIN, NEUTRAL_C_MAX, NEUTRAL_MIN_N),
                      n_cards=len(doc.get('cards') or []), n_wb=n_ok)
    doc['_meta'] = meta
    tmp = CARDS + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CARDS)
    print('\n写出 %s：%d/%d 张卡有白点' % (CARDS, n_ok, len(doc.get('cards') or [])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
