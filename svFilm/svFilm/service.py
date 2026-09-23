# -*- coding: utf-8 -*-
r"""svFilm 常驻 HTTP 服务 —— 引擎对外的第二条口子（给工作台 svStudio 调）。

## 为什么必须常驻
实测（700 长边，2026-09-14）：

| 做法 | 换一次卷 |
|---|---|
| 每次调一下、起一个 Python 进程 | **5.03 s**（且解码缓存留不下来）|
| **常驻 + 缓存解码** | **1.45 s** |

差 3.5 倍 —— `io.load_raw` 一个人占全链 **57%**。**常驻是硬要求，不是优化选项。**

## 设计要点
- **零依赖**（只用标准库 `http.server`）—— 为了"能搬到别人机器上"。
- ★ **一条 `/render` 就够**：`GET /render?id=3&stock=Portra400薄荷&side=700` → 图。
  前端直接 `<img src="...">`，**改参数 = 改 URL**，不用写任何取字节的代码。
- **只绑 127.0.0.1**（不对外）。
- 缓存 = `io.load()` 的产物（Sample），LRU 上限可调。

## 跑
```bash
$PY -m svFilm.service                 # 默认 127.0.0.1:8765
$PY -m svFilm.service --port 8800 --cache 40
```

## 接口
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{ok, cached, version}` |
| GET | `/stocks` | **胶片风格**列表（9 条预设：name/label/desc） |
| GET | `/styles` | **曝光风格**列表（高长调 / 中性调 / 暗调） |
| GET | `/load?paths=a,b&side=700` | **同步**载入并缓存（慢，1.9 s/张；前端分批调） |
| GET | `/render?id=3&stock=Portra400薄荷&style=中性调&side=700` | 出图（直接返回图片字节） |
| GET | `/stats?id=3` | 只出数字，不出图（调参时看指标用） |
| GET | `/list` | 当前缓存里有什么 |
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import config as C
from . import io, pipeline, presets, tone

VERSION = 'svstudio-1'
DEFAULT_PORT = 8765
DEFAULT_SIDE = 700          # ★ 预览用 700（1080 屏根本显示不到 1000 px）
DEFAULT_CACHE = 40          # 最多缓存多少张

_cache = OrderedDict()      # id -> dict(sample=..., path=..., side=..., t=...)
_cache_lock = threading.Lock()
_next_id = [1]

# ★ 按「路径+尺寸」缓存已解码的 Sample —— 前端重复 /load 同一张时直接命中，
#   不再重新解码（RAW 一张 2.3 秒）。和上面的 id 缓存是两层：上面那层给 /render 用，
#   这一层给「同一张图反复 /load」用（换模式、翻回来、重出图）。
_path_cache = OrderedDict()
_path_cache_lock = threading.Lock()
_PATH_CACHE_MAX = [24]

# ★★ 段缓存（09-15 SV 选「A」）：把「胶片出图」那一段及其之前的产物留下来。
#   一次出图 3.7 s 里真卷渲染占 2.4 s，而拖「脸/白区」那几根滑杆时它**一个像素都不会变**
#   ⇒ 只重跑 L3 肤色 + L4 护栏：**3.8 s → 0.99 s（实测 −74%）**。
#   ⚠ 关掉（`config.CACHE_ENABLE=False` 或起服务时 `--no-stage-cache`）＝ 每次全跑，
#     与加缓存之前**逐位相同**（自检里钉着这条）。
_STAGES = [pipeline.StageCache() if getattr(C, 'CACHE_ENABLE', False) else None]

# ★★ 「解码阶段」参数 —— `ENTRY_*`（入口零点 + 成形 + 趾部 + 逐张落点）和 `RAW_DECODE`
#   是在**解码那一步**就跑掉的（`io.load_raw`），算完才存进 Sample。
#   而 Sample 被下面两层缓存留着 ⇒ **不重新解码的话，改这些参数根本不会生效**：
#   只有第一次 /load 那一下管用，之后怎么拉都没反应。
#
#   这是 09-15 上「暗部亮度 / 整张亮暗(总)」这两根滑杆时**必须先修的**：
#   不修它，那两根滑杆就是"拉半天没反应"，**比没有更糟**（看起来像坏了）。
#   ⚠ 顺带纠正一个旧误会：`pipeline.SIG_CACHE` 里也有 `ENTRY_` —— 那条只让**段缓存**失效，
#     管不到解码这一层；真正要改的是下面的解码缓存键。
#   ⇒ 做法：解码缓存的键里带上「解码签名」，签名一变就重新解码。
#     代价 = 重新解码一次（JPG 约 0.36 s / RAW 约 2.3 s @700 长边，和拖「整张亮暗」同量级）。
#   ⚠ 不收 `MAX_SIDE`：它是**默认参数**（`def load_raw(path, max_side=C.MAX_SIDE)`），
#     真正的尺寸由调用方传进来、而且已经算在键里了（见 `_load_key` 的第 2 个元素）。
_DECODE_SIG_KEYS = tuple(sorted(
    [k for k in dir(C) if k.startswith('ENTRY_')] + ['RAW_DECODE']))


def _decode_sig():
    """解码阶段那一段的参数指纹。

    """
    out = []
    for k in _DECODE_SIG_KEYS:
        v = getattr(C, k, None)
        if isinstance(v, (list, tuple)):
            v = tuple(v)
        elif isinstance(v, dict):
            v = tuple(sorted(v.items()))
        elif not isinstance(v, (int, float, str, bool, type(None))):
            v = repr(v)
        out.append((k, v))
    return tuple(out)


def _load_key(path, side):
    """解码缓存的键 = 路径 + 尺寸 + **解码签名**。

    ⚠ `side=None` = **原图全尺寸**（09-15 SV 选「A」的导出默认）⇒ 必须映射成**独立的键**，
      不能 `int(None)` 崩、也不能跟别的尺寸撞在一起（撞了就是"拿 700 那份当真"）。
    """
    return (os.path.abspath(path), 'full' if side is None else int(side), _decode_sig())


def _cache_put(path, side, sample):
    with _cache_lock:
        i = _next_id[0]
        _next_id[0] += 1
        _cache[i] = dict(sample=sample, path=path, side=side, t=time.time(),
                         decoded=_decode_sig())
        while len(_cache) > _CACHE_MAX[0]:
            _cache.popitem(last=False)
    return i


def _cache_get(i):
    with _cache_lock:
        return dict(_cache.get(i) or {})


def _ensure_decoded(i, row):
    """出图前比一次解码签名：不一致就**重新解码**（否则入口那两根滑杆是死的）。

    """
    cur = _decode_sig()
    if row.get('decoded') == cur:
        return row['sample']
    s, _ms = _load_one(row.get('path'), row.get('side') or DEFAULT_SIDE)
    with _cache_lock:
        if i in _cache:                       # 顺手把 id 那层也换掉，下一发就直接命中
            _cache[i]['sample'] = s
            _cache[i]['decoded'] = cur
    return s


def _load_one(path, side):
    """★ 慢的那一步（解码 + 入口 + 锚点）—— 只在这里做一次。

    ⚠ 必须**按「路径 + 尺寸 + 入口签名」复用**：台子上换模式 / 翻回来 / 重新出图都会再喊一次 /load，
      如果没有这一层，每次都要重新解码一遍（实测 RAW 2.3 秒）⇒ 前端就觉得"卡"。
      之前这里每次都真解码（只有 `/render` 那一层缓存），是 09-14 SV 报「点调色台非常卡」的根因之一。
    ★ 09-15 键里加了**入口签名**（`_load_key`）：入口参数一改就作废、重新解码。
    """
    key = _load_key(path, side)
    with _path_cache_lock:
        hit = _path_cache.get(key)
    if hit is not None:
        return hit['sample'], 0.0
    t = time.perf_counter()
    s = io.load(path, side, src=None)
    ms = (time.perf_counter() - t) * 1000.0
    with _path_cache_lock:
        _path_cache[key] = dict(sample=s, t=time.time())
        while len(_path_cache) > _PATH_CACHE_MAX[0]:
            _path_cache.popitem(last=False)
    return s, ms


def _want_side(side):
    """把请求里的 `side` 解析成「要多大长边」。

    返回 `(want, err, req)`：
      * `want=None`   = **没要求**（不给 / 空 / `0`）⇒ 调用方沿用已经解码好的那份（老行为）。
      * `want=<int>`  = 要这个长边（夹在 `[16, C.MAX_SIDE]`；被夹了 `req` 记下原值）。
      * `err` 非空    = 这个 side 不合法 —— **说清楚**，不许静默当没给。

    ⚠ 上限取 `C.MAX_SIDE`（这台引擎自己的工作分辨率，2048）：
      比它更大对"在屏幕上看细节"没有意义，而**原图全尺寸会打爆内存**
      （实测 40MP 时 spektrafilm 内部一个 (81, 40M) 的 float64 中间量要 24.2 GiB，
       进程当场死 —— 见 `_export_one` 的注释）。被夹住要回 `side_clamped`，不静默降级。
    """
    if side is None or str(side).strip() in ('', '0'):
        return None, None, None
    try:
        req = int(float(str(side).strip()))
    except (TypeError, ValueError):
        return None, 'side 不合法：%r' % (side,), None
    cap = int(getattr(C, 'MAX_SIDE', 2048) or 2048)
    return max(16, min(req, cap)), None, req


def _render_bytes(i, stock, style, side, fmt, quality):
    """出图。

    ★★ 09-15 修一个真 bug：`side` 以前是**收下就扔**（文档写的是"前端要别的尺寸得
      重新 /load"，可前端从来不 /load 第二个尺寸）⇒ 结果**任何 side 都返回 /load 那个尺寸**。
      实测（真 HTTP，同一张片）：请求 side=700 / 1400 / 2048，三发回来**字节完全相同**
      （都是 467x700）⇒ 调色台想看清细节**根本没法**要一张更大的预览。
      这就是「屏幕上那张图只有长边 700 像素、还被放大着画 ⇒ 脸/皮肤看不到像素」的根因。
      ⇒ 现在真的按 `side` 走：
        * 不给 / 与缓存那份一致 ⇒ **用缓存那份**（老行为，逐位不变）；
        * 给了别的尺寸 ⇒ 按那个尺寸**重新解码**再跑链（`_load_one` 按「路径 + 尺寸 +
          入口签名」缓存 ⇒ 重复要同一尺寸不会重复解码；RAW 约 2.3 s @700，更大按像素数涨）。
      ⚠ 换尺寸**不复用**别的尺寸那份 Sample —— 尺寸不同，颗粒/锐化/降噪的**相对**效果
        都不一样（实测同一块平坦区的颗粒：2048/3000/原图 = 0.63/0.77/1.87），
        复用就是拿小尺寸的像素冒充大尺寸。

    `style` = 曝光风格（高长调 / 中性调 / 暗调）。空 = 用 `config.STYLE`。
      ⚠ 胶片风格与曝光风格**互相独立**：9 条 × 3 档 = 27 种组合，都能出。
    """
    row = _cache_get(i)
    if not row:
        return None, {'error': 'id 不在缓存里，先 /load'}
    want, err, req = _want_side(side)
    if err:
        return None, {'error': err}
    cur_side = int(row.get('side') or DEFAULT_SIDE)
    if want is None or want == cur_side:
        s = _ensure_decoded(i, row)       # ★ 解码阶段参数改了要重新解码（见 _decode_sig）
    else:
        s, _ms = _load_one(row.get('path'), want)      # ★ 换尺寸 = 重新解码
    r = pipeline.run_from(s, stock=stock or None, style=style or None,
                          cache=_STAGES[0])
    disp = np.clip(r.disp, 0.0, 1.0)
    arr = (disp * 255.0 + 0.5).astype(np.uint8)
    info = {'ms': round(r.report.get('ms', 0)),
            # ★ 把**真正用出去的尺寸**回给调用方（走响应头）—— 不然"它有没有照我说的
            #   尺寸出"只能靠读图猜，而 `side` 被无视这件事正是这么藏了这么久的。
            'w': int(disp.shape[1]), 'h': int(disp.shape[0]),
            'side': want if want is not None else cur_side}
    if want is not None and req is not None and want != req:
        info['side_clamped'] = True
        info['side_requested'] = req
    if fmt in ('jpg', 'jpeg'):
        from PIL import Image
        import io as _io
        buf = _io.BytesIO()
        Image.fromarray(arr).save(buf, format='JPEG', quality=int(quality))
        info['mime'] = 'image/jpeg'
        return buf.getvalue(), info
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    Image.fromarray(arr).save(buf, format='PNG')
    info['mime'] = 'image/png'
    return buf.getvalue(), info


def _export_one(i, out_path, stock, style, side, quality, src_path=None):
    r"""★ 导出**成片**（09-15 SV 选「A」第 ② 项）：把渲染结果写成真照片文件。

    为什么这件事由引擎做（而不是把 base64 交给前端让它写）：
      `io.save` 已经处理好了 **EXIF / 4:4:4（无色度抽样）/ 质量**；
      前端拿 base64 再写一遍 = 丢相机信息 + 多一次编解码。

    ⚠ 导出尺寸与预览尺寸**不是**同一个：预览固定 700，导出**默认就是原图全尺寸**
      （09-15 SV 选「A」定的）。胶片颗粒是**物理量**，尺寸一变观感就会变 ——
      **而且是"越大颗粒越明显"**（每个像素收集到的银盐颗粒 ∝ 像素面积，越小越少 ⇒ 相对噪声越大）：
      同一块平坦区实测 2048/3000/原图 = 0.63/0.77/1.87。这不是 bug，是这条链的本性。
      所以由调用方**显式**给 `side`，引擎照那个尺寸**重新解码 + 重新跑一遍**
      （不复用预览那份 Sample：尺寸不同，复用就是作弊）。
    ⚠ **不挂段缓存**（`cache=None`）：导出是大尺寸，塞进 LRU 会把预览那几套挤掉。
    ⚠ 原图全尺寸现在**跑得动**（`spektra._install_band()` 把 3→81→3 那个逐像素的
      大中间量按行切条带，逐位不变）：实测 7752×5178 = **380 s/张、峰值提交 13.3 GB、19 MB**。
      上限由 `C.EXPORT_MAX_SIDE` 把关（**默认 None = 不限**；设成数字就夹住并回 `side_clamped`）。

    返回 `(info, err)`；成功时 `info['ok'] = True`。
    """
    row = _cache_get(i) if i else {}
    # ★ 源图两条路：`src`（**直接给原图路径** —— 导出走这条，它不依赖"预览先 /load 过"）
    #   或 `id`（复用缓存里那条记录的原图路径）。两条都没有 ⇒ 说清楚，别猜。
    src = src_path or row.get('path')
    if not src:
        return None, {'error': '缺 src（原图路径）或 id（先 /load）'}
    if not out_path:
        return None, {'error': '缺 path（导出到哪）'}
    out_path = os.path.abspath(out_path)
    d = os.path.dirname(out_path)
    if not d or not os.path.isdir(d):
        return None, {'error': '目录不存在: %s' % d}
    # ★★ 导出尺寸**必须有上限**（实测出来的，不是保守估计）：
    #   原图全尺寸（7752×5164 = 40MP）在这条链上**直接 OOM** —— spektrafilm 内部
    #   一个 (81, 40M) 的 float64 中间量就要 **24.2 GiB**，引擎进程会当场死掉。
    #   上限 3000 长边（6MP）：真跑完 56 秒、峰值数组 138 MB，安全。
    #   ⚠ 被夹住要**说出来**（返回 `side_clamped`）—— 不许静默降级成"小一点的图"。
    # ★★ 尺寸口径（09-15 SV 选「A」：**默认就出原图尺寸**）：
    #   `side=None`（调用方不传）= **原图全尺寸**；给数字 = 那个长边。
    #   ⚠ `io._resize` 对 `max_side=None` 是"保持原样" ⇒ 这条路上不需要别的哨兵值。
    _cap = getattr(C, 'EXPORT_MAX_SIDE', None)      # None = 不设上限
    if side is None:
        side, side_clamped = None, False
    else:
        side = int(side)
        side_clamped = bool(_cap) and side > int(_cap)
        if side_clamped:
            side = int(_cap)
    try:
        # ★ 按导出尺寸重新解码 + 重新跑（`_load_one` 自带"路径+尺寸+入口签名"那一层缓存）
        s, _ms = _load_one(src, side)
        r = pipeline.run_from(s, stock=stock or None, style=style or None, cache=None)
    except MemoryError:
        # 原图尺寸要 ~13 GB（65% 在"胶片出图"那一大段）。内存不够时**说清楚**，
        # 别让它冒一个 numpy 的 `_ArrayMemoryError` 让人看不懂。
        return None, {'error': '内存不够跑这个尺寸（%s）。关掉几个占内存的程序再试，'
                               '或把 config.EXPORT_MAX_SIDE 设成 3000 当上限。'
                               % ('原图' if side is None else '%d 长边' % side)}
    io.save(r.disp, out_path, exif=(s.exif or None), quality=int(quality))
    return dict(ok=True, path=out_path.replace('\\', '/'),
                w=int(r.disp.shape[1]), h=int(r.disp.shape[0]), side=side,
                full=side is None, side_clamped=side_clamped,
                ms=round(r.report.get('ms', 0)),
                bytes=os.path.getsize(out_path)), None


def _base_bytes(i, fmt='jpg', quality=92, side=None):
    """「原图」栏用：把缓存的 Sample 直接出图 —— **不跑任何调色**（恒等）。

    为什么不让前端去读原始 JPG：① 原始 JPG 的尺寸/方向跟渲染结果不是一把尺子，并排看会误导；
    ② 走这里出来的影像与 `/render` **同分辨率、同口径**，A/B 才公平。

    ★★ 09-15：`side` **也得认**（原来根本没有这个参数）—— 不然 `/render` 提到更大的
      尺寸之后，左栏还是 700 ⇒ **两栏就不是一把尺子了**，而那正是本函数上面警告过的事。
      口径与 `/render` 完全一致：不给 / 与缓存一致 = 用缓存那份；给了别的 = 按那个尺寸重新解码。
    """
    row = _cache_get(i)
    if not row:
        return None, {'error': 'id 不在缓存里，先 /load'}
    want, err, req = _want_side(side)
    if err:
        return None, {'error': err}
    cur_side = int(row.get('side') or DEFAULT_SIDE)
    sample = row['sample']
    if want is not None and want != cur_side:
        sample, _ms = _load_one(row.get('path'), want)
    disp = np.clip(sample.disp, 0.0, 1.0)
    arr = (disp * 255.0 + 0.5).astype(np.uint8)
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    if str(fmt).lower() in ('png',):
        Image.fromarray(arr).save(buf, format='PNG')
        mime = 'image/png'
    else:
        Image.fromarray(arr).save(buf, format='JPEG', quality=int(quality))
        mime = 'image/jpeg'
    info = {'mime': mime, 'w': int(disp.shape[1]), 'h': int(disp.shape[0]),
            'side': want if want is not None else cur_side}
    if want is not None and req is not None and want != req:
        info['side_clamped'] = True
        info['side_requested'] = req
    return buf.getvalue(), info


def _stats_of(i, stock, style=None):
    row = _cache_get(i)
    if not row:
        return {'error': 'id 不在缓存里'}
    s = _ensure_decoded(i, row)           # ★ 解码阶段参数改了要重新解码
    r = pipeline.run_from(s, stock=stock or None, style=style or None,
                          cache=_STAGES[0])
    from . import color
    lab = color.to_lab(np.clip(r.disp, 0, 1))
    L = lab[..., 0]
    Cc = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    t = r.report.get('tone') or {}
    return dict(ms=round(r.report.get('ms', 0)),
                stock=r.report.get('stock'), style=r.report.get('style'),
                # ★ 曝光这一道**实打实做到哪了**（不是请求里那个）—— 一眼看出
                #   "靶是多少 / 实际到多少"，免得画面跟预期不一样却查不出原因。
                L5=round(float(t.get('L5_out', 0)), 1),
                L50=round(float(t.get('L50_out', 0)), 1),
                L95=round(float(t.get('L95_out', 0)), 1),
                ev=round(float(t.get('ev_mid', 0)), 2),
                L50_measured=round(float(np.median(L)), 1),
                b=round(float(np.median(lab[..., 2])), 2),
                c50=round(float(np.median(Cc)), 2))


class _H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):        # 别刷屏
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b)

    def _img(self, b, mime, info=None):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        # ★★ 把**实际出的尺寸**写进响应头（09-15）：不写的话"它到底有没有照我说的尺寸出"
        #   只能读图去猜 —— 而 `side` 被无视这件事正是这么藏了这么久的。
        #   自检/探针拿这几个头就能断言，不必解 JPEG。
        for k, h in (('w', 'X-Sv-W'), ('h', 'X-Sv-H'), ('side', 'X-Sv-Side'),
                     ('side_requested', 'X-Sv-Side-Requested'),
                     ('side_clamped', 'X-Sv-Side-Clamped')):
            if info and info.get(k) is not None:
                self.send_header(h, str(info[k]))
        self.send_header('Access-Control-Expose-Headers',
                         'X-Sv-W, X-Sv-H, X-Sv-Side, X-Sv-Side-Requested, X-Sv-Side-Clamped')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == '/health':
                with _cache_lock:
                    n = len(_cache)
                return self._json(dict(ok=True, version=VERSION, cached=n,
                                       side=DEFAULT_SIDE,
                                       # ★ 段缓存的状态：sets/max_sets/hits/misses/mb
                                       #   （前端状态条想显示"这一发是重算的还是复用的"就看 hit）
                                       stage_cache=(_STAGES[0].stats() if _STAGES[0] else None)))
            if u.path == '/stocks':
                # ★ 胶片风格列表从 `presets` 现读（别在这里写死名字：09-14 出过 bug，
                #   `air` 从卷表删掉后这里还在点名 ⇒ /stocks 直接 500）。
                #   每一条 = 一份 public GUI 的完整参数快照（`data/presets/*.json`）。
                return self._json([dict(name=n, label=presets.label_of(n)[0],
                                        desc=presets.label_of(n)[1], engine=True)
                                   for n in presets.names()])
            if u.path == '/styles':
                # ★ 曝光风格列表。**默认哪一档由引擎给**（`config.STYLE`）——
                #   前端不许自己写死档位名。
                # ⚠ 两套语义按 `config.TONE_AFTER_ENGINE` 走：
                #   动作在引擎之后 ⇒ 每档是**三套力度**（压曝光 / 压高光 / 提阴影），
                #   动作在引擎之前 ⇒ 每档是**三个绝对靶**（从大师真片量出来的 L5/L50/L95）。
                if bool(getattr(C, 'TONE_AFTER_ENGINE', False)):
                    return self._json([
                        dict(name=n, desc=tone.rel_of(n)['desc'],
                             isDefault=bool(n == getattr(C, 'STYLE', None)),
                             evDown=tone.rel_of(n)['ev_down'],
                             hiDown=tone.rel_of(n)['hi_down'],
                             shUp=tone.rel_of(n)['sh_up'])
                        for n in tone.names()])
                return self._json([dict(name=n, desc=tone.get(n)['desc'],
                                        isDefault=bool(n == getattr(C, 'STYLE', None)),
                                        L50=tone.get(n)['mid_L'],
                                        L5=tone.get(n)['black_L'],
                                        L95=tone.get(n)['white_L'])
                                   for n in tone.names()])
            if u.path in ('/', '/index.html') and _WEB[0]:
                # ★ 可选：把工作台的静态页 serve 出来（路径由 `--web` 给，**不写死** ⇒ 边界不破）
                fp = os.path.join(_WEB[0], 'index.html')
                if os.path.exists(fp):
                    with open(fp, 'rb') as f:
                        b = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                    return
            if u.path == '/scan':
                # 只列**用户指定的**目录（不自己去翻盘）—— 按 .RAF/.JPG 收，自然序排
                d = q.get('dir') or ''
                if not d or not os.path.isdir(d):
                    return self._json({'error': '目录不存在: %s' % d}, 400)
                exts = tuple('.' + e.strip().lower()
                             for e in (q.get('ext') or 'raf,jpg,jpeg').split(',') if e.strip())
                lim = int(q.get('limit') or 400)
                ps = []
                for n in sorted(os.listdir(d)):
                    if n.lower().endswith(exts):
                        ps.append(os.path.join(d, n).replace('\\', '/'))
                return self._json(dict(dir=d, n=len(ps), files=ps[:lim]))
            if u.path == '/list':
                with _cache_lock:
                    out = [dict(id=i, path=v['path'], side=v['side'])
                           for i, v in _cache.items()]
                return self._json(out)
            if u.path == '/load':
                ps = [x.strip() for x in (q.get('paths') or '').split(',') if x.strip()]
                side = int(q.get('side') or DEFAULT_SIDE)
                if not ps:
                    return self._json({'error': '缺 paths'}, 400)
                out = []
                for p in ps:
                    if not os.path.exists(p):
                        out.append(dict(path=p, error='文件不存在'))
                        continue
                    s, ms = _load_one(p, side)
                    out.append(dict(id=_cache_put(p, side, s), path=p, ms=round(ms)))
                return self._json(out)
            if u.path == '/base':
                # 「原图」栏：缓存里的 Sample 直接出图（恒等、不跑调色）
                # ★ `side` 也传进去 —— 两栏必须同尺寸，不然并排看会误导（见 `_base_bytes`）
                b, info = _base_bytes(int(q.get('id') or 0),
                                      q.get('fmt') or 'jpg', q.get('q') or 92,
                                      q.get('side'))
                if b is None:
                    return self._json(info, 404)
                return self._img(b, info['mime'], info)
            if u.path == '/render':
                b, info = _render_bytes(int(q.get('id') or 0), q.get('stock'),
                                        q.get('style'), q.get('side'),
                                        (q.get('fmt') or 'jpg').lower(),
                                        q.get('q') or 92)
                if b is None:
                    return self._json(info, 404)
                return self._img(b, info['mime'], info)
            if u.path == '/export':
                # ★★ 导出成片（09-15 SV 选「A」）：把渲染结果写成**真照片文件**。
                #   ⚠ `side` 不传 = **原图全尺寸**（SV 选「A」定的默认）——
                #     绝不是悄悄用 700 那份预览（那是"看着对、其实缩水"）。
                #   ⚠ 写盘要时间：2048 长边十来秒、**原图尺寸 ~6 分半**（RAW）⇒ 前端超时要放宽。
                _sv = (q.get('side') or '').strip()
                info2, err2 = _export_one(int(q.get('id') or 0), q.get('path'),
                                          q.get('stock'), q.get('style'),
                                          int(_sv) if _sv else None,
                                          q.get('q') or C.JPEG_QUALITY,
                                          src_path=q.get('src'))
                if info2 is None:
                    return self._json(err2, 404)
                return self._json(info2)
            if u.path == '/stats':
                return self._json(_stats_of(int(q.get('id') or 0), q.get('stock'),
                                            q.get('style')))
            return self._json({'error': 'no such path', 'path': u.path}, 404)
        except Exception as e:                                    # noqa: BLE001
            return self._json({'error': '%s: %s' % (type(e).__name__, str(e)[:200])}, 500)



_CACHE_MAX = [DEFAULT_CACHE]
_WEB = [None]        # --web <dir>：要不要顺手 serve 一个静态前端（可选）


def _port_taken(port, host='127.0.0.1', timeout=2.0):
    r"""端口上**已经有人应答**了吗？（只连一下，不发请求）

    ★★★ 09-15（SV 报「点出图没反应，后台也没动静」）挖出来的：
      台子每次启动**都起了两个引擎**（`engine_start.log` 里 spawn 一律成对出现），
      两个都 LISTENING 8765 —— 因为 `ThreadingHTTPServer.allow_reuse_address = 1`
      在 Windows 上走的是 **SO_REUSEADDR**，语义是"**可以抢**"而不是"用完立刻能重绑"：
      **第二个 bind 不报错**，两个进程就这么同时挂在一个端口上（POSIX 上 SO_REUSEADDR
      只管 TIME_WAIT，不会有这种事 —— 这是 Windows 特有的坑）。
      ⇒ 后果是**请求被哪个进程收到不确定**：落在"那个刚起、什么都没载入"的空引擎上时，
        它会瞬间回一句"id 不在缓存里"就完事 ⇒ 界面上"点了没反应"、而那个真在干活的引擎
        CPU 一动不动 —— 正是 SV 描述的现象。
      ⇒ 所以：**启动前先探一下端口**，有人应答就**响亮地退出**，绝不静默开第二个。
    """
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


class _Server(ThreadingHTTPServer):
    r"""★ 只给本机用的 HTTP 服务器 —— **故意关掉 `allow_reuse_address`**。

    `http.server.HTTPServer` 默认 `allow_reuse_address = 1`，也就是 `setsockopt(SO_REUSEADDR)`。
    POSIX 上它只管「TIME_WAIT 的端口可以重绑」，**Windows 上语义不一样：它是"可以抢"**
    —— 第二个进程 `bind` 同一个端口**不报错**，于是两个引擎会同时挂在 8765 上，
    请求随机落到其中一个（09-15 那个「点出图没反应」就是这么来的，详见 `_port_taken`）。

    关掉之后 Windows 上第二次 bind 会**老实报错**（`serve()` 里接住并打印原因），
    POSIX 上唯一的代价是「刚停掉的一瞬间重绑可能失败」，对本机常驻服务无所谓。
    真要并排跑两个引擎，显式换 `--port`。
    """
    allow_reuse_address = False


def serve(port=DEFAULT_PORT, host='127.0.0.1', cache=DEFAULT_CACHE, web=None):
    _CACHE_MAX[0] = int(cache)
    _WEB[0] = web
    # ★★★ 拒绝"悄悄开第二个"（见 `_port_taken` 的说明）——
    #   这一条只防**我们自己的重复启动**；真要并排放两个，显式换 `--port`。
    if _port_taken(port, host):
        print('!! 端口 %d 上已经有一个 svFilm 引擎在跑了 —— 这次启动**直接退出**，不会开第二个。' % port)
        print('   · 想用它：什么都不用做（台子会自己接上去）')
        print('   · 想重启：先把那个进程杀掉（任务管理器里的 python.exe / `taskkill`）')
        print('   · 想并排放一个：显式给别的端口，例如 --port 8766')
        return 1
    try:
        srv = _Server((host, int(port)), _H)
    except OSError as e:
        print('!! 端口 %d 绑不上：%s' % (port, e))
        print('   （多半是已经有一个引擎在跑 —— 见上一段的三种处理办法）')
        return 1
    srv.daemon_threads = True
    print('svFilm 服务已起： http://%s:%d   （缓存上限 %d 张，预览长边 %d）'
          % (host, port, _CACHE_MAX[0], DEFAULT_SIDE))
    if web:
        print('  工作台页面： http://%s:%d/   （静态目录 %s）' % (host, port, web))
    else:
        print('  试一下： curl "http://%s:%d/health"' % (host, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n停了。')


def main(argv=None):
    ap = argparse.ArgumentParser('svFilm.service', description='svFilm 常驻 HTTP 服务')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--cache', type=int, default=DEFAULT_CACHE)
    ap.add_argument('--no-stage-cache', action='store_true',
                    help='关掉「段缓存」（每次全跑；用于对照/排错，行为与加缓存前逐位相同）')
    ap.add_argument('--web', default=None,
                    help='可选：顺手 serve 一个静态前端目录（例如 ../svStudio/web）')
    a = ap.parse_args(argv)
    if a.no_stage_cache:
        _STAGES[0] = None
    # ★ `serve()` 返回非 0 = **没有起来**（端口上已经有一个，或绑不上）——
    #   用非 0 退出码说出去，台子那边 `[child exit] code=1` 就能和日志对上。
    rc = serve(a.port, a.host, a.cache, a.web)
    if rc:
        raise SystemExit(rc)


if __name__ == '__main__':
    main()
