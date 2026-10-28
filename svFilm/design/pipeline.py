# -*- coding: utf-8 -*-
"""编排：三态 · 一次算一次验 · 回归检查。

三态（分工设计 §1）：
    ① 引擎片      ← 引擎求解对着这一态
    ② 过曲线      ← LR 影调求解对着这一态
    ③ 成片        ← 肤色求解 / 验收 / 匹配对着这一态

**铁律**：每个旋钮只能对着它作用之前那一态量。
- 拿 ③ 的数去定 ② 的参数 = 之前所有口径错位的来源。

用法（骨架）：
    from design.pipeline import Stage, run_batch
    b = run_batch(plan, stems, render)      # render(stem, stage, params) -> 记录
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .invariants import check_S, check_T, report


@dataclass
class Stage:
    """一条链上的一个状态 + 它的测量 + 它的口径指纹。"""
    name: str                       # engine / curve / final
    records: Dict[str, dict] = field(default_factory=dict)
    measure_sha: str = ''
    params: Dict[str, object] = field(default_factory=dict)


@dataclass
class Batch:
    engine: Stage = field(default_factory=lambda: Stage('engine'))
    curve: Stage = field(default_factory=lambda: Stage('curve'))
    final: Stage = field(default_factory=lambda: Stage('final'))
    log: List[str] = field(default_factory=list)

    # ---- 口径一致性：三态必须同一套测量代码 ----
    def caliber_ok(self) -> bool:
        shas = {s.measure_sha for s in (self.engine, self.curve, self.final) if s.measure_sha}
        return len(shas) <= 1

    # ---- 回归检查：改上游后，下游负责的量是否被带动 ----
    def regression(self) -> Dict[str, str]:
        out = {}
        for stem in self.engine.records:
            a = self.engine.records.get(stem)
            b = self.curve.records.get(stem)
            c = self.final.records.get(stem)
            if b:
                r = check_S(a, b)            # 曲线只该动明暗，不该动肤色/背景
                out['%s@curve' % stem] = report(r)
            if c:
                r2 = check_S(b or a, c)      # 点颜色只该动肤色，不该动明暗/背景
                out['%s@colour' % stem] = report(r2)
        return out

    # ---- 三态技术底线 ----
    def gates(self) -> Dict[str, str]:
        out = {}
        for stage, recs, mode in (('engine', self.engine.records, 'engine'),
                                  ('final', self.final.records, 'final')):
            bad = []
            for stem, rec in recs.items():
                r = check_T(rec, stage=mode)
                if not r.ok:
                    bad.append('%s: %s' % (stem, ','.join(i.name for i in r.failures())))
            out[stage] = '通过' if not bad else ('破了 %d 张: %s' % (len(bad), bad[:3]))
        return out


def run_batch(stems: List[str],
              render_engine: Callable[[str], dict],
              render_curve: Callable[[str], dict],
              render_final: Callable[[str], dict],
              measure_sha: str = '') -> Batch:
    """跑一批三态。三个 render_* 各自返回该态的 measure_all 记录。

    真正的渲染/导出/测量由调用方注入 —— 这样编排层不绑任何具体工具。
    """
    b = Batch()
    b.engine.measure_sha = b.curve.measure_sha = b.final.measure_sha = measure_sha
    for stem in stems:
        b.engine.records[stem] = render_engine(stem)
        b.curve.records[stem] = render_curve(stem)
        b.final.records[stem] = render_final(stem)
    return b
