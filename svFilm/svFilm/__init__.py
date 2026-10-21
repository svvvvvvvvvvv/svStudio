# -*- coding: utf-8 -*-
"""svFilm —— 数码仿胶片管线。"""
from . import config  # noqa: F401
from .pipeline import run, Result  # noqa: F401

__version__ = config.VERSION
__all__ = ['run', 'Result', 'config', '__version__']
