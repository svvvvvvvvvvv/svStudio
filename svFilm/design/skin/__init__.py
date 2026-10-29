# -*- coding: utf-8 -*-
"""肤色测量（生产口径 v7）。

    from design.skin import sample_skin, face_skin, ita, ita_class

三个层次，**只做一件事**：
  `face_skin`  —— 语义掩膜：把"脸皮肤"从手/头发/衣服/背景里分出来（selfie_multiclass 第 3 类）
  `sample_skin` —— 颊 ROI 内去镜面/阴影/离群后的**分量中位** → L*/C*/h*/ITA°
  `ita`        —— 肤色明度指数（文献标准），给"这套多白"一个单值

口径与出处见 `CALIBER.md`；旧的四条证明单点法留在 `_corpus/kurai/full/skin_point.py`
的 `sample_skin_v6`（只做 A/B 与回退，不再是生产口径）。
"""
from .face_mask import MODEL, body_skin, face_skin, hair, person
from .sampler import (DARK_PCT, DE_MED, MIN_C, MIN_PX, OFF_X, R_ROI, SPEC_PCT,
                      ita, ita_class, sample_skin)

__all__ = ['sample_skin', 'face_skin', 'body_skin', 'hair', 'person',
           'ita', 'ita_class', 'MODEL',
           'R_ROI', 'OFF_X', 'SPEC_PCT', 'DARK_PCT', 'MIN_C', 'DE_MED', 'MIN_PX']
