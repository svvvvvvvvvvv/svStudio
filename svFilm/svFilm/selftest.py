# -*- coding: utf-8 -*-
r"""自检 —— 动完任何一层都要跑。不依赖任何样片，纯合成图 + 不变量。

  python -m svFilm.selftest

★ 09-29 起这条链只剩：
  **解码 + 白平衡 + 护栏 → 判场景 → 胶片引擎(0.3.4) → 颜色层（混色 → 分色）→ 出图**。

  影调层（`tone.py`）/ 肤色层（`grade.skin()`）/ 认人认脸（`face.py` · `facegain.py` · `region.py`）
  **整段删除**（不是关开关）⇒ 那几百条检查随模块一起删了（它们验的对象已经不存在）。
  另立一组 `t_dropped_layers` 钉"**真的删了**"。
"""
import os
import sys

import numpy as np

from . import color, config as C, io, pipeline, presets

# ★ 09-23：凡是要点名「一条胶片风格」的地方就用这一条（别把中文名写死到各处）。
_PRESET = '鹿井预设'

FAIL = []


def check(name, cond, extra='', why=''):
    """`extra` = 现场（成败都打）；`why` = **红了意味着什么**（只在红时打）。"""
    print(('  ok   ' if cond else '  FAIL ') + name
          + (('   ' + extra) if extra else '')
          + (('   ⇒ ' + why) if (why and not cond) else ''))
    if not cond:
        FAIL.append(name + ((' — ' + why) if why else ''))


# ---------------------------------------------------------------------------
# 合成图
# ---------------------------------------------------------------------------

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
    """颜色层关掉时，`pipeline` 的出图应当**就是** `presets.render` 的输出。

    ⚠ 引擎的颗粒是随机的 ⇒ 判据只能是"差值在引擎自身的噪声量级内"，
      **不能**要求逐位相等。参照量 = 同一输入连跑两次纯引擎的差值。
    ★★ 09-29：参照物必须带上**生产那份引擎覆盖**（`_scene_engine`，例：全局那条
      `density_curves_morph` 把跨度 76.2 拉到 81.87）。不带 ⇒ 拿"没有覆盖的引擎"去比
      "有覆盖的引擎"，差的 0.032 **全是覆盖带来的**，与颜色层无关。
    ★ `style` 参数保留只为兼容调用处签名 —— 影调层已删，不再需要它。
    """
    lin = np.clip(sample.lin, 0.0, None)
    a = np.asarray(res.disp)
    from . import targets as _TS
    _sc = res.report.get('scene')
    ov = _TS.scene_engine(_sc, stock=res.report['stock'], cfg=C) or None
    stock = res.report['stock']
    b = np.asarray(presets.render(lin, stock, C, overrides=ov))
    c = np.asarray(presets.render(lin, stock, C, overrides=ov))
    noise = float(np.max(np.abs(b - c)))          # 引擎自身不可复现的那点
    diff = float(np.max(np.abs(a - b)))
    return diff <= max(noise * 2.5, 5e-3)


# ---------------------------------------------------------------------------
# R1「黑端收窄比」—— 解算器（`AGENTS.md` §一.5 · 10-13 入自检）
# ---------------------------------------------------------------------------

def _r1_per_image(lab, c_max=None):
    r"""**一张图**的 R1 = `C*(L*0-20) ÷ C*(L*20-40)`（中性像素：C\* <= `_R1_NEUTRAL_C`）。

    ★ 分母用**相邻档**（20-40）。`NA0/max(NA1..3)` 是 **A1 黑端**，**不是** R1 ——
      两条尺子共用同一个分子，**别混**（混了就是把 A1 的界套到 R1 上）。
    ★ 单张、单档的中性像素 < `_R1_MIN_PX` ⇒ 返回 `nan`（**不许**拿几十个像素的档位出数）。
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

    两种顺序只在"所有图逐位相同"时才相等 ⇒ 差别**在合成图上量不出来**，
    必须靠**手搓的 Lab 数组**来钉（`t_r1_neutral_end_1013` 的检查②）。
    ★ **R1 只能判批**（`AGENTS §一.5`）：返回的是**整批中位**，单张的比只当输入。
    无效图（`nan`）**计入** `n_bad` 并从分母 `n` 里剔除 —— 不剔除就会拿 nan 去比大小。
    """
    v = [_r1_per_image(x) for x in labs]
    good = np.array([x for x in v if np.isfinite(x)], np.float64)
    if good.size == 0:
        return {'median': float('nan'), 'n': 0, 'n_bad': len(v),
                'iqr': float('nan'), 'per_image': v}
    return {'median': float(np.median(good)), 'n': int(good.size), 'n_bad': len(v) - int(good.size),
            'iqr': float(np.percentile(good, 75) - np.percentile(good, 25)), 'per_image': v}


def _r1_verdict(median, n, iqr, band=None, master_iqr=None):
    r"""把「整批中位」译成判定 —— ★★ **分辨力不够时只报方向，不当缺陷**。

    `分辨力 = 2·sqrt((大师逐图IQR/√12)^2 + (我们逐图IQR/√n)^2)`（`AGENTS §一.1`）

    | 情形 | 判定 |
    |---|---|
    | 中位在带内 | `在带内` |
    | 离界距离 < 分辨力 | `判不了（分辨力不够）` —— **只报方向**，**不当缺陷** |
    | 离界距离 >= 分辨力 | `真越界（分辨力足够）` |

    ★★ **10-13 实测：我们这个样本量（n=11）下，"判不了"就是常态，不是意外。**
      生产路径三条链的现场中位 = 鹿井 **0.8703** ｜ 増田 **0.6935** ｜ 滨田 **0.9138**
      （`eedd967` 提交信息，n=11；分辨率 0.17~0.20）
      ⇒ **三条全是 `判不了`**（离界 0.033 / 0.000 / 0.077，全部 < 分辨力）。
      ⇒ ★ **所以这条尺子今天给出的不是"红/绿"，是"方向"**（§一.5 早已写明）。
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
    r"""★★★ 10-08 代码评审后的**防复发**断言。

    这一组守的都是"接线断了但没人发现"的一类 —— 项目里已经栽过三次
    （`agx_particle_*` 字段改名、`print_render.density_curve_gamma` 被删、
    `service._DECODE_SIG_KEYS` 靠的 `ENTRY_*` 前缀被删光）。
    它们的共同点是：**没有任何东西会报错**。
    """
    import numpy as _np
    from . import config as C, presets, service, targets

    # ---- ④ 解码签名不能再是空的（`ENTRY_*` 那次的教训）----
    _gone = [k for k in service._DECODE_SIG_KEYS if not hasattr(C, k)]
    check('★★★ 解码签名有键、且键真的在 config 里', bool(service._DECODE_SIG_KEYS) and not _gone,
          '键=%s 缺=%s' % (list(service._DECODE_SIG_KEYS), _gone or '无'),
          '老写法靠 `ENTRY_` 前缀取键，前缀被删光后恒为空 ⇒ 机制静默失效、无人发现')

    # ---- ⑥ 印相中灰配平必须**显式钉住**，不许吃 vendor 的 schema 默认 ----
    _p = presets.digested(_PRESET, C)
    check('★★ `normalize_print_exposure` / `print_exposure_compensation` 显式钉为 True',
          bool(_p.enlarger.normalize_print_exposure)
          and bool(_p.enlarger.print_exposure_compensation),
          'norm=%s comp=%s' % (_p.enlarger.normalize_print_exposure,
                               _p.enlarger.print_exposure_compensation),
          '它是整张落点的命门；吃默认值 ⇒ 换 vendor 版本会**静默位移一档亮度**')

    # ---- ⑧ 可叠加的轴覆盖（`{"add": Δ}`）：数据自洽 + 组合行为正确 ----
    #   ★ 10-08：`targets.scene_engine` 原来用 `out.update(blk)` ⇒ **后匹配的轴赢、不叠加**。
    #     而 `span=平` 与 `back=逆光` 实测是**两个相加的因子** ⇒ 必须能叠。已加 `{"add": Δ}` 约定。
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
    # ★★ 10-12 晚：**这两条锁改法**。`span=平` 已删（`三条设计_1012\A_标定_1012.md`：方向反了）
    #   ⇒ `_scene_engine` 现在**一条场景轴都没有**（`back=` 10-09 删、`lighttype=` 10-11 撤、
    #   `span=平` 10-12 删）⇒ 原来那两句**没有对象可指**，必然红。
    #   但它们要验的是**机制**（"逐场景参数真的接线了" / "两轴真的叠加"），
    #   **不该依赖某条轴恰好活着** ⇒ 改成**临时造一条轴、断言、跑完还原**（就地改 `_se`，保住它的身份）。
    #   ⚠ 注意 `_se` 是**活对象**（`_d['_scene_engine']`）；这里**不能**整体换 dict，
    #     否则 `_se` 会指着一份不再生效的旧快照（上一版就这么错过一次）。
    _GN = 'print_render.density_curves_morph.gamma_factor'
    _has_span = 'span=平' in _se
    _span_save = _se.get('span=平')
    _inj = {'print_render.density_curves_morph.gamma_factor': {'add': 0.07}}
    try:
        _se['span=平'] = {_GN: 1.23}
        _g1 = targets.scene_engine({'span': '平', 'exp': None, 'back': None, 'overwhite': None},
                                   stock=_PRESET, cfg=C).get(_GN)
        # ★ **证明叠加真的生效**：临时再加一条 `back=正逆光` 的 `{"add": Δ}`，
        #   断言"两轴同时命中 = 两轴各自之和"。跑完还原。
        #   ⚠ 不这么做的话，"支持叠加"就只是注释里的一句话 —— 本项目最恨"说了没接上"。
        _se['back=正逆光'] = dict(_inj)
        _a = float(targets.scene_engine({'span': '平', 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock=_PRESET,
                                        cfg=C).get(_GN))
        _b = float(targets.scene_engine({'span': None, 'exp': None, 'back': '正逆光',
                                         'overwhite': None}, stock=_PRESET,
                                        cfg=C).get(_GN))
    finally:
        _se.pop('back=正逆光', None)
        if _has_span:
            _se['span=平'] = _span_save
        else:
            _se.pop('span=平', None)
    _g2 = targets.scene_engine({'span': None, 'exp': None, 'back': None, 'overwhite': None},
                               stock=_PRESET, cfg=C).get(_GN)
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

    # ---- ⑦ config 真的进了 `_params_for` 的键（否则第一次调用者的 cfg 永久污染）----
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


# =====================================================================================
# ★★★★★ 10-11：合成探针的**绝对电平**（仪器参数，不是风格靶）
# =====================================================================================
# 锚落地（`00935da`：`ev' = (1-w)*ev + b`，`w = 1.0`）之后 `ev'` **是常数** ⇒ 整条链变成
# **固定增益**（每像素 × 2^1.578 = 2.986），与"这张图整体有多亮"**无关**；
# 而旧链是**硬锚 0.184**（`ev = -log2(meanY/0.184)`）⇒ **增益随图变**。
# ⇒ 那 6 条传递函数锁标在旧链上，锚一落地必须重标（`中灰锚落地_1011\中灰锚落地.md` §7.1）。
#
# ## ★ 10-12：`w` 1.0 -> 0.6 ⇒ **探针工作点第二次移动**（**实测**，`人本落地_1012\probe_ev.py`）
# 探针自己的测光读数没变（`ev_raw = 1.723`，`method = matrix`）—— 变的是"曝光主人"：
#     `ev' = 1.578`（锚独裁）-> `0.4 x 1.723 + 1.1181 = 1.807` ⇒ 增益 `2.986 -> 3.500`
#     ⇒ **工作点 +0.229 档**。
# 于是下面这几条的读数漂了（`人本落地_1012\selftest_HEAD.txt` vs `selftest_w06.txt`）：
#     **② 54.3 -> 57.8**（容差 ±4 ⇒ **离边界只剩 0.5 L\***）、④ 91.9 -> 92.6、
#     ⑤ b\* +0.15 -> +0.52、⑧ L\*21 -6.80 -> -5.76。
# ★ **① （出口白）没有动**（输入 L\*99.9 -> 输出 **93.6**，仍是同一条红线）——
#   因为纯白输入顶在 1.0，增益推不动它 ⇒ "**116 ok / 1 红**" 的红数**没变、红的也没换**。
# ⚠ **本轮故意不重标**（不拿"把容差挪到现值"替自己的改动背书）。但这条**结构性**问题记下来：
#   探针挂在**曝光主人**下游 ⇒ 以后每改一次曝光，这 6 条都会漂一次；
#   ②「54.3 ± 4」现在就只剩 0.5 L\* 余量。
#
# ## 探针该在什么电平（**真实数据定**，产物在 `E:\ds\photo\测试台与肤色复核_1011\`）
# 口径**照抄引擎自己那一套**（`io.load(raf, 1024)` → `small_preview` 长边 256 最近邻 →
# ProPhoto 线性 Y 的**全图均值**），52 张真实 RAW 实测（`中灰锚_1011\meter52.json`）：
#     `meanY`  P10 0.0439 ｜ **中位 0.0693** ｜ P90 0.1400 ｜ 极差 0.0249 ~ 0.2014
#     `s = log2(meanY/0.184)`   P10 -2.07 ｜ **中位 -1.41** ｜ P90 -0.40
#     （生产测光口径 `subject` 的 `s` 中位 -1.13；`matrix` -1.22）
# 旧探针（增益 `1.0`）：`meanY = 0.3246`、`s = +0.819` ⇒ **比真实中位亮 1.7 档**。
# ⇒ `0.0693 / 0.3233 = 0.2144` —— **本体**电平 = 真实 `Y` 中位（精确对齐，见下"白端"）。
#
# ## ★ 绝对电平的灰阶条（为什么另加一条，而不是给整张图乘增益）
# 探针本体降电平之后最高输入只剩 **L\*47**（显示 1.0 × 0.2018）。而锁①②③④⑧问的都是
# **绝对输入电平**（"纯白进去出来是不是白"、"输入 L\*20 不许过冲"、"中性轴在 L\*20/50/70/100"）
# ⇒ 那几条必须喂**绝对电平**的灰阶，否则**等于把问题换掉了（改考题）**。
# ⇒ 在黑白灰那一段的顶部放 **`_PROBE_ABS_ROWS` 行**（占面积 1.78%）**不乘增益**，
#    重建一条 0→1 的**绝对灰阶斜坡**，承载 ①②③④⑧；⑤ 也从这条上取 18% 灰那一点。
# 代价：全图 `meanY` 由 0.0649（本体）抬到 **0.0693 = 真实中位**（增益按此反解）。
#
# ## ⚠ 两条必须写下来的边界（**别把这一节读成"探针修好了"**）
# 1. **实测（`a1_probe_levels.py`）：把探针整体乘 1.0 / 0.5 / 0.2135 / 0.1067（跨 3.2 档）
#    各渲一次，`输出 L* ~ 输入 L*` 是**同一条曲线****（"基臂曲线沿输入轴平移 log2 g 档"
#    的预测 vs 实测 **max|Δ| = 1.11 L\***）⇒ **链是逐像素的（pointwise）**，
#    探针的整体电平**不改传递函数**，只决定"锁采在输入轴的哪一段"。
#    ⇒ **6 条锁的位移 100% 由锚造成**；这一节改的是"探针代表哪个工作点"。
# 2. **硬事实：一张含全量程灰阶的图表，不可能同时拥有真实照片的 `meanY`**
#    —— 全量程灰阶一项就贡献 ~0.10 > 真实中位 0.0693。本设计把这条灰阶压到 1.78% 面积，
#    才使两者同时成立（全图 0.0693）。**再想降就只能动图表构成，不能动增益。**
#
# ## 回退（一行）
#   `_PROBE_SCENE_GAIN = 1.0` 且 `_PROBE_ABS_ROWS = 0` ⇒ **逐位恢复旧探针**（meanY 0.3246）。
_PROBE_SCENE_GAIN = 0.2018      # ← 旧值 = 1.0（回退点）；本体 meanY = 0.0649，全图 = 真实中位 0.0693
_PROBE_ABS_ROWS = 16            # ← 旧值 = 0（回退点）；这一段不乘增益，承载绝对电平的锁

# ---------------------------------------------------------------------------
# R1「黑端收窄比」—— 常数（10-13 入自检；定义见 `AGENTS.md` §一.5）
# ---------------------------------------------------------------------------
# ★ **界**：`[0.538, 0.837]` = **大师逐图并集的 [P25, P75]**（与 §一.1 同口径）。
#   现场复核（`datapack_1011` 的 `NA_C`，三条大师 n=1060）：并集 [0.538, 0.837]、
#   **并集 IQR = 0.299**；三位各自的整批中位 = 鹿井 0.652 / 増田 0.797 / 滨田 0.698。
#   ⚠ 别拿这里的"组中位"当界 —— §一.1 明写那是**过时口径**。
#   ★ 我们（生产路径，n=11，`eedd967` 提交信息）：鹿井 **0.8703** / 増田 **0.6935** / 滨田 **0.9138**
#     —— 分辨率 0.17~0.20 ⇒ **三条都只报方向**（见 `_r1_verdict`）。
_R1_BAND = (0.538, 0.837)
_R1_MASTER_IQR = 0.299          # 大师逐图并集 IQR（= P75−P25，n=1060）—— 分辨力的分子
_R1_NEUTRAL_C = 8.0             # 中性判据：C* <= 8（与 §一.2 的中性轴表同口径）
_R1_DEN_BAND = (20.0, 40.0)     # ★ 分母 = **相邻档**（不许用"五档最大值"，那是 A1 黑端，不是 R1）
_R1_MIN_PX = 30                 # 单张、单档的中性像素下限（不够 ⇒ 这张的比不可用，如实标 NaN）
# ★ 合成图表探针（不是生产口径）—— 见 `t_r1_neutral_end_1013` 的长注释。
#   ⚠ 每张探针 = 一次全链 `presets.render`（本机实测 ~3.6 s）⇒ **图数就是成本**：
#     4 张够"整批中位"用（且图间散布比单张抖动大一个量级），再多只买时间。
_R1_PROBE_GAINS = (0.70, 0.85, 1.00, 1.20)      # 乘在 `_PROBE_SCENE_GAIN` 上
_R1_PROBE_KNOWN = 0.407472      # 关掉 glare 后的**逐位可复现**批中位（本机 10-13 实测，n=4，两进程逐位相同）
_R1_PROBE_TOL = 0.006           # ±1.5%：已实测的图间散布 IQR = 0.0008、极差 = 0.0091


def t_synth_transfer():
    r"""★★★★★ **合成测试图的传递函数**（内容无关的回归基准）。

    为什么要有这一组：项目里**全部测量都在不可控的照片上做**，而"两组不同照片的分布差"
    **无法归因到渲染**（10-08 夜栽了五次）。合成测试图把"我们的变换"变成一**条可读的曲线**，
    且**真实照片从此只用于验证、不再用于拟合**。
    ⇒ 这一组同时是 **10-09 三个硬缺陷的回归锁**：
        ① **出口没有白**：旧曲线输入 L*100 → 输出 **88.7**（纯白被压到 RGB≈220）
        ② **暗部过冲**：旧曲线输入 L*20 → 输出 **10.8**（先压暗再交叉）
        ③ **中调鼓包**：旧曲线输入 45~55 抬 **+17**
      修法（已落 `targets._scene_engine["*"]`）：`lightness_compression` 0.7→**0.95**、
      `gamma_factor_slow` 1.3→**0.85**。实测：L100 88.7→**95.2**、L20 10.8→**20.2**、曲线单调。
    ⚠ 本组**不含逐场景覆盖**（用 `scene_engine(None, ...)` 的全局 `"*"` 层）——
      传递函数应当在**基线**上量，逐场景覆盖是"基线之上的条件修正"。

    ## ★★★★ 10-11（任务 A）：探针电平 + 6 条锁的**重标**
    锚落地后这 6 条锁全部位移（`中灰锚落地_1011\\中灰锚落地.md` §7.1）。本轮的处置：
    · **探针本体降到真实 RAW 电平**（`_PROBE_SCENE_GAIN`，见模块里那段长注释）；
    · **另加一条绝对电平的灰阶条**（`_PROBE_ABS_ROWS`）⇒ ①②③④⑧ 仍然**在绝对输入电平上发问**
      （**问题没换**，只是探针本体代表真实工作点）；
    · ②④ 的期望值**重标**，依据是**同内容配对**（真实 RAW ↔ 同一张的富士直出，同像素）：

      | 输入 RAW | 旧链 | **锚落地后(我们)** | 富士直出 |
      |---|---|---|---|
      | L\\*21.2 | 47.5 | **57.5** | 60.5 |
      | L\\*53.3 | 86.5 | **91.5** | 88.5 |
      | L\\*70~100 | 92.5~93.5 | **93.5（顶死）** | 94.5~**99.5** |

      ⇒ 锚把整条曲线从"远低于目标"搬到"贴着目标"（L\\*21~60 段逐点差 ≤5 L\\*）；
        ② 的旧窗口 16~24 与 ④ 的旧值 67.7 **编码的是旧链的法则**，故重标。
      ⇒ **① 相反：真实照片上我们顶到 93.5、直出到 99.5（差 6 L\\*）⇒ ① 该红，保留原阈值 94。**
    · 数据与推导：`E:\\ds\\photo\\测试台与肤色复核_1011\\`（`a1` / `a2` / `b5` / `报告`）。
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

    # ★★★★ 10-11（任务 A）：**探针的绝对电平**。图表的**构成与标称值一个字没改**
    #   （灰阶斜坡 / 16 灰块 / 12 肤色块 L\*30~85 / 12 色相环 L\*60 / 黑白灰），
    #   改的只有"这张图表以什么整体增益喂进引擎"——**这就是仪器参数本身**。
    lin_in = np.clip(s2l(ch) * _PROBE_SCENE_GAIN, 0.0, None)
    # ★ 绝对电平的灰阶条：这一段**不乘增益**（0→1），承载"必须以绝对输入电平发问"的那几条锁。
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
    # ★★ 10-10：颜色层已整段删除 ⇒ 传递函数直接在**引擎输出**上量（引擎出图 = 成片）。
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
    # 18% 灰（线性 0.184 ≈ L\*50）那一点的**中性**。
    # ★ 新探针：从**绝对电平灰阶条**上取（本体已降电平，那条"显示 0.5 灰块"不再是 18% 灰）。
    # ★ 回退（`_PROBE_ABS_ROWS = 0`）：仍走原来那个"黑白灰"段的 0.5 块 ⇒ **逐位等于旧探针**。
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

    check('★★★★★ 合成图 ①：**输入纯白 L*100 的输出必须 >= 94**（"出口没有白"的回归锁）',
          L100 >= 94.0, '输入 L*%.1f（绝对电平条）-> 输出 L*%.1f' % (_i100, L100),
          '旧曲线只有 **88.7**（纯白被压到 RGB≈220）=> 这就是"白衬衫是奶色"的根。'
          '修法 = `io.output_gamut_compress.lightness_compression` 0.7->0.95。'
          '★ 10-11 用**真实成片**复核过（18 张分层、1.5 亿像素的同像素配对）：'
          '输入 RAW L*>=70 时**我们顶到 93.5、富士直出到 99.5** ⇒ **白端确实还差 6 L***，'
          '这条**该红**（不是探针的错）。')
    # ★★★★ 10-11（任务 A）：**② 与 ④ 的期望值是重标的**（旧值 16~24 / 67.7±2 编码的是**旧链**）。
    #   凭什么说新值是对的 —— **同内容配对**（`D:\\照片库\\...\\RAF` ↔ 同一张的富士直出，
    #   `测试台与肤色复核_1011\\b5_transfer_real.py`，18 张分层 / 1.5 亿像素 / 同像素）：
    #       输入 RAW L*21.2：我们 **57.5** ｜ 直出 **60.5** ｜（旧链 47.5）
    #       输入 RAW L*53.3：我们 **91.5** ｜ 直出 **88.5** ｜（旧链 86.5）
    #   ⇒ 锚落地后整条传递曲线已经贴到直出上（L*21~60 段逐点差 ≤5 L*），
    #     **"输入 L*20 出来必须在 16~24"这个窗口是旧链的法则，不是物理规律**。
    #   ⚠ 探针读数（54.3 / 91.9）比真实照片同电平的读数（57.5 / 91.5）在暗端低 **3.2 L\*** ——
    #     那是**探针几何**造成的（绝对灰阶条左右是降了电平的暗图块 ⇒ glare 比真实场景少）。
    #     **锁钉的是"探针读数"，不是真值**；3.2 L* 是这台仪器的系统偏置，不影响回归判据力。
    _T20, _T50 = 54.3, 91.9
    check('★★★★★ 合成图 ②：**暗部不过冲**（输入 L*20 的输出钉在 %.1f ± 4）' % _T20,
          abs(L20 - _T20) <= 4.0,
          '输入 L*%.1f -> 输出 L*%.1f（离输入有 %+.1f）' % (_i20, L20, L20 - _i20),
          '旧链的 **10.8 过冲**（暗部先被压暗再交叉）已由 `gamma_factor_slow` 1.3->0.85 修掉。'
          '★ 期望值 10-11 由 **16~24 重标为 %.1f**：锚把链改成固定增益后，'
          '真实照片在同一 RAW 电平上是 **57.5**（富士直出 **60.5**）⇒ 旧窗口是**旧链的法则**。' % _T20)
    check('★★★★ 合成图 ③：灰阶传递函数**单调无回折**',
          not bad_mono, '21 点采样，回折处 %s' % (bad_mono or '无'),
          '曲线回折 = 某些亮度区间"越亮越暗"，是影调映射的硬伤')
    check('★★★ 合成图 ④：中调抬升钉住（重标后 %.1f ± 2.5）' % _T50,
          abs(L50 - _T50) <= 2.5, '输入 L*%.1f -> 输出 L*%.1f' % (_i50, L50),
          '配方说"暗部上提"，但过头多少**没有依据** => 只钉住、不改。'
          '★ 期望值 10-11 由 **67.7±2 重标为 %.1f±2.5**：同一张真实照片在 RAW L*53.3 上'
          '我们出 **91.5**、直出 **88.5**（18 张分层配对）⇒ 新值有实测依据。' % _T50)
    check('★★★★★ 合成图 ⑤：**中灰必须中性**（|b*|<=1.5）—— 10-09 已修好，从"钉住"升级为"要求"',
          abs(midb) <= 1.5,
          '18pct 灰（线性 0.184，输入 L*' + ('%.1f' % inL(50)) + '）输出 b* = ' + ('%+.2f' % midb),
          '纯中性的灰喂进去，出来必须是中性。这条曾经是**已知缺陷**（b* -6.1，整条灰阶偏蓝）'
          '⇒ 10-09 两步修好：① 相纸换富士水晶（中性查表跟着换）中调 -5.95→-2.81；'
          '② CC 黄滤片 5.253→2.0 ⇒ **中调 -2.81→+0.79**。'
          '⚠ 当年扫 `sat`/`white_level`/`gamma`/`zone_abs`+`sh_abs`+`hi_abs` 全清零都修不动 '
          '⇒ **病因在上游（相纸/滤片），不在颜色层** —— 这条现在是回归锁')
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

    # ---- ★★★ 10-09：**中性轴**（这是"物理层"的验收仪器，对应审计的 ②）----
    #   为什么单独立一条：**喂纯中性的灰进去，出来必须是中性**。这是唯一无内容争议的判据。
    #   10-09 实测：整条灰阶都偏蓝（b* −2.9~−6.1），中调最重（−6.0）
    #   ⇒ 与用户的 LR 配对参照、以及"中灰 RGB [172,174,187]（B 最高）"三方一致。
    #   ⚠ **配方允许"暗部偏青蓝"** ⇒ 暗部那一档要单独放宽，不能一刀切。
    #   同时报**有符号色相**（审计 A4：方向盲的标量是"把肤色推成发绿"的根因）。
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
    r"""★★★★ R1「**中性彩度的黑端收窄比**」入自检（10-13）。

    ## 它是什么

        R1 = C*(L\*0-20) ÷ C*(L\*20-40)，**中性像素**（C\* <= 8）按 L\* 分档取中位
        判据：**整批中位 ∈ [0.538, 0.837]** = 大师**逐图并集** [P25, P75]（`AGENTS §一.5`）

    ★ `AGENTS §一.5` 把它写成**目前唯一"能当靶"的自检尺子** ——
      所以"不进自检"本身就是个缺口：**每次落值都可能悄悄把它推坏**
      （实测：`amount 0.90` 把鹿井那条从 0.868 推到 0.830）。

    ## ★★ 三条使用限制（写在**解算器**里，不是写在注释里就完事）

    | # | 限制 | 解算器里怎么钉住的 |
    |---|---|---|
    | ① | 分母**必须用相邻档**（不能用"五档最大值"）| `_R1_DEN_BAND = (20,40)`，**硬编码**；用五档最大值的是 **A1 黑端**（分子相同、分母不同 ⇒ 界也不同，混了就是拿 A1 的界判 R1）|
    | ② | **必须先逐图算比、再取中位**（不是先取中位再相除）| `_r1_batch()` 先对每张调 `_r1_per_image` 再 `median`；**这条在合成图上量不出差别**（图都一样）⇒ 检查② 用**手搓的 Lab 数组**钉 |
    | ③ | **只能判"整批"，不能判单张** | 判定只吃 `_r1_batch()` 的**整批中位** —— 单张的比只当输入，没有任何"单张超界"的出口 |

    ## ★★ n 不够时怎么办 —— **选 (a)：只报方向，不当红**（理由）

    选项是 (a) 只当提示 / (b) 按 n 给动态门限 / (c) 只在 n>=20 时判。**选 (a)**，两条理由：

    1. ★ **`AGENTS §一.5` 已经明写**「只报方向，不当缺陷」——
          (c) 只是把同一个结论换了个写法；**(b) 给不出可辩护的门限**：
          `§一.1` 刚把「三位散度×2」判成**过时口径**，再自造一个"随 n 变的界"就是重犯同一个错。
    2. ★ **判定本身已经有分级**（`_r1_verdict`）：`离界距离 < 分辨力 ⇒ 判不了`。
          这一条**比"按 n 设门限"更严** —— 分辨力里含**大师逐图 IQR**（0.299，n=1060，
          压不下去的那一项），所以**加 n 也救不了它**。
          ⇒ ★ **"判不了"是我们的常态，不是异常**：10-13 生产路径三条链（n=11）
            **三条全是"判不了"**（离界 0.026 / 0.000 / 0.079 vs 分辨力 0.175~0.274）。
          ⇒ 若把"名义出带"当红，**今天就会红三条**，而红的三条**一条都没被判出来** ——
            那正是 `§一.1` 说的「误判」。**所以这条尺子只许报方向。**

    ## ⚠⚠ 合成图表探针**不是**生产口径 —— 别把这节读成"R1 上靶了"

    ★ **实测（10-13）：内容一换，R1 就换一个数**（同一个引擎、同一天、同一台机）：

    | 内容 | R1 |
    |---|---|
    | 灰阶斜坡（0→1 线性，768px 宽）| 0.543 |
    | 16 级灰块 | 0.466 |
    | 黑白灰三块（含**纯黑 1.2% 面积**，`C*=2.07` 是死值）| 0.309 |
    | 整张图表 | **0.395 ~ 0.409** |
    | 真实照片批（生产路径 n=11）| **0.689 / 0.863 / 0.916** |

    ⇒ ★ 合成图表的 R1 **恒低于**真实照片带（0.40 vs 0.54 下界）⇒
      **探针值不许拿 `[0.538, 0.837]` 当界判**（那是**真实照片**口径的界，`AGENTS §一.1`）。
      本节因此**分两半**：**带内判定只对真实照片批印**（`_r1_verdict`，今天全印"判不了"）；
      **KPI 只有一个"回归锁"** —— 在**固定探针**上钉住**固定值**，
      它的用途是**"谁动了预设/引擎，这里必须动"**，**不是**"我们上靶了"。

    ## ⚠ 探针为什么要关 `print_render.glare`

    ★ 引擎在 `print_render.glare.active=True` 下**跨进程不可逐位复现**
      （`presets.py:920-923` 自己记了根因：`glare.py → fast_stats.fast_lognormal`
       是 `@njit` 里的 **numba `np.random.randn()`，不受 `np.random.seed()` 约束**）。
      实测同一张探针：**同进程 5 连跑极差 1.0e-4**，**跨进程跑到 0.395 / 0.409**。
      ⇒ 关掉 glare 后**逐位可复现**（本次实测：跨进程两次都是 `0.407203`）⇒ 才配当锁。

    ## 本组三条检查各管什么

      ① **公式对不对**：手搓 Lab（每档中位都是整数）⇒ 逐位比 0.25 / 2.0，并断言分母**是相邻档**
      ② **顺序对不对**：三张"逐图比值一致、但逐档中位不同"的图 ⇒ 两种顺序给出**不同**答案，
         断言实现给出的是**先逐图算比**那个（这条是本组唯一能钉住限制②的检查）
      ③ **探针回归锁 + 判定的分级**：固定探针批的批中位钉在已知值；并证明"离界 < 分辨力"**不当缺陷**
    """
    import numpy as _np

    from . import color as _cl, presets as _pr, targets as _ts

    # ---- ① 公式：手搓 Lab ⇒ 逐位可算（`C*` 只放 a 轴，b=0，便于手算）----
    #   张 A：L*10 -> C*=5, L*30 -> C*=20  ⇒ R1 = 0.25
    #   张 B：L*10 -> C*=20, L*30 -> C*=10 ⇒ R1 = 2.0
    #   ★ 两档都塞满 `_R1_MIN_PX` 以上、且都 <= C*<=8 的中性门限内？—— 不，
    #     `C*=20` 不中性 ⇒ 这里**显式**用 `c_max=1e9` 关掉中性门（测的是公式，不是门限）。
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

    # ★ 限制①：分母**是相邻档**，不是"五档最大值"（那是 A1 黑端）
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

    # ---- ② 限制②：**先逐图算比、再取中位**（合成图上量不出差别，必须手搓三张）----
    #   三张图各自 R1 都是 1.0（2/2、4/4、6/6）；但两档的**中位**是 4 与 4 ⇒
    #   先取中位再相除 = 1.0 —— 两者恰好相同 ⇒ **换成"逐图比值不一致"的一组**：
    #   图1: 1/4 = 0.25 ｜ 图2: 4/4 = 1.0 ｜ 图3: 9/4 = 2.25
    #     · 先逐图算比再中位 = median(0.25, 1.0, 2.25) = **1.0**
    #     · 先取中位再相除 = median(1,4,9)=4 ÷ median(4,4,4)=4 = **1.0** —— 还是相同！
    #   ⇒ ★ 要分开这两种顺序，必须让**两档的中位之比 != 逐图比值的中位**。
    #     取：图1 (0-20)=1,(20-40)=4 ⇒ 0.25 ｜ 图2 (0-20)=8,(20-40)=4 ⇒ 2.0
    #         图3 (0-20)=1,(20-40)=1 ⇒ 1.0
    #     · 逐图再中位 = median(0.25, 2.0, 1.0) = **1.0**
    #     · 先中位再相除 = median(1,8,1)=1 ÷ median(4,4,1)=4 = **0.25**   ← 分开了
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

    # ---- ③ 探针回归锁：固定图表 × 6 档增益（`glare` 关掉 ⇒ 逐位可复现）----
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
    # ★ 与 `t_synth_transfer` 逐行同构：本体乘 `_PROBE_SCENE_GAIN`，顶部 `_PROBE_ABS_ROWS` 行不乘。
    _ov = dict(_ts.scene_engine(None, stock=_PRESET, cfg=C))
    _ov['print_render.glare.active'] = False          # ← 见 docstring：numba RNG ⇒ 跨进程不可复现
    _labs = []
    for _g in _R1_PROBE_GAINS:
        _lin = _np.clip(_cl.s2l(_ch) * (_PROBE_SCENE_GAIN * _g), 0.0, None)
        _lin[750:750 + _PROBE_ABS_ROWS, :, :] = _cl.s2l(_np.linspace(0.0, 1.0, _W))[None, :, None]
        _labs.append(_cl.to_lab(_pr.render(_lin, _PRESET, C, overrides=_ov)))
    _pb = _r1_batch(_labs)
    _pv = _r1_verdict(_pb['median'], _pb['n'], _pb['iqr'])
    check('★★★★ R1 探针回归锁：固定图表 × 4 档增益的**批中位**钉在 %.4f ± %.4f（= ±1.5%%）' % (
              _R1_PROBE_KNOWN, _R1_PROBE_TOL),
          abs(_pb['median'] - _R1_PROBE_KNOWN) <= _R1_PROBE_TOL,
          '批中位=%.6f（n=%d ｜ 图间 IQR=%.6f）｜ 判定=%s（离界 %.3f vs 分辨力 %.3f）' % (
              _pb['median'], _pb['n'], _pb['iqr'], _pv['verdict'], _pv['dist'], _pv['res']),
          '预设/引擎动了而这把尺子没跟着动 ⇒ **尺子接线断了**（不是"画面对了"）。'
          '★ 反过来说：这个数**不是**生产口径（合成图表的 R1 恒低于真实照片带）'
          '⇒ 它只保证"尺子还活着"，**不许**拿它当"我们上靶了"')

    # ★ 判定分级：**离界 < 分辨力 ⇒ 只报方向**（这条是 §一.5「不当缺陷」的可执行版）
    #   ⚠ 下面这组数是**现场真实读数**（鹿井那条 n=11：离界 0.033、我们自己 IQR 0.175）
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
        ('胶片风格：只有一条预设（鹿井预设）', t_presets),
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


# ---------------------------------------------------------------------------
# 1. 胶片风格（**只有一条**：鹿井预设）
# ---------------------------------------------------------------------------

def t_presets():
    """唯一那条预设的接线（10-15 从零重立后重写）。

    ★ 旧版钉的是"原来那 9 条一条都不少""10 条的冷暖基准不全同""逐卷真的不同" ——
      那些断言随旧 10 条一起删了：**n=1 时它们要么没有意义、要么恒真**（恒真 = 假绿）。
    ★ 留下的都是**单条也成立**、且都属于"静默失效"那一类：
      字段名写错 / 被卷标签冲掉 / 假旋钮 / 配平基准换源 / profile 名拼错。
    """
    ns = presets.names()
    check('预设目录 = 只有一条「鹿井预设」', ns == [_PRESET], '、'.join(ns) or '（空）',
          '10-15 从零重立：旧 10 条已删（分支 feature/kashii-create；原样留在 data/_archive/pre_clean_slate/）')
    check('它的 JSON 真在磁盘上', presets.has(_PRESET))

    # ★★ 关键字段必须**真的落到 params 上**，不是被引擎冲回出厂
    #   （`GrainParams`/`HalationParams` 都是普通 dataclass、没有 `__slots__`
    #    ⇒ 字段名写错**不报错**，只会静默多出一个没人读的属性）
    #    ⚠ 字段名跟 vendor 版本绑死：**0.3.2 = `agx_particle_*`**、0.3.4 = `particle_*`。
    #      ★★★ 09-30：原来这条自检验的是"我们写进去的那个名字" ⇒ 写进去、读出来、对上了 ⇒ **假绿**。
    #      ⇒ 现在**必须验 vendor 真正读的那个名字**。
    p = presets.digested(_PRESET)
    d = presets.load_raw(_PRESET)
    check('颗粒真落到 vendor 实读的字段（particle_*，不是 agx_particle_*）',
          abs(float(p.film_render.grain.particle_area_um2)
              - float(d['grain']['particle_area_um2'])) < 1e-9,
          '%.3f vs %.3f' % (p.film_render.grain.particle_area_um2,
                            d['grain']['particle_area_um2']),
          '颗粒字段对不上 ⇒ 有人改了 vendor 的字段名（0.3.2 叫 agx_particle_*、0.3.4 叫 particle_*）')
    check('颗粒不许再有 agx_ 死属性（防回退到"写了没人读"）',
          not hasattr(p.film_render.grain, 'agx_particle_area_um2')
          and abs(float(p.film_render.grain.particle_scale[0])
                  - float(d['grain']['particle_scale'][0])) < 1e-9,
          'agx_ 残留=%s ｜ scale %.2f vs %.2f'
          % (hasattr(p.film_render.grain, 'agx_particle_area_um2'),
             p.film_render.grain.particle_scale[0], d['grain']['particle_scale'][0]))
    check('光晕强度真落到 params（不被卷的抗晕层标签冲掉）',
          abs(float(p.film_render.halation.halation_strength[0]) * 100.0
              - float(d['halation']['halation_strength'][0])) < 1e-6,
          '%.4f vs %.1f' % (p.film_render.halation.halation_strength[0] * 100.0,
                            d['halation']['halation_strength'][0]),
          '`apply_stocks_specifics=True` 会按卷的抗晕层把它冲回出厂 ⇒ 必须保持 False')

    # ★★ 光晕幅度必须**在物理带内**（人像上限 8）。出处（README / GUI tooltip）：
    #   红通道典型值：弱 AH 2-8，无 AH 8-25；人像提示「拍人别超 8，超了脸上高光泛红」。
    #   ★ 10-15 从零重立后它是**引擎 schema 默认 5/1.5/0**（不是按卷标签手工抄的那档：
    #     `config.PRESET_APPLY_STOCK_SPECIFICS=False` ⇒ 卷的 AH 标签在生产链上一次都不执行）。
    #   ⇒ 这里钉的是**带**（物理事实），不是某个具体值（值属于创作，改它不用改自检）。
    _hs = tuple(round(float(v) * 100.0, 4) for v in p.film_render.halation.halation_strength)
    _amt = float(p.film_render.halation.halation_amount)
    check('★ 光晕幅度在物理带内（R 通道 2~8%，人像上限 8）', 2.0 <= _hs[0] <= 8.0,
          'R/G/B = %s ｜ 倍率 %.2f' % (_hs, _amt),
          '超 8 ⇒ 脸上高光泛红（GUI 人像提示原话）；超 25 ⇒ 只有"完全无抗晕层"的卷才允许')
    check('★ 光晕强度倍率在带内（1.0~1.4）', 1.0 - 1e-9 <= _amt <= 1.4 + 1e-9, '%.2f' % _amt,
          '`halation_amount` 是人像最容易翻车的一根：1.2~1.3 是上限，2 以上脸上高光泛红')

    # ★★ 「整张冷暖基准」的落点 = 印相端校色滤片（负片→印相体系里"白平衡"本来就是它）。
    #    钉两件事：① 这个落点**真通电**；② 它带 `PRESET_FILTER_M_TRIM` 的剂量（现在是 0.0）。
    _trim = float(getattr(C, 'PRESET_FILTER_M_TRIM', 0.0))
    _sim = d['simulation']
    _wb = (round(float(p.enlarger.y_filter_shift), 3), round(float(p.enlarger.m_filter_shift), 3))
    _wbb = (round(float(_sim['print_y_filter_shift']), 3),
            round(float(_sim['print_m_filter_shift']) + _trim, 3))
    check('★ 整张冷暖基准落在印相滤片上（Y/M **真进 params**，不是假旋钮）', _wb == _wbb,
          'params y=%.3f m=%.3f ｜ JSON y=%.3f m=%.3f' % (_wb + _wbb),
          'JSON 写了但 params 里不是那个数 ⇒ 又是一组"改了没反应"的假旋钮')

    # ★★★★ 配平基准**走引擎数据库**（逐 (相纸, 光源, 底片) 查表）。
    #   绝对锚：本链 = fujifilm_crystal_archive_typeii / TH-KG3 / kodak_ultramax_400
    #   ⇒ 库里是 [C, M, Y] = [0, 74.946, 85.099]（读 DB 的顺序是 c,m,y ⇒ **不许按 y/m 猜**）。
    #   为什么要绝对锚：光看"开着 DB"看不出 **Y/M 对调**（09-23 踩过一次）。
    check('★★ 配平基准走引擎数据库（不是一对硬编码常数）',
          bool(p.settings.neutral_print_filters_from_database),
          'db=%s' % p.settings.neutral_print_filters_from_database,
          '关掉就会退回"一对常数发给不同底片" —— 那正是 18%% 灰偏色的原因')
    check('★★ 而且 Y/M 不许对调（绝对锚：CAII/TH-KG3/ultramax400 ⇒ M 74.946 / Y 85.099）',
          abs(float(p.enlarger.m_filter_neutral) - 74.946) < 5e-3
          and abs(float(p.enlarger.y_filter_neutral) - 85.099) < 5e-3,
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
    """★★ 10-15：预设只剩一条 ⇒ 旧的「换预设 ⇒ 画面不同」没法测了（n=1）。

    换成**创作模式真正依赖的那根线**：`overrides` 必须真的改得动画面。
    为什么它不冗余：`overrides` 是**整份替换**（`presets.render` 的契约），
    少传基线会让 `print_render.density_curves_morph` **整根静默死掉** —— 表现正是
    "改完看不出变化 ⇒ 以为这根旋钮没用"（已坑过两个人）。
    """
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

# ---------------------------------------------------------------------------
# 2. 删层纪律：影调层 / 肤色层 / 认人认脸 **真的删了**（不是关开关）
# ---------------------------------------------------------------------------

def t_dropped_layers():
    r"""★★★ 09-29 `drop-tone-and-skin` + **10-10 `drop-grade`**：**整段删掉的层**。

    这组是"删干净了没有"的钉子 —— 防的是"关了开关但代码还在、谁哪天又把它接回去"：
      · **模块级**：`tone` / `face` / `facegain` / `region` / **`grade`** 必须 import 不进来；
      · **config**：`TONE_*` / `FACE_*` / `SKIN_*` / **`GRADE_*`** / `SCENE_SHOT_*` 一个都不许剩，
        `STYLE` / `GRADE_SCOPE` / `GRADE_REGION_SCOPE` / `PRESET_MID_SHIFT` / `TARGET_*_FLOOR_L` 同样；
      · **pipeline 的代码里**不许再出现那几层的名字；
      · **服务路由 `/styles`** 必须没了。
    ★ 10-10：**颜色层（`grade.py`）也整段删了** —— 理由见 `pipeline.run_from` 里那段注释。
    """
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

    # ★ 09-29 晚：`PERSON_*` / `SCENE_BACK_*` **不在这个黑名单里** ——
    #   它们是**重新加回来**的光位判据（只用低开销的"人在哪"，见 `t_person_light`）。
    _bad = [k for k in dir(C) if k.startswith(('TONE_', 'FACE_', 'SKIN_', 'GRADE_',
                                               'ANCHOR_', 'SCENE_SHOT_'))]
    _bad += [k for k in ('STYLE', 'PRESET_MID_SHIFT', 'TARGET_BLACK_FLOOR_L',
                         'TARGET_HI_FLOOR_L', 'GRADE_REGION_SCOPE', 'GRADE_SCOPE')
             if hasattr(C, k)]
    check('★★ config 里那几个键一个都不剩（`PERSON_*` / `SCENE_BACK_*` 例外：光位加回来了）',
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


# ---------------------------------------------------------------------------
# 2.5 「人在哪」+ 光位：**只用低开销那条**（09-29 晚加回来）
# ---------------------------------------------------------------------------

def t_person_light():
    r"""光位（`back` 轴）—— 判据是 **09-27 的 v3（分块亮度场 + 全相对量）**，
    输入只用 `person.py` 那个 **~0.3 s 的粗"人在哪"**（**不跑人脸检测**）。

    这组钉三件事：
      ① **只用低开销那条** —— 源码里不许出现人脸检测 / birefnet / onnxruntime；
      ② **判据对得上** —— 逆光判得出逆光族、平光判得出面光；
      ③ ★★ **判不动时必须弃权**（`None`），**不许硬给一个"顺平光"** —— 那是 v1 的老毛病，
         扰动关一测就翻（v3 的立身之本）。
    """
    import inspect
    import re as _re

    from . import person as _pmod
    from . import scene

    # ① ★★★ 只做"人在哪"：源码里（**剥掉注释与文档串之后**）不许有人脸检测 / birefnet
    _raw = inspect.getsource(_pmod)
    _code = _re.sub(r'r?"""[\s\S]*?"""', '', _raw)
    _code = '\n'.join(l.split('#')[0] for l in _code.splitlines())
    _bad = [n for n in ('FaceDetector', 'yunet', 'YuNet', 'birefnet', 'onnxruntime',
                        'face_skin', 'landmark', 'FaceLandmarker') if n in _code]
    check('★★★ `person.py` **只做人在哪**：不许有人脸检测 / birefnet / onnxruntime',
          not _bad, '命中: %s' % (_bad or '无'),
          '又把高开销那条接回来了 ⇒ 不要那个开销')
    check('★ 低开销那条 = mediapipe `selfie_multiclass`（输入固定 256×256）',
          'selfie_multiclass' in _code and 'SEG_SIDE = 256' in _code,
          '模型 = %s' % os.path.basename(_pmod.SELFIE))
    check('★ 模型文件在仓库里（不靠 pip / 不靠下载）', os.path.exists(_pmod.SELFIE),
          _pmod.SELFIE)

    # ② 判据本身（合成图，不需要 mediapipe —— 直接喂假掩膜）
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

    # ③ ★★★ 弃权必须是合法输出（v1 最大的毛病就是"无论如何都硬给一个答案"）
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

    # ④ `classify`：不给"人在哪" ⇒ 只有光位那一位是 '-'，其余照常
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

# ---------------------------------------------------------------------------
# 3. 契约
# ---------------------------------------------------------------------------



def t_hist():
    """直方图工具（`hist.py`）：LR 那四条曲线 + 5 个区 + 裁切三角。

    ⚠ 这一组是给"以后**看直方图不看数字**"兜底的 —— 图要是画错了，
      照着一张错的图做判断，比数字错还危险。
    """
    from . import hist

    # ① 四条曲线：长度 256、0~1、无 NaN
    c, clip = hist.channels(_gray_img(seed=31))
    check('★ 四条直方图曲线（亮度 + R/G/B）：各 256 bin、落在 0~1、无 NaN',
          len(c) == 4 and all(len(x) == 256 for x in c)
          and all(np.all(np.isfinite(x)) and x.min() >= 0.0 and x.max() <= 1.0 + 1e-9 for x in c),
          '亮度最大 %.2f' % float(np.max(c[0])))
    check('亮度那条跟 RGB 三条不是同一条（真算了，不是复制）',
          float(np.max(np.abs(c[0] - c[1]))) > 0.01)
    # ★ 纵轴必须是**固定口径**（满格 = 单档占画面 YMAX_RATIO），不是按每张图自己的最大值
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

    # ② 端点（裁切）：全黑 ⇒ 阴影端亮；全白 ⇒ 高光端亮；中间灰 ⇒ 都不亮
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

    # ③ ★★★ 09-30 修的漏洞：老判据数 `luma == 255`（= 三通道同时 255 = 纯白），
    #   会把"只有单通道到端点"整类漏掉。实测代价：鹿井参照的蓝通道裁了 0.227%，
    #   老判据报 0.000%（三角根本不亮）。这一条就是防它回退。
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

    # ④ 出图：尺寸对、别炸
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

    # ⑤ 命令行入口别断（`python -m svFilm.hist` 以后要常用）
    check('命令行入口在（`python -m svFilm.hist`）', callable(hist._main))


def t_targets():
    """靶（`targets.json`）：**唯一那条预设必须有自己的靶，且数字来自它自己那 26 张**。

    ★ 旧版要求"三条大师预设各自有专属靶 + 换预设 span 真的不一样" —— 预设只剩一条后
      那两条都测不了。现在钉的是**靶的出处**（26 张那批的 span 是外部真值），
      而不是"A 与 B 不同"。
    """
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
    r"""★★★ 10-09 新增：**连续调制** `{"by": <程度量>, "delta": Δ}` + **"光位不驱动影调"**这条设计约束。

    这一组守两件性质相反的事：
      ① **新机制对**：`{"by": d, "delta": Δ}` 必须**连续**（d=0/0.25/0.5/1 线性）、
         必须能落到**预设基线**（典型用法就是调光晕幅度这种"预设里的值"）、
         且**拿不到程度量 ⇒ 什么都不加**（弃权 = 安全侧，这条最关键）。
      ② **旧毛病不许回来**：`_scene_engine` 里 **`back=` 轴块不许再出现影调参数**
         （`print_render.density_curves_morph.*`）。为什么钉这条 —— 10-09 体检结论：
         "离散档 + 动态参数 = 阶跃"，而且它解释力几乎全来自 `E_bg` **同义反复**
         （φ 见 `效果debug/2026-10-09/1009_光位分组体系_体检与设计.html` 与技能 §194）。
         ⚠ 这条断言是**故意的**：以后谁想再把光位接回影调，会当场红，逼他先看那段证据。
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

    # ---- ② ★★★ 设计约束：光位不许驱动影调（**默认禁止 + 显式例外**）----
    #   ★ 10-11 改：原版只扫 `back=`，而 10-10 起我们把光位轴迁成了 `lighttype=`
    #     ⇒ 原断言**形同虚设**（查的键已没人用），规则实际被绕过。
    #     现改成扫**两种轴块**，并把例外**登记在 `targets.json` 的 `_tone_allow` 里**
    #     —— 从"没人看得见的漏洞"变成"有记录、要写理由的例外"。
    #   ⚠ 理由文字也改了：原文写"光位→影调已被证伪（同义反复 + 阶跃，§194）"，
    #     那是 10-09 用**打靶判据**（信噪比 0.75）得出的结论；按 10-11 的新规矩
    #     （摈弃打靶结论）**不该再写成"已被证伪"**。规则仍成立，但理由是**工程理由**：
    #     **离散档驱动全局影调 ⇒ 阶跃**。要开，必须给出**目检依据**并登记例外。
    _allow = (d.get('_tone_allow') or {}) if isinstance(d, dict) else {}
    off, allowed = [], []
    for k, blk in se.items():
        if not (isinstance(k, str) and (k.startswith('back=')
                                        or k.startswith('lighttype='))
                and isinstance(blk, dict)):
            continue
        for kk in blk:
            if 'density_curves_morph' not in kk:
                continue
            if kk in (_allow.get(k) or []):
                allowed.append('%s.%s' % (k, kk))
            else:
                off.append('%s.%s' % (k, kk))
    check('★★★ 光位轴块（`back=` / `lighttype=`）里的影调参数**必须登记在 `_tone_allow`**',
          not off, '未登记: %s ｜ 已登记: %s' % (off or '无', allowed or '无'),
          '离散档驱动全局影调 ⇒ 阶跃。要开必须给出**目检依据**并写进 `_tone_allow`（见 §194 的原始证据）')

    # ---- ③ 机制：注入一条 `{"by": ...}`（调**预设基线**、**逐通道** Δ），跑完还原 ----
    #   ⚠ 两个坑都是这条自检自己抓出来的：
    #     ① `back=*` 只在 **`back` 判出来**时命中（`_v is None ⇒ continue`）⇒
    #        **要"无条件"调制必须写进 `"*"` 层**，不能写 `back=*`；
    #     ② 光晕幅度在引擎里是 **0~1 的元组**（预设 JSON 的 8/2/0 是 ×100 过的）
    #        ⇒ Δ 必须**逐通道给列表**，给标量会默默改掉 R:G:B 比例。
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
    """★★ 09-26：**可调参数只在 `config.py`** —— 防"假旋钮"。

    症状：代码里写的是 `getattr(cfg, 'X', 字面默认)`，而 `config.py` 里**没有 X 这个键**
    ⇒ 永远取那个字面默认，**改源码里那个常量毫无反应**。
    """
    # ★★ 10-10：颜色层整段删除 ⇒ 原来那一串 `GRADE_*` 的"键 / 真被读"检查全部作废。
    #   现在只钉**引擎侧真正在用的键**（那些删不掉：引擎靠它们出图）。
    for k in ('SPEK_PE_SHIFT', 'MAX_SIDE', 'EXPORT_MAX_SIDE', 'GRAIN_BLUR_REF_LONG_SIDE',
              'CHROMA_BLUR_ENABLE', 'CHROMA_BLUR_W', 'PERSON_MODEL',
              'SCANFIX_ENABLE', 'PUBLIC_COLORSPACE'):
        check('config.%s 这个键真的在（不是 getattr 的裸默认）' % k,
              hasattr(C, k), '当前 %r' % getattr(C, k, None),
              '缺它 ⇒ 代码里 `getattr(cfg, …)` 永远取那个字面默认，'
              '改源码里同名常量**不会有任何反应**')



def t_scene():
    """场景判据（`scene.py`）：三轴都到得了 · key 带版本 · 源头过曝只在线性域判 ·
    `targets._scene` 覆盖**只加不减**、不给场景就一个字段不动。"""
    from . import scene, targets

    # ① 三轴都在，key 带版本号，且**同一张图判两次一样**
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

    # ② 每根轴的每一档都要**到得了**（落不到的分档 = 死的专家）
    #   ⚠⚠ 给的是**显示域**的值，而分档线在 **Lab L\*** 上 —— 中间隔着 sRGB 解码 + 立方根。
    #     "看起来中等"的 0.35 显示域其实是 L*≈38（落在「亮」档），在这儿红过两次。
    #     实测对应：0.087→L*7.3 / 0.19→L*20.1 / 0.95→L*95.6。
    dark = np.full((32, 32, 3), 0.087)
    mid = np.full((32, 32, 3), 0.19)
    bright = np.full((32, 32, 3), 0.95)
    flat = np.full((32, 32, 3), 0.35)
    wide = np.zeros((32, 32, 3)); wide[16:] = 1.0
    e = {scene.classify(x)['exp'] for x in (dark, mid, bright)}
    s = {scene.classify(x)['span'] for x in (flat, wide)}
    check('「曝光」三档都到得了（暗/正常/亮）', len(e) == 3, str(sorted(e)))
    check('「光比」至少两档到得了（平/大）', len(s) >= 2, str(sorted(s)))

    # ③ ★★ 源头过曝**只在线性域**判：显示域一样、线性不一样 ⇒ 结论必须不同
    #   ⚠ 判据是「通道最大值 ≥ 白点」，**不是**"三通道同时贴顶"（那样写永远不会响，
    #     因为显示域的"白"只是 sRGB 把 1.0 以上压到 255 的假象，见 `config` 里那段）。
    a = scene.classify(flat, lin=np.full((32, 32, 3), 0.5))['overwhite']
    b = scene.classify(flat, lin=np.full((32, 32, 3), 1.2))['overwhite']
    check('★★ 源头过曝在线性域判得出来（显示域一样、线性不一样 ⇒ 结论不同）',
          a is False and b is True, '线性 0.5 → %s ；线性 1.2 → %s' % (a, b),
          '只看显示域的话这两张"一样" ⇒ 这一轴就废了（成片那边早被重渲染压过了）')
    check('不给线性图 ⇒ overwhite = None（不硬猜）',
          scene.classify(flat)['overwhite'] is None)

    # ④ ★ 10-10：原来这里测「`_scene` 场景覆盖」—— 那个机制**只服务颜色层**
    #   （它覆盖的都是分色/彩度靶），颜色层删掉后**零消费者** ⇒ 机制与这段测试一起删。
    #   「按场景分参数」这件事仍然活着，但走的是 `_scene_engine`（改**引擎参数**）。


def t_contract():
    # ① ★★ 10-10：颜色层已整段删除 ⇒ 原来的"颜色层跑在引擎之后"这条**作废**。
    #   改成一条**更有力**的：整条链上**只有引擎在动像素** ⇒ 出图 = `presets.render` 的输出。
    _r_pure = pipeline.run_from(_mk_sample(_gray_img(seed=11)), stock=_PRESET)
    check('★★★ 整条链上**只有引擎在动像素**：出图 = `presets.render` 的输出',
          _pure_engine_same(_r_pure, _mk_sample(_gray_img(seed=11))),
          '与"只跑 presets.render"的差超出了引擎自身噪声',
          '还有别的层在偷偷改像素 ⇒ 颜色层没删干净')

    # ② 不认得的名字**当场报错**
    try:
        pipeline.run_from(_mk_sample(_gray_img(seed=13)), stock='根本没有这一条')
        ok = False
    except KeyError:
        ok = True
    check('胶片风格名字不认得 ⇒ 当场报错（不是静默出一张别的）', ok,
          '', '"名字不认得就静默走默认"是本项目最阴的一类坑，出现过三次')

    # ③ 报告要说实话（'grade' 这个键**不该再有** —— 它有 ⇒ 颜色层没删干净）
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
    # ★ 10-15：原来这里有一条「换胶片风格 ⇒ 不命中缓存」—— 预设只剩一条 ⇒ 删。
    #   靶 / 场景覆盖那条（下面）仍然钉着「缓存键必须含靶」。

    # ★ 10-10：原来这里有一条"换 `GRADE_ENABLE` ⇒ 不许命中缓存"的断言，随颜色层一起删
    #   （那个开关已经不存在了）。"引擎 overrides / 靶必须进键"那条**保留**（见下）。

    # ★★★ 09-29：**靶 / 场景覆盖（引擎 overrides）也必须进键**。
    #   以前这两项因为 `_sc` 在缓存键那段还没定义而**永远是 None**（被 except 吞掉）
    #   ⇒ 改了 `targets.json`（靶或 `_scene_engine`）仍命中旧缓存 = "拧了没反应"第 4 类。
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
        # ★★ 10-12 晚：**这条锁原来动的是 `["*"]` 的 `gamma_factor`，已经测不到东西了**。
        #   原因：10-12 把 4 个键（`gamma_factor` / `gamma_factor_slow` /
        #   `lightness_compression` / `grain.blur`）**下沉到 `_stock_engine[<预设>]`**，
        #   而它**优先级高于 `["*"]`**（见 `_note_stock_engine_1012` / `targets.py:218-225`）
        #   ⇒ 对本用例这 10 条已登记预设，改 `["*"]` 的 `gamma_factor`
        #   **对解析出来的 overrides 零影响** ⇒ 键不变 ⇒ 命中 ⇒ 假红。
        #   ⇒ 它此前之所以能过，是**靠 `span=平` 恰好活着**（`_d['_scene_engine'] = {'*': …}`
        #   顺手把那条轴删了，而用例图恰好命中 `span=平` ⇒ 解析值从 1.23 掉回 0.98 ⇒ miss）。
        #   `span=平` 10-12 一删就露馅。⇒ 改用**没有下沉的那一根**，锁的还是同一件事
        #   （"引擎 overrides 真的进了缓存键"）。
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
    r"""★★★★★ 工具层的两个**静默**陷阱（10-12）—— T4 `_base_overrides` · T6 `targets.load` 指纹。

    ⚠ 为什么值得立钉子：这两个**都不会报错**，只会把「我改的值没生效」
      变成「**我拿到的是另一份数**」—— 与 `_check_white_gate` / `_warn_if_morph_dead`
      同一类（本项目最贵的一类）。
    """
    import json as _json
    import tempfile as _tf

    from . import config as _C, targets as _T

    _lc = 'io.output_gamut_compress.lightness_compression'

    # ---- ① 基线**只能有一个来源**：`_base_overrides` ≡ `scene_engine({})` ----
    #   ★ 10-15：`_stock_engine` 的生产数据已清（只剩一条预设 ⇒ 那一层没有信息）。
    #     但 `_base_overrides(stock)` 这条 API 还在被脚本用 ⇒ 仍然要验它 == 生产基线。
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

    # ---- ② 没给 stock ⇒ 当场抛（不许静默给一份「跟预设无关」的基线）----
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

    # ---- ③ 破法：**注入**一层 `_stock_engine` 证明上面两条不是恒真 ----
    #   ★ 10-15：生产数据里已经没有 `_stock_engine`（只剩一条预设） ⇒ 不能靠"生产里有两条不同的值"
    #     来证明；改成**临时注入一条**（改内存里的 targets 字典，跑完还原），
    #     断言 `_base_overrides` 真的会用到它。层序机制仍然活着，只是当前没有数据。
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

    # ---- ④    # ---- ④ `targets.load()` 必须有文件指纹（T6）：**同一进程内**改文件要立刻生效 ----
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
