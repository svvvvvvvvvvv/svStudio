# -*- coding: utf-8 -*-
"""编排 —— 一条链跑完。"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict

import numpy as np

from . import config as C, io, presets


class Result:
    __slots__ = ('disp', 'report', 'sample', 'path')

    def __init__(self, disp, report, sample, path):
        self.disp = disp
        self.report = report
        self.sample = sample
        self.path = path

    def save(self, out_path, exif=True):
        return io.save(self.disp, out_path,
                       exif=(self.sample.exif if exif else None),
                       quality=C.JPEG_QUALITY)

    def summary(self):
        from . import color
        _l50 = float(np.median(color.to_lab(np.clip(self.disp, 0.0, 1.0))[..., 0]))
        return ('{}  [{}]  胶片 {}  |  落点 L*{:.1f}  |  {:.0f}ms'.format(
            self.sample.name, self.sample.kind,
            self.report.get('stock_label') or self.report.get('stock'),
            _l50, self.report['ms']))


def _sample_uid(s):
    r"""样本身份 = 「路径 + 类型 + 尺寸」；**无路径时用内容摘要，绝不用 `id()`**。"""
    _shape = tuple(np.shape(s.lin))
    p = getattr(s, 'path', None) or ''
    if not p:
        _h = hashlib.blake2b(np.ascontiguousarray(s.lin).tobytes(),
                             digest_size=8).hexdigest()
        return ('<无路径>', _shape, _h)
    return (os.path.abspath(p), getattr(s, 'kind', ''), _shape)


class StageCache:
    r"""按「一张图 × 一条胶片风格」缓存成片（spektrafilm 那段最贵）。"""

    def __init__(self, max_sets=None):
        self.max_sets = int(max_sets if max_sets is not None
                            else getattr(C, 'CACHE_MAX_SETS', 4))
        self._d = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return hit

    def put(self, key, **kw):
        with self._lock:
            self._d[key] = kw
            self._d.move_to_end(key)
            while len(self._d) > self.max_sets:
                self._d.popitem(last=False)
        return kw

    def clear(self):
        with self._lock:
            self._d.clear()

    def stats(self):
        with self._lock:
            mb = 0.0
            for v in self._d.values():
                for a in v.values():
                    if hasattr(a, 'nbytes'):
                        mb += a.nbytes / 1024.0 ** 2
            return dict(sets=len(self._d), max_sets=self.max_sets, hits=self.hits,
                        misses=self.misses, mb=round(mb, 1))


def run(path, src=None, max_side=None, cfg=C, out=None, stock=None):
    r"""一次性：**解码 + 跑完整链**。"""
    t0 = time.perf_counter()
    name = str(stock if stock is not None else cfg.STOCK)
    wb = presets.wb_of(name) if presets.has(name) else None
    s = io.load(path, max_side or cfg.MAX_SIDE, src=src, wb=wb)
    return run_from(s, cfg=cfg, stock=stock, out=out, t0=t0, path=path)


def run_from(sample, cfg=C, stock=None, out=None, t0=None, path=None, cache=None):
    r"""★ 从**已经 load 好的** sample 起跑（常驻服务的入口）。"""
    t0 = time.perf_counter() if t0 is None else t0
    s = sample
    path = path if path is not None else getattr(s, 'path', None)

    name = str(stock if stock is not None else cfg.STOCK)
    if not presets.has(name):
        raise KeyError('没有这个胶片风格: %s（可选：%s）'
                       % (name, '、'.join(presets.names())))

    _ckey = None
    if cache is not None:
        _ckey = ('film', _sample_uid(s), name, tuple(np.shape(s.lin)),
                 C.key_signature(cfg), presets._file_sig(name))
        _entry = cache.get(_ckey)
    else:
        _entry = None

    if _entry is not None:
        disp = _entry['disp']
    else:
        disp = presets.render(np.clip(s.lin, 0.0, None), name, cfg)
        if _ckey is not None:
            cache.put(_ckey, disp=disp)

    rep = dict(
        camera=s.cam,
        entry_bias_ev=((s.cam or {}).get('idt_bias_ev') if s.kind == 'raw' else None),
        fuji_dr=(s.cam or {}).get('fuji_dr'),
        stock=name,
        stock_label=presets.label_of(name)[0],
        stock_desc=presets.label_of(name)[1],
        print_exposure=presets.pe_of(name),
        stage_cache=dict(hit=bool(_entry is not None)),
        ms=(time.perf_counter() - t0) * 1000.0,
    )
    res = Result(disp, rep, s, path)
    if out:
        res.save(out)
    return res
