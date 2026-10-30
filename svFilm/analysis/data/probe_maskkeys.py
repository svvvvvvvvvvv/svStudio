# -*- coding: utf-8 -*-
"""查 LR 的蒙版组里到底存了哪些局部键（决定 LocalShadows/Highlights 的正确键名）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, r'E:\svStudio\svFilm')
sys.path.insert(0, r'E:\ds\photo\_corpus\kurai\full')
import lrstate as LS  # noqa: E402

SRC = r'E:\ds\photo\_run_1029_tex2\engine\DSCF1608_engine.tif'


def main() -> int:
    print('ADD  ->', LS.run('ADD ' + SRC, timeout=300)[:160])
    print('MASK ->', LS.run('MASK %s subject' % SRC, timeout=300)[:160])
    for k in ('LocalExposure2012', 'LocalShadows2012', 'LocalShadows', 'Shadows2012',
              'LocalHighlights2012', 'LocalHighlights', 'Highlights2012'):
        r = LS.run('MASKPARAM %s %s 0.2' % (SRC, k), timeout=180)
        print('%-22s -> %s' % (k, r[:150]))
    print('\n--- MASKSX ---')
    print(LS.run('MASKSX ' + SRC, timeout=300)[:1600])
    return 0


if __name__ == '__main__':
    sys.exit(main())
