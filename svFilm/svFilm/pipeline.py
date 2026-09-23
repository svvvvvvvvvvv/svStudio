# -*- coding: utf-8 -*-
"""编排：L0 分析 -> L1 修正 -> L2 风格 -> ┃空间域┃ -> L3 局部 -> L4 护栏。

层序不是随便定的，它是"标准修图顺序"的直接翻译：
  修正在前、风格在后、局部最后、输出只划上限。

空间域（颗粒/黑柔/Halation）夹在 L2 与 L3 之间：它是"胶片的物理过程"，
发生在颜色定下来之后、局部修补之前。见 spatial.py。
层内顺序 = **bloom（镜头）→ halation（乳剂/片基）→ grain（银盐）**
（09-13 晚按 P1-4 改：光学在前，镜头像差发生在乳剂之前）。
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

import numpy as np

from . import analyze, color, config as C, denoise, guard, io, local, presets, spatial, spektra, stocks, style, tone


# ========== 段缓存：「胶片出图」那一层及其之前的产物（09-15 SV 选「A」）==========
# 为什么是这一段：一次出图 3.7 s 里真卷渲染占 2.4 s（65%），而拖「脸/白区」那几根滑杆时
# **真卷的产物一个像素都不会变** ⇒ 把「降噪后」和「胶片出图后」留下来，
# 只重跑 L3 肤色 + L4 护栏：**3.8 s → 0.99 s**（实测 −74%）。
#
# ★★ 失效判定 = **每段读的参数白名单**（不是拿整个 config 去哈希 —— 那会误杀）。
#    ⚠ 两个方向都要守（`selftest.t_stage_cache` 各有一条盯着）：
#      ① **漏列**一个 ⇒ 变成"拧了没反应"（SV 最烦的那个症状）⇒ 检出办法 = 源码扫描；
#      ② **多列**一个 ⇒ 那根滑杆白白失去缓存收益 ⇒ 检出办法 = "改它键必须不变"。
#      ② 不是"宁多不少"就能糊过去的：脸那组滑杆就在缓存段里（`FACE_` 是个陷阱 ——
#         `FACE_DET_*` 是**检测**参数、缓存段确实要用；`FACE_SPAN_*`/`FACE_DEPTH_*` 是 L3 的旋钮，
#         一起圈进来会把「脸的层次」那根滑杆的收益整根抹掉）。所以脸这里**逐项写**。
SIG_CACHE = ('ENTRY_', 'ANCHOR_', 'CLIP_GUARD_', 'DENOISE_', 'TONE_',
             'SPEK_', 'STOCK', 'BASE', 'LUT_', 'PCT_', 'TGT_', 'NOISE_FLOOR',
             'GUARD_MID_L', 'MID_DEADZONE_L',
             'FACE_DET_', 'FACE_GATE_', 'FACE_MIN_', 'FACE_BOX_', 'FACE_FEATHER_REL',
             'PERSON_')

_CFG_KEYS = tuple(sorted(k for k in dir(C) if k.isupper()))


def _sig(cfg, prefixes):
    r"""把 cfg 里**匹配这些前缀**的参数打成一个可哈希的指纹（顺序固定 ⇒ 可比较）。

    只认标量 / 字符串 / 元组 / 列表；其它类型退回 `repr`（宁可多失效，也不漏）。
    """
    out = []
    for k in _CFG_KEYS:
        if not any(k.startswith(p) for p in prefixes):
            continue
        v = getattr(cfg, k, None)
        if callable(v) or isinstance(v, type):
            continue
        if isinstance(v, (int, float, bool, str)) or v is None:
            out.append((k, v))
        elif isinstance(v, (tuple, list)):
            out.append((k, tuple(v)))
        else:
            out.append((k, repr(v)))
    return tuple(out)


def _sample_uid(s):
    """样本的身份。用「路径 + 类型 + 尺寸」—— 别用 `id()`（对象被回收后 id 会复用，会误命中）。"""
    p = getattr(s, 'path', None) or ''
    if not p:
        return ('<无路径>', id(s), tuple(np.shape(s.lin)))
    return (os.path.abspath(p), getattr(s, 'kind', ''), tuple(np.shape(s.lin)))


class StageCache:
    r"""按「一张图 × 一卷 × 一组参数」缓存**真卷出图那一段**的产物。

    存两份：`disp1`（降噪后的画面，L3 拿它当肤色参考）+ `disp2`（胶片出图后的画面）。
    这两份正好是 L3 的全部输入 ⇒ 拖 L3 那几根滑杆时，前面一步都不用重算。

    ⚠ 缓存里放的是**引用**：调用方只许读，不许原地改（服务里都是 `np.clip` 出新的，安全）。
    ⚠ **调试脚本要开 `keep_stages=True` 时缓存会自动让位** —— 调试要的是"完整一遍"。
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
        a = r['analyze']
        sp = r.get('spatial') or {}
        tag = []
        if sp.get('grain', {}).get('applied'):
            tag.append('颗粒')
        if sp.get('bloom', {}).get('applied'):
            tag.append('黑柔')
        if sp.get('halation', {}).get('applied'):
            tag.append('Halation')
        btag = r.get('base_label') or '无'
        b = r.get('entry_bias_ev')
        if b is None:
            etag = '入口无基线'
        else:
            dr = r.get('fuji_dr')
            etag = '入口%+.2fEV%s' % (b, ('(DR%s)' % dr) if dr else '')
        return ('{}  [{}]  {}  |  {}  |  基准{}  |  {}  |  降噪{}  |  修正{}  |  空间{}  |  护栏{}  |  {:.0f}ms'.format(
            self.sample.name, self.sample.kind, analyze.summarize(a), etag, btag,
            stocks.label_of((r.get('style') or {}).get('stock')),
            ('开' if (r.get('denoise') or {}).get('applied') else '关'),
            ('ev%+.2f%s' % (r['tone']['ev_mid'],
                            '(有界兜底)' if r['tone'].get('dark') else ''))
            if r['tone']['applied'] else '未触发',
            ('+'.join(tag) if tag else '无'),
            ('/'.join(r['guard']['actions']) if r['guard']['actions'] else '无'),
            r['ms']))


def _stock_of(stock, cfg):
    """stock 可以是卷名字符串 / 卷 dict / None（None 时取 config.STOCK）。"""
    if stock is None:
        return stocks.get(cfg.STOCK)
    if isinstance(stock, str):
        return stocks.get(stock)
    return stock


def _entry_bias(sample):
    """入口实际补掉了几档基线曝光（io.load_raw 写在 cam 里）。JPG 路径没有这一项。"""
    if sample.kind != 'raw':
        return None
    v = (sample.cam or {}).get('idt_bias_ev')
    return None if v is None else float(v)




def run(path, src=None, max_side=None, cfg=C, out=None, lut=None, keep_stages=False,
        stock=None, base=None, paper=None):
    """一次性：**解码 + 跑完整链**。常驻服务请用 `run_from`（跳过解码）。"""
    t0 = time.perf_counter()
    s = io.load(path, max_side or cfg.MAX_SIDE, src=src)
    return run_from(s, cfg=cfg, stock=stock, base=base, out=out, lut=lut,
                    keep_stages=keep_stages, t0=t0, path=path, paper=paper)


def run_from(sample, cfg=C, stock=None, base=None, out=None, lut=None,
             keep_stages=False, t0=None, path=None, cache=None, paper=None):
    r"""★ 从**已经 load 好的** sample 起跑 —— 常驻服务的入口。

    ★★ 为什么必须有它：实测 `io.load_raw` **一个人占全链 57%**
    （700 长边：解码 1.911 s / 全链 3.357 s）⇒ **常驻 + 缓存 sample**
    ⇒ 换一次卷从 3.36 s 降到 **1.45 s**（见 README「对外入口」）。
    ⚠ **一份实现、两条入口**：`run()` = `io.load()` + `run_from()` —— 不许各写一套。

    ⚠ 缓存的 sample 必须**同尺寸**（`io.load(..., max_side)` 的产物）；换尺寸要重新 load。

    ★ `cache` = `StageCache`（默认 `None`）—— **不传就跟没有缓存时逐位相同**。
      传了、且走真卷那一路时，「胶片出图」及其之前整段可以复用（省 ~2.7 s / 一发）。
    """
    t0 = time.perf_counter() if t0 is None else t0
    s = sample
    path = path if path is not None else getattr(s, 'path', None)
    st = _stock_of(stock, cfg)

    # ★★ 位置由「脸」的锚点决定（09-14 SV 选「乙」）：由脸算一个曝光偏移，
    #   **把入口那条曲线整体重打**。不是"分区域压脸/压背景"—— 一条曲线、不用掩膜，
    #   背景的亮度是这条曲线算出来的**结果**。
    # ★★ 真卷（`spek=`）**不用锚点**：实测真卷自己就把脸放到 L* 78~86（比我们靶 68 还亮），
    #   再提一遍就是过曝（中位 61 → 83）。⇒ 真卷模式下位置整段交给胶片。
    # ★★★ 09-23 SV 定案：卷表换成 public GUI 的 9 条**大师预设**（`preset=`）。
    #   预设跟真卷一样是"胶片性格整段自带"（曝光 / 曲线 / 耦合剂 / 颗粒 / halation / 柔光 / 锐化
    #   全在预设 JSON 里）⇒ 凡是对真卷**让位**的那几处（入口锚点 / L1 影调 / L2 颜色 / 空间层 /
    #   段缓存），对预设**同样让位**。所以把"走引擎物理链"统一成一个标记 `_engine`。
    _spek = (st or {}).get('spek')          # 真卷：{'film','print','pe'}
    _preset = (st or {}).get('preset')      # 预设：预设 JSON 的文件名（`data/presets/`）
    _pre_engine = bool(_spek or _preset)
    _engine = _pre_engine

    # ---- ★ 相纸（09-15 SV 选「C」）---------------------------------------------
    # 印相纸是**成色的另一半**（同一卷负片印在不同纸上 = 两套不同的颜色，尤其肤色）。
    # 默认 = 这一卷**配套**的那张（卷表里就有，**由引擎给**，前端不许自己挑）。
    # ⚠⚠ 名字不认得（旧配方 / 手改过的配置）**必须当场回落 + 说清楚**：
    #   不许原样塞进 `init_params`（那是 `FileNotFoundError` 直接崩），
    #   也不许静默换一张 —— "名字不认得 ⇒ 静默走默认"是本项目最阴的一类坑（已出现三次）。
    _paper, _paper_fb, _paper_why = spektra.resolve_paper((st or {}).get('name'), paper)

    # ---- ★ 段缓存：命中就整段跳过，只留 L3 肤色 + L4 护栏（09-15 SV 选「A」）----
    # ⚠ 要 `keep_stages` 的调试脚本**自动让位** —— 调试要的是"从头完整跑一遍"。
    if keep_stages:
        cache = None
    _ckey, _entry = None, None
    if cache is not None and _pre_engine:
        # ⚠ 键里**必须带相纸**：换了纸而出图还是上一张 = 白换（而且看不出来）。
        _ckey = ('film', _sample_uid(s), (st or {}).get('name'), _paper or '',
                 _sig(cfg, SIG_CACHE))
        _entry = cache.get(_ckey)

    # ---- ★★ 脸掩膜：**解码后算一次、整条链共用**（09-15 SV 选「A」修的那个洞）--------
    # 为什么必须挪到这一步：`face.parse` 的分割模型输入固定 256×256、且**对发白的脸本来就不稳**。
    #   链尾（胶片出图后）真卷已经把脸放到 L\*88~90 ⇒ 模型认不出 ⇒ 掩膜是空的 ⇒ **两层一起静默失效**
    #   （收脸 `no_face`、脸的层次 `no_skin`），而且**图越小越认不出**（断崖，不是渐变）。
    #   而**解码后**那张脸还是正常曝光 ⇒ 稳定、随尺寸平滑缩放（900/700/400 = 12675/7678/2503 px）。
    #   ★ 也正是本项目原有的那条原则：「只在 base 上解析一次脸掩膜、各层共用」。
    # ⚠ 尺寸：掩膜恒等于画面尺寸（700/900 实测都相等）⇒ 原对象直接传下去，不重采样。
    # ⚠ 命中段缓存时**复用缓存里那份** —— 它只依赖 `FACE_DET_*`/`FACE_GATE_*`/`FACE_BOX_*`/
    #   `FACE_FEATHER_REL`/`PERSON_*`，这些**全在 `SIG_CACHE` 里**（换一个键就变）⇒ 不会张冠李戴。
    # ⚠ 拿不到（模型缺失等）⇒ 退回空掩膜，下游照旧报 `no_face`/`no_skin`，**不崩、不静默改行为**。
    _mbox = [_entry.get('msk') if _entry is not None else None]

    def _masks():
        if _mbox[0] is None:
            try:
                from . import face as _face
                _mbox[0] = _face.parse(np.clip(s.disp, 0.0, 1.0))
            except Exception as e:                          # noqa: BLE001
                _mbox[0] = dict(masks={}, face=None, person_weight=None,
                                err='%s: %s' % (type(e).__name__, e))
        return _mbox[0]

    if _entry is not None:
        # 命中：上游全部复用。几个小 dict 要**复制** —— 下游会往 `anc` 里写 finish，
        #   调用方也可能改 report，不复制就会污染缓存里的那一份。
        rep0 = dict(_entry['rep0'])
        anc = dict(_entry['anc'])
        _on = bool(_entry['on'])
        t_info = dict(_entry['t_info'])
        d_info = dict(_entry['d_info'])
        s_info = dict(_entry['s_info'])
        disp1 = _entry['disp1']          # 降噪后的画面（L3 拿它当肤色参考）
        disp2 = _entry['disp2']          # 胶片出图后的画面（L3 的另一半输入）
        lin_in = disp_in = None          # 命中时用不到（keep_stages 那条路缓存已让位）
    else:
        # ⚠ 真卷（带 `spek=`）**不跑入口锚点**：实测真卷自己就把脸放到 L* 78~86
        #   （比我们靶 68 还亮），再提一遍就是过曝（中位 61 → 83）。
        #   ★ 09-15 SV 选「D」的「脸太亮收回」**不走这里** —— 它在**胶片之后**
        #   （`finish_anchor`）做，因为入口那一半压不动脸（实测只 −5 L\*，见 io.finish_anchor 注释）。
        if _pre_engine and not bool(getattr(cfg, 'SPEK_ANCHOR', False)):
            d_ev, anc = 0.0, dict(applied=False, reason='real_stock_真卷自己定曝光')
        else:
            # ★ 掩膜走同一份（`s.disp` 就是解码后那张 ⇒ 与"它自己现算"逐位相同，只是省一次解析）
            d_ev, anc = io.anchor_ev(s.disp, cfg, masks=_masks()['masks'])
        _on = bool(anc.get('applied'))
        lin_in = io.refocus(s.lin, d_ev, cfg) if _on else s.lin
        if _on:
            # ★★ 09-14 评审修：锚点把入口曲线**重打**了 ⇒ 入口那道裁切护栏（`clip_guard`）
            #   是**在重打之前**算的，结论已经失效 ⇒ 这里**必须再跑一遍**。
            #   不跑的话"提亮救脸"会顺手绕过护栏把背景推爆（实测 0805 背景 75→96、护栏当时算出 k=1.0）。
            lin_in, _gk2 = io.clip_guard(lin_in, cfg)
            anc['clip_guard_k'] = _gk2
            if abs(_gk2 - 1.0) > 1e-9:
                anc['clip_guard_ev'] = float(np.log2(_gk2))
        disp_in = (np.clip(color.l2s(np.clip(lin_in, 0.0, None)), 0.0, 1.0)
                   if _on else s.disp)

        rep0 = analyze.analyze(lin_in, disp_in, s.kind)                  # L0
        # L1 影调修正（**只压不提**：提亮交给入口 settle + 脸锚点，兜底提亮那套 09-14 已删）
        # ★★ 09-14 SV：「丢弃作者线，全部用真卷」。
        #   真卷（带 `spek=` 标记）**自带完整 H&D 曲线** ⇒ 我们的 L1 影调修正要**让位**（不然是两条曲线串）。
        if _engine:
            lin1, t_info = lin_in, dict(applied=False, reason='real_stock_自带H&D曲线')
            disp1 = disp_in
        else:
            lin1, t_info = tone.correct(lin_in, rep0, cfg)               # L1
            disp1 = np.clip(color.l2s(np.clip(lin1, 0.0, 1.0)), 0.0, 1.0)
        disp1, d_info = denoise.apply(disp1, cfg)                        # 降噪（L1 之后、L2 之前）

        if lut is None and cfg.LUT_PATH:
            lut = style.cube_read(cfg.LUT_PATH)
        # 锁中灰的参照 = 修正层实际交出来的中灰（不是配置里的靶）
        if _engine:
            # ★★ L2 整段换成**引擎物理链**：喂**场景线性**（`lin_in`），出显示域。
            #   两条来源：
            #     · **预设**（`preset=`，09-23 SV 定案后是常态）→ `presets.render()`，
            #       参数照读预设 JSON（负片/相纸也由它自带）；落点 = 预设自带的 pe × 系数。
            #     · **真卷**（`spek=`，老路子，保留兼容）→ `spektra.render()`；
            #       落点 = 每卷标定的 pe × 系数。
            #   两者的共同点：都自带 H&D + dir_couplers(彩度) + 染料 + 颗粒 + halation
            #   ⇒ 所以上面几处（入口锚点 / L1 / 空间层 / 段缓存）都让位。
            #   ★ 相纸从这里进物理链。`_paper` 在上面解析好了：不传 = 这一卷配套的那张；
            #     传了但认不得 = 已回落成配套纸 + 在下面 `print_fallback` 里说明。
            _shift = float(getattr(cfg, 'SPEK_PE_SHIFT', 1.0) or 1.0)
            if _preset:
                _base_pe = presets.pe_of(_preset)
                disp2 = presets.render(lin_in, _preset, cfg, print_profile=_paper)
                _how, _film = 'preset', presets.film_of(_preset)
            else:
                _base_pe = float(_spek.get('pe') or getattr(cfg, 'SPEK_PRINT_EXPOSURE', 0.55))
                disp2 = spektra.render(lin_in, st['name'], cfg,
                                       print_exposure=_base_pe * _shift,
                                       print_profile=_paper)
                _how, _film = 'spektrafilm', _spek.get('film')
            _pe = _base_pe * _shift
            s_info = dict(applied=True, how=_how, stock=st['name'],
                          film=_film, print=_paper,
                          print_default=bool(_paper and _paper == spektra.default_paper(st['name'])),
                          print_fallback=bool(_paper_fb),
                          print_fallback_reason=_paper_why,
                          print_exposure=_pe, pe_base=_base_pe, pe_shift=_shift,
                          tone_curve=False, film_color_w=0.0)
        else:
            disp2, s_info = style.apply(disp1, cfg, lut=lut, lock_ref=style.mid_of(disp1),
                                        stock=st, base=base)
        if _ckey is not None:
            # ⚠ 存的是**引用**：调用方只许读（服务里都是 np.clip 出新的，安全）
            cache.put(_ckey, rep0=rep0, anc=anc, on=_on, t_info=t_info,
                      d_info=d_info, s_info=s_info, disp1=disp1, disp2=disp2,
                      msk=_masks())     # 掩膜也是这一段算出来的 ⇒ 一起留下（省 ~90ms/发）
    # ★ 锚点**收尾**（09-14 SV 选「①」）：L1 那一步把脸放到靶上了，但 **L2 影调曲线又把它抬上去**
    #   （实测 +8.4 L*）⇒ 这里量一次脸、用**全局增益**把它挪回靶 ⇒ **最终脸真的落在靶上**。
    #   只在锚点真的动过（脸偏暗）时才做；仍是一条曲线，不分区。
    # ★★ 09-15 SV 选「D」：「脸太亮收回」（`ANCHOR_DOWN_GAIN`）—— **默认 0 = 一个像素都不动**。
    #   ★ 为什么这个滑杆落在**这里**（胶片之后）而不是入口：
    #     入口那半实测压不动脸（重打 −0.96 档 ⇒ 脸只从 89.4 掉到 84.2，真卷的 H&D 又把它拉回来），
    #     而这里（量一次脸、整张乘同一个增益）一步就送到靶 68。**只留这一个口子。**
    #   ★ 它开了之后**不必**入口锚点也动过（`_on`）：两条路各走各的 ——
    #     `_on` 那条是老路径（提亮收尾，力度 1.0，**逐位不变**）；`_gain` 这条是新的"收回"。
    #   ⚠ 由它触发的收尾一律 `down_only=True`（只许往下压）：真卷自己把脸放到 L*78~86，
    #     "提亮"那一半必须关着，否则一转滑杆就把偏暗的片也推亮。
    _gain = float(getattr(cfg, 'ANCHOR_DOWN_GAIN', 0.0) or 0.0)
    if bool(getattr(cfg, 'ANCHOR_FINISH', True)) and (_on or _gain > 0.0):
        disp2, _fin = io.finish_anchor(
            disp2, cfg,
            masks=_masks()['masks'],          # ★ 解码后算的那一份（不再在发白的画面上现算）
            strength=(1.0 if _on else min(_gain, 1.0)),
            down_only=bool(not _on))
        anc['finish'] = _fin
    # ⚠ 09-14 SV：「把脸部立体感的部分删掉」⇒ 原来在 L2 之后 / 空间层之后各插一道
    #   `local.face_tone`（第 4 条「每层护脸」），**已整段删除**。
    disp2r = disp2
    if _engine:
        # ★ 引擎自带的 grain / halation / glare 已经是物理级的（分通道、R 最强 ⇒ 红橙）
        #   ⇒ 我们的空间层**必须让位**，不然是两套颗粒叠一起。
        #   （预设那 9 条同样自带：颗粒、柔光、halation、输出锐化全在预设 JSON 里。）
        disp2b, sp_info = disp2, dict(applied=False, reason='引擎自带颗粒/halation')
    else:
        disp2b, sp_info = spatial.apply(disp2, cfg, stock=st)        # 空间域（颗粒/黑柔/Halation）
    disp2br = disp2b
    # ★★ 同一份掩膜一路传到底（收脸 / 脸的层次 用的是**同一个人脸**，不该各算各的）
    disp3, l_info = local.apply(disp1, disp2b, cfg, masks=_masks()['masks'])   # L3
    disp4, g_info = guard.enforce(disp3, cfg)                        # L4

    rep = dict(
        camera=s.cam,
        entry_bias_ev=_entry_bias(s),
        fuji_dr=(s.cam or {}).get('fuji_dr'),
        stock=(st or {}).get('name'),
        stock_label=stocks.label_of((st or {}).get('name')),
        base=stocks.resolve_base(cfg, base)['name'],
        base_label=stocks.base_label(cfg, base),
        analyze=rep0,
        tone=t_info,
        denoise=d_info,
        style=s_info,
        spatial=sp_info,
        local=l_info,
        anchor=anc,
        guard=g_info,
        stage_cache=dict(hit=bool(_entry is not None)),
        ms=(time.perf_counter() - t0) * 1000.0,
    )
    if keep_stages:
        rep['stages'] = dict(
            base=disp_in,           # 入口归一后的样子（RAW 就是线性直出；锚点已重打过）
            after_tone=disp1,       # 做完 L1 影调修正 + 降噪
            after_style=disp2,      # 再过 L2 风格（含第 4 条护脸那道）
            after_spatial=disp2b,   # 再过空间域（含第 4 条护脸那道）
            after_local=disp3,
            style_raw=disp2r,       # ★ 没护脸的 L2 出口（探针用：看"护脸"到底动了多少）
            spatial_raw=disp2br,    # ★ 没护脸的空间层出口
        )
    res = Result(disp4, rep, s, path)
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
