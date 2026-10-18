# -*- coding: utf-8 -*-
r"""自检 —— 动完任何一层都要跑。不依赖任何样片，纯合成图 + 不变量。


  python -m svFilm.selftest

★ 这条链只剩：**解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(0.3.4) → 出图**
  （影调层 / 肤色层 / 认人认脸**整段删除**，它们的检查随模块一起删了）。
"""
import os
import sys

import numpy as np

from . import color, config as C, io, pipeline, presets
# ⚠ **不要在模块顶层 import spektrafilm**：那会抢在仓库的"来源守卫"之前把引擎加载进来，
#   守卫会判定"不是指定的那一份"⇒ 整组检查全炸（10-16 踩过）。要用时**惰性 import**。


_PRESET = '鹿井预设'

FAIL = []


def check(name, cond, extra='', why=''):
    """`extra` = 现场（成败都打）；`why` = **红了意味着什么**（只在红时打）。"""
    print(('  ok   ' if cond else '  FAIL ') + name
          + (('   ' + extra) if extra else '')
          + (('   ⇒ ' + why) if (why and not cond) else ''))
    if not cond:
        FAIL.append(name + ((' — ' + why) if why else ''))


# ---- 合成图 ----

def _gray_img(h=180, w=240, gamma=0.45, seed=0, tint=(1.02, 0.99, 0.95)):
    """一张有结构的灰度图（不是纯噪声）—— 分位判据要的是有宽有窄的分布。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    g = 0.5 + 0.34 * np.sin(xx / 23.0) * np.cos(yy / 31.0)
    g = np.clip(g, 0.02, 0.98) ** gamma
    g = np.clip(g + 0.03 * rng.standard_normal((h, w)), 1e-3, 1.0)
    out = np.stack([g * tint[0], g * tint[1], g * tint[2]], -1)
    return np.clip(out, 0.0, 1.0)


def _lin_from_disp(d):
    return np.clip(color.s2l(np.clip(np.asarray(d, np.float64), 0.0, 1.0)), 0.0, None)


def _mk_sample(disp, path='<自检>'):
    return io.Sample(_lin_from_disp(disp), disp, 'jpg', path)


def _pure_engine_same(res, sample, style=None):
    """整条链上**只有引擎在动像素** ⇒ 出图应当**就是** `presets.render_person_pe` 的输出。

    ★ 10-16 改了契约：生产走的是**「按脸反解印相曝光」**（两遍印相；力度 = 报告里的 `light_back`）
      ⇒ 参照物必须**同参**跑 `render_person_pe`。没人脸 / 量不到 ⇒ 它只印一遍 = `render()`。
    ⚠ 颗粒随机 ⇒ 判据是"差值在引擎自身噪声量级内"；参照物必须带**生产那份引擎覆盖**。
    """
    lin = np.clip(sample.lin, 0.0, None)
    a = np.asarray(res.disp)
    from . import targets as _TS
    _sc = res.report.get('scene')
    ov = _TS.scene_engine(_sc, stock=res.report['stock'], cfg=C) or None
    stock = res.report['stock']
    _back = float(res.report.get('light_back') or 0.0)
    b = np.asarray(presets.render_person_pe(lin, stock, C, overrides=ov, back=_back))
    c = np.asarray(presets.render_person_pe(lin, stock, C, overrides=ov, back=_back))
    noise = float(np.max(np.abs(b - c)))          # 引擎自身不可复现的那点
    diff = float(np.max(np.abs(a - b)))
    return diff <= max(noise * 2.5, 5e-3)


# ---- R1 解算器（§一.5）----

def _r1_per_image(lab, c_max=None):
    r"""**一张图**的 R1 = `C*(L*0-20) ÷ C*(L*20-40)`（中性像素 C\* <= `_R1_NEUTRAL_C`）。

    ★ 分母必须是**相邻档**（`NA0/max(NA1..3)` 是 **A1 黑端**）；中性像素不够 ⇒ `nan`。
    """
    lab = np.asarray(lab, np.float64)
    L = lab[..., 0]
    C = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    cm = _R1_NEUTRAL_C if c_max is None else float(c_max)
    neu = C <= cm
    m0 = neu & (L >= 0.0) & (L < 20.0)
    m1 = neu & (L >= _R1_DEN_BAND[0]) & (L < _R1_DEN_BAND[1])
    if int(m0.sum()) < _R1_MIN_PX or int(m1.sum()) < _R1_MIN_PX:
        return float('nan')
    c1 = float(np.median(C[m1]))
    return float(np.median(C[m0]) / c1) if c1 > 0 else float('nan')


def _r1_batch(labs):
    r"""**整批** R1 —— ★★ **先逐图算比、再取整批中位**（不是先取中位再相除）。

    ★ 两种顺序只在"所有图逐位相同"时才相等 ⇒ 差别在合成图上量不出来、必须手搓 Lab 钉；
      无效图（`nan`）**计入 `n_bad`** 并从分母 `n` 剔除。
    """
    v = [_r1_per_image(x) for x in labs]
    good = np.array([x for x in v if np.isfinite(x)], np.float64)
    if good.size == 0:
        return {'median': float('nan'), 'n': 0, 'n_bad': len(v),
                'iqr': float('nan'), 'per_image': v}
    return {'median': float(np.median(good)), 'n': int(good.size), 'n_bad': len(v) - int(good.size),
            'iqr': float(np.percentile(good, 75) - np.percentile(good, 25)), 'per_image': v}


def _r1_verdict(median, n, iqr, band=None, master_iqr=None):
    r"""把「整批中位」译成判定：`分辨力 = 2·sqrt((大师逐图IQR/√12)^2 + (我们逐图IQR/√n)^2)`。

    ★★ **离界 < 分辨力 ⇒ 只报方向，不当缺陷** —— 分辨力含**不随 n 下降**的大师逐图 IQR。
    """
    band = _R1_BAND if band is None else band
    mi = _R1_MASTER_IQR if master_iqr is None else float(master_iqr)
    lo, hi = float(band[0]), float(band[1])
    if not np.isfinite(median) or n <= 0 or not np.isfinite(iqr):
        return {'verdict': '量不了', 'dist': float('nan'), 'res': float('nan')}
    dist = (lo - median) if median < lo else ((median - hi) if median > hi else 0.0)
    res = 2.0 * float(np.sqrt((mi / np.sqrt(12.0)) ** 2 + (iqr / np.sqrt(float(n))) ** 2))
    if dist == 0.0:
        v = '在带内'
    elif dist < res:
        v = '判不了（分辨力不够）'
    else:
        v = '真越界（分辨力足够）'
    return {'verdict': v, 'dist': float(dist), 'res': float(res), 'band': band,
            'median': float(median), 'n': int(n)}


def t_review_1008():
    r"""**防复发**断言：守的都是"接线断了但没人发现"的一类。

    它们的共同点是：**没有任何东西会报错**（字段名写错 / 缓存键缺项 / 前缀被删光）。
    """
    import numpy as _np
    from . import config as C, presets, service, targets


    _gone = [k for k in service._DECODE_SIG_KEYS if not hasattr(C, k)]
    check('★★★ 解码签名有键、且键真的在 config 里', bool(service._DECODE_SIG_KEYS) and not _gone,
          '键=%s 缺=%s' % (list(service._DECODE_SIG_KEYS), _gone or '无'),
          '老写法靠 `ENTRY_` 前缀取键，前缀被删光后恒为空 ⇒ 机制静默失效、无人发现')


    _p = presets.digested(_PRESET, C)
    check('★★ `normalize_print_exposure` / `print_exposure_compensation` 显式钉为 True',
          bool(_p.enlarger.normalize_print_exposure)
          and bool(_p.enlarger.print_exposure_compensation),
          'norm=%s comp=%s' % (_p.enlarger.normalize_print_exposure,
                               _p.enlarger.print_exposure_compensation),
          '它是整张落点的命门；吃默认值 ⇒ 换 vendor 版本会**静默位移一档亮度**')


    #   ★ 约定：`{"add": Δ}` 要有一个**数值底**，且多轴命中时**相加**（不是后匹配的轴盖掉前面的）。
    _d = targets.load()
    _se = _d.get('_scene_engine') or {}
    _star = _se.get('*') or {}
    _bad_add = []
    for _k, _blk in _se.items():
        if not isinstance(_blk, dict):
            continue
        for _kk, _vv in _blk.items():
            if isinstance(_vv, dict) and 'add' in _vv:
                _b = _star.get(_kk)
                if not isinstance(_b, (int, float)) or isinstance(_b, bool):
                    _bad_add.append('%s.%s（基准 %r）' % (_k, _kk, _b))
    check('★★★ 每个 `{"add": Δ}` 轴覆盖的键在 `"*"` 层里都有**数值基准**',
          not _bad_add, '有问题: %s' % (_bad_add or '无'),
          '加法需要一个数值底。没有底 ⇒ 该 add 会被静默忽略（"写了没反应"），'
          '所以必须静态钉住：**要叠加的键，先在 `"*"` 里给基准值**')
#   ⚠ `_se` 是**活对象**：临时造轴要**就地改、跑完还原**，不能整体换 dict。
    _GN = 'print_render.chemistry.gamma_factor'
    _has_span = 'span=平' in _se
    _span_save = _se.get('span=平')
    #   ★★ 10-15：基线层（`_scene_engine["*"]`）已**清零**（只剩 `morph.active`）
    #     ⇒ `{"add": Δ}` 的"数值底"**不再由生产数据提供**。这条测试测的是**机制**
    #     （"多轴命中要相加、不许退化成覆盖"），所以**自带底**：临时写进 `"*"`、跑完还原。
    #     ⚠ 否则 `out.get(_GN)` 是 None ⇒ 后面 `float(None)` 当场 TypeError（10-15 踩过）。
    _star_blk = _se.setdefault('*', {})
    _had_base = _GN in _star_blk
    _base_save = _star_blk.get(_GN)
    _inj = {'print_render.chemistry.gamma_factor': {'add': 0.07}}
    try:
        _star_blk[_GN] = 0.98
        _se['span=平'] = {_GN: 1.23}
        _g1 = targets.scene_engine({'span': '平', 'exp': None, 'back': None, 'overwhite': None},
                                   stock=_PRESET, cfg=C).get(_GN)

        _se['back=正逆光'] = dict(_inj)
        _a = float(targets.scene_engine({'span': '平', 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock=_PRESET,
                                        cfg=C).get(_GN))
        _b = float(targets.scene_engine({'span': None, 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock=_PRESET,
                                        cfg=C).get(_GN))
        _g2 = targets.scene_engine({'span': None, 'exp': None, 'back': None, 'overwhite': None},
                                   stock=_PRESET, cfg=C).get(_GN)
    finally:
        _se.pop('back=正逆光', None)
        if _has_span:
            _se['span=平'] = _span_save
        else:
            _se.pop('span=平', None)
        if _had_base:
            _star_blk[_GN] = _base_save
        else:
            _star_blk.pop(_GN, None)
    check('★★ 只用 `span=平` 时确实命中（其余轴为 None 也不受影响）',
          _g1 is not None and _g2 is not None and abs(float(_g1) - float(_g2)) > 1e-9,
          '（临时造一条 span=平 → 1.23）span=平 → %s ／ 无 span → %s' % (_g1, _g2),
          '这是"逐场景参数真的接线了"的最小证据')
    check('★★★ 两轴同时命中时**真的叠加**（注入 {"add":0.07} 验证，跑完还原）',
          abs(_a - (float(_g1) + 0.07)) < 1e-9 and abs(_b - (float(_g2) + 0.07)) < 1e-9,
          'span=平+正逆光 %.4f（应 %.4f）／ 只正逆光 %.4f（应 %.4f）'
          % (_a, float(_g1) + 0.07, _b, float(_g2) + 0.07),
          '`span=平` 与 `back=逆光` 实测是**相加的两个因子**（组内单调：正逆光+平 69.77 < '
          '正逆光+正常 74.62 < 正逆光+大 78.09）⇒ 原来的 `out.update()` 让后匹配的轴**盖掉**'
          '前面的 ⇒ 会少补一个因子。这条断言钉住"叠加不许退化成覆盖"')


    _v0 = float(presets.digested(_PRESET, C).enlarger.m_filter_shift)
    _t0 = C.PRESET_FILTER_M_TRIM
    C.PRESET_FILTER_M_TRIM = _t0 + 3.0
    try:
        _v1 = float(presets.digested(_PRESET, C).enlarger.m_filter_shift)
    finally:
        C.PRESET_FILTER_M_TRIM = _t0
        presets.clear_cache()
    check('★★★ 改 config 的 `PRESET_*` ⇒ 参数对象**立刻重建**（cfg 进了缓存键）',
          abs(_v1 - (_v0 + 3.0)) < 1e-6,
          'trim %.1f→%.1f 时 m_shift %.3f→%.3f' % (_t0, _t0 + 3.0, _v0, _v1),
          '老版本键里只有 name ⇒ 首次调用者的 config 永久污染缓存 ⇒ A/B 试验两边一样')


# ---- 合成探针的绝对电平（仪器参数，不是风格靶；推导见 `引擎手册.md`）----
# · `_PROBE_SCENE_GAIN`：探针**本体**增益 ⇒ 代表**真实工作点**（真实 RAW 的 `meanY` 中位）。
# · `_PROBE_ABS_ROWS`：顶部这几行**不乘增益**，重建 0→1 的**绝对灰阶斜坡**（锁①②③④⑧问的
#   是**绝对输入电平**，不给这条灰阶就等于改考题）。回退：两个值设回 `1.0` / `0`。
_PROBE_SCENE_GAIN = 0.2018      # ← 旧值 = 1.0（回退点）；本体 meanY = 0.0649，全图 = 真实中位 0.0693
_PROBE_ABS_ROWS = 16            # ← 旧值 = 0（回退点）；这一段不乘增益，承载绝对电平的锁

# ---- R1 常数（定义与使用限制见 §一.5）----
# ★ 界 = 大师**逐图并集** [P25, P75]（成片语料：鹿井 52 + 滨田 22，过门 46+21）；
#   **别拿"组中位"当界**。数值与 `_lib/bands.json` 的 R1 一致（10-18 重标）。
_R1_BAND = (0.579, 0.839)
_R1_MASTER_IQR = 0.260          # 大师逐图并集 IQR（= P75−P25，n=74）—— 分辨力的分子
_R1_NEUTRAL_C = 8.0             # 中性判据：C* <= 8（与 §一.2 的中性轴表同口径）
_R1_DEN_BAND = (20.0, 40.0)     # ★ 分母 = **相邻档**（不许用"五档最大值"，那是 A1 黑端，不是 R1）
_R1_MIN_PX = 30                 # 单张、单档的中性像素下限（不够 ⇒ 这张的比不可用，如实标 NaN）
# ★ 探针**不是生产口径**（见 R1 那组）；每张 = 一次全链 render（~3.6 s）⇒ **图数就是成本**。
_R1_PROBE_GAINS = (0.70, 0.85, 1.00, 1.20)      # 乘在 `_PROBE_SCENE_GAIN` 上
_R1_PROBE_KNOWN = 0.231817      # 关掉 glare 后的**逐位可复现**批中位（dev 6cd00c8 + 鹿井 amount 1.10，n=4）
_R1_PROBE_TOL = 0.008           # 图间 IQR = 0.0057 ⇒ 取 ≈1.4×IQR 当锁宽
# ★ 这个数**不是生产口径**（合成图表的 R1 恒低于真实照片带）⇒ 它只钉"尺子还活着"；
#   换链（卷/纸/化学/扫描端点）后必须重测再改锁。


def t_synth_transfer():
    r"""★★★★★ **合成测试图的传递函数**（内容无关的回归基准）。

    真实照片的分布差**无法归因到渲染** ⇒ 合成图把"我们的变换"变成一**条可读的曲线**；
    ⚠ 本组**不含逐场景覆盖**（在**基线**上量）；①②④ 是**回归锁**（值 = dev 实测，不是风格靶）。
    """
    import numpy as np
    from . import color, presets, targets

    W, H = 768, 900

    def s2l(c):
        c = np.asarray(c, np.float64)
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

    ch = np.zeros((H, W, 3), np.float64)
    ch[0:300] = np.linspace(0.0, 1.0, W)[None, :, None]              # ① 灰阶斜坡
    for i in range(16):
        ch[300:450, int(i * W / 16):int((i + 1) * W / 16)] = i / 15.0  # ② 16 级灰块
    for i in range(12):                                              # ③ 12 档肤色块
        hh = np.radians(55.0)
        lab = np.array([[[30.0 + i * 5.0, 20.0 * np.cos(hh), 20.0 * np.sin(hh)]]], np.float64)
        ch[450:620, int(i * W / 12):int((i + 1) * W / 12)] = np.clip(color.from_lab(lab)[0, 0], 0, 1)
    for i in range(12):                                              # ④ 色相环
        hh = np.radians(i * 30.0)
        lab = np.array([[[60.0, 35.0 * np.cos(hh), 35.0 * np.sin(hh)]]], np.float64)
        ch[620:750, int(i * W / 12):int((i + 1) * W / 12)] = np.clip(color.from_lab(lab)[0, 0], 0, 1)
    ch[750:900, 0:W // 3] = 0.0                                      # ⑤ 黑白灰
    ch[750:900, W // 3:2 * W // 3] = 0.5
    ch[750:900, 2 * W // 3:] = 1.0


    lin_in = np.clip(s2l(ch) * _PROBE_SCENE_GAIN, 0.0, None)
# ★ 这一段**不乘增益**（0→1）：承载"必须以**绝对输入电平**发问"的那几条锁。
    _R0 = 750
    _R1 = _R0 + int(_PROBE_ABS_ROWS)
    if _PROBE_ABS_ROWS >= 4:
        lin_in[_R0:_R1, :, :] = s2l(np.linspace(0.0, 1.0, W))[None, :, None]
        _ABS = slice(_R0 + 1, _R1 - 1)          # 取内部，避开与上下的边界
    else:
        _ABS = slice(0, 300)                    # 回退：用原来那条斜坡

    ov = targets.scene_engine(None, stock=_PRESET, cfg=C)
    e = presets.render(lin_in, _PRESET, C, overrides=ov)
    lab = color.to_lab(e)
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    # ★★ 传递函数直接在**引擎输出**上量（引擎出图 = 成片）。
    lab2 = lab
    Lo, ao, bo = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    # ★ 输入 Lab 从**真正喂进去的那张图**算（不再是标称值）⇒ 每条锁都能报"实际输入 L\*"
    LABI = color.to_lab(np.ascontiguousarray(np.clip(color.l2s(lin_in), 0.0, 1.0)))
    Li, ai, bi = LABI[..., 0], LABI[..., 1], LABI[..., 2]

    def ramp(v):
        x = int(np.clip(v / 100.0 * (W - 1), 0, W - 1))
        return float(np.median(Lo[_ABS, max(0, x - 2):x + 3]))

    def inL(v):
        """斜坡位置 `v`% 处的**实际**输入 L\\*（旧代码直接把 `v` 当输入 L\\*，只在增益 1.0 时近似成立）。"""
        x = int(np.clip(v / 100.0 * (W - 1), 0, W - 1))
        return float(np.median(Li[_ABS, max(0, x - 2):x + 3]))

    L100, L50, L20 = ramp(100), ramp(50), ramp(20)

    if _PROBE_ABS_ROWS >= 4:
        _x50 = int(np.clip(color.l2s(0.1842) * (W - 1), 0, W - 1))
        _mid_sl = (_ABS, slice(max(0, _x50 - 3), _x50 + 4))
    else:
        _mid_sl = (slice(760, 890), slice(W // 3 + 8, 2 * W // 3 - 8))
    midb = float(np.median(bo[_mid_sl]))
    sk = []
    for i in range(12):
        sl = (slice(460, 610), slice(int(i * W / 12) + 10, int((i + 1) * W / 12) - 10))
        ci = float(np.hypot(np.median(ai[sl]), np.median(bi[sl])))
        co = float(np.hypot(np.median(ao[sl]), np.median(bo[sl])))
        ho = float(np.degrees(np.arctan2(np.median(bo[sl]), np.median(ao[sl]))) % 360)
        sk.append((float(np.median(Li[sl])), ci, co, ho))
    s50 = min(sk, key=lambda t: abs(t[0] - 50.0))
    skinC = s50[2] / max(s50[1], 1e-6)
    hh = [t[3] for t in sk if t[3] < 200]
    hsp = max(hh) - min(hh)
    xs = [int(v / 100.0 * (W - 1)) for v in range(0, 101, 5)]
    ys = [float(np.median(Lo[_ABS, max(0, x - 2):x + 3])) for x in xs]
    bad_mono = [i for i in range(1, len(ys)) if ys[i] < ys[i - 1] - 0.05]
    # ★ 每条锁都报**实际输入 L\***（探针降电平后，"斜坡位置 v" 不再等于 "输入 L\* v"）
    _i20, _i50, _i100 = inL(20), inL(50), inL(100)

    # ★★★ ① 是**回归锁**，不是"大师白端带"：合成 L*100 探针（绝对电平条）与真实成片的
    #   高光分位不是同一个量（大师带 [91.70,93.45] 是 52 张成片的逐图 P25/P75）。
    #   锁值 = **dev 6cd00c8 实测**（10-18 重标）。跌破 ⇒ 白端又塌（历史上 88.7 就是那个病）。
    _T100, _T100_TOL = 98.1, 0.6
    check('★★★★★ 合成图 ①：**白端回归锁**（L*99.9 进 → 出 %.1f ± %.1f）' % (_T100, _T100_TOL),
          abs(L100 - _T100) <= _T100_TOL,
          '输入 L*%.1f（绝对电平条）-> 输出 L*%.1f' % (_i100, L100),
          '★ 它不是"大师白端带"（那是**真实成片**的高光分位 [91.70,93.45]；合成 L*100 探针'
          '不该拿它判）。本锁只抓"白端塌陷"：dev 基线 = %.1f，允许 ±%.1f；'
          '换卷/纸/扫描端点后必须重标。' % (_T100, _T100_TOL))
# ★★★★ ②④ 是**回归锁**：钉住 dev 6cd00c8 的探针读数（10-18 实测）。它们**不是**"对大师的目标"；
#   要对大师重定，得拿真实照片在同一 RAW 电平上重测（另立任务）。
    _T20, _T50 = 39.9, 90.7
    check('★★★★★ 合成图 ②：**暗部回归锁**（输入 L*20 的输出钉在 %.1f ± 3）' % _T20,
          abs(L20 - _T20) <= 3.0,
          '输入 L*%.1f -> 输出 L*%.1f（离输入有 %+.1f）' % (_i20, L20, L20 - _i20),
          '性质 = 回归锁（防"暗部先被压暗再交叉"复发）；值 = dev 6cd00c8 实测 %.1f。'
          '换卷/纸/化学后要按新链重测再改锁。' % _T20)
    check('★★★★ 合成图 ③：灰阶传递函数**单调无回折**',
          not bad_mono, '21 点采样，回折处 %s' % (bad_mono or '无'),
          '曲线回折 = 某些亮度区间"越亮越暗"，是影调映射的硬伤')
    check('★★★ 合成图 ④：**中调回归锁**（重标后 %.1f ± 2.5）' % _T50,
          abs(L50 - _T50) <= 2.5, '输入 L*%.1f -> 输出 L*%.1f' % (_i50, L50),
          '性质 = 回归锁；值 = dev 6cd00c8 实测（10-18）。“暗部上提”的**过头量**没有独立依据，'
          '所以只钉住、不改。')
    check('★★★★★ 合成图 ⑤：**中灰必须中性**（|b*|<=1.5）',
          abs(midb) <= 1.5,
          '18pct 灰（线性 0.184，输入 L*' + ('%.1f' % inL(50)) + '）输出 b* = ' + ('%+.2f' % midb),
          '纯中性的灰喂进去，出来必须是中性。它曾经是**已知缺陷**（b* −6.1，整条灰阶偏蓝）；'
          '病因在**上游（相纸/滤片）**，不在颜色层 —— 扫 `sat`/`white_level`/`gamma`/中性轴端点都修不动。'
          '这条现在是回归锁')
    check('★★★ 合成图 ⑥：**肤色块彩度倍率**（报告，不设界 —— 见说明：这是"探针性质"不是"风格靶"）',
          True, '肤色块(C*20) -> 出 C*%.1f（x%.2f）' % (s50[2], skinC),
          '★ **10-09 夜定的界（<=1.40）已撤销，理由如下（这是一处真实冲突，不是妥协）**：'
          '· 合成图探针只量「**变换对探针的放大倍率**」—— 它**不知道鹿井的输出水平该是多少**；'
          '· **鹿井成片 747 张（统一尺子）**：45° 带彩度中位 **19.0**（我们 10.3）、'
          '  **暖区−冷区彩度差 +7.28**（我们 +1.05，= 冷暖对比被砍没了）；'
          '· 配方（第三方教程）说"橙色**饱和略降**" —— 但用户明确裁定「**以鹿井的成片为标准**」，'
          '  且项目调研早已记过「橙色饱和度各家方向相反 = **两种美学**，不是谁错」；'
          '· ⇒ 把探针倍率当风格验收标准是**范畴错误**（审计 A2：靶的可辨识性）。'
          '· 落值：`band_gain[1]` 0.0 → **0.9**（接近原值 0.949）⇒ 45° 带 10.3 → **17.2**（靶 19.0）、'
          '  暖冷 dC 1.05 → **4.77**（靶 7.28），逐张 +4.77/+7.03/+3.32（无一变差）。'
          '⚠ 教训：**配对锚（好不好看）与语料锚（像不像鹿井）是两把不同的尺子，不能互相替代。**')
    check('★★ 合成图 ⑦：肤色色相随明度的漂移钉住（现状 %.0f°，已从 36° 改善到 15°）' % hsp,
          hsp <= 25.0,
          '12 档肤色块（**降电平后实际输入 L\\* %.1f~%.1f**，跨度 %.1f）输出的色相跨度 = %.1f°'
          % (min(t[0] for t in sk), max(t[0] for t in sk),
             max(t[0] for t in sk) - min(t[0] for t in sk), hsp),
          '同一个 Lab 色相 55° 的肤色块，**只改明度**，输出色相漂 %.0f° => '
          '一张脸的亮处与暗处会是两种颜色。这是"肤色难看"的另一半原因。'
          '⚠ **探针降电平后这 12 档的输入 L\\* 跨度也变小了** ⇒ 跨度类读数与旧探针不能直接比；'
          '要比就得比"每 10 L\\* 的色相漂移"（真实成片上的对应量见 '
          'E:\\ds\\photo\\测试台与肤色复核_1011\\ 的报告）' % hsp)

# ---- ★★★ 中性轴（物理层的验收仪器）----
#   **喂纯中性的灰进去，出来必须是中性**（唯一无内容争议的判据）；⚠ 配方允许"暗部偏青蓝"
#   ⇒ L20 单独放宽；同时报**有符号色相**（方向盲的标量会把肤色推成发绿）。
    _gr = [ramp(v) for v in (20, 50, 70, 100)]
    _bs, _as_, _hs, _ils = [], [], [], []
    for _v in (20, 50, 70, 100):
        _x = int(np.clip(_v / 100.0 * (W - 1), 0, W - 1))
        _sl = (_ABS, slice(max(0, _x - 3), _x + 4))
        _bs.append(float(np.median(bo[_sl])))
        _as_.append(float(np.median(ao[_sl])))
        _hs.append(float(np.degrees(np.arctan2(np.median(bo[_sl]), np.median(ao[_sl]))) % 360))
        _ils.append(inL(_v))
    _worst = min(_bs[1:])                       # 中调/高光里最负的那个（最蓝）
    check('★★★ 合成图 ⑧：**中性轴**全貌（中调/高光现状最蓝 b*%+.1f；中灰那条已是硬界限⑤）' % _worst,
          _worst >= -3.0,
          '纯中性输入的输出 b*：%s'
          % ' ｜ '.join('L*%.0f %+.2f' % (a, b) for a, b in
                        zip(_ils, _bs)),
          '**喂纯中性进去，出来必须中性**（b*≈0）。10-09 实测整条灰阶偏蓝、中调最重 '
          '⇒ 与"配对参照"和"中灰 RGB B 最高"三方一致。'
          '⚠ **配方允许"暗部偏青蓝"** ⇒ L20 那一档单独放宽（−1~−3 算合格）；'
          '中调/高光该收到 |b*|≤1。'
          '★ 病因已定位在**上游（胶片/相纸/扫描）**：把 zone_abs/sh_abs/hi_abs 全清零，'
          '中性轴几乎不动 ⇒ **不是颜色层干的**。修法 = 先查"中性查表(相纸,光源,底片)"对不对，'
          '而不是硬掰 `print_y_filter_shift`')
    check('★★★ 合成图 ⑨：报告**有符号色相**（审计 A4：方向盲的标量会把肤色推成发绿）',
          len(_hs) == 4, '灰阶四档的有符号色相 h_ab = %s'
          % ' '.join('%d°' % v for v in _hs),
          '只用"彩度/占比"这类标量做决策，**分不出偏橙还是偏绿** —— 10-09 就是这么把肤色'
          '推绿的。以后每个彩度类指标都必须**成对**报（有符号色相 + 彩度）')


def t_r1_neutral_end_1013():
    r"""★★★★ R1「**中性彩度的黑端收窄比**」入自检。

        R1 = C*(L\*0-20) ÷ C*(L\*20-40)，中性像素（C\* <= 8）按 L\* 分档取中位
        判据：**整批中位 ∈ [0.538, 0.837]** = 大师**逐图并集** [P25, P75]（`AGENTS §一.5`）

    ★★ 三条限制钉在**解算器**里：① 分母必须**相邻档**；② **先逐图算比、再取中位**；
       ③ **只能判整批**（没有"单张超界"的出口）。
    ⚠⚠ 合成图表探针恒 ~0.40（低于带下界）⇒ 它只当**回归锁**（"谁动了预设/引擎，这里必须动"）。
    """
    import numpy as _np

    from . import color as _cl, presets as _pr, targets as _ts


    def _mk(l0, c0, l1, c1, n=60):
        lab = _np.zeros((2, n, 3), _np.float64)
        lab[0, :, 0], lab[0, :, 1] = l0, c0
        lab[1, :, 0], lab[1, :, 1] = l1, c1
        return lab

    _rA = _r1_per_image(_mk(10.0, 5.0, 30.0, 20.0), c_max=1e9)
    _rB = _r1_per_image(_mk(10.0, 20.0, 30.0, 10.0), c_max=1e9)
    check('★★★★ R1 公式：`C*(L*0-20) / C*(L*20-40)` 逐位对（手搓 Lab 5/20 -> 0.250、20/10 -> 2.000）',
          abs(_rA - 0.25) < 1e-12 and abs(_rB - 2.0) < 1e-12,
          '实测 %.12f / %.12f' % (_rA, _rB),
          '公式被改过（分子分母档位、或比值方向）⇒ 拿它判的任何数都不作数')


    _lab5 = _np.zeros((5, 80, 3), _np.float64)
    for _i, (_lo, _c) in enumerate(((0.0, 3.0), (20.0, 6.0), (40.0, 60.0),
                                    (60.0, 60.0), (80.0, 60.0))):
        _lab5[_i, :, 0], _lab5[_i, :, 1] = _lo + 5.0, _c
    _r1v = _r1_per_image(_lab5, c_max=1e9)
    _a1v = 3.0 / max(6.0, 60.0, 60.0)
    check('★★★★ 限制①：R1 的分母是**相邻档**(20-40) —— 不是"五档最大值"（那是 A1 黑端）',
          abs(_r1v - 0.5) < 1e-12 and abs(_a1v - 0.05) < 1e-12 and abs(_r1v - _a1v) > 0.4,
          'R1=%.6f  A1黑端=%.6f（两者分子相同、分母不同）' % (_r1v, _a1v),
          '两条尺子被混成一个 ⇒ `AGENTS §一.5` 的界 [0.538,0.837] 会被套到 A1 上（或反过来）')


    _b1 = _mk(10.0, 1.0, 30.0, 4.0)
    _b2 = _mk(10.0, 8.0, 30.0, 4.0)
    _b3 = _mk(10.0, 1.0, 30.0, 1.0)
    _bt = _r1_batch([_b1, _b2, _b3])
    _med_first = 1.0 / 4.0                      # 先取中位再相除 = median(1,8,1)/median(4,4,1)
    check('★★★★★ 限制②：**先逐图算比、再取整批中位**（不是先取中位再相除）',
          abs(_bt['median'] - 1.0) < 1e-12 and abs(_med_first - 0.25) < 1e-12,
          '逐图再中位=%.4f ｜ 先中位再相除=%.4f' % (_bt['median'], _med_first),
          '顺序写反了 ⇒ 每一批 R1 都可能是另一个数（差别只在"图与图不同"时才现形，'
          '合成图上恒等 ⇒ 所以必须用手搓数组钉）')

    # ★ 无效图必须**剔出 n**（不剔就会拿 nan 比大小 ⇒ 判定悄悄失效）
    _bn = _r1_batch([_b1, _np.zeros((2, 5, 3)), _b2])
    check('★ 中性像素不够的图 ⇒ 标 `nan` 且**不计入 n**（`n_bad` 记着）',
          _bn['n'] == 2 and _bn['n_bad'] == 1 and not _np.isfinite(_bn['per_image'][1]),
          'n=%d n_bad=%d per_image=%s' % (_bn['n'], _bn['n_bad'],
                                          ['%.2f' % x for x in _bn['per_image']]),
          'nan 混进中位 ⇒ 整批 R1 变 nan，而判定会静默走"量不了"（看起来像没事）')


    _W, _H = 768, 900
    _ch = _np.zeros((_H, _W, 3), _np.float64)
    _ch[0:300] = _np.linspace(0.0, 1.0, _W)[None, :, None]              # 灰阶斜坡
    for _i in range(16):                                                # 16 级灰块
        _ch[300:450, int(_i * _W / 16):int((_i + 1) * _W / 16)] = _i / 15.0
    for _i in range(12):                                                # 12 档肤色块
        _hh = _np.radians(55.0)
        _lb = _np.array([[[30.0 + _i * 5.0, 20.0 * _np.cos(_hh), 20.0 * _np.sin(_hh)]]], _np.float64)
        _ch[450:620, int(_i * _W / 12):int((_i + 1) * _W / 12)] = _np.clip(
            _cl.from_lab(_lb)[0, 0], 0.0, 1.0)
    for _i in range(12):                                                # 12 档色相环
        _hh = _np.radians(_i * 30.0)
        _lb = _np.array([[[60.0, 35.0 * _np.cos(_hh), 35.0 * _np.sin(_hh)]]], _np.float64)
        _ch[620:750, int(_i * _W / 12):int((_i + 1) * _W / 12)] = _np.clip(
            _cl.from_lab(_lb)[0, 0], 0.0, 1.0)
    _ch[750:900, 0:_W // 3] = 0.0
    _ch[750:900, _W // 3:2 * _W // 3] = 0.5
    _ch[750:900, 2 * _W // 3:] = 1.0
# ★ 与 `t_synth_transfer` 逐行同构（本体乘增益、顶部那几行不乘）。
    _ov = dict(_ts.scene_engine(None, stock=_PRESET, cfg=C))
    _ov['print_render.glare.active'] = False          # ← 见 docstring：numba RNG ⇒ 跨进程不可复现
    _labs = []
    for _g in _R1_PROBE_GAINS:
        _lin = _np.clip(_cl.s2l(_ch) * (_PROBE_SCENE_GAIN * _g), 0.0, None)
        _lin[750:750 + _PROBE_ABS_ROWS, :, :] = _cl.s2l(_np.linspace(0.0, 1.0, _W))[None, :, None]
        _labs.append(_cl.to_lab(_pr.render(_lin, _PRESET, C, overrides=_ov)))
    _pb = _r1_batch(_labs)
    _pv = _r1_verdict(_pb['median'], _pb['n'], _pb['iqr'])
    check('★★★★ R1 探针回归锁：固定图表 × 4 档增益的**批中位**钉在 %.4f ± %.4f' % (
              _R1_PROBE_KNOWN, _R1_PROBE_TOL),
          abs(_pb['median'] - _R1_PROBE_KNOWN) <= _R1_PROBE_TOL,
          '批中位=%.6f（n=%d ｜ 图间 IQR=%.6f）｜ 判定=%s（离界 %.3f vs 分辨力 %.3f）' % (
              _pb['median'], _pb['n'], _pb['iqr'], _pv['verdict'], _pv['dist'], _pv['res']),
          '预设/引擎动了而这把尺子没跟着动 ⇒ **尺子接线断了**（不是"画面对了"）。'
          '★ 反过来说：这个数**不是**生产口径（合成图表的 R1 恒低于真实照片带）'
          '⇒ 它只保证"尺子还活着"，**不许**拿它当"我们上靶了"')

# ★ 判定分级：**离界 < 分辨力 ⇒ 只报方向**（§一.5「不当缺陷」的可执行版）。
    _v_small = _r1_verdict(0.8703, 11, 0.175, band=(0.538, 0.837), master_iqr=0.299)
    _v_big = _r1_verdict(2.500, 400, 0.020, band=(0.538, 0.837), master_iqr=0.299)
    _v_in = _r1_verdict(0.700, 11, 0.175, band=(0.538, 0.837), master_iqr=0.299)
    check('★★★★★ n 不够时**只报方向、不当缺陷**：离界 < 分辨力 ⇒ `判不了`（不许红）',
          _v_small['verdict'] == '判不了（分辨力不够）'
          and _v_big['verdict'] == '真越界（分辨力足够）'
          and _v_in['verdict'] == '在带内',
          'n=11 离界 .033 -> %s（离界 %.3f vs 分辨力 %.3f）｜ 大样本离界 .663 -> %s ｜ 带内 -> %s' % (
              _v_small['verdict'].split('（')[0], _v_small['dist'], _v_small['res'],
              _v_big['verdict'].split('（')[0], _v_in['verdict']),
          '把"名义出带"直接当缺陷 ⇒ 今天生产路径三条链**全红**，而三条**一条都没被判出来**'
          '（`AGENTS §一.1` 的"误判 14/36 格"就是这么来的）')
    check('★★ 判定里**带着分辨力**（两个数一起报，不许只报"在带内/越界"）',
          _np.isfinite(_v_small['res']) and _v_small['res'] > 0.0,
          '分辨力=%.4f ｜ 公式 = 2*sqrt((大师逐图IQR/sqrt12)^2 + (我们逐图IQR/sqrt n)^2)' % _v_small['res'],
          '只报"越界"不报分辨力 ⇒ 又把过时口径"组中位范围"当界用了')


def _main():
    groups = [
        ('胶片风格：预设集 = 已知清单', t_presets),
        ('胶片风格：改一根旋钮真的改画面（`overrides` 接线）', t_override_bites),
        ('直方图（LR 画法：亮度 + RGB 叠加 + 5 个区）', t_hist),
        ('靶按预设分组', t_targets),
        ('可调键：config 里真的接上了（防"假旋钮"）', t_config_keys),
        ('场景判据：四轴 / 只在线性域判过曝 / 覆盖只加不减', t_scene),
        ('契约：颜色层在引擎之后 + 名字不认得要报错', t_contract),
        ('段缓存：同参数命中、换风格不命中', t_cache),
        ('服务：路由只剩该有的那几条', t_routes),
        ('★★★★★ 删层纪律：影调层 / 肤色层 / 认人认脸 **真的删了**', t_dropped_layers),
        ('★ 「人在哪」+ 光位：**只用低开销那条** / 判不出要弃权', t_person_light),
        ('★★★★★ 10-08 评审防复发：接线断了必须有人喊（缓存键/透传/报告同形）', t_review_1008),
        ('★★★★★ 合成测试图的传递函数（内容无关的回归基准：出口白/暗部不过冲/曲线单调）',
         t_synth_transfer),
        ('★★★★ R1「黑端收窄比」入自检（`AGENTS §一.5` 唯一能当靶的尺子；只报方向不当缺陷）',
         t_r1_neutral_end_1013),
        ('★★★★★ 连续调制 `{"by":…}` + **光位不驱动影调**（10-09）', t_by_modulation),
        ('★★★★★ 工具层：基线**只有一个来源** + `targets.json` 文件指纹（10-12 T4/T6）',
         t_tooling_1012),
    ]
    for title, fn in groups:
        print('[%s]' % title)
        try:
            fn()
        except Exception as e:                                    # noqa: BLE001
            check('★ 这一组跑到底（没炸）', False,
                  '%s: %s' % (type(e).__name__, str(e)[:160]),
                  '这一组里有一个异常没接住 —— 后面的检查全都没跑到')
    print('-' * 68)
    if FAIL:
        print('失败 %d 条：' % len(FAIL))
        for f in FAIL:
            print('  ✗ ' + f)
        return 1
    print('全部通过')
    return 0


# ---- 1. 胶片风格（已知的预设）----

# ★ 锁的是"**不许出现计划外的预设**"（静默多出来的第 N 条 = 有人手滑落了一份），
#   不是"只能有一条"。加新预设 = **有意的动作** ⇒ 必须同时把它写进这个清单。
_PRESETS = (_PRESET, '滨田预设', 'xhs预设')


def t_presets():
    """已知预设的接线 —— 钉的都是"静默失效"那一类：字段名写错 / 被卷标签冲掉 / 假旋钮。"""
    ns = presets.names()
    check('预设目录 = 已知清单（%s）' % '、'.join(_PRESETS), sorted(ns) == sorted(_PRESETS),
          '、'.join(ns) or '（空）',
          '10-15 从零重立：旧 10 条已删（分支 feature/kashii-create；原样留在 data/_archive/pre_clean_slate/）')
    check('它的 JSON 真在磁盘上', presets.has(_PRESET))
    # ★ 10-17：卷表变两条之后，`_DESC` 少写一条 ⇒ 界面那行说明是**空的**（不报错、只是空着）。
    #   同族坑：`_param_defs` 少写一条 ⇒ 滑杆静默消失。凡是"表驱动的显示"，都要钉"表覆盖全"。
    _miss = [n for n in ns if not str(presets.label_of(n)[1] or '').strip()]
    check('每条预设都有一句人话说明（`_DESC` 覆盖全）', not _miss,
          '缺：%s' % ('、'.join(_miss) or '（无）'),
          '漏写说明 ⇒ 右栏那行字是空的，界面上看不出是"没写"还是"这条预设没内容"')

# ★★ 关键字段必须**真的落到 params 上**（`GrainParams`/`HalationParams` 是普通 dataclass、
#   没有 `__slots__` ⇒ 字段名写错**不报错**，只会静默多出一个没人读的属性）
#   ⇒ ★★★ 必须验 **vendor 真正读的那个名字**，不能验"我们写进去的那个"（那是假绿）。
    p = presets.digested(_PRESET)
    d = presets.load_raw(_PRESET)
    check('颗粒规格真落到 vendor 实读字段（dev：`rms_granularity`，不是旧 `particle_area_um2`）',
          all(float(v) > 0.0 for v in p.film_render.grain.rms_granularity)
          and not hasattr(p.film_render.grain, 'particle_area_um2'),
          'rms=%r ｜ 旧字段残留=%s'
          % (tuple(p.film_render.grain.rms_granularity),
             hasattr(p.film_render.grain, 'particle_area_um2')),
          'dev 把颗粒改成 48 µm RMS 粒度高；旧 particle_area_um2 已删，写了不报错也不生效')
    check('颗粒 blur / uniformity 真落到 params（= 预设值）',
          abs(float(p.film_render.grain.blur) - float(d['grain']['blur'])) < 1e-9
          and tuple(float(v) for v in p.film_render.grain.uniformity)
          == tuple(float(v) for v in d['grain']['uniformity']),
          'blur %.2f vs %.2f ｜ uniformity %r'
          % (p.film_render.grain.blur, d['grain']['blur'],
             tuple(p.film_render.grain.uniformity)),
          '字段名对不上 ⇒ 写进去的是没人读的属性（静默回引擎默认）')
    check('光晕强度真落到 params（不被卷的抗晕层标签冲掉）',
          abs(float(p.film_render.halation.halation_strength[0]) * 100.0
              - float(d['halation']['halation_strength'][0])) < 1e-6,
          '%.4f vs %.1f' % (p.film_render.halation.halation_strength[0] * 100.0,
                            d['halation']['halation_strength'][0]),
          '`apply_stocks_specifics=True` 会按卷的抗晕层把它冲回出厂 ⇒ 必须保持 False')

# ★★ 光晕幅度必须**等于引擎按本卷抗晕层类别给的值**（`params_builder._HALATION_PRESETS`）。
#   钉"按类别取表"而不是钉一条固定的数字带：旧的 `2~8` 是**弱抗晕卷**的带，
#   换成强抗晕卷（`strength = 1.5/0.5/0`）后会**误判为越界**。
    _hs = tuple(round(float(v) * 100.0, 4) for v in p.film_render.halation.halation_strength)
    _amt = float(p.film_render.halation.halation_amount)
    _info = p.film.info
    from spektrafilm.runtime.params_builder import _HALATION_PRESETS   # 惰性：见文件头的守卫说明
    _want = _HALATION_PRESETS.get((str(_info.use), str(_info.antihalation)))
    _exp = (tuple(round(float(v) * 100.0, 4) for v in _want['strength']) if _want else None)
    check('★ 光晕幅度 = 引擎按本卷抗晕层类别给的值（%s/%s）'
          % (getattr(_info, 'use', '?'), getattr(_info, 'antihalation', '?')),
          bool(_exp) and all(abs(a - b) < 5e-3 for a, b in zip(_hs, _exp)),
          'R/G/B = %s ｜ 表值 %s ｜ 倍率 %.2f' % (_hs, _exp, _amt),
          'R 通道典型值：强 AH 1.5、弱 AH 8、无 AH 30（`_HALATION_PRESETS`）；'
          '超 8 ⇒ 脸上高光泛红（GUI 人像提示原话）')
    check('★ 光晕强度倍率在带内（1.0~1.4）', 1.0 - 1e-9 <= _amt <= 1.4 + 1e-9, '%.2f' % _amt,
          '`halation_amount` 是人像最容易翻车的一根：1.2~1.3 是上限，2 以上脸上高光泛红')

# ★★ 「整张冷暖基准」的落点 = 印相端校色滤片（负片→印相体系里的"白平衡"）。
    _trim = float(getattr(C, 'PRESET_FILTER_M_TRIM', 0.0))
    _sim = d['simulation']
    _wb = (round(float(p.enlarger.y_filter_shift), 3), round(float(p.enlarger.m_filter_shift), 3))
    _wbb = (round(float(_sim['print_y_filter_shift']), 3),
            round(float(_sim['print_m_filter_shift']) + _trim, 3))
    check('★ 整张冷暖基准落在印相滤片上（Y/M **真进 params**，不是假旋钮）', _wb == _wbb,
          'params y=%.3f m=%.3f ｜ JSON y=%.3f m=%.3f' % (_wb + _wbb),
          'JSON 写了但 params 里不是那个数 ⇒ 又是一组"改了没反应"的假旋钮')

# ★★★★ 配平基准**走引擎数据库**；绝对锚 = **本链当前那三联**（换卷/换纸必须来这里同步）。
#   现值：`kodak_portra_endura` / TH-KG3 / `kodak_portra_400` ⇒ [C,M,Y] = [0, 51.230, 52.240]
#   ★ 10-18 引擎升级（dev 6cd00c8）重写了 `neutral_print_filters.json`：全库每格 −0.30±0.02
#     （旧 [0, 51.568, 52.534]）⇒ 锚跟着 DB 走，不是我们改值。
#   （**读 DB 顺序是 c,m,y**；路径 data/filters/neutral_print_filters.json）
    check('★★ 配平基准走引擎数据库（不是一对硬编码常数）',
          bool(p.settings.neutral_print_filters_from_database),
          'db=%s' % p.settings.neutral_print_filters_from_database,
          '关掉就会退回"一对常数发给不同底片" —— 那正是 18%% 灰偏色的原因')
    _nm, _ny = 51.230, 52.240
    check('★★ 而且 Y/M 不许对调（绝对锚：portra_endura/TH-KG3/portra_400 ⇒ M %.3f / Y %.3f）'
          % (_nm, _ny),
          abs(float(p.enlarger.m_filter_neutral) - _nm) < 5e-3
          and abs(float(p.enlarger.y_filter_neutral) - _ny) < 5e-3,
          'M=%.3f Y=%.3f' % (p.enlarger.m_filter_neutral, p.enlarger.y_filter_neutral),
          '对调了整张偏色；换卷/换纸都要来这里更新锚值（路径：data/filters/neutral_print_filters.json）')

    # 负片 / 相纸 profile 名拼错 = 渲染那一步直接崩
    bad = []
    for n in ns:
        try:
            presets.pe_of(n)
            assert presets.film_of(n) and presets.paper_of(n)
        except Exception as e:                                    # noqa: BLE001
            bad.append('%s(%s)' % (n, e))
    check('负片 / 相纸名字都能被 spektrafilm 认得', not bad, '、'.join(bad),
          'profile 名拼错 ⇒ 渲染那一步 FileNotFoundError')


def t_override_bites():
    """★★ `overrides` 必须真的改得动画面 —— 它是**整份替换**（`presets.render` 的契约），
    少传基线会让 `print_render.density_curves_morph` **整根静默死掉**（表现是"改完看不出变化"）。"""
    from . import targets
    lin = _lin_from_disp(_gray_img(seed=23))
    ov0 = dict(targets.scene_engine({}, stock=_PRESET, cfg=C))
    base = presets.render(lin, _PRESET, C, overrides=ov0)
    ov1 = dict(ov0)
    ov1['film_render.dir_couplers.amount'] = 0.5
    alt = presets.render(lin, _PRESET, C, overrides=ov1)
    _d = float(np.max(np.abs(alt - base)))
    check('★★ 改一根旋钮（`couplers.amount` 1.0 → 0.5）⇒ 画面真的变', _d > 0.005,
          'max|Δ| = %.4f' % _d,
          '没变化 ⇒ overrides 没接上（最常见的成因：只传了要动的那一根、把基线丢了 ⇒ morph 静默死）')

# ---- 2. 删层纪律（真的删了，不是关开关）----

def t_dropped_layers():
    r"""★★★ **整段删掉的层**（不是关开关）—— 防的是"关了开关但代码还在、谁哪天又接回去"：
    模块 import 不进来 · config 的键不剩 · pipeline 里不出现那些名字 · 路由 `/styles` 没了。"""
    import importlib
    import inspect

    for m in ('tone', 'face', 'facegain', 'region', 'grade'):
        try:
            importlib.import_module('svFilm.%s' % m)
            _ok = False
        except ModuleNotFoundError:
            _ok = True
        check('★ 模块 `svFilm.%s` 已经删掉（import 不进来）' % m, _ok, '',
              '还在 ⇒ 影调层 / 肤色层 / 认人那套随时会被接回链上')

# ★ 例外名单：`PERSON_*` / `SCENE_BACK_*` / `SCENE_LIGHT_RIM_*`（光位判据，重新加回来的）
#   + `FACE_FALLBACK_*`（找脸兜底 —— SV 10-16 令「提高找人找脸准确率」）。
#   ⚠ 别把 `FACE_*` 整段解禁 —— 那一段是**删掉的肤色层**，解禁 = 那层随时能被接回来。
    _EXEMPT = ('PERSON_', 'SCENE_BACK_', 'SCENE_LIGHT_RIM_', 'FACE_FALLBACK')
    _bad = [k for k in dir(C) if k.startswith(('TONE_', 'FACE_', 'SKIN_', 'GRADE_',
                                               'ANCHOR_', 'SCENE_SHOT_'))
            and not k.startswith(_EXEMPT)]
    _bad += [k for k in ('STYLE', 'PRESET_MID_SHIFT', 'TARGET_BLACK_FLOOR_L',
                         'TARGET_HI_FLOOR_L', 'GRADE_REGION_SCOPE', 'GRADE_SCOPE')
             if hasattr(C, k)]
    check('★★ config 里那几个键一个都不剩（例外：`PERSON_*` / `SCENE_BACK_*` / '
          '`SCENE_LIGHT_RIM_*` / `FACE_FALLBACK*`）',
          not _bad, '还剩: %s' % (_bad or '无'),
          '留着就是"没人读的开关"—— 改了没反应（本项目第 4 类坑）')

    _src = '\n'.join(l.split('#')[0] for l in
                     inspect.getsource(pipeline.run_from).splitlines())
    _hit = [n for n in ('tone', 'facegain', 'FACE_STEP_ENABLE', 'TONE_ENABLE',
                        'GRADE_SCOPE', 'render_with_face', 'skin_gap',
                        'grade.apply', 'GRADE_ENABLE') if n in _src]
    check('★★ pipeline **代码里**不再出现那几层的名字', not _hit,
          '命中: %s' % (_hit or '无'),
          '又接回去了 ⇒ 这不是"删掉"，是"关开关"')

    _ssrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              'service.py'), encoding='utf-8').read()
    check('★ 服务里 `/styles` 路由确实删了', "u.path == '/styles'" not in _ssrc)


# ---- 2.5 「人在哪」+ 光位（只用低开销那条）----

def t_person_light():
    r"""光位（`back` 轴）—— 输入只用 `person.py` 那个 **~0.3 s 的粗"人在哪"**（不跑人脸检测）。

    ★★ **判不动时必须弃权**（`None`），**不许硬给一个"顺平光"** —— 那会一测就翻。
    """
    import inspect
    import re as _re

    from . import person as _pmod
    from . import scene

    # ① ★★★ 10-16 新契约：**两档都允许，高开销那条仍然禁止**。
    #   允许：mediapipe `selfie_multiclass`（人在哪 + 脸上的皮肤，256×256，~20 ms）
    #        + **YuNet 人脸定位器**（~20 ms，SV 10-16 令「提高找人找脸准确率」的兜底）。
    #   禁止：birefnet / onnxruntime / landmark —— 那条 ~7 s，就是这个契约存在的理由。
    _raw = inspect.getsource(_pmod)
    _code = _re.sub(r'r?"""[\s\S]*?"""', '', _raw)
    _code = '\n'.join(l.split('#')[0] for l in _code.splitlines())
    _bad = [n for n in ('birefnet', 'onnxruntime', 'face_skin',
                        'landmark', 'FaceLandmarker') if n in _code]
    check('★★★ `person.py` **不许把高开销那条接回来**（birefnet / onnxruntime / landmark）',
          not _bad, '命中: %s' % (_bad or '无'),
          '那条 ~7 s ⇒ 不要那个开销')
    check('★★ 低开销那条 = mediapipe `selfie_multiclass`（输入固定 256×256）',
          'selfie_multiclass' in _code and 'SEG_SIDE = 256' in _code,
          '模型 = %s' % os.path.basename(_pmod.SELFIE))
    # ★★ 找脸兜底的**契约**：定位器只许 YuNet；**框只用来指路**（还要过同一个分割模型 ⇒
    #    口径与一级同源，锚不用重标）。⚠ 直接把框当测光区会系统性暗 0.13~0.28 EV（见文档串）。
    check('★★★ 人脸定位器**只许 YuNet**（`cv2.FaceDetectorYN`，~20 ms）',
          'FaceDetectorYN' in _code and 'yunet' in _code,
          '模型 = %s' % os.path.basename(getattr(_pmod, 'YUNET', '') or ''))
    check('★★★ 定位框**只用来指路**：裁块后仍走同一个分割模型（口径与一级同源）',
          'face_mask_in' in _code and 'face_fallback' in _code,
          '直接把检测框当测光区 ⇒ 比皮肤掩膜系统性暗 0.13~0.28 EV，会把调好的图推亮')
    check('★ 模型文件在仓库里（不靠 pip / 不靠下载）',
          os.path.exists(_pmod.SELFIE) and os.path.exists(getattr(_pmod, 'YUNET', '')),
          '%s ／ %s' % (_pmod.SELFIE, getattr(_pmod, 'YUNET', '')))


    H, W = 120, 160
    d = np.full((H, W, 3), 0.25)
    d[: H // 3] = 0.95                                  # 上方一大片亮 ⇒ 光在背后
    person = np.zeros((H, W), np.float64)
    person[H // 2:, W // 3: 2 * W // 3] = 1.0
    L = color.to_lab(d)[..., 0]

    _b1, _r1 = scene._light_position(d, L, person, C)
    check('★★ 上亮下暗、主体在下方 ⇒ 判成**逆光族**（正逆光 / 侧逆光）',
          _b1 in ('正逆光', '侧逆光'),
          'back=%s  E_tb=%+.1f E_bg=%+.1f clip_bg_blk=%.2f' % (
              _b1, _r1['E_tb'], _r1['E_bg'], _r1['clip_blk_off']),
          '判成面光 ⇒ 判据没接上（v3 的第一条触发条件是 E_tb ≤ −TS）')

    d2 = np.full((H, W, 3), 0.2)
    p2 = np.zeros((H, W), np.float64)
    p2[H // 3: 2 * H // 3, W // 3: 2 * W // 3] = 1.0
    d2[H // 3: 2 * H // 3, W // 3: 2 * W // 3] = 0.95   # 最亮的就是主体自己
    _b2, _r2 = scene._light_position(d2, color.to_lab(d2)[..., 0], p2, C)
    check('★ 最亮的就是主体自己 ⇒ **面光 / 顺平光**', _b2 == '面光/顺平光',
          'back=%s  E_tb=%+.1f E_bg=%+.1f spike=%.1f' % (
              _b2, _r2['E_tb'], _r2['E_bg'], _r2['spike']))

# ③ ★★★ 弃权必须是合法输出
    _b3, _r3 = scene._light_position(d, L, None, C)
    check('★★★ 没有"人在哪" ⇒ **弃权**（返回 None，不许硬判面光）',
          _b3 is None and _r3['why'], 'why=%s' % _r3['why'],
          '硬判 ⇒ 下游会照着一个假标签去改参数')
    tiny = np.zeros((H, W), np.float64)
    tiny[0:8, 0:8] = 1.0
    _b4, _r4 = scene._light_position(d, L, tiny, C)
    check('★★ 主体太小 ⇒ 同样**弃权**（`SCENE_BACK_MIN_SUB_PX` 兜着）',
          _b4 is None and _r4['why'], '主体 %d px · why=%s' % (int(tiny.sum()), _r4['why']))
    dark = np.zeros((H, W, 3))                          # 全黑 ⇒ 极端亮度，判不动
    _b5, _r5 = scene._light_position(dark, color.to_lab(dark)[..., 0], person, C)
    check('★★ 整张太暗 ⇒ **弃权**（`extreme_luma`）', _b5 is None, 'why=%s' % _r5['why'])
    allp = np.ones((H, W), np.float64)                  # 掩膜盖满 ⇒ 没有背景可比
    _b6, _r6 = scene._light_position(d, L, allp, C)
    check('★★ 只有主体、没有背景 ⇒ **弃权**（`no_subject`）',
          _b6 is None, 'why=%s' % _r6['why'])


    sc = scene.classify(d, C)
    check('★★ 不给"人在哪" ⇒ 只有光位弃权（`-`），exp / span / overwhite 照常',
          sc['back'] is None and sc['key'].split('|')[3] == '-' and sc['exp'] and sc['span'],
          'key=%s' % sc['key'])
    sc2 = scene.classify(d, C, person=person)
    check('★ 给了"人在哪" ⇒ key 里光位那一位不再是 `-`',
          sc2['key'].split('|')[3] != '-', 'key=%s' % sc2['key'])
    _need = ('E_lr', 'E_tb', 'E_span', 'E_bg', 'spike', 'z_span', 'clip_pct',
             'clip_blk_off', 'hot_blk_off', 'has_spike_src', 'person_pct', 'cx_diff', 'why')
    _miss = [k for k in _need if k not in sc2['raw']]
    check('★ 光位判据的 raw 里带着**全部诊断量**（事后标定阈值用）', not _miss,
          '缺: %s' % (_miss or '无'))

# ---- 3. 契约 ----



def t_hist():
    """直方图工具：LR 那四条曲线 + 5 个区 + 裁切三角 —— 图画错了比数字错更危险。"""
    from . import hist


    c, clip = hist.channels(_gray_img(seed=31))
    check('★ 四条直方图曲线（亮度 + R/G/B）：各 256 bin、落在 0~1、无 NaN',
          len(c) == 4 and all(len(x) == 256 for x in c)
          and all(np.all(np.isfinite(x)) and x.min() >= 0.0 and x.max() <= 1.0 + 1e-9 for x in c),
          '亮度最大 %.2f' % float(np.max(c[0])))
    check('亮度那条跟 RGB 三条不是同一条（真算了，不是复制）',
          float(np.max(np.abs(c[0] - c[1]))) > 0.01)

    g = np.asarray(_gray_img(seed=31), np.float64)
    ys = hist._codes(hist._luma(g) / 255.0).ravel()   # ★ 用实现自己的口径复算（四舍五入）
    cnt = np.bincount(ys, minlength=256).astype(np.float64)
    want = (cnt.max() / (cnt.sum() * hist.YMAX_RATIO)) ** hist.Y_GAMMA
    got = float(np.max(hist.channels(g)[0][0]))      # [0]=四条曲线, [0][0]=亮度那条
    check('★★ 纵轴是**固定口径**（满格 = 单档占画面 %.0f%%），不是每张自己归一'
          % (hist.YMAX_RATIO * 100),
          abs(got - min(want, 1.0)) < 1e-6, '实到 %.4f  应到 %.4f' % (got, min(want, 1.0)),
          '改成"按每张图自己的最大值归一"的话每张刻度都不一样 ⇒ 跨图不可比')
    check('★ 同一张图喂两次峰值完全一样（口径稳定）',
          abs(float(np.max(hist.channels(g)[0][0])) - got) < 1e-12)


    bk = hist.channels(np.zeros((32, 32, 3)))[1]
    wh = hist.channels(np.ones((32, 32, 3)))[1]
    gy = hist.channels(np.full((32, 32, 3), 0.5))[1]
    check('★ 全黑 ⇒ 阴影端要亮（LR 里那个左上的三角）', bk['lo']['any'] > 0.9,
          '%.3f' % bk['lo']['any'])
    check('★ 全白 ⇒ 高光端要亮（右上的三角）', wh['hi']['any'] > 0.9, '%.3f' % wh['hi']['any'])
    check('★ 全白 ⇒ 三个通道都到端点（`which` 应为 R+G+B）', wh['hi']['which'] == 'R+G+B',
          wh['hi']['which'])
    check('中间灰 ⇒ 两个三角都不亮',
          gy['lo']['any'] < 5e-4 and gy['hi']['any'] < 5e-4,
          '%.4f / %.4f' % (gy['lo']['any'], gy['hi']['any']))

# ③ ★★★ 老判据数 `luma == 255`（= 三通道同时 255）⇒ 会漏掉"只有单通道到端点"整类。
    _b = np.zeros((16, 16, 3)); _b[..., 0] = 0.6; _b[..., 1] = 0.6; _b[..., 2] = 1.0
    _sb = hist.clip_stats(_b)
    _old = float((hist._codes(hist._luma(_b) / 255.0) == 255).mean())
    check('★★ 只有蓝通道到端点时必须报出来（老"纯白"判据会漏成 0）',
          _sb['hi']['which'] == 'B' and _sb['hi']['any'] > 0.9 and _old == 0.0,
          'which=%s any=%.2f  老判据=%.4f' % (_sb['hi']['which'], _sb['hi']['any'], _old))
    check('★ 端点的颜色 = 被裁通道的混色（R+G→黄、三个→白、没有→灰）',
          hist._clip_tint('R+G') == (255, 255, 151)
          and hist._clip_tint('R+G+B') == (255, 255, 255)
          and hist._clip_tint('') == hist.TRI_OFF,
          '%s / %s' % (hist._clip_tint('R+G'), hist._clip_tint('R+G+B')))
    check('★ 档位是四舍五入（与 `color.display_to_u8` 同口径，不是截断）',
          int(hist._codes(np.array([254.6 / 255.0]))[0]) == 255
          and int(hist._codes(np.array([254.4 / 255.0]))[0]) == 254)


    im = hist.draw(_gray_img(seed=33), w=400, h=160, title='自检')
    check('画得出来、尺寸对', im.size == (400, 160), str(im.size))
    im_w = hist.waveform(_gray_img(seed=33), w=400, h=180, title='自检')
    check('★ 波形图也画得出来（横轴＝画面左右，纵轴＝亮度）', im_w.size == (400, 180),
          str(im_w.size))
    pn = hist.panel(_gray_img(seed=35), w=400, title='自检')
    check('缩略图 + 直方图 一体也画得出来', pn.size[0] == 400 and pn.size[1] > 160, str(pn.size))
    pnw = hist.panel(_gray_img(seed=35), w=400, title='自检', mode='wave')
    check('★ 一体版换波形图也画得出来（`mode="wave"`）', pnw.size[0] == 400 and pnw.size[1] > 160,
          str(pnw.size))
    st = hist.stack([(None, '参照', c, clip)], w=400)
    check('★ 只喂曲线（画"一组片的平均直方图"）也画得出来', st.size == (400, 240), str(st.size))


    check('命令行入口在（`python -m svFilm.hist`）', callable(hist._main))


def t_targets():
    """靶（`targets.json`）：唯一那条预设必须有自己的靶，且数字来自它自己那批（钉**出处**）。"""
    from . import targets

    t = targets.for_stock(_PRESET)
    check('★ 唯一那条预设是**专属靶**（不是落回 `_default`）', bool(t['own']),
          'own=%s' % t['own'],
          '落回 `_default` ⇒ 靶文件中那条不见了，或名字对不上（静默用到别人的靶）')
    check('★ 靶带 `span`，且数字来自那 26 张（逐张 p95−p5 的中位 = 85.613）',
          'span' in t and abs(float(t.get('span', 0.0)) - 85.61) < 0.5,
          'span=%r' % t.get('span'),
          '对不上 ⇒ 靶被换成了别的语料（或 span 的口径改了没写进 `span_note`）')
    _lb = (targets.load().get('_lc_band') or {}).get('groups') or {}
    check('★ 大师 `lc` 带（`_lc_band`）三位都在（数据块没被清账顺手删掉）',
          set(_lb) == {'鹿井', '増田彩来', '滨田英明'}, '%s' % sorted(_lb),
          '`scorecard` 读它；丢了 ⇒ 局部对比那道尺子静默失效')


def t_by_modulation():
    r"""★★★ **连续调制** `{"by": <程度量>, "delta": Δ}` + **"光位不驱动影调"**这条设计约束。

    ① `{"by": …}` 必须**连续**、能落到**预设基线**、且**拿不到程度量 ⇒ 什么都不加**。
    ② 光位轴块里不许再出现影调参数 —— **离散档驱动全局影调 ⇒ 阶跃**。
    """
    from . import config as _C
    from . import presets as _PR
    from . import scene as _S
    from . import targets

    d = targets.load()
    se = d.get('_scene_engine') or {}

    # ---- ① 静态：`{"by": …}` 的名字必须是 `scene.DEGREES` 里的 ----
    bad_name = []
    for k, blk in se.items():
        if k.startswith('_') or not isinstance(blk, dict):
            continue
        for kk, vv in blk.items():
            if isinstance(vv, dict) and 'by' in vv and vv.get('by') not in _S.DEGREES:
                bad_name.append('%s→%s' % (k, vv.get('by')))
    check('★★ `targets.json` 里每个 `{"by": …}` 用的都是 `scene.DEGREES` 里的名字',
          not bad_name, '非法: %s' % (bad_name or '无'),
          '名字写错 ⇒ `_deg()` 恒返回 0 ⇒ 调制**静默失效**（这正是本项目最怕的那类坑）')

# ---- ② ★★★ 光位不许驱动影调：例外须登记在 `targets.json` 的 `_tone_allow`
#   （**离散档驱动全局影调 ⇒ 阶跃**；要开必须给**目检依据**）----
    _allow = (d.get('_tone_allow') or {}) if isinstance(d, dict) else {}
    off, allowed = [], []
    for k, blk in se.items():
        if not (isinstance(k, str) and (k.startswith('back=')
                                        or k.startswith('lighttype='))
                and isinstance(blk, dict)):
            continue
        for kk in blk:
            if 'chemistry' not in kk:
                continue
            if kk in (_allow.get(k) or []):
                allowed.append('%s.%s' % (k, kk))
            else:
                off.append('%s.%s' % (k, kk))
    check('★★★ 光位轴块（`back=` / `lighttype=`）里的影调参数**必须登记在 `_tone_allow`**',
          not off, '未登记: %s ｜ 已登记: %s' % (off or '无', allowed or '无'),
          '离散档驱动全局影调 ⇒ 阶跃。要开必须给出**目检依据**并写进 `_tone_allow`（见 §194 的原始证据）')

# ---- ③ 机制：注入一条 `{"by": ...}`（调**预设基线**、**逐通道** Δ），跑完还原 ----
#   ⚠ `back=*` 只在 `back` 判出来时命中 ⇒ 要"无条件"调制必须写进 `"*"` 层；Δ 必须**给列表**。
    key = 'film_render.halation.halation_strength'
    base = list(getattr(*_PR._walk(_PR._params_for(_PRESET, _C), key)))
    delta = [0.04, 0.02, 0.0]
    star = d.setdefault('_scene_engine', {}).setdefault('*', {})
    assert key not in star, '预计 `"*"` 层里没有这个键'
    star[key] = {'by': 'deg_back', 'delta': list(delta)}

    def _run(deg, hit=True):
        sc = {'back': ('侧光' if hit else None),
              'raw': {'light': ({'deg_back': deg} if deg is not None else {})}}
        return targets.scene_engine(sc, stock=_PRESET, cfg=_C).get(key)

    try:
        got = [_run(x) for x in (0.0, 0.25, 0.5, 1.0)]
        want = [[b + dd * x for b, dd in zip(base, delta)] for x in (0.0, 0.25, 0.5, 1.0)]
        check('★★★ `{"by": …}` **连续**：d=0/0.25/0.5/1 ⇒ 逐通道 = 基线 + d·Δ',
              all(all(abs(a - b) < 1e-9 for a, b in zip(g, w)) for g, w in zip(got, want)),
              '基线 %s ⇒ %s' % (base, [list(g) for g in got]),
              '不是线性 ⇒ 没做成"连续插值"（还是阶跃）')
        check('★★ `"*"` 层里的 `{"by": …}` **无条件生效**（光位判不出来时也照调）',
              _run(1.0, hit=False) == want[-1],
              'back=None 时 %s（应 %s）' % (_run(1.0, hit=False), want[-1]),
              '写进 `back=*` 而不是 `"*"` ⇒ 判不出光位的那批片会漏掉（轴为 None 整轴跳过）')
        check('★★★ 程度量**拿不到** ⇒ 什么都不加（弃权 = 安全侧，绝不瞎加）',
              _run(None) == base, '%s（应 == 基线 %s）' % (_run(None), base),
              '缺程度量还硬加 ⇒ 判不出光位时会乱改画面')
        try:
            star[key] = {'by': 'deg_back', 'delta': 0.04}      # ← 故意给标量
            _run(1.0)
            _bad = False
        except TypeError:
            _bad = True
        check('★★ 基准是"逐通道列表"时给**标量 Δ** ⇒ **当场抛**（不许默默改 R:G:B 比例）',
              _bad, '标量 Δ 被 %s' % ('拒绝' if _bad else '接受了'),
              '静默接受 ⇒ 悄悄改掉三通道比例（自检第一版就踩了这个）')
        # 老契约不许破：`{"add"}` 的基准必须在前面的层里（否则忽略，不半生效）
        star.pop(key, None)
        se2 = d['_scene_engine'].setdefault('span=平', {})
        se2[key] = {'add': list(delta)}
        try:
            check('★★ 老契约不变：`{"add"}` 的基准不在前面的层里 ⇒ **忽略**（不半生效）',
                  targets.scene_engine({'span': '平', 'back': None, 'raw': {}},
                                       stock=_PRESET, cfg=_C).get(key) is None,
                  '（`{"add"}` 只在基准已存在时生效 —— 与 `{"by"}` 不同，见 docstring）')
        finally:
            se2.pop(key, None)
    finally:
        star.pop(key, None)


def t_config_keys():
    """★★ **可调参数只在 `config.py`**：`getattr(cfg, 'X', 字面默认)` 而 config 没这个键
    ⇒ 永远取那个字面默认，改源码里同名常量**毫无反应**（"假旋钮"）。"""
# ★★ 颜色层已整段删除 ⇒ `GRADE_*` 那串检查作废；只钉**引擎侧真正在用的键**。
    for k in ('SPEK_PE_SHIFT', 'MAX_SIDE', 'EXPORT_MAX_SIDE', 'GRAIN_BLUR_REF_LONG_SIDE',
              'CHROMA_BLUR_ENABLE', 'CHROMA_BLUR_W', 'PERSON_MODEL',
              'SCANFIX_ENABLE', 'PUBLIC_COLORSPACE'):
        check('config.%s 这个键真的在（不是 getattr 的裸默认）' % k,
              hasattr(C, k), '当前 %r' % getattr(C, k, None),
              '缺它 ⇒ 代码里 `getattr(cfg, …)` 永远取那个字面默认，'
              '改源码里同名常量**不会有任何反应**')



def t_scene():
    """场景判据：三轴都到得了 · `key` 带 `VERSION`（判据一改缓存就不同） · 源头过曝**只在线性域**判。"""
    from . import scene, targets


    sc = scene.classify(_gray_img(seed=91))
    check('场景判据给出三根轴 + 版本化的 key（改了判据就要作废缓存）',
          all(k in sc for k in scene.AXES) and sc['key'].startswith('v%d|' % scene.VERSION),
          'key = %s' % sc['key'])
    check('判据稳定：同一张图判两次 key 完全一样',
          scene.classify(_gray_img(seed=91))['key'] == sc['key'])
    check('★ 靠"人"的三根轴里：`back`（光位）**加回来了**，`shot` / `face` **仍然没有**',
          'back' in scene.AXES and not [k for k in ('shot', 'face') if k in scene.AXES],
          'AXES = %s' % (scene.AXES,),
          '09-29：光位用 `person.py`（~0.3 s 的粗"人在哪"）重建；景别 / 脸可见度随认人整套删了、没回来')

# ② 每根轴的每一档都要**到得了** —— ⚠ 给的是**显示域**值，分档线在 **Lab L\*** 上（0.35 其实是 L*≈38）。
    dark = np.full((32, 32, 3), 0.087)
    mid = np.full((32, 32, 3), 0.19)
    bright = np.full((32, 32, 3), 0.95)
    flat = np.full((32, 32, 3), 0.35)
    wide = np.zeros((32, 32, 3)); wide[16:] = 1.0
    e = {scene.classify(x)['exp'] for x in (dark, mid, bright)}
    s = {scene.classify(x)['span'] for x in (flat, wide)}
    check('「曝光」三档都到得了（暗/正常/亮）', len(e) == 3, str(sorted(e)))
    check('「光比」至少两档到得了（平/大）', len(s) >= 2, str(sorted(s)))

# ③ ★★ 源头过曝**只在线性域**判（判据 = 通道最大值 >= 白点，**不是**"三通道同时贴顶"）。
    a = scene.classify(flat, lin=np.full((32, 32, 3), 0.5))['overwhite']
    b = scene.classify(flat, lin=np.full((32, 32, 3), 1.2))['overwhite']
    check('★★ 源头过曝在线性域判得出来（显示域一样、线性不一样 ⇒ 结论不同）',
          a is False and b is True, '线性 0.5 → %s ；线性 1.2 → %s' % (a, b),
          '只看显示域的话这两张"一样" ⇒ 这一轴就废了（成片那边早被重渲染压过了）')
    check('不给线性图 ⇒ overwhite = None（不硬猜）',
          scene.classify(flat)['overwhite'] is None)

# ④ 「按场景分参数」仍活着，但走 `_scene_engine`。


def t_contract():
# ① ★★ 整条链上**只有引擎在动像素** ⇒ 出图 = `presets.render` 的输出。
    _r_pure = pipeline.run_from(_mk_sample(_gray_img(seed=11)), stock=_PRESET)
    check('★★★ 整条链上**只有引擎在动像素**：出图 = `presets.render_person_pe` 的输出',
          _pure_engine_same(_r_pure, _mk_sample(_gray_img(seed=11))),
          '与"同参只跑 presets.render_person_pe"的差超出了引擎自身噪声',
          '还有别的层在偷偷改像素 ⇒ 颜色层没删干净')

    # ② ★★★ 10-16：**「以人为本」的印相曝光真的进了生产**（力度 = 光位；留痕 + 缓存键都要有）
    _s23 = _mk_sample(_gray_img(seed=23))
    _r23 = pipeline.run_from(_s23, stock=_PRESET)
    _ppe = _r23.report.get('person_pe')
    check('★★★ 报告里有「按脸反解印相曝光」的留痕（`person_pe` / `light_back`）',
          ('person_pe' in _r23.report) and ('light_back' in _r23.report),
          'person_pe=%r ｜ light_back=%r' % (_ppe, _r23.report.get('light_back')),
          '没有留痕 ⇒ "两遍印相一次都没动"也看不出来')
    check('★★ 没人的图上**只印一遍、一个像素不动**（脸量不到 ⇒ 退回 `render`）',
          (isinstance(_ppe, dict) and _ppe.get('acted') is False),
          'person_pe=%r' % (_ppe,),
          '没人脸却在动像素 ⇒ 掩膜 / 判定链出问题（弃权必须走安全侧）')

    # ③ ★★★ 光位现在**真的定画面** ⇒ 它必须进缓存键（否则改了判据还是老图 = 第 4 类坑）
    from . import scene as _scmod
    _cc = pipeline.StageCache()
    _s29 = _mk_sample(_gray_img(seed=29))
    _h1 = pipeline.run_from(_s29, stock=_PRESET, cache=_cc).report['stage_cache']['hit']
    _h2 = pipeline.run_from(_s29, stock=_PRESET, cache=_cc).report['stage_cache']['hit']
    _lv = int(getattr(_scmod, 'LIGHT_VERSION', 0) or 0)
    try:
        _scmod.LIGHT_VERSION = _lv + 1
        _h3 = pipeline.run_from(_s29, stock=_PRESET, cache=_cc).report['stage_cache']['hit']
    finally:
        _scmod.LIGHT_VERSION = _lv
    check('★★★ 同参第二次**命中**缓存；改 `scene.LIGHT_VERSION` ⇒ **必须不命中**',
          (_h1 is False) and (_h2 is True) and (_h3 is False),
          '首跑 hit=%s ／ 同参 hit=%s ／ 版本+1 hit=%s' % (_h1, _h2, _h3),
          '光位进了生产却不在缓存键里 ⇒ 改了 `light_evidence` 还是老图（"拧了没反应"）')

    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=13)), stock='根本没有这一条')
        ok = False
    except KeyError:
        ok = True
    check('胶片风格名字不认得 ⇒ 当场报错（不是静默出一张别的）', ok,
          '', '"名字不认得就静默走默认"是本项目最阴的一类坑，出现过三次')

# ③ 报告里不该再有 `grade` 键（有 ⇒ 颜色层没删干净）。
    r = pipeline.run_from(_mk_sample(_gray_img(seed=17)), stock=_PRESET)
    check('★ 报告里带 `scene`（判出来的场景）、且**不再有 `grade` 键**',
          'scene' in r.report and 'grade' not in r.report,
          '键: %s' % sorted(r.report.keys()),
          '`grade` 还在 ⇒ 颜色层没删干净（报告形状会骗人）')

    check('成片没有 NaN / Inf 且在 [0,1]',
          bool(np.all(np.isfinite(r.disp))) and float(r.disp.min()) >= 0.0
          and float(r.disp.max()) <= 1.0)


def t_cache():
    s = _mk_sample(_gray_img(seed=19))
    c = pipeline.StageCache()
    r1 = pipeline.run_from(s, stock=_PRESET, cache=c)
    r2 = pipeline.run_from(s, stock=_PRESET, cache=c)
    check('同参数 ⇒ 命中缓存（切回来不用重跑）',
          bool(r2.report['stage_cache']['hit']))




# ★★★ **靶 / 场景覆盖（引擎 overrides）也必须进键**（以前这两项永远是 None，被 except 吞掉）。
    from . import targets as _TS
    _d = _TS.load()

    _sat_save = (_d.get(_PRESET) or {}).get('sat')
    try:
        _d.setdefault(_PRESET, {})
        _d[_PRESET]['sat'] = 0.999 if _sat_save != 0.999 else 0.998
        r6 = pipeline.run_from(s, stock=_PRESET, cache=c)
    finally:
        if _sat_save is None:
            _d.get(_PRESET, {}).pop('sat', None)
        else:
            _d[_PRESET]['sat'] = _sat_save
    check('★★ 改这条预设的**靶**（`sat`）⇒ **不**命中（靶真的进了键）',
          not r6.report['stage_cache']['hit'],
          '命中=%s' % r6.report['stage_cache']['hit'],
          '不进键 ⇒ 改了靶仍命中旧缓存 = "拧了没反应"')

    _se_save = _d.get('_scene_engine')
    try:
# ★★ 用**没有下沉的那一根**改基线，测的仍是"overrides 进了缓存键"（改 `["*"]` 会假红）。
        _star = dict((_se_save or {}).get('*') or {})
        _pf = 'enlarger.preflash_exposure'
        _star[_pf] = float(_star.get(_pf, 0.01)) + 0.01
        _d['_scene_engine'] = dict(_se_save or {}, **{'*': _star})
        r7 = pipeline.run_from(s, stock=_PRESET, cache=c)
    finally:
        if _se_save is None:
            _d.pop('_scene_engine', None)
        else:
            _d['_scene_engine'] = _se_save
    check('★★ 改 `_scene_engine`（引擎覆盖）⇒ **不**命中（overrides 真的进了键）',
          not r7.report['stage_cache']['hit'],
          '命中=%s' % r7.report['stage_cache']['hit'],
          '不进键 ⇒ 改了场景覆盖仍命中旧缓存 = "拧了没反应"第 4 类')


def t_routes():
    from . import service as svc
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'service.py'), encoding='utf-8').read()
    for p in ('/stocks', '/render', '/export', '/load', '/base', '/health'):
        check('路由 %s 在' % p, ("u.path == '%s'" % p) in src)
    for p in ('/params', '/bases', '/papers', '/styles'):
        check('★ 已经删掉的路由 %s 确实不在了' % p, ("u.path == '%s'" % p) not in src,
              '', '滑杆 / 相纸 / 成色基准都随新边界删了，接口不该还留着')
    check('默认端口还是 8765（台子那边钉着）', svc.DEFAULT_PORT == 8765, str(svc.DEFAULT_PORT))


def t_tooling_1012():
    """★★★★★ 工具层的两个**静默**陷阱：基线来源唯一 · `targets.load` 文件指纹
    —— 都不报错，只会把「我改的值没生效」变成「**我拿到的是另一份数**」。"""
    import json as _json
    import tempfile as _tf

    from . import config as _C, targets as _T

    _lc = 'io.output_gamut_compress.lightness_compression'


    _bad, _unres = [], []
    for _n in (_PRESET,):
        try:
            _a = presets._base_overrides(_C, _n)
        except Exception as _e:                              # noqa: BLE001
            _bad.append('%s(%s)' % (_n, type(_e).__name__))
            continue
        if _a != _T.scene_engine({}, stock=_n, cfg=_C):
            _bad.append(_n)
        _unres += [k for k, v in _a.items() if isinstance(v, dict) and 'mul' in v]
    check('★★ `_base_overrides(stock)` 与生产基线 `scene_engine({})` **逐位一致**',
          not _bad, '不一致=%s' % (_bad or '无'),
          '两份合并逻辑一漂 ⇒ 实验脚本拿到的基线不是生产那份 = 又一次「口径错了」')
    check('★★ `_base_overrides(stock)` 里**没有未解析的 `{"mul":k}`**',
          not _unres, '未解析=%s' % (sorted(set(_unres)) or '无'),
          '未解析 dict 喂给 render ⇒ setattr 处炸 `UFuncOutputCastingError`')


    try:
        presets._base_overrides(_C)
        _exc = '没抛'
    except ValueError:
        _exc = ''
    except Exception as _e:                                  # noqa: BLE001
        _exc = '抛的是 %s（要 ValueError）' % type(_e).__name__
    check('★★ `_base_overrides(cfg)`（**没给 stock**）当场抛 `ValueError`',
          not _exc, _exc,
          '静默返回「跟预设无关」的基线 ⇒ 拿到的可能是别人的那份值')


    _d0 = _T.load()
    _star = ((_d0.get('_scene_engine') or {}).get('*') or {}).get(_lc)
    _save_sk = _d0.get('_stock_engine')
    try:
        _d0['_stock_engine'] = {_PRESET: {_lc: [0.9, 1.0, 2.0]}}
        _hit = presets._base_overrides(_C, _PRESET).get(_lc)
        check('★★★ 破法（注入式）：`_stock_engine[预设]` 一旦有值，`_base_overrides` 会用它（层序没死）',
              _hit != _star and list(_hit) == [0.9, 1.0, 2.0],
              '注入后 = %r ｜ `["*"]` = %r' % (_hit, _star),
              '拿到的还是 `["*"]` 那份 ⇒ `_stock_engine` 那一层被静默丢了（10-12 前的真实缺陷）')
    finally:
        if _save_sk is None:
            _d0.pop('_stock_engine', None)
        else:
            _d0['_stock_engine'] = _save_sk


    _save = _T._P
    _fd, _tmp = _tf.mkstemp(suffix='.json', prefix='_selftest_tg_')
    os.close(_fd)
    try:
        with open(_tmp, 'w', encoding='utf-8') as _f:
            _json.dump({'_default': {'probe': 1}}, _f)
        _T._P = _tmp
        _T.load()
        with open(_tmp, 'w', encoding='utf-8') as _f:        # ★ 同一个进程内改
            _json.dump({'_default': {'probe': 2}, '_scene_engine': {'*': {}}}, _f)
        _v = (_T.load().get('_default') or {}).get('probe')
    finally:
        _T._P = _save
        try:
            os.remove(_tmp)
        except OSError:
            pass
    _T._SIG = None
    _T.load()                                                # 读回真文件
    check('★★★ 同一进程里改 `targets.json` ⇒ `load()` **立刻**读到新值（T6）',
          _v == 2, 'probe=%r（应为 2）' % _v,
          '进程内永久缓存 ⇒ 改了不生效且不报错（"拧了没反应"第 4 类）')


if __name__ == '__main__':
    sys.exit(_main())
