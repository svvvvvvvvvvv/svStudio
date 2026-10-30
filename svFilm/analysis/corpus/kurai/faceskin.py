# -*- coding: utf-8 -*-
"""薄转发层：把语料脚本的 `import faceskin` 接到仓库里的 `design.skin.face_mask`。

生产实现已收进 git：`svFilm/design/skin/`（见 CALIBER.md）。
这个文件只是为了让 `E:\\ds\\photo\\_corpus` 下的老脚本不用改 import。
"""
from __future__ import annotations

import sys

_SV = r'E:\svStudio\svFilm'
if _SV not in sys.path:
    sys.path.insert(0, _SV)

from design.skin.face_mask import (MODEL, body_skin, face_skin, hair,  # noqa: E402,F401
                                   person)

__all__ = ['face_skin', 'body_skin', 'hair', 'person', 'MODEL']
