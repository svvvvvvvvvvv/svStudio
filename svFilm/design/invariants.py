# -*- coding: utf-8 -*-
"""不变量：整套判据只有"不许破"和"意图"，没有"该到多少"。

设计出处：`E:\\ds\\photo\\分工设计_引擎与LR_1029.md` 第三部分 §14 / 第六部分 §34。

分两组：
  T*  技术不变量 —— 破了 = 成像错误（引擎负责）
  S*  结构不变量 —— 破了 = 用下游补上游（LR 负责）

用法：
    from design.invariants import check_T, check_S, report
    res = check_T(rec_engine)                 # rec = measure_all.py 的单图记录
    print(report(res))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---- 阈值：只从「量化下限 / 物理不可逆 / 生理常识」来，不从任何一批成片来 ----
# ★ T0 修正（2026-10-29 首次真跑 + 大师实测）：
#   原来写 p99 ≤ 92 —— 比任何一套大师都严 3~6 L*（大师 p99 中位：鹿井 94.7 /
#   xhs 97.9 / 滨田 94.6），结果逼着引擎"为留余量把脸压到 L40"。
#   改为与大师同口径的**区间**（区间不是靶）：
#       p95 ≤ 93（大师 p95 中位 90.6~92.9，p75 上沿 92.9~96.5）
#       clip < 0.5%（大师中位 0.08~0.39%）
#   "还能抬几档"（headroom）只作为**信息**报出，不再当通过/不通过。
#   p1 也按大师实测放宽：大师 p1 中位 鹿井 6.6 / xhs 1.6 / 滨田 16.4（跨语料差 10 倍）
#   取 1.0 = "1% 处不许已经是纯黑"，只是防死黑，不管风格。
T0 = dict(p95_max=93.0, clip_max=0.5, p1_min=1.0)
T1 = dict(clip_max=2.5)                             # 终片不爆白（可见剪裁量级）
T2 = dict(p1_min=2.0)                               # 黑端不堵
T3 = dict(ab_max=2.0)                               # 白点不偏（8-bit 量化下限 ~1）
T4 = dict(L_lo=55.0, L_hi=85.0)                     # 脸在合理曝光区（顺光/未知）
# ★ 逆光单独一套（2026-10-29 目录卡实测，n=43）：
#   目录卡的逆光片脸 L 中位 50.4（P10~P90 = 42~58），整段都在 55 以下；
#   按 55 判，目录卡自己的逆光片全不合格 ⇒ 这条不变量本来就该分光位。
T4_BACKLIT = dict(L_lo=45.0, L_hi=70.0)
T5 = dict(C_min=10.0)                               # 脸不灰
S1 = dict(dL_max=1.0)                               # 改色不改明暗
S2 = dict(dab_max=1.0)                              # 改局部不动全局
S3 = dict(anchor_max=3.0)                           # 曲线不替介质背锅


@dataclass
class Item:
    name: str
    ok: bool
    value: object = None
    limit: object = None
    why: str = ''


@dataclass
class Result:
    items: List[Item] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i.ok for i in self.items)

    def add(self, name, ok, value=None, limit=None, why=''):
        self.items.append(Item(name, bool(ok), value, limit, why))
        return self

    def failures(self):
        return [i for i in self.items if not i.ok]


def report(res: Result) -> str:
    lines = []
    for i in res.items:
        mark = '✓' if i.ok else '✗'
        lines.append('  %s %-6s 值=%-10s 限=%-10s %s'
                     % (mark, i.name, _f(i.value), _f(i.limit), i.why))
    lines.append('  ⇒ %s' % ('全部通过' if res.ok else '破了 %d 条' % len(res.failures())))
    return '\n'.join(lines)


def _f(v):
    if v is None:
        return '—'
    if isinstance(v, float):
        return '%.2f' % v
    return str(v)


# ---------------------------------------------------------------- T 组

def check_T(rec: dict, stage: str = 'final') -> Result:
    """技术不变量。stage='engine' 时用 T0（出片余量），'final' 时用 T1/T2。

    rec 是 `measure_all.py` 的单图记录。
    """
    r = Result()
    img = rec.get('img') or {}
    skin = rec.get('skin') or {}
    wp = rec.get('white_point') or None

    if stage == 'engine':
        r.add('T0-p95', (img.get('p95') or 0) <= T0['p95_max'],
              img.get('p95'), T0['p95_max'], '高光上沿与大师同区间')
        r.add('T0-clip', (img.get('clip_pct') or 0) < T0['clip_max'],
              img.get('clip_pct'), T0['clip_max'], '出片不许贴白')
        r.add('T0-p1', (img.get('p1') or 0) >= T0['p1_min'],
              img.get('p1'), T0['p1_min'], '出片不许堵黑')
    else:
        r.add('T1-clip', (img.get('clip_pct') or 0) < T1['clip_max'],
              img.get('clip_pct'), T1['clip_max'], '终片不爆白')
        r.add('T2-p1', (img.get('p1') or 0) >= T2['p1_min'],
              img.get('p1'), T2['p1_min'], '黑端不堵')

    if wp:
        worst = max(abs(wp.get('a', 0.0)), abs(wp.get('b', 0.0)))
        r.add('T3-白点', worst <= T3['ab_max'], worst, T3['ab_max'], '白点不偏色')

    if skin:
        r.add('T4-脸L', T4['L_lo'] <= skin.get('L', 0) <= T4['L_hi'],
              skin.get('L'), '%g~%g' % (T4['L_lo'], T4['L_hi']), '脸在合理曝光区')
        r.add('T5-脸C', (skin.get('C') or 0) >= T5['C_min'],
              skin.get('C'), T5['C_min'], '脸不灰')
    return r


# ---------------------------------------------------------------- S 组

def check_S(before: dict, after: dict, curve_anchors: Optional[List[float]] = None) -> Result:
    """结构不变量：拿同一张图"改动前/改动后"的两份测量比。

    before/after 都是 measure_all 的单图记录；curve_anchors 是这一步曲线在各锚点的位移（L*）。
    """
    r = Result()
    sb, sa = before.get('skin') or {}, after.get('skin') or {}
    if sb and sa:
        dL = abs((sa.get('L') or 0) - (sb.get('L') or 0))
        r.add('S1-改色不改明暗', dL <= S1['dL_max'], dL, S1['dL_max'], '肤色 L 位移')
        r.add('S4-仍是人样',
              T4['L_lo'] <= (sa.get('L') or 0) <= T4['L_hi'] and (sa.get('C') or 0) >= T5['C_min'],
              'L=%.1f C=%.1f' % (sa.get('L') or 0, sa.get('C') or 0), 'T4/T5', '改色后仍在人样范围')

    bb, ba = before.get('bg') or {}, after.get('bg') or {}
    if bb and ba:
        d = max(abs(ba.get('a', 0) - bb.get('a', 0)), abs(ba.get('b', 0) - bb.get('b', 0)))
        r.add('S2-不动背景', d <= S2['dab_max'], d, S2['dab_max'], '背景 a/b 位移')
    if before.get('env_share') is not None and after.get('env_share') is not None:
        d = abs(after['env_share'] - before['env_share'])
        r.add('S2-背景占比', d <= 0.02, d, 0.02, '背景占比位移')

    if curve_anchors:
        w = max(abs(x) for x in curve_anchors)
        r.add('S3-曲线预算', w <= S3['anchor_max'], w, S3['anchor_max'], '每锚点位移（L*）')
    return r


def at_limit(values: Dict[str, tuple], margins: Dict[str, float]) -> List[str]:
    """S5：哪些旋钮顶到了量程。values = {name: (v, lo, hi)}，margins = {name: 余量}。

    顶到量程 ⇒ 记欠账，不许用别的旋钮补。
    """
    out = []
    for k, (v, lo, hi) in values.items():
        m = margins.get(k, 0.0)
        if (v - lo) <= m or (hi - v) <= m:
            out.append('%s=%.3f 顶到 [%g, %g]' % (k, v, lo, hi))
    return out
