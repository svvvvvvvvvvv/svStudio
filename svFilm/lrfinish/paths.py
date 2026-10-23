# -*- coding: utf-8 -*-
r"""所有路径的唯一入口。

本目录 = <repo>/svFilm/lrfinish/。代码里不许再出现 `E:\lr_agent` 或
`E:\Debug_svStudio\...` 这类绝对路径；需要换机器时只改这里的环境变量。

环境变量（都可选）：
  LR_ROOT                 工作根（layer1/2/5 的输入输出），默认 <debug>/lr_agent_out/2026-10-23
  LRFINISH_DEBUG          调试根，默认 E:\Debug_svStudio
  LRFINISH_BATCH_ROOT     底片根，默认 <debug>/svFilm_out
  LRFINISH_CMD            LR 桥队列目录，默认本目录/lr_cmd
  LRFINISH_MASTER         大师成片根，默认 E:\ds\大师成片
  LRFINISH_LR_EXE         Lightroom.exe
  LRFINISH_LR_CATALOG     .lrcat
  LRFINISH_LR_CONSOLE     lrc_console.log
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # <repo>/svFilm/lrfinish
OPT = ROOT / 'opt'
PROTO = ROOT / 'prototype'
ENGINE = ROOT.parent                           # <repo>/svFilm（含 svFilm 包）
REPO = ENGINE.parent                           # <repo>
PRESETS = ENGINE / 'svFilm' / 'data' / 'presets'   # 胶片预设目录（finish.json 就在这里）


def _p(name: str, default) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else Path(default)


DEBUG = _p('LRFINISH_DEBUG', r'E:\Debug_svStudio')
WORK = _p('LR_ROOT', DEBUG / 'lr_agent_out' / '2026-10-23')
BATCH_ROOT = _p('LRFINISH_BATCH_ROOT', DEBUG / 'svFilm_out')
CMD_DIR = _p('LRFINISH_CMD', ROOT / 'lr_cmd')
MASTER = _p('LRFINISH_MASTER', r'E:\ds\大师成片')
PROFILES = ROOT / 'profiles'
CALIB = ROOT / 'calibration'

APPDATA = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
LR_EXE = _p('LRFINISH_LR_EXE', r'D:\LR\Adobe Lightroom Classic\Lightroom.exe')
LR_CATALOG = _p('LRFINISH_LR_CATALOG',
                Path.home() / 'Pictures' / 'Lightroom' / 'Lightroom Catalog.lrcat')
LR_CONSOLE = _p('LRFINISH_LR_CONSOLE',
                APPDATA / 'Adobe' / 'Lightroom' / 'lrc_console.log')


def ensure_import_path() -> None:
    """把 lrfinish 根、opt、prototype、引擎父目录加进 sys.path（幂等）。"""
    import sys
    for p in (str(ROOT), str(OPT), str(PROTO), str(ENGINE)):
        if p not in sys.path:
            sys.path.insert(0, p)
