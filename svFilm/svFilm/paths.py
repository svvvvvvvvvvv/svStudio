# -*- coding: utf-8 -*-
"""产出路径规范。**所有效果图/调试产物一律走这里，不许在别处拼路径。**"""
import os
import time

TOP = '效果debug'


def today():
    return time.strftime('%Y-%m-%d')


def slug(s):
    r"""把目的说明收拾成安全的目录名（去首尾空格、路径分隔符换下划线）。"""
    s = (s or '').strip()
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, '_')
    return s or '未命名'


def debug_dir(purpose, root='.', date=None):
    """建好并返回 <root>/效果debug/<日期>/<目的>/。"""
    p = os.path.join(root, TOP, date or today(), slug(purpose))
    os.makedirs(p, exist_ok=True)
    return p


def debug_path(purpose, filename, root='.', date=None):
    return os.path.join(debug_dir(purpose, root, date), filename)
