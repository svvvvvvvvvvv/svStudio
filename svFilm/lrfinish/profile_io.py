# -*- coding: utf-8 -*-
r"""读预设（profile）。

预设已经统一：**一个预设 = 一个目录**，和胶片预设放在一起：
    svFilm/svFilm/data/presets/<名字>/
        preset.json    ← 胶片/介质参数（引擎读）
        finish.json    ← LR 收尾参数（本工具读）
        bands.json     ← 五段靶带
        calibration/   ← 分级关系/标定

用法：
    import profile_io
    p = profile_io.load('kurai')          # 按 finish.id 找；也认 '鹿井预设'
    bands = profile_io.bands_file(p)
    gr = profile_io.calib(p, 'grade_rel')
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import paths


def preset_root() -> Path:
    v = os.environ.get('SVFILM_PRESET_DIR')
    return Path(v) if v else paths.PRESETS


def _with_paths(data: dict, path: Path) -> dict:
    data['_path'] = str(path)
    data['_dir'] = str(path.parent)
    data['_preset'] = path.parent.name
    data.setdefault('id', path.parent.name)
    data.setdefault('status', 'calibrated')
    return data


def _scan_by_id(pid: str):
    root = preset_root()
    if not root.is_dir():
        return None
    for sub in sorted(root.iterdir()):
        f = sub / 'finish.json'
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except Exception:                       # noqa: BLE001
            continue
        if str(data.get('id')) == str(pid) or sub.name == str(pid):
            return _with_paths(data, f)
    return None


def load(id_or_path: str = 'kurai') -> dict:
    p = Path(id_or_path)
    if p.is_file():
        return _with_paths(json.loads(p.read_text(encoding='utf-8')), p)
    if p.is_dir() and (p / 'finish.json').is_file():
        return _with_paths(json.loads((p / 'finish.json').read_text(encoding='utf-8')),
                           p / 'finish.json')

    prof = _scan_by_id(id_or_path)
    if prof:
        return prof

    # 兼容旧目录（迁移期）
    legacy = paths.PROFILES / id_or_path / 'profile.json'
    if legacy.is_file():
        return _with_paths(json.loads(legacy.read_text(encoding='utf-8')), legacy)

    names = []
    root = preset_root()
    if root.is_dir():
        for sub in sorted(root.iterdir()):
            f = sub / 'finish.json'
            if f.is_file():
                try:
                    names.append(str(json.loads(f.read_text(encoding='utf-8')).get('id')))
                except Exception:               # noqa: BLE001
                    pass
    raise SystemExit('找不到预设：%s（可用：%s）' % (id_or_path, ', '.join(n for n in names if n)))


def bands_file(prof: dict) -> Path:
    f = prof.get('channel', {}).get('bands_file') or 'bands.json'
    p = Path(f)
    return p if p.is_absolute() else Path(prof['_dir']) / p


def calib(prof: dict, name: str) -> Path:
    """profile 里的标定文件；相对路径先找预设目录，再找 lrfinish 根。"""
    f = (prof.get('grading', {}) or {}).get(name)
    if not f:
        f = 'calibration/%s.json' % name
    p = Path(f)
    if p.is_absolute():
        return p
    for base in (Path(prof['_dir']), paths.ROOT):
        q = base / p
        if q.is_file():
            return q
    return paths.ROOT / p


def tone(prof: dict) -> dict:
    d = dict(face_lo=63.7, face_hi=74.7, span_lo=15.7, span_hi=27.7,
             span_target=24.0, black_p90=2.34, white_p90=2.85, l_per_ev=16.0)
    d.update(prof.get('tone') or {})
    return d


def grading(prof: dict) -> dict:
    d = dict(shadow_hue=220, high_hue=30, factor=1.0 / 3.0, clamp=0.6,
             sat_max=5.0, skin_max=0.6)
    d.update(prof.get('grading') or {})
    return d


def look(prof: dict) -> dict:
    """v9 LookProfile：固定曲线 + HSL + 分级（全是常量）。"""
    d = dict(curve=dict(g=0.90, toe=4.0, shoulder=4.0),
             hsl={}, grading=dict(shadow_hue=220, shadow_sat=4,
                                  high_hue=30, high_sat=2, balance=-100))
    d.update(prof.get('look') or {})
    return d


def scene(prof: dict) -> dict:
    """v9 ScenePolicy：脸锚、曝光、白平衡、黑白场护栏。"""
    t = tone(prof)
    d = dict(anchor='face', face_zone=[t['face_lo'], t['face_hi']],
             wb='auto', black_white='guard')
    d.update(prof.get('scene') or {})
    return d


def skin(prof: dict) -> dict:
    """v9 肤色目标/椭球（专题未开，只做护栏占位）。"""
    d = dict(L=68.0, C=20.0, ellipsoid=dict(L=10.0, a=4.0, b=8.0))
    d.update(prof.get('skin') or {})
    return d


def guards(prof: dict) -> dict:
    t = tone(prof)
    d = dict(white_p90=t['white_p90'], black_p90=t['black_p90'],
             true_clip=0.5, monotone=True)
    d.update(prof.get('guards') or {})
    return d
