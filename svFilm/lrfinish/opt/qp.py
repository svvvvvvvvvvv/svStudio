# -*- coding: utf-8 -*-
r"""有界最小二乘 / 小 QP 的公共入口。

问题统一写成：
    min_x  || A x - b ||²  +  ridge·||x||²
    s.t.   lb ≤ x ≤ ub

硬约束（如 dL=0、R≈G、平滑）用**加权行**塞进 A/b：
    A = [A_main; w1·A_hard; ...],  b = [b_main; w1·b_hard; ...]
权重越大越硬；真硬约束就给 1e3 量级。

不引入新依赖：scipy 已在环境里；没有 scipy 时退回投影梯度（简单、慢）。
"""
from __future__ import annotations

import numpy as np


def solve_box(A, b, lb, ub, ridge=0.0, x0=None):
    """返回 x（有界最小二乘）。A: (m,n), b: (m,), lb/ub: (n,)。"""
    A = np.asarray(A, dtype=float)
    b = np.asarray(b, dtype=float)
    n = A.shape[1]
    H = A.T @ A + float(ridge) * np.eye(n)
    g = -A.T @ b
    x0 = np.zeros(n) if x0 is None else np.asarray(x0, dtype=float)
    try:
        from scipy.optimize import minimize
        fun = lambda x: 0.5 * x @ H @ x + g @ x
        jac = lambda x: H @ x + g
        res = minimize(fun, x0, jac=jac, method='L-BFGS-B',
                       bounds=list(zip(lb, ub)))
        return res.x if res.success else np.clip(res.x, lb, ub)
    except Exception:                       # noqa: BLE001
        # 无 scipy：投影梯度（只在兜底用）
        x = np.clip(x0, lb, ub)
        L = float(np.linalg.eigvalsh(H).max()) or 1.0
        for _ in range(500):
            x = np.clip(x - (H @ x + g) / L, lb, ub)
        return x


def add_rows(A, b, rows, vals, weight=1.0):
    """把若干硬约束行拼到 A/b 上（返回新的 A,b）。"""
    rows = np.atleast_2d(np.asarray(rows, dtype=float))
    vals = np.atleast_1d(np.asarray(vals, dtype=float))
    return (np.vstack([A, float(weight) * rows]),
            np.concatenate([b, float(weight) * vals]))
