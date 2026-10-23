# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'opt'))
import qp  # noqa: E402


class TestQP(unittest.TestCase):
    def test_identity(self):
        A = np.eye(3)
        b = np.array([1.0, 2.0, 3.0])
        x = qp.solve_box(A, b, -10 * np.ones(3), 10 * np.ones(3))
        np.testing.assert_allclose(x, b, atol=1e-6)

    def test_bounds(self):
        A = np.eye(2)
        b = np.array([5.0, -5.0])
        x = qp.solve_box(A, b, np.array([-2.0, -2.0]), np.array([2.0, 2.0]))
        np.testing.assert_allclose(x, [2.0, -2.0], atol=1e-6)

    def test_ridge_shrinks(self):
        A = np.eye(1)
        b = np.array([10.0])
        x = qp.solve_box(A, b, np.array([-100.0]), np.array([100.0]), ridge=1.0)
        self.assertLess(float(x[0]), 10.0)


if __name__ == '__main__':
    unittest.main()
