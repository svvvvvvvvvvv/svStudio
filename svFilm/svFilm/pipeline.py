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

    # ---- ★★★ **光位程度量**（`deg_back`）—— 10-16 起它**真的进生产**（= 印相曝光的"力度"）----
    #   ⚠ 它是**光位**（几何），**不是**"脸比背景暗"：判据 = 人的**轮廓光 / 发丝光**
    #     （`scene.light_evidence`，只看人自己）。拿不到 / 弃权 ⇒ **0.0**（顺光档 = 只保下限）。
    _pe_back = 0.0
    try:
        _pe_back = float((((_sc or {}).get('raw') or {}).get('light') or {}
                          ).get('deg_back') or 0.0)
    except Exception:                                        # noqa: BLE001
        _pe_back = 0.0

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
        # ★★ 出图现在**还依赖光位**（`deg_back` 定印相曝光的力度）⇒ 必须进键，
        #    否则改了 `light_evidence` 的判据、缓存里还是老图（"拧了没反应"）。
        _pe_key = ('pe', int(getattr(scene, 'LIGHT_VERSION', 0) or 0), round(_pe_back, 3))
        # ★★ **预设文件本身的指纹**（`os.stat` 的 mtime_ns + size）：`person_pe_band` /
        #    `person_pe_target_l` 这些可选键只影响 `presets._params_for` 与两遍印相，
        #    **不经过** cfg 签名 / overrides / targets ⇒ 不带它 = 改了预设、缓存里还是老图。
        _pf_key = ('pf', presets._file_sig(name))
        _ckey = ('film', _sample_uid(s), name, tuple(np.shape(s.lin)),
                 int(getattr(scene, 'VERSION', 0)),
                 C.key_signature(cfg), _ov_key, _tg_key, _pe_key, _pf_key)
        _entry = cache.get(_ckey)

    if _entry is not None:
        disp = _entry['disp']
    else:
        # ========== 跑链 ==========  引擎出图 = 成片。
        # ★★★ **「以人为本」的印相曝光落点（10-16 上线）**：印相曝光**不再写死** ——
        #   先按预设印一遍 → 量成片里**那张脸**多亮（口径 = 测光同一块分割掩膜）→ 偏暗就按脸
        #   重算 `print_exposure`**再印一遍**（= 暗房的"试条"）。**力度由光位给**：
        #   逆光 ⇒ 提亮目标推到带的**亮端**、单张上限放大（背景反正要被顶曝，脸不能暗）；
        #   顺光 ⇒ 只保"脸别再暗下去"的下限。**只提不压**（`print_exposure` 是整张一起动的）。
        #   ⚠ `PERSON_PE_DYNAMIC=False` ⇒ **逐位退回** `render()`；没人脸 / 量不到 ⇒ **只印一遍**。
        disp = presets.render_person_pe(np.clip(s.lin, 0.0, None), name, cfg,
                                        overrides=(_ov or None), back=_pe_back)
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
        # ★★★ 「按脸反解印相曝光」到底动没动、动了多少（`presets._LAST_PERSON_PE` 的留痕）
        person_pe=dict(presets._LAST_PERSON_PE) if presets._LAST_PERSON_PE else None,
        light_back=round(_pe_back, 3),
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
