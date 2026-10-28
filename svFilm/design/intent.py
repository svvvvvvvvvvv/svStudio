# -*- coding: utf-8 -*-
"""意图：人给的"往哪边推一点"，永远不许把系统推出不变量区。

设计出处：分工设计 §15 / §29。

意图是**方向 + 幅度**（−1..+1），不是数值靶：
    亮/暗 · 暖/冷 · 浓/淡 · 软/硬
什么时候可以用：只能在**不变量都满足之后**；冲突时以不变量为准。

用法：
    from design.intent import Intent
    it = Intent(brighter=0.3, warmer=-0.2)
    it.describe()
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional


@dataclass
class Intent:
    brighter: float = 0.0     # + 更亮 / − 更暗
    warmer: float = 0.0       # + 更暖 / − 更冷
    denser: float = 0.0       # + 更浓（彩度）/ − 更淡
    softer: float = 0.0       # + 更柔 / − 更硬（只能走卷纸或柔光档）

    def clamp(self):
        for k, v in asdict(self).items():
            setattr(self, k, max(-1.0, min(1.0, float(v))))
        return self

    def is_empty(self) -> bool:
        return all(abs(v) < 1e-6 for v in asdict(self).values())

    def describe(self) -> str:
        if self.is_empty():
            return '意图：无（只守不变量）'
        parts = []
        for k, label in (('brighter', '亮度'), ('warmer', '暖冷'),
                         ('denser', '浓淡'), ('softer', '软硬')):
            v = getattr(self, k)
            if abs(v) > 1e-6:
                parts.append('%s%+.2f' % (label, v))
        return '意图：' + ' · '.join(parts)

    # ---- 换算成各旋钮的方向（幅度留给实测斜率；这里只给"往哪推"） ----
    def exposure_sign(self) -> float:
        return self.brighter

    def highlights_sign(self) -> float:
        # 想更亮且不愿爆白 ⇒ 先收高光（方向与 brighter 相反）
        return -max(0.0, self.brighter)

    def saturation_sign(self) -> float:
        return self.denser

    def point_color_scale_sign(self) -> float:
        # 浓淡的"局部版"：只在肤色上推，幅度由求解给定
        return self.denser

    def temperature_sign(self) -> float:
        return self.warmer

    def diffusion_sign(self) -> float:
        return self.softer


def merge(a: Intent, b: Intent) -> Intent:
    return Intent(**{k: getattr(a, k) + getattr(b, k) for k in asdict(a)}).clamp()
