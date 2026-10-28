# -*- coding: utf-8 -*-
"""整条引擎侧的流程：**选卡 → 出质感参数 → 渲一次 → 交 LR**。

单位是「卡」（见 `cards.py`）：一个卡 = 一卷 + 一张纸 + 一组质感档位 + 风格档。
对外只有卡；预设文件只是卡的落地实现。

流程：
    need       ← 场景测量 + 意图（只用场景侧的量，不用自己出片的偏差）
    rank_cards ← 现有卡之间排序（**能立刻用**）
    rank_stocks← 卷之间排序（**值得做新卡的参考**，不是能选的）
    texture    ← 选中那张卡的质感参数（查表 + 换算 + 档位，逐条带出处）
    render     ← 注入式：渲一次、量一次
    handoff    ← 只告诉 LR"这张底片是什么"，不给靶

用法：
    from design.flow import choose, describe
    offer = choose(recs_engine, intent)
    print(describe(offer))
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import measure as M
from .cards import Card, cards as all_cards
from .cards_match import CardHit, rank_cards, table_cards
from .intent import Intent
from .match import Need, need_from_batch, rank as rank_stocks
from .stocks import Registry, registry
from .texture import Style, TexturePlan, plan_texture, warn


@dataclass
class Choice:
    hit: CardHit
    texture: TexturePlan
    note: str = ''

    @property
    def card(self) -> Card:
        return self.hit.card

    @property
    def stock(self) -> str:
        return self.hit.card.stock

    @property
    def paper(self) -> str:
        return self.hit.card.paper

    @property
    def renderable(self) -> bool:
        return True                       # 卡本身就是"能渲"的组合


@dataclass
class Offer:
    choices: List[Choice] = field(default_factory=list)
    new_card_candidates: List[str] = field(default_factory=list)
    need: Optional[Need] = None

    def best(self) -> Optional[Choice]:
        return self.choices[0] if self.choices else None


def choose(recs_engine: Dict[str, dict], intent: Optional[Intent] = None,
           style: Optional[Style] = None, reg: Optional[Registry] = None,
           topn: int = 3) -> Offer:
    reg = reg or registry()
    tb = all_cards()
    need = need_from_batch(recs_engine, intent)
    offer = Offer(need=need)
    for h in rank_cards(tb, reg, need)[:topn]:
        tp = plan_texture(h.card.stock, h.card.paper, long_side=1080, style=style)
        offer.choices.append(Choice(hit=h, texture=tp))
    for h in rank_stocks(reg, need)[:3]:
        offer.new_card_candidates.append(h.stock.name)
    return offer


def describe(offer: Offer) -> str:
    reg = registry()
    lines = ['【选卡】']
    lines.append(table_cards(all_cards(), reg, offer.need))
    if offer.new_card_candidates:
        lines.append('')
        lines.append('【值得做新卡的卷】（参考，不是现在能选的）：')
        lines.append('  ' + ', '.join(offer.new_card_candidates))
        lines.append('  ! 换卷 = 做一张新卡，不是改一个键'
                     '（卷/纸在卡构建时传进 params，overrides 改不了）')
    if offer.choices:
        lines.append('')
        lines.append('【选中卡的质感参数】（查表 + 换算 + 档位，逐条带出处）')
        lines.append(str(offer.choices[0].texture))
        lines.extend(warn(offer.choices[0].texture, long_side=1080).splitlines())
    return '\n'.join(lines)


# ---------------------------------------------------------------- 渲染/交接（注入式）

def render_stage(choice: Choice, stems: List[str],
                 render_fn: Optional[Callable] = None,
                 measure_fn: Optional[Callable] = None) -> Dict[str, dict]:
    """渲一次并量 ①。渲染与测量都由调用方注入，本层不绑任何具体实现。

    render_fn(stem, card) -> 出片路径
    measure_fn(path)      -> measure_all 的单图记录
    """
    if render_fn is None:
        raise RuntimeError('没有注入渲染入口。当前可用卡：%s'
                           % ', '.join(all_cards().keys()))
    if measure_fn is None:
        raise RuntimeError('没有注入测量函数（用 measure_all 那套）')
    return {stem: measure_fn(render_fn(stem, choice.card)) for stem in stems}


def handoff(choice: Choice, recs_engine: Dict[str, dict]) -> str:
    """给 LR 的交接：只说"这张底片是什么"，不给靶。"""
    return ('底片 %s ｜ n=%d ｜ %s'
            % (choice.card.label(), len(recs_engine), M.summarize(recs_engine)))
