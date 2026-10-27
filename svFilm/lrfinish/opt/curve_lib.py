# -*- coding: utf-8 -*-
"""固定曲线族：端点钉死、支点不动，可带趾钩/肩滚（v9 LookProfile 用）。"""
from __future__ import annotations

XS = list(range(0, 256, 16)) + [255]


def curve(P, g=0.90, toe=0.0, shoulder=0.0):
    out = []
    for x in XS:
        if x >= P:
            u = (x - P) / (255.0 - P) if 255.0 - P > 1e-6 else 0.0
            y = P + (255.0 - P) * (u ** g) - shoulder * 4.0 * u * (1.0 - u)
        else:
            t = x / P if P > 1e-6 else 0.0
            y = P * (t ** (1.0 / g)) + toe * 4.0 * t * (1.0 - t)
        out.append(int(round(min(255.0, max(0.0, y)))))
    # 单调护栏：鼓包太大就减半
    for _ in range(4):
        if all(b >= a for a, b in zip(out, out[1:])):
            break
        toe *= 0.5
        shoulder *= 0.5
        out = curve(P, g, toe, shoulder)
    return out


def filmic(P, low_g=1.0, high_g=1.0, toe=0.0, shoulder=0.0):
    """压缩两端族：low_g>1 压低端、high_g>1 压高端；中间在 P 处不动。

    与 `curve`（g<1：低端展开、高端抬高）相反——对应大师残差的"两端压、中间抬"。
    """
    out = []
    for x in XS:
        if x >= P:
            u = (x - P) / (255.0 - P) if 255.0 - P > 1e-6 else 0.0
            y = P + (255.0 - P) * (u ** high_g) - shoulder * 4.0 * u * (1.0 - u)
        else:
            t = x / P if P > 1e-6 else 0.0
            y = P * (t ** low_g) + toe * 4.0 * t * (1.0 - t)
        out.append(int(round(min(255.0, max(0.0, y)))))
    for _ in range(4):
        if all(b >= a for a, b in zip(out, out[1:])):
            break
        toe *= 0.5
        shoulder *= 0.5
        out = filmic(P, low_g, high_g, toe, shoulder)
    return out


def L_to_code(L):
    Y = ((L + 16.0) / 116.0) ** 3 if L > 8.0 else L / 903.3
    v = 12.92 * Y if Y <= 0.0031308 else 1.055 * Y ** (1 / 2.4) - 0.055
    return float(max(0.0, min(1.0, v)) * 255.0)
