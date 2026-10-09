# -*- coding: utf-8 -*-
r"""编排 —— 一条链跑完（09-29 `drop-tone-and-skin` 之后）。

    解码 + 白平衡 + 护栏  →  判场景  →  胶片引擎(spektrafilm 0.3.4)
        →  **颜色层（混色 → 分色）**  →  出图

★ 为什么是这个顺序：曝光/反差归**引擎**（"曝光 → 显影 → 密度"这条因果链的最前面一环）。
  反过来说：**在胶片之后改亮度 = 对印好的照片再翻拍调增益**，物理上不存在"冲好了再曝光"，
  而且到显示域就没有高光余量了。

★ 影调层（`tone.py`）/ 肤色层（`grade.skin()`）/ 认人认脸（`face.py` · `facegain.py` · `region.py`）
  09-29 新分支 `drop-tone-and-skin` **整段删除**（不是关开关）—— 见 `config.py` 文件头。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict

import numpy as np

from . import config as C, grade, io, presets, scene


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
        g = r.get('grade') or {}
        return ('{}  [{}]  胶片 {}  |  落点 L*{:.1f}  |  {:.0f}ms'.format(
                    self.sample.name, self.sample.kind,
                    r.get('stock_label') or r.get('stock'),
                    float(g.get('L50_out', float('nan'))), r['ms']))


def _sample_uid(s):
    """样本的身份。用「路径 + 类型 + 尺寸」；**无路径时用内容摘要，绝不用 `id()`**
    （对象被回收后 `id` 会复用 ⇒ 两个不同的内存样本会撞成同一个键、互相串图）。"""
    _shape = tuple(np.shape(s.lin))
    p = getattr(s, 'path', None) or ''
    if not p:
        # ★ 10-08：老代码在这里用了 `id(s)`，与它自己上面那句注释**直接矛盾**。
        #   无路径样本（自检、以及将来任何"内存里造图"的入口）改按**内容**取身份。
        _h = hashlib.blake2b(np.ascontiguousarray(s.lin).tobytes(),
                             digest_size=8).hexdigest()
        return ('<无路径>', _shape, _h)
    return (os.path.abspath(p), getattr(s, 'kind', ''), _shape)


class StageCache:
    """按「一张图 × 一条胶片风格 × 一条曝光风格」缓存成片。

    spektrafilm 那一段是全链最贵的（700 长边 ~2.4 s）⇒ 切回来不用重跑。
    """

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
    """一次性：**解码 + 跑完整链**。常驻服务请用 `run_from`（跳过解码）。"""
    t0 = time.perf_counter()
    s = io.load(path, max_side or cfg.MAX_SIDE, src=src)
    return run_from(s, cfg=cfg, stock=stock, out=out, t0=t0, path=path)


def run_from(sample, cfg=C, stock=None, out=None, t0=None, path=None, cache=None):
    r"""★ 从**已经 load 好的** sample 起跑 —— 常驻服务的入口。

    ⚠ 一份实现、两条入口：`run()` = `io.load()` + `run_from()` —— 不许各写一套。
    """
    t0 = time.perf_counter() if t0 is None else t0
    s = sample
    path = path if path is not None else getattr(s, 'path', None)

    # ---- 胶片风格（= 卷）----
    name = str(stock if stock is not None else cfg.STOCK)
    if not presets.has(name):
        # ★ "名字不认得 ⇒ 静默走默认"是本项目最阴的一类坑（出现过三次）⇒ 当场说清楚
        raise KeyError('没有这个胶片风格: %s（可选：%s）' % (name, '、'.join(presets.names())))

    # ---- ★★ 「人在哪」（`person.py`）—— **只给光位那一轴用**（~0.3 s/张）----
    #   09-29：认人 / 认脸整套删掉了，但**光位**要判（判据是"主体 vs 它身后的背景"）
    #   ⇒ 只把"人物整体位置"这一件事留下（只要低开销的粗位置）。
    #   ⚠ 拿不到（模型 / 依赖缺）⇒ `None` ⇒ 光位**弃权**（写 `-`），**其余三根轴照常、不崩**。
    try:
        from . import person as _per
        _pz = _per.person(s.disp)
    except Exception:                                        # noqa: BLE001
        _pz = None

    # ---- ★★★ 场景判据（09-26，「按场景分参数」的入口）----
    #   · 场景 → **引擎参数覆盖**（`_scene_engine`）。当前只有一条**全局 `"*"`** →
    #     `density_curves_morph`，它**无条件生效**，实测把跨度送到共识靶 81.87。
    #   · `lin=s.lin` 只给「源头过曝」那一轴用：它**只能在解码后的线性域判**，
    #     显示域那边早被重渲染压过了。`person=_pz` 只给「光位」那一轴用。
    #   · 判不出来 ⇒ `None`，下游一个字段都不盖，**不崩**。
    #   ⚠⚠ 09-29 修一个**真 bug**：原来场景是在**缓存键之后**才算的，而缓存键里又要用它
    #      ⇒ 那时 `_sc` **还没定义** ⇒ `NameError` 被 `except` 吞掉 ⇒
    #      「引擎 overrides / 靶」**从来没进过缓存键**（"拧了没反应"第 4 类，静默）。
    #      现在把它挪到缓存键之前，键里那两项才真的生效。
    try:
        _sc = scene.classify(s.disp, cfg, lin=s.lin, person=_pz)
    except Exception:                                        # noqa: BLE001
        _sc = None

    # ---- 场景 → 引擎参数覆盖（柔光 / 颗粒 / 光晕这类「质感」参数在引擎里）----
    #   没配 `_scene_engine` ⇒ 空 dict ⇒ 逐位同旧行为。
    _TS = None
    _ov_err = None
    try:
        from . import targets as _TS
        _ov = _TS.scene_engine(_sc, stock=name, cfg=cfg)
    except Exception as _e:                            # noqa: BLE001
        # ★ 10-08：**不许静默**。这里失败 ⇒ `_scene_engine` 的全局 `"*"`（含
        #   `density_curves_morph` 与 `print_exposure ×1.15`）**整批失效**，跨度立刻掉一截，
        #   而报告里原来一句话都没有 ⇒ 会被误判成"引擎参数坏了"。现在记进报告。
        _ov, _ov_err = {}, '%s: %s' % (type(_e).__name__, str(_e)[:160])

    _ckey, _entry = None, None
    if cache is not None:
        # ★ 09-26：键里带上 **判据版本号**。场景是**这张图**的确定函数（同一张图永远同一套标签），
        #   所以不用把标签本身塞进键；但判据一改（`scene.VERSION` +1）就是另一套参数 ⇒ 必须作废。
        #   ⚠ `config.SCENE_*` 阈值改了不带版本号 ⇒ 同一进程内不会作废（config 都是进程内冻结的，无妨）。
        # ★★ 09-27：**开关本身也必须进键**。漏了 `GRADE_ENABLE` ⇒ 常驻进程里改了它仍会命中
        #   旧缓存，表现就是**"拧了没反应"**。
        # ★★★ 09-29：**overrides 和靶也必须进键**（原来那两项因为 `_sc` 未定义而永远是 `None`）。
        # ★★★ 10-08：键的构造**换代**（见 `config.CACHE_KEY_PREFIXES` 的注释）。
        #   老写法手抄一个元组、只列了 `GRADE_ENABLE` ⇒ 22 个 `GRADE_*` 里 21 个
        #   翻了对画面没反应。现在用 `config.key_signature()` 前缀表驱动。
        #   ⚠ 失败**不许**退化成 `None`（那会让所有 overrides/靶变体塌成同一个键）——
        #     用带标记的哨兵元组，并保持可哈希。
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
        g_info = dict(_entry['g_info'])
    else:
        # ========== 跑链 ==========
        # 引擎之前一个像素都不动：喂进去的就是 RAW 解码出来的场景线性（`io.load_raw`）。
        disp = presets.render(np.clip(s.lin, 0.0, None), name, cfg, overrides=(_ov or None))
        # ---- L2 分色 + L3 混色（颜色）----
        # ⚠ 这一层**不做曝光**（显示域乘增益 = 拉噪声 + 高光切白），只按亮度/色相加权染色。
        # ★ 可整体关掉（`config.GRADE_ENABLE`）。
        if bool(getattr(cfg, 'GRADE_ENABLE', True)):
            # ★★ 09-30 晚：把「人在哪」一起喂进去 —— L3 的「人物区域整体提亮」要用它
            #   （`person_dl`）。拿不到 ⇒ None ⇒ **那一块不生效**（弃权），不崩。
            disp, g_info = grade.apply(disp, cfg, stock=name, scene=_sc, person=_pz)
        else:
            g_info = dict(applied=False, note='颜色层已关')
        if _ckey is not None:
            cache.put(_ckey, disp=disp, g_info=g_info)

    rep = dict(
        camera=s.cam,
        entry_bias_ev=((s.cam or {}).get('idt_bias_ev') if s.kind == 'raw' else None),
        fuji_dr=(s.cam or {}).get('fuji_dr'),
        stock=name,
        stock_label=presets.label_of(name)[0],
        stock_desc=presets.label_of(name)[1],
        # ★ 09-29：报告形状调整 —— 影调层没了 ⇒ `grade` / `scene` 直接挂在**根上**
        #   （原来是塞在 `report['tone']` 里；`tone` 这个键随 `tone.py` 一起删了）。
        grade=g_info,
        scene=(None if _sc is None else dict(_sc)),
        stage_cache=dict(hit=bool(_entry is not None)),
        # ★ 10-08：`scene_engine` 失败**必须能看见**（它一失败，全局 `"*"` 就整批失效）
        scene_engine_error=_ov_err,
        ms=(time.perf_counter() - t0) * 1000.0,
    )
    res = Result(disp, rep, s, path)
    if out:
        res.save(out)
    return res


def run_pair(stem_or_path, prefer='raw', **kw):
    """同一张照片，RAW 与机内 JPG 各跑一遍（用于验证"换底"）。"""
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
