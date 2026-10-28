# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'opt'))
import curve_lib  # noqa: E402
import scene      # noqa: E402


class TestV9(unittest.TestCase):
    def test_curve_endpoints_monotone(self):
        ys = curve_lib.curve(120.0, 0.90, 4.0, 4.0)
        self.assertEqual(ys[0], 0)
        self.assertEqual(ys[-1], 255)
        self.assertTrue(all(b >= a for a, b in zip(ys, ys[1:])))

    def test_face_ev(self):
        self.assertEqual(scene.face_ev(60.0, [55.0, 67.0]), 0.0)
        self.assertLess(scene.face_ev(80.0, [55.0, 67.0]), 0.0)
        self.assertGreater(scene.face_ev(40.0, [55.0, 67.0]), 0.0)

    def test_missing_face(self):
        self.assertEqual(scene.missing_anchor({'face': True}), '')
        self.assertEqual(scene.missing_anchor({}), 'missing_face')

    def test_face_blown(self):
        # 1150：脸 clip95 10.25% / top255 2.45% ⇒ 跳过；其余 4 张都是 0 ⇒ 不动
        self.assertEqual(scene.missing_anchor(
            {'face': True, 'clip95': 10.25, 'top255': 2.45}), 'face_blown')
        self.assertEqual(scene.missing_anchor(
            {'face': True, 'clip95': 0.0, 'top255': 0.0}), '')
        # 背景爆不算：clip95/top255 只看脸框
        self.assertEqual(scene.missing_anchor(
            {'face': True, 'clip95': 0.0, 'top255': 0.0, 'white': 17.0}), '')


if __name__ == '__main__':
    unittest.main()
