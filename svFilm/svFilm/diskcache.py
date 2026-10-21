# -*- coding: utf-8 -*-
r"""磁盘缓存 —— **跨进程、跨重启**，只存「与胶片参数无关」的东西。

## 为什么需要它

`pipeline.StageCache` 只在**内存**里、只活一个进程（给 `service.py` 那种常驻服务用）。
CLI 一遍跑完进程就退，缓存跟着没了；而真正反复迭代的场景恰恰是**同一个文件夹、同一批 RAW、
反复换胶片参数**。这种迭代里下面三样**一个都没变**（实测 @2048）：

    解码 RAW → 场景线性      3.55 s
    「人在哪」分割            2.35 s
    「场景/光位」判据         3.08 s
    ────────────────────────────────
                              8.98 s    ← 每轮白扔

只有引擎那 17 s 真的在变。所以缓存这两块，调参一轮 27.5 s → 18.5 s。

## 两层，键的口径不同

| 层 | 存什么 | 键里有什么 | 文件 |
|---|---|---|---|
| `raw/` | 解码后的场景线性（+ EXIF/机型） | 路径 · mtime · size · max_side · WB 三元组 · 目标色彩空间 | `*.npy` + `*.json` |
| `judge/` | 找人掩膜 / 场景分类结果 | **解码结果本身的指纹**（不看胶片参数） | `*.npz` |

★ `judge` 层刻意用「解码指纹」而不是路径：同一张 RAW 换个目录/改个名，缓存照样命中。
★ 两层都**不许改变结果** —— 存的就是同一份数组，读回来逐位相同（`selftest` 验）。

## 三条例外，不许缓存

① `max_side=None`（原图尺寸）：一张 300 MB 级的数组，存了反而更慢。
② `config.CACHE_DIR` 为空 / 磁盘写不进去：静默降级成不缓存，**不许因此报错**。
③ 缓存文件读坏：当 miss 处理并删掉，**不许把坏数据喂给引擎**。
"""
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
# raw 层：解码
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
# judge 层：判据（找人 / 场景）
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
# 维护
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
