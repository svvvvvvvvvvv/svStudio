# -*- coding: utf-8 -*-
r"""svFilm —— 数码仿胶片管线。

★ 与 `config.py` 的「一句话现状」、`pipeline.py` 的文件头**必须同一口径**。
**一条链**：`解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(spektrafilm) → 出图` ⇒ **引擎出图 = 成片**，
**链上只有引擎在动像素**（`selftest.t_contract` 钉着）；影调 / 肤色 / 认人认脸 / **颜色层**已整段删代码
（钉在 `selftest.t_dropped_layers`）⇒ 只剩「入口 + 引擎 + `_post_scan`（扫描段）」。
"""
from . import config  # noqa: F401
from .pipeline import run, Result  # noqa: F401

__version__ = config.VERSION
__all__ = ['run', 'Result', 'config', '__version__']
