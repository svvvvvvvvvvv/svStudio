# -*- coding: utf-8 -*-
"""引擎侧求解器：**只做三件事**，而且都不逐图。

    ① 选卷/纸（风格）
    ② 收片校验 T0（余量：p99 ≤ 92、clip < 1%、p1 ≥ 3）
    ③ 白点校验 T3（|a|,|b| ≤ 2）

设计出处：分工设计 第四部分 §20-§21。
**没有逐图参数**：同一批所有图用同一套引擎参数出片。
引擎自带的中灰归一要保留（它是余量保证机制，不是我们的曝光旋钮）。

用法：
    from design.engine import EnginePlan, check_batch
    plan = EnginePlan(stock='kodak_portra_400', paper='kodak_portra_endura')
    bad = check_batch(plan, engine_records)     # engine_records = ① 态的 measure_all 记录
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .invariants import check_T, report


@dataclass
class EnginePlan:
    stock: str
    paper: str
    # 入口 WB：只在 RAW 导入器生效（运行时没有 WB 参数）
    wb_temperature: Optional[float] = None      # None = 用卷纸配对关系给的缺省
    wb_tint: Optional[float] = None
    note: str = ''

    def key(self) -> str:
        return '%s+%s' % (self.stock, self.paper)


@dataclass
class BatchCheck:
    plan_key: str
    n: int = 0
    fail_T0: List[str] = field(default_factory=list)
    fail_T3: List[str] = field(default_factory=list)
    details: Dict[str, object] = field(default_factory=dict)


def check_batch(plan: EnginePlan, recs: Dict[str, dict]) -> BatchCheck:
    """对一批「① 引擎出片」的记录做 T0/T3 校验。recs = {文件名: 记录}。"""
    out = BatchCheck(plan_key=plan.key(), n=len(recs))
    for name, rec in recs.items():
        r = check_T(rec, stage='engine')
        if not r.ok:
            bad = [i.name for i in r.failures()]
            if any(b.startswith('T0') for b in bad):
                out.fail_T0.append('%s: %s' % (name, ','.join(bad)))
            if any(b.startswith('T3') for b in bad):
                out.fail_T3.append('%s: %s' % (name, ','.join(bad)))
        out.details[name] = r
    return out


def judge_plan(plan: EnginePlan, recs: Dict[str, dict], log=print) -> bool:
    """一个卷纸组合能不能用：T0/T3 全过才算过。打印归因，不猜。"""
    c = check_batch(plan, recs)
    log('== %s ｜ n=%d ==' % (c.plan_key, c.n))
    if c.fail_T0:
        log('  T0 不过 %d 张（余量不足）：' % len(c.fail_T0))
        for x in c.fail_T0[:5]:
            log('    ' + x)
        log('  ⇒ 这不是"再乘一个系数"能修的：查入口 WB / 卷纸 / 引擎自带中灰归一')
    if c.fail_T3:
        log('  T3 不过 %d 张（白点偏色）：' % len(c.fail_T3))
        for x in c.fail_T3[:5]:
            log('    ' + x)
        log('  ⇒ 白点在入口 WB；运行时没有 WB 参数，别在预设里找')
    if not c.fail_T0 and not c.fail_T3:
        log('  ✓ T0/T3 全过 —— 这张底片可以交给 LR')
    return not (c.fail_T0 or c.fail_T3)


# ---------------------------------------------------------------- 与 LR 的交接

def handoff_note(plan: EnginePlan, recs: Dict[str, dict]) -> str:
    """给 LR 侧的一段交接说明（人可读），只描述"这张底片是什么"，不给靶。"""
    n = len(recs)
    p99 = [r['img']['p99'] for r in recs.values() if r.get('img')]
    clip = [r['img']['clip_pct'] for r in recs.values() if r.get('img')]
    faceL = [r['skin']['L'] for r in recs.values() if r.get('skin')]
    m = lambda xs: (sum(xs) / len(xs)) if xs else float('nan')
    return ('底片：%s ｜ n=%d ｜ p99 中位 %.1f ｜ clip 中位 %.2f%% ｜ 脸 L 中位 %.1f'
            % (plan.key(), n, m(p99), m(clip), m(faceL)))
