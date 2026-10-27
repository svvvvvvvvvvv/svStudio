# -*- coding: utf-8 -*-
"""placement policy：脸锚曝光 + 两端护栏（不逐张解通道）。"""
from __future__ import annotations


def face_ev(face_p50, face_zone, l_per_ev=16.0, lo=-2.0, hi=2.0):
    """脸落带：高于上沿压、低于下沿抬；带内 0。"""
    a, b = float(face_zone[0]), float(face_zone[1])
    if face_p50 > b:
        ev = (b - 0.5 - face_p50) / l_per_ev
    elif face_p50 < a:
        ev = (a + 2.0 - face_p50) / l_per_ev
    else:
        ev = 0.0
    return float(max(lo, min(hi, ev)))


# 脸的硬门：脸自己（皮肤）过曝才算缺陷；背景爆/暗是风格（10-24 用户拍板）
FACE_CLIP95_MAX = 0.5      # 脸颊∩肤色内 L*>95 的百分比
FACE_TOP255_MAX = 0.1      # 脸颊∩肤色内任通道 >=254 的百分比
FACE_ACCESSORY_TOP255 = 1.5  # 整脸框的顶死上限：只用来兜白花/头纱这类配件（1150）


def face_blown(m, clip95_max=FACE_CLIP95_MAX, top255_max=FACE_TOP255_MAX,
               accessory_max=FACE_ACCESSORY_TOP255):
    """脸自己（皮肤）有没有过曝；背景不算。

    优先用 cheek∩skin 的 clip95/top255（不含白花/头纱/泡泡）；
    没有这两个键时回退脸框；整脸框顶死 >1.5% 只兜配件（1150），不误伤 1231。
    """
    clip = float(m.get('clip95_skin', m.get('clip95', 0.0)))
    top = float(m.get('top255_skin', m.get('top255', 0.0)))
    face_top = float(m.get('top255', 0.0))
    return clip > clip95_max or top > top255_max or face_top > accessory_max


def within(m, white_p90, black_p90, tol=0.05):
    """只保护脸；背景的爆/暗是风格，不再当缺陷（两个参数保留兼容旧调用）。"""
    return not face_blown(m)


def missing_anchor(m):
    if not m or not m.get('face'):
        return 'missing_face'
    if face_blown(m):
        return 'face_blown'
    return ''
