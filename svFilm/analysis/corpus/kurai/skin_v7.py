# -*- coding: utf-8 -*-
"""薄转发层：把语料脚本的 `import skin_v7` 接到仓库里的 `design.skin.sampler`。

生产实现已收进 git：`svFilm/design/skin/`（见 CALIBER.md）。
这个文件只是为了让 `E:\\ds\\photo\\_corpus` 下的老脚本不用改 import。
"""
from __future__ import annotations

import sys

_SV = r'E:\svStudio\svFilm'
if _SV not in sys.path:
    sys.path.insert(0, _SV)

from design.skin.sampler import (DARK_PCT, DE_MED, FACE_W_MIN, MIN_C,  # noqa: E402,F401
                                 MIN_PX, OFF_X, R_ROI, SPEC_PCT,
                                 ita, ita_class, sample_skin)

__all__ = ['sample_skin', 'ita', 'ita_class', 'R_ROI', 'OFF_X',
           'SPEC_PCT', 'DARK_PCT', 'MIN_C', 'DE_MED', 'MIN_PX']
