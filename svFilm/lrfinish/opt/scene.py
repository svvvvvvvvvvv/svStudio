# -*- coding: utf-8 -*-
"""v9 ScenePolicy：脸锚曝光 + 两端护栏（不逐张解通道）。"""
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


def within(m, white_p90, black_p90, tol=0.05):
    return (float(m.get('white', 0)) <= white_p90 + tol
            and float(m.get('black', 0)) <= black_p90 + tol)


def missing_anchor(m):
    if not m or not m.get('face'):
        return 'missing_face'
    return ''
