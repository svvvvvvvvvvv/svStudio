# -*- coding: utf-8 -*-
r"""编排 —— **两步**（09-23 SV 重新划边界之后）。

    svFilm：曝光风格（落点 + 反差，线性域）
        ↓
    spektrafilm：胶片风格（负片 / 相纸 / 颗粒 / 柔光 / 光晕 / 扫描）

★ 为什么是这个顺序：曝光是"给胶片多少光"，是"曝光 → 显影 → 密度"这条因果链最前面的一环。
  反过来说：**在胶片之后改亮度 = 对印好的照片再翻拍调增益**，物理上不存在"冲好了再曝光"，
  而且到显示域 + 8bit 就没有高光余量了。

★ 为什么这里**没有**别的层了：
  迭代期堆过「风格层 / 空间层（颗粒·黑柔·光晕）/ 局部肤色 / 降噪 / 成色基准 / 脸部锚点」，
  但它们要么跟 spektrafilm 重复（我们自己手搓了一份简化版），要么属于"颜色"不属于"曝光影调"。
  09-23 全部删掉 —— 那些事现在归 spektrafilm，或者归 Lightroom。
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

import numpy as np

from . import config as C, grade, io, presets, scene, tone


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
        t = r.get('tone') or {}
        return ('{}  [{}]  胶片 {}  |  曝光 {}  |  落点 L*{:.1f}（靶 {:.1f}）'
                '  黑位 {:.1f}  亮部 {:.1f}  |  曝光 {:+.2f}EV  反差 γ{:.2f}  |  {:.0f}ms'.format(
                    self.sample.name, self.sample.kind,
                    r.get('stock_label') or r.get('stock'),
                    r.get('style'),
                    t.get('L50_out', float('nan')), t.get('mid_L', float('nan')),
                    t.get('L5_out', float('nan')), t.get('L95_out', float('nan')),
                    t.get('ev', 0.0), t.get('g', 1.0),
                    r['ms']))


def _sample_uid(s):
    """样本的身份。用「路径 + 类型 + 尺寸」—— 别用 `id()`（对象被回收后 id 会复用）。"""
    p = getattr(s, 'path', None) or ''
    if not p:
        return ('<无路径>', id(s), tuple(np.shape(s.lin)))
    return (os.path.abspath(p), getattr(s, 'kind', ''), tuple(np.shape(s.lin)))


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


def run(path, src=None, max_side=None, cfg=C, out=None, stock=None, style=None):
    """一次性：**解码 + 跑完整链**。常驻服务请用 `run_from`（跳过解码）。"""
    t0 = time.perf_counter()
    s = io.load(path, max_side or cfg.MAX_SIDE, src=src)
    return run_from(s, cfg=cfg, stock=stock, style=style, out=out,
                    t0=t0, path=path)


def run_from(sample, cfg=C, stock=None, style=None, out=None,
             t0=None, path=None, cache=None):
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
    style = tone.DEFAULT if style is None else str(style)

    # 曝光风格作用在哪一段（见 config.TONE_AFTER_ENGINE）。缓存键要带上它，
    # 否则改了开关、缓存里还是另一条路出来的那张（"拧了没反应"的经典长相）。
    _after = bool(getattr(cfg, 'TONE_AFTER_ENGINE', False))

    # ★ 脸增益的报告（`FACE_GAIN_ENABLE` 关着时恒为 `None`）。
    #   先在这里初始化：缓存命中那条路不会再算它，但 `rep` 里照样要能看到"这张到底跑没跑脸增益"。
    _fg = None
    _ckey, _entry = None, None
    if cache is not None:
        # ★ 09-26：键里带上 **判据版本号**。场景是**这张图**的确定函数（同一张图永远同一套标签），
        #   所以不用把标签本身塞进键；但判据一改（`scene.VERSION` +1）就是另一套参数 ⇒ 必须作废。
        #   ⚠ `config.SCENE_*` 阈值改了不带版本号 ⇒ 同一进程内不会作废（config 都是进程内冻结的，无妨）。
        # ★★ 09-27：**开关本身也必须进键**。原来只带了 `_after`（`TONE_AFTER_ENGINE`）
        #   ⇒ 漏了 `GRADE_SCOPE` / `GRADE_ENABLE` / `TONE_ENABLE`：常驻进程里改了它们仍会命中
        #   旧缓存，表现就是**"拧了没反应"**（和下面老路那段注释里说过的同一类坑）。
        # ★★★ 09-29：**overrides 和靶也必须进键**。
        #   原来只带了"判据版本号"（理由：场景是这张图的确定函数）—— 那个理由本身没错，
        #   但漏了：`_scene` / `_scene_engine` 真正影响的是【靶】和【引擎 overrides】，
        #   而这两样都不在键里 ⇒ 同一进程里改了靶/覆盖 ⇒ **仍命中旧缓存** ⇒
        #   表现就是"拧了没反应"（今天在这上面栽了好几次：入口 A/B 三组一样、按场景覆盖不生效）。
        _ov_key = tuple(sorted(
            (k, tuple(v) if isinstance(v, list) else v) for k, v in ({}).items())) \
            if False else None
        try:
            from . import targets as _TSk
            _ov = _TSk.scene_engine(_sc, stock=name, cfg=cfg) if _sc else {}
            _ov_key = tuple(sorted((k, tuple(v) if isinstance(v, list) else str(v))
                                   for k, v in (_ov or {}).items()))
            _tg_key = _TSk.cache_key(name, _sc)
        except Exception:                                  # noqa: BLE001
            _ov_key, _tg_key = None, None
        _ckey = ('film', _sample_uid(s), name, style, getattr(cfg, 'MAX_SIDE', None), _after,
                 int(getattr(scene, 'VERSION', 0)),
                 str(getattr(cfg, 'GRADE_SCOPE', 'all')),
                 bool(getattr(cfg, 'GRADE_ENABLE', True)),
                 bool(getattr(cfg, 'TONE_ENABLE', False)),
                 # ★★ 09-29：**脸增益开关也进键**。它改的是出图本身（每张的负片 CMY 密度），
                 #   不进键 ⇒ 常驻进程里把它一开，仍会命中"没开脸增益"的旧缓存
                 #   （与上面 `GRADE_ENABLE` / `TONE_ENABLE` 同一类坑 —— "拧了没反应"第 4 类）。
                 bool(getattr(cfg, 'FACE_GAIN_ENABLE', False)),
                 _ov_key, _tg_key)
        _entry = cache.get(_ckey)

    if _entry is not None:
        disp = _entry['disp']
        t_info = dict(_entry['t_info'])
        gk = float(_entry['gk'])
        anc = dict(_entry['anc'])
    elif _after:
        # ========== 曝光风格作用在胶片引擎**之后**的成片上 ==========
        # 引擎之前一个像素都不动：喂进去的就是 RAW 解码出来的场景线性（`io.load_raw`，
        # 默认走 public 那条加载）。理由见 `config.TONE_AFTER_ENGINE` 那段注释 ——
        # 在引擎之前调亮度，控制不了成片亮度（引擎的印相配平会把它抹平）。
        # ⚠ 既然动作在之后，脸锚点 / 高光护栏这两道"引擎之前"的工序就不参与：
        #   一个是给"自己标的真卷"校落点用的，另一个是给入口曲线兜高光用的。
        #   新路（public 的加载）不做入口提亮 ⇒ 两道都无事可做。
        #
        # ★★★ 09-26 修一个漏：**人脸掩膜在「解码后」那张图上算一次**（同 `else` 分支的理由）。
        #   链尾（胶片出图后）画面已经发白 ⇒ 分割模型认不出脸 ⇒ 掩膜空 ⇒ 后续静默失效。
        #   老路 (`else`) 早就把这件事挪到解码后了，新路当时漏掉 ⇒ `grade` 是在**成片**上现算的。
        #   实测同一批 12 张：解码后检出 12/12、引擎出图后只有 11/12（`DSCF1141` 就是丢在那一步）。
        #   算一次、传下去，`grade` 里 region 与 L4 共用 ⇒ 顺带把重复的那次分割也省掉。
        #   ⚠ 拿不到（模型缺失）⇒ `None`，下游自己降级，**不崩**。
        _pz = None
        try:
            from . import face as _face
            _pz = _face.parse(np.clip(s.disp, 0.0, 1.0))
        except Exception:                                        # noqa: BLE001
            _pz = None

        # ★★★ 场景判据（09-26，「按场景分参数」的**入口**）
        #   · 跟人脸掩膜**用同一张图、同一次解析**（`parsed=_pz`）—— 不重复算、也不会两张图。
        #   · `lin=s.lin` 只给 `blown`（源头过曝）那一轴用：它**只能在解码后的线性域判**，
        #     显示域那边早被重渲染压过了。
        #   · 判不出来 ⇒ `None`，下游 `targets.for_stock(…, None)` 一个字段都不盖，**不崩**。
        #
        # ⚠⚠⚠ 09-27：**试过把它拆成两段（引擎前 `blown` + 引擎后 `classify_after`），又退回来了。**
        #   为什么退：`scene` 的**阈值是按「解码域」标的**（`SCENE_EXP_DARK=18.5` 这种整张中位），
        #   一旦改到**成片域**去量，`exp` / `span` **会全部错档**（实测出图 77% 像素变了）。
        #   ⇒ **要切两段，必须先把那两个阈值按"成片域"重新标定**，那是**另一件事、要单独做**。
        #   新函数已经备好（`scene.blown` / `scene.classify_after`），**标定完再切**。
        try:
            _sc = scene.classify(s.disp, _pz, cfg, lin=s.lin)
        except Exception:                                        # noqa: BLE001
            _sc = None

        # ★★ 09-28：**场景 → 引擎参数覆盖**（柔光 / 颗粒 / 光晕这类「质感」参数在引擎里，
        #   `_scene` 只够到后期层的靶 ⇒ 走这里喂给引擎）。没配 `_scene_engine` ⇒ 空 dict ⇒ 逐位同旧行为。
        try:
            from . import targets as _TS
            _ov = _TS.scene_engine(_sc, stock=name, cfg=cfg)
        except Exception:                                  # noqa: BLE001
            _ov = {}
        # ★★ 09-29：**脸增益**（可选）—— 在负片 CMY 密度上「只给脸加密度」，
        #   闭环迭代到脸的 Lab 靶（ΔE00 达标）。为什么不能在测光上做：测光定的是**整张落点**
        #   ⇒ 提脸必然推亮整张（实测 partial/median 把脸拉到 75~79 而整张也到 73~78）。
        #   靶自动从 `targets` 读（`skin_L_abs/C_abs/hue`）⇒ **没这几项的预设自动不启用**。
        _fg = None
        if bool(getattr(cfg, 'FACE_GAIN_ENABLE', False)) and _pz is not None:
            try:
                from . import targets as _TF
                _ft = _TF.face_lab_target(name, _sc)
                if _ft:
                    disp, _fg = presets.render_with_face(
                        s.lin, name, cfg, pz=_pz,
                        target_L=_ft[0], target_a=_ft[1], target_b=_ft[2],
                        overrides=(_ov or None))
            except Exception as _e:                            # noqa: BLE001
                _fg = dict(applied=False, note='脸增益失败：%s' % str(_e)[:120])
        if _fg is None:
            disp = presets.render(np.clip(s.lin, 0.0, None), name, cfg,
                                  overrides=(_ov or None))
        # ---- L1 影调（明度分布）----
        # ★ 当前阶段可整体关掉（`config.TONE_ENABLE`）：只做胶片引擎时不要这一层。
        if bool(getattr(cfg, 'TONE_ENABLE', True)):
            disp, t_info = tone.settle_finished(disp, style, cfg, stock=name, scene=_sc)
        else:
            t_info = dict(applied=False, note='影调层已关（当前阶段只做胶片引擎）')
        # ---- L2 分色 + L3 混色 + L4 肤色（颜色）----
        # ⚠ 这一层**不做曝光**（显示域乘增益 = 拉噪声 + 高光切白），只按亮度/色相加权染色。
        # ★ 同样可整体关掉（`config.GRADE_ENABLE`）—— 关掉后脸也不碰。
        if bool(getattr(cfg, 'GRADE_ENABLE', True)):
            disp, g_info = grade.apply(disp, cfg, stock=name, parsed=_pz, scene=_sc)
        else:
            g_info = dict(applied=False, note='颜色层已关（当前阶段只做胶片引擎）')
        t_info['grade'] = g_info
        t_info['scene'] = (None if _sc is None else dict(_sc))
        gk = 1.0
        anc = dict(applied=False, note='曝光风格在引擎之后 ⇒ 不做脸锚点')
        if _ckey is not None:
            cache.put(_ckey, disp=disp, t_info=t_info, gk=gk, anc=anc)

    rep = dict(
        camera=s.cam,
        entry_bias_ev=((s.cam or {}).get('idt_bias_ev') if s.kind == 'raw' else None),
        fuji_dr=(s.cam or {}).get('fuji_dr'),
        stock=name,
        stock_label=presets.label_of(name)[0],
        stock_desc=presets.label_of(name)[1],
        style=style,
        style_target=(dict(tone.rel_of(style)) if _after else dict(tone.get(style))),
        tone=t_info,
        anchor=anc,
        face_gain=_fg,
        stage_cache=dict(hit=bool(_entry is not None)),
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
