# -*- coding: utf-8 -*-
"""把 lrfinish 根加进 sys.path（opt/ 下的脚本直接运行时也能 import paths）。"""
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
