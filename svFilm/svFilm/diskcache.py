# -*- coding: utf-8 -*-
"""磁盘缓存 —— **跨进程、跨重启**，只存「与胶片参数无关」的东西。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time

import numpy as np

from . import config as C

# ★ 改了解码/判据的**算法**就 +1：老缓存自动失效（不靠路径/mtime 猜）
RAW_VER = 1
JUDGE_VER = 1


def root() -> str:
    r"""缓存根目录（默认 `<仓库>/.cache`）。空字符串 = 关掉缓存。"""
    d = getattr(C, 'CACHE_DIR', None)
    if d is None:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.cache')
    return str(d)


def enabled() -> bool:
    return bool(getattr(C, 'CACHE_ENABLE', True)) and bool(root())


def key(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode('utf-8', 'replace'))
        h.update(b'\x1f')
    return h.hexdigest()[:24]


def _path(layer, k, ext):
    return os.path.join(root(), layer, k + ext)


def _mkdir(layer):
    d = os.path.join(root(), layer)
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------
def raw_key(path, max_side, wb, colorspace):
    r"""解码缓存的键。`mtime` + `size` 保证 RAW 被换掉/改过就失效。"""
    p = os.path.abspath(str(path))
    try:
        st = os.stat(p)
        sig = (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        sig = (0, 0)
    _wb = wb or {}
    return key('raw', RAW_VER, p, sig, max_side,
               str(_wb.get('white_balance')), float(_wb.get('temperature', -1)),
               float(_wb.get('tint', -1)), str(colorspace))


def raw_get(k):
    r"""返回 `(lin_float32, exif_bytes, make, model)` 或 `None`。"""
    if not enabled():
        return None
    f = _path('raw', k, '.npy')
    m = _path('raw', k, '.json')
    if not (os.path.exists(f) and os.path.exists(m)):
        return None
    try:
        lin = np.load(f, mmap_mode=None)
        with open(m, 'r', encoding='utf-8') as fh:
            meta = json.load(fh)
        exif = meta.get('exif')
        exif = bytes.fromhex(exif) if exif else None
        return lin, exif, meta.get('make'), meta.get('model')
    except Exception:                                   # noqa: BLE001
        # ★ 坏缓存不许喂给引擎：删掉、当 miss
        for x in (f, m):
            try:
                os.remove(x)
            except OSError:
                pass
        return None


def raw_put(k, lin, exif, make, model):
    if not enabled():
        return
    try:
        d = _mkdir('raw')
        f = os.path.join(d, k + '.npy')
        m = os.path.join(d, k + '.json')
        # 先写临时名再换名 —— 并发进程写同一张图时不会读到半个文件
        np.save(f + '.tmp.npy', np.ascontiguousarray(lin, dtype=np.float32))
        os.replace(f + '.tmp.npy', f)
        with open(m + '.tmp', 'w', encoding='utf-8') as fh:
            json.dump(dict(exif=(exif.hex() if exif else None), make=make, model=model), fh)
        os.replace(m + '.tmp', m)
    except Exception:                                   # noqa: BLE001
        pass


# --------------------------------------------------------------------------
def judge_key(fingerprint):
    return key('judge', JUDGE_VER, fingerprint)


def judge_get(k):
    if not enabled():
        return None
    f = _path('judge', k, '.npz')
    if not os.path.exists(f):
        return None
    try:
        with np.load(f, allow_pickle=False) as z:
            return {n: z[n] for n in z.files}
    except Exception:                                   # noqa: BLE001
        try:
            os.remove(f)
        except OSError:
            pass
        return None


def judge_put(k, **arrays):
    if not enabled():
        return
    try:
        d = _mkdir('judge')
        f = os.path.join(d, k + '.npz')
        np.savez(f + '.tmp.npz', **{n: np.asarray(v) for n, v in arrays.items()})
        os.replace(f + '.tmp.npz', f)
    except Exception:                                   # noqa: BLE001
        pass


# --------------------------------------------------------------------------
def stats():
    out = {}
    for layer in ('raw', 'judge'):
        d = os.path.join(root(), layer)
        n, mb = 0, 0.0
        if os.path.isdir(d):
            for fn in os.listdir(d):
                try:
                    n += 1
                    mb += os.path.getsize(os.path.join(d, fn)) / 1024.0 ** 2
                except OSError:
                    pass
        out[layer] = dict(files=n, mb=round(mb, 1))
    out['root'] = root()
    return out


def prune(max_mb=None):
    r"""按**最后访问时间**删到 `max_mb` 以内（默认 `config.CACHE_MAX_MB`）。"""
    cap = float(max_mb if max_mb is not None else getattr(C, 'CACHE_MAX_MB', 8000))
    rows = []
    for layer in ('raw', 'judge'):
        d = os.path.join(root(), layer)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rows.append((st.st_atime, st.st_size, p))
    total = sum(r[1] for r in rows) / 1024.0 ** 2
    if total <= cap:
        return dict(removed=0, mb=round(total, 1))
    rows.sort()
    removed, freed = 0, 0.0
    for _at, size, p in rows:
        if total <= cap:
            break
        try:
            os.remove(p)
            removed += 1
            total -= size / 1024.0 ** 2
            freed += size / 1024.0 ** 2
        except OSError:
            pass
    return dict(removed=removed, freed_mb=round(freed, 1), mb=round(total, 1))


def clear():
    r"""整目录清掉（测试/排查用；不影响仓库里任何源码）。"""
    d = root()
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return d
