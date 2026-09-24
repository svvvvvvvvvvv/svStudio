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

from . import config as C, grade, io, presets, tone


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

    _ckey, _entry = None, None
    if cache is not None:
        _ckey = ('film', _sample_uid(s), name, style, getattr(cfg, 'MAX_SIDE', None), _after)
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
        disp = presets.render(np.clip(s.lin, 0.0, None), name, cfg)
        # ---- L1 影调（明度分布）----
        disp, t_info = tone.settle_finished(disp, style, cfg, stock=name)
        # ---- L2 分色 + L3 混色（颜色）----
        # ⚠ 这一层**不做曝光**（显示域乘增益 = 拉噪声 + 高光切白），只按亮度/色相加权染色。
        disp, g_info = grade.apply(disp, cfg, stock=name)
        t_info['grade'] = g_info
        gk = 1.0
        anc = dict(applied=False, note='曝光风格在引擎之后 ⇒ 不做脸锚点')
        if _ckey is not None:
            cache.put(_ckey, disp=disp, t_info=t_info, gk=gk, anc=anc)
    else:
        # ========== 老路：曝光风格作用在引擎**之前**的线性图上 ==========
        # ---- ★★ 脸掩膜：**解码后算一次**（09-15 修的那个洞）----
        #   链尾（胶片出图后）画面已经发白 ⇒ 分割模型认不出脸 ⇒ 掩膜空 ⇒ 两层一起静默失效。
        #   解码后那张脸还是正常曝光 ⇒ 稳。拿不到（模型缺失）⇒ 空掩膜，下游报 no_face，**不崩**。
        try:
            from . import face as _face
            _msk = _face.parse(np.clip(s.disp, 0.0, 1.0))['masks']
        except Exception as e:                                   # noqa: BLE001
            _msk = {}

        # ---- ★★ 曝光谁定：**曝光风格定基准，脸做有限幅的修正** ----
        #   两个都能算出一个"要补几档"——风格看**整张中位**、锚点看**脸**。
        #   直接相加/先后施加都会互相抵消（都是全局增益），所以合成：
        #     以风格为准，允许脸把它拉偏最多 `ANCHOR_LIMIT_EV` 档。
        #   脸偏暗 ⇒ bias > 0（多提一点救脸）；脸已经够亮而整张偏暗 ⇒ bias < 0（少提，护脸）。
        ev_style = tone.ev_needed(s.lin, style, cfg, preset=name)
        d_face, anc = io.anchor_ev(s.disp, cfg, masks=_msk)
        lim = float(getattr(cfg, 'ANCHOR_LIMIT_EV', 0.6))
        bias = 0.0
        if anc.get('applied'):
            bias = float(np.clip(float(d_face) - ev_style, -lim, lim))
        anc['ev_style'] = float(ev_style)
        anc['ev_bias'] = float(bias)

        # ---------- svFilm：曝光风格（线性域）----------
        lin_out, t_info = tone.apply(s.lin, style, cfg, preset=name, ev_bias=bias)
        # ---------- 高光护栏（只往下）----------
        lin_out, gk = io.clip_guard(lin_out, cfg)
        t_info['clip_guard_k'] = float(gk)
        # ---------- spektrafilm：胶片风格 ----------
        disp = presets.render(lin_out, name, cfg)
        # ---------- 脸收尾（默认关：`ANCHOR_DOWN_GAIN=0`）----------
        # ⚠ 这是**唯一留在胶片之后**的曝光动作，理由有实测：胶片之前压不动脸
        #   （入口重打 −0.96 档，脸只从 89.4 掉到 84.2，真卷的 H&D 又把它拉回来）。
        #   默认一个像素都不动 ⇒ 边界仍然是"曝光归 svFilm、胶片归 spektrafilm"。
        if float(getattr(cfg, 'ANCHOR_DOWN_GAIN', 0.0) or 0.0) > 0.0:
            disp, _fin = io.finish_anchor(disp, cfg, masks=_msk, down_only=True)
            anc['finish'] = _fin
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
