# -*- coding: utf-8 -*-
"""整条引擎侧的流程：**选卷纸 → 出质感参数 → 渲一次 → 交 LR**。

设计出处：分工设计 §8（卷子怎么选）/ §20（引擎只做三件事）/ §16（匹配=选卷纸）。

这一步解决"匹配推荐得再好，渲不出来也没用"的问题：
  · 匹配能给 16 个候选卷；
  · 但**预设只有 3 份、覆盖 2 个卷**（`卡38`/`滨田预设` = portra_400，`xhs预设` = gold_200），
    纸全是 endura；
  · 所以候选要标 `renderable`：有没有对应预设。没有 ⇒ 得先按卷生成预设（那是下一步的活）。

用法：
    from design.flow import choose, describe, presets_table
    ch = choose(recs_engine, intent)
    print(describe(ch))
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import measure as M
from .intent import Intent
from .match import Hit, need_from_batch, rank
from .stocks import Registry, registry
from .texture import Style, TexturePlan, plan_texture, warn

PRESET_DIRS = [r'E:\svStudio\svFilm\svFilm\data\presets']


# ---------------------------------------------------------------- 预设盘点

@dataclass
class Preset:
    name: str
    stock: str
    paper: str
    path: str
    rms: tuple = ()
    amount: Optional[float] = None
    format_mm: Optional[float] = None


def presets_table() -> Dict[str, Preset]:
    """扫所有预设，读它们实际绑定的卷/纸/颗粒档位（**预设才是渲染的真源**）。"""
    out = {}
    for base in PRESET_DIRS:
        for f in sorted(glob.glob(os.path.join(base, '*', 'preset.json'))):
            try:
                j = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            sim = j.get('simulation') or {}
            grain = j.get('grain') or {}
            coup = j.get('couplers') or {}
            name = os.path.basename(os.path.dirname(f))
            out[name] = Preset(
                name=name, stock=sim.get('film_stock') or '?',
                paper=sim.get('print_paper') or '?', path=f,
                rms=tuple(grain.get('rms_granularity') or ()),
                amount=coup.get('amount'),
                format_mm=sim.get('film_format_mm'))
    return out


def preset_for(stock: str, paper: str, table: Optional[Dict[str, Preset]] = None) -> Optional[Preset]:
    table = table if table is not None else presets_table()
    for p in table.values():
        if p.stock == stock and p.paper == paper:
            return p
    return None


# ---------------------------------------------------------------- 选择结果

@dataclass
class Choice:
    hit: Hit
    texture: TexturePlan
    preset: Optional[Preset]
    renderable: bool
    note: str = ''

    @property
    def stock(self) -> str:
        return self.hit.stock.name

    @property
    def paper(self) -> Optional[str]:
        return self.hit.paper


def choose(recs_engine: Dict[str, dict], intent: Optional[Intent] = None,
           topn: int = 5, style: Optional[Style] = None,
           reg: Optional[Registry] = None,
           renderable_only: bool = False) -> List[Choice]:
    """选卷纸：需求 → 排序 → 每个候选附上质感计划 + 能否渲。"""
    reg = reg or registry()
    need = need_from_batch(recs_engine, intent)
    hits = rank(reg, need)
    table = presets_table()
    if renderable_only:
        hits = [h for h in hits
                if preset_for(h.stock.name, h.paper or '', table) is not None][:topn]
    out = []
    for h in hits:
        tp = plan_texture(h.stock.name, h.paper or '', long_side=1080, style=style)
        ps = preset_for(h.stock.name, h.paper or '', table)
        note = ''
        if ps is None:
            note = '没有对应预设 ⇒ 得先按卷生成预设才能渲'
        out.append(Choice(hit=h, texture=tp, preset=ps, renderable=ps is not None,
                          note=note))
    return out


def _row(c: Choice) -> str:
    tag = ('可渲(%s)' % c.preset.name) if c.renderable else '需新预设'
    return ('  %6.3f  %-22s rms=%-5s anti=%-6s 纸=%-30s %s'
            % (c.hit.score, c.stock,
               ('%.1f' % (sum(c.hit.stock.rms) / len(c.hit.stock.rms)))
               if c.hit.stock.rms else '—',
               c.hit.stock.anti, c.paper or '-', tag))


def describe(choices: List[Choice], topn: int = 5) -> str:
    """给人看：**分两层**——能立刻渲的 / 要先做预设的。

    为什么要分层：预设只有 3 份、覆盖 2 个卷；若把"排序前 4"直接列出来，
    大概率全是渲不出来的 ⇒ 表就没用了。
    """
    lines = ['【选卷纸】需求来自场景 + 意图；排序只表示方向对齐']
    ok = [c for c in choices if c.renderable][:topn]
    no = [c for c in choices if not c.renderable][:topn]
    lines.append('')
    lines.append('  ▸ 有预设、可立刻渲（%d）：' % len(ok))
    if ok:
        lines.extend(_row(c) for c in ok)
    else:
        lines.append('    （无）—— 现有预设覆盖不到任何候选，见下面一层')
    lines.append('')
    lines.append('  ▸ 需要先按卷生成预设（%d）：' % len(no))
    lines.extend(_row(c) for c in no)
    lines.append('    ! 卷/纸是**预设构建时**传进 params 的（overrides 改不了 film_stock）')
    lines.append('    ! ⇒ 换卷 = 先按卷生成预设（medium_profiles 那套），不是改一个键')
    if choices:
        lines.append('')
        lines.append('【质感参数】（查表 + 换算 + 档位，逐条带出处）')
        lines.append(choices[0].texture.__str__())
        w = warn(choices[0].texture, long_side=1080).splitlines()
        lines.extend(w)
    return '\n'.join(lines)


# ---------------------------------------------------------------- 渲染/交接（注入式）

def render_stage(choice: Choice, stems: List[str], render_fn=None,
                 measure_fn=None) -> Dict[str, dict]:
    """渲一次并量 ①：**渲染与测量都靠注入**，本层不绑任何具体实现。

    render_fn(stem, choice) -> 出片路径
    measure_fn(path) -> measure_all 的单图记录
    """
    if render_fn is None:
        raise RuntimeError(
            '没有可用的渲染入口。卷/纸是**预设构建时**传进 params 的'
            '（AGENTS §四：overrides 改不了 film_stock）⇒ 换卷必须先按卷生成预设。'
            '现有预设见 design.flow.presets_table()。')
    if measure_fn is None:
        raise RuntimeError('没有注入测量函数；用 design.measure 那套 measure_all')
    recs = {}
    for stem in stems:
        p = render_fn(stem, choice)
        recs[stem] = measure_fn(p)
    return recs


def handoff(choice: Choice, recs_engine: Dict[str, dict]) -> str:
    """给 LR 的一段交接：只说"这张底片是什么"，不给靶。"""
    return ('底片 %s + %s ｜ n=%d ｜ %s'
            % (choice.stock, choice.paper or '-', len(recs_engine),
               M.summarize(recs_engine)))
