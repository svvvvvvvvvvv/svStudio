# -*- coding: utf-8 -*-
r"""编排 —— 一条链跑完。

    解码 + 白平衡 + 护栏  →  判场景  →  胶片引擎(spektrafilm)  →  出图

★ 曝光/反差归**引擎**（胶片之后改亮度 = 对印好的照片再翻拍调增益）；★ 引擎输出**就是成片**；影调 /
颜色 / 认人认脸那几层已整段删代码（`selftest.t_dropped_layers`）。★ 一条实现两条入口：`run()` =
`io.load()` + `run_from()`。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict

import numpy as np

from . import config as C, io, presets, scene


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
        r = self.report
        # ★ **直接从成片量**（`scene.raw.L50` 是未曝光的值）。
        from . import color
        _l50 = float(np.median(color.to_lab(np.clip(self.disp, 0.0, 1.0))[..., 0]))
        return ('{}  [{}]  胶片 {}  |  落点 L*{:.1f}  |  {:.0f}ms'.format(
                    self.sample.name, self.sample.kind,
                    r.get('stock_label') or r.get('stock'), _l50, r['ms']))


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
            n = len(self._d)
            mb = 0.0
            for v in self._d.values():
                for a in v.values():
                    if hasattr(a, 'nbytes'):
                        mb += a.nbytes / 1024.0 ** 2
            return dict(sets=n, max_sets=self.max_sets, hits=self.hits,
                        misses=self.misses, mb=round(mb, 1))


def run(path, src=None, max_side=None, cfg=C, out=None, stock=None):
    r"""一次性：**解码 + 跑完整链**；常驻服务请用 `run_from`（跳过解码）。"""
    t0 = time.perf_counter()
    s = io.load(path, max_side or cfg.MAX_SIDE, src=src)
    return run_from(s, cfg=cfg, stock=stock, out=out, t0=t0, path=path)


def run_from(sample, cfg=C, stock=None, out=None, t0=None, path=None, cache=None):
    r"""★ 从**已经 load 好的** sample 起跑（常驻服务的入口）。"""
    t0 = time.perf_counter() if t0 is None else t0
    s = sample
    path = path if path is not None else getattr(s, 'path', None)

    name = str(stock if stock is not None else cfg.STOCK)
    if not presets.has(name):
        # ★ 名字不认得就**当场报错**，不许静默走默认。
        raise KeyError('没有这个胶片风格: %s（可选：%s）' % (name, '、'.join(presets.names())))

    # ---- ★★ 「人在哪」（`person.py`）—— **只给光位那一轴用**。
    try:
        from . import person as _per
        _pz, _pface = _per.person_face(s.disp)
    except Exception:                                        # noqa: BLE001
        _pz, _pface = None, None

    # ---- ★★★ 场景判据（「按场景分参数」的入口）----
    #   场景 → **引擎参数覆盖**（`_scene_engine`）；当前只有一条**全局 `"*"`**，无条件生效。
    #   ⚠⚠ 场景**必须在算缓存键之前**算，否则异常被吞 ⇒ overrides / 靶**永远进不了缓存键**。
    try:
        _sc = scene.classify(s.disp, cfg, lin=s.lin, person=_pz, face=_pface)
    except Exception:                                        # noqa: BLE001
        _sc = None

    _TS = None
    _ov_err = None
    try:
        from . import targets as _TS
        _ov = _TS.scene_engine(_sc, stock=name, cfg=cfg)
    except Exception as _e:                            # noqa: BLE001
        # ★ **不许静默**：这里失败 ⇒ 全局 `"*"`**整批失效**，记进报告。
        _ov, _ov_err = {}, '%s: %s' % (type(_e).__name__, str(_e)[:160])

    _ckey, _entry = None, None
    if cache is not None:
        # ★ 键里带**判据版本号**；★★ **开关 / overrides / 靶 / config 签名都必须进键**（漏一项 = 拧了没反应）。
        try:
            _ov_key = tuple(sorted((k, tuple(v) if isinstance(v, list) else str(v))
                                   for k, v in (_ov or {}).items()))
        except Exception as _e:                        # noqa: BLE001
            _ov_key = ('ov-err', type(_e).__name__, repr(_ov)[:200])
        try:
            _tg_key = _TS.cache_key(name, _sc)
        except Exception as _e:                        # noqa: BLE001
            _tg_key = ('tg-err', name, type(_e).__name__, repr(_sc)[:200])
        _ckey = ('film', _sample_uid(s), name, tuple(np.shape(s.lin)),
                 int(getattr(scene, 'VERSION', 0)),
                 C.key_signature(cfg), _ov_key, _tg_key)
        _entry = cache.get(_ckey)

    if _entry is not None:
        disp = _entry['disp']
    else:
        # ========== 跑链 ==========  引擎出图 = 成片。
        disp = presets.render(np.clip(s.lin, 0.0, None), name, cfg, overrides=(_ov or None))
        if _ckey is not None:
            cache.put(_ckey, disp=disp)

    rep = dict(
        camera=s.cam,
        entry_bias_ev=((s.cam or {}).get('idt_bias_ev') if s.kind == 'raw' else None),
        fuji_dr=(s.cam or {}).get('fuji_dr'),
        stock=name,
        stock_label=presets.label_of(name)[0],
        stock_desc=presets.label_of(name)[1],
        scene=(None if _sc is None else dict(_sc)),
        stage_cache=dict(hit=bool(_entry is not None)),
        # ★ `scene_engine` 失败**必须能看见**（否则全局 `"*"` 整批失效）。
        scene_engine_error=_ov_err,
        ms=(time.perf_counter() - t0) * 1000.0,
    )
    res = Result(disp, rep, s, path)
    if out:
        res.save(out)
    return res


def run_pair(stem_or_path, prefer='raw', **kw):
    p = io.find_pair(stem_or_path, prefer='raw')
    a = run(p, src='raw', **kw)
    stem = os.path.splitext(stem_or_path)[0]
    jp = None
    for e in ('.JPG', '.jpg', '.JPEG', '.jpeg'):
        if os.path.exists(stem + e):
            jp = stem + e
            break
    b = run(jp, src='jpg', **kw) if jp else None
    return a, b
