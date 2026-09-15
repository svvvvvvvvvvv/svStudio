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
- ★ **一条 `/render` 就够**：`GET /render?id=3&stock=portra400&side=700` → 图。
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
| GET | `/stocks` | 卷列表（name/label/desc） |
| GET | `/bases` | 基准成色列表 |
| GET | `/load?paths=a,b&side=700` | **同步**载入并缓存（慢，1.9 s/张；前端分批调） |
| GET | `/render?id=3&stock=&base=&side=&fmt=jpg` | 出图（直接返回图片字节） |
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
from . import io, pipeline, spektra, stocks

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

    ⚠ 必须在 `_Overrides` 上下文**里面**调用才准 —— 滑杆传进来的覆盖就是靠那个生效的。
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

    ⚠ 必须在 `_Overrides` 里调用（要看到滑杆传进来的参数）。
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


def _render_bytes(i, stock, base, side, fmt, quality, params=None, paper=None):
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

    `paper` = 相纸（09-15 SV 选「C」）。空 = 这一卷**配套**的那张（默认值由引擎给）。
      认不得的名字**不会**直接崩：`pipeline.run_from` 会回落成配套纸，
      并在报告里标 `print_fallback`（不静默 —— 这是本项目最阴的那一类坑）。
    """
    row = _cache_get(i)
    if not row:
        return None, {'error': 'id 不在缓存里，先 /load'}
    want, err, req = _want_side(side)
    if err:
        return None, {'error': err}
    cur_side = int(row.get('side') or DEFAULT_SIDE)
    with _Overrides(_parse_params(params)):
        if want is None or want == cur_side:
            s = _ensure_decoded(i, row)       # ★ 解码阶段参数改了要重新解码（见 _decode_sig）
        else:
            s, _ms = _load_one(row.get('path'), want)      # ★ 换尺寸 = 重新解码
        r = pipeline.run_from(s, stock=stock or None, base=base or None,
                              cache=_STAGES[0], paper=paper or None)
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


def _export_one(i, out_path, stock, base, paper, params, side, quality, src_path=None):
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
    with _Overrides(_parse_params(params)):
        try:
            # ★ 按导出尺寸重新解码 + 重新跑（`_load_one` 自带"路径+尺寸+入口签名"那一层缓存）
            s, _ms = _load_one(src, side)
            r = pipeline.run_from(s, stock=stock or None, base=base or None,
                                  cache=None, paper=paper or None)
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


def _stats_of(i, stock, base, params=None, paper=None):
    row = _cache_get(i)
    if not row:
        return {'error': 'id 不在缓存里'}
    with _Overrides(_parse_params(params)):
        s = _ensure_decoded(i, row)           # ★ 同上：解码阶段参数改了要重新解码
        r = pipeline.run_from(s, stock=stock or None, base=base or None,
                              cache=_STAGES[0], paper=paper or None)
    from . import color
    lab = color.to_lab(np.clip(r.disp, 0, 1))
    L = lab[..., 0]
    Cc = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    st = r.report.get('style') or {}
    return dict(ms=round(r.report.get('ms', 0)),
                stock=r.report.get('stock'), base=r.report.get('base'),
                # ★ 报出**实际用的那张相纸**（不是请求里那个）—— 认不得的名字会被回落成
                #   配套纸，这里带 `paper_fallback` 说明"回落过了"，
                #   免得画面跟预期不一样却查不出原因。
                paper=st.get('print'), paper_default=st.get('print_default'),
                paper_fallback=st.get('print_fallback'),
                paper_fallback_reason=st.get('print_fallback_reason'),
                L50=round(float(np.median(L)), 1),
                L90=round(float(np.percentile(L, 90)), 1),
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
                # ★ 卷列表从引擎取（`stocks.NAMES`）—— 别在这里写死名字：
                #   09-14 出过 bug：`air` 从卷表删掉后，这里还在点名它 ⇒
                #   `stocks.get('air')` 抛 KeyError ⇒ /stocks 直接 500。
                out = []
                for n in stocks.NAMES:
                    try:
                        s = stocks.get(n) or {}
                    except KeyError:
                        continue
                    out.append(dict(name=n, label=s.get('label') or n,
                                    desc=s.get('desc') or '',
                                    # ★ 是否真卷（物理链）。前端据此**只列生效的滑杆**
                                    #   （真卷模式下影调/质感那几步被让位，拧了没反应）。
                                    spek=bool(s.get('spek'))))
                return self._json(out)
            if u.path == '/bases':
                out = []
                for n, d in (C.BASE_TABLE or {}).items():
                    out.append(dict(name=n, label=d.get('label') or n,
                                    desc=d.get('desc') or '',
                                    # ★ 哪一支是引擎当前配置的默认（`config.BASE`）。
                                    #   前端据此**选初值** —— 不许自己写死基准名。
                                    #   过去前端写死 'all'，而 'all' 不在表里 ⇒
                                    #   `stocks.resolve_base` **静默**回落成 `BASE_NONE`
                                    #   （"不套基准"）⇒ 默认出图等于没套基准，
                                    #   界面上还一支都选不中（看不出哪里不对）。
                                    isDefault=bool(n == getattr(C, 'BASE', None))))
                return self._json(out)
            if u.path == '/papers':
                # ★ 相纸清单（09-15 SV 选「C」：印相纸要能选，别写死）。
                #   **必须带 `stock`** —— 默认相纸是**跟着卷走的**：引擎在"这一卷配套的那张"
                #   上标 `isDefault`，前端只认它（同基准那条规矩：默认值一律由引擎给）。
                #   ⚠ 一个都不标（stock 不是真卷）= 这条路没有"相纸"概念（neutral 走 Lab 引擎）。
                return self._json(spektra.papers(q.get('stock') or None))
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
            if u.path == '/params':
                # ★ 09-15：改由 `_param_defs()` 出，每项带 `dv`（= 引擎此刻实际用的值，
                #   `inv` 的已翻成人话）。**前端必须用它当滑杆初值** —— 过去那版这里也返回了
                #   一个 `value`，但前端从来没用，而是自己取"区间中点" ⇒ 显示的数和实际生效的
                #   数对不上（13 根里 12 根）。那个 `value` 字段一并去掉，避免两个名字并存再踩一次。
                return self._json(_param_defs())
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
                                        q.get('base'), q.get('side'),
                                        (q.get('fmt') or 'jpg').lower(),
                                        q.get('q') or 92, q.get('params'),
                                        q.get('paper'))
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
                                          q.get('stock'), q.get('base'), q.get('paper'),
                                          q.get('params'),
                                          int(_sv) if _sv else None,
                                          q.get('q') or C.JPEG_QUALITY,
                                          src_path=q.get('src'))
                if info2 is None:
                    return self._json(err2, 404)
                return self._json(info2)
            if u.path == '/stats':
                return self._json(_stats_of(int(q.get('id') or 0), q.get('stock'),
                                            q.get('base'), q.get('params'),
                                            q.get('paper')))
            return self._json({'error': 'no such path', 'path': u.path}, 404)
        except Exception as e:                                    # noqa: BLE001
            return self._json({'error': '%s: %s' % (type(e).__name__, str(e)[:200])}, 500)


# ---- ★ 通用参数口子：`params=KEY:VAL,KEY:VAL` 临时覆盖 config -------------------
# 为什么这么做：**不用为每一个旋钮写代码**。前端只要知道「哪个参数、什么范围」，
# 就能自动生成滑杆；引擎这边一个口子全接住。
_param_lock = threading.Lock()

# 允许被外部覆盖的前缀（**白名单**，防止前端乱改引擎契约里的东西）
# ★ 09-15 补 `CONTRAST` / `CHROMA_`：这两类**名字配不上任何前缀** ⇒
#   过去就算加了滑杆也**传不进来**，而且是**静默丢弃**（下面的契约就是不合法就丢、
#   不报错）⇒ 拧了没反应还不知道为什么。这是"加滑杆"必须先修的地基。
# ★ 同时清掉两个**死前缀**（历史遗留，config 里根本没有以它们开头的键）：
#   `COLOR_`（整体色偏其实叫 `COL_A/COL_B`，配不上）、`SHARP_`（查无此键）。
PARAM_PREFIX = ('TONE_', 'GRAIN_', 'BLOOM_', 'HALATION_', 'DENOISE_', 'WHITE_MICRO',
                'FACE_', 'DENSITY_', 'CROSSTALK_', 'LAYER_', 'ENTRY_', 'SHADOW_',
                'SKIN_', 'ANCHOR_', 'CAP_', 'SPEK_', 'CONTRAST', 'CHROMA_')

# 不收白名单里的这些（结构性/开关类/内部系数，乱改会破契约）
PARAM_BLOCK = ('ENTRY_CURVE', 'ENTRY_SHOULDER_KIND', 'LUT_PATH', 'BASE', 'STOCK',
               # ⚠ 前缀一放开就会**连坐**这几个"只是名字撞上前缀"的内部系数：
               'CONTRAST_S_SCALE', 'CONTRAST_S_CLAMP',   # style.py 的 S 形振幅系数
               'CHROMA_REF')                             # 彩度 gamma 的参考点


# ★ 09-15（B3）：布尔开关的几种写法都认（前端发的是 1/0，手写 A/B 时可能写 true/false）。
#   认不出来 ⇒ 返回 None ⇒ **丢掉**（不是当成 False：那会把"写错了"变成"悄悄关掉一层"）。
_TRUE_WORDS = ('1', 'true', 'yes', 'on', 't', 'y')
_FALSE_WORDS = ('0', 'false', 'no', 'off', 'f', 'n')


def _as_bool(v):
    s = (v or '').strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None


def _parse_params(txt):
    """`KEY:VAL,KEY:VAL` → dict。不合法/不在白名单的**静默丢掉**（不让前端报错卡住）。

    ★ 09-15 起这里还负责两件事，两件都**由 `PARAMS` 表驱动**（单一真相源，
      所以不许在这里写死参数名）：
        · `inv`  方向翻转：滑杆的**显示值**与引擎值互为倒数（见「整张亮暗」）
        · `pair` 成对联动：有的旋钮必须**同时**改两个常量才成立（见「黑柔」的守恒）
    """
    out = {}
    for seg in (txt or '').split(','):
        if ':' not in seg:
            continue
        k, v = seg.split(':', 1)
        k, v = k.strip(), v.strip()
        if not k or k in PARAM_BLOCK:
            continue
        if not any(k.startswith(p) for p in PARAM_PREFIX):
            continue
        if not hasattr(C, k):
            continue
        cur = getattr(C, k)
        # ★★ 09-15（B3）三类控件：数字 / 整层开关（bool）/ 下拉（enum）。
        #   `kind` **由 PARAMS 表给**（单一真相源，所以这里不许写死参数名）。
        #   ⚠ 这条闸原来只认 `(int, float)` —— `DENOISE_ENABLE` 这种 bool 被
        #     `not isinstance(cur, bool)` 挡掉、`SPEK_DIFFUSION_FAMILY` 这种字符串连
        #     `float()` 都过不去 ⇒ **界面上有控件、拧不动、还不报错**（本项目最阴的一类）。
        kind = _PARAM_KIND.get(k, 'num')
        if kind == 'bool':
            _b = _as_bool(v)
            if _b is not None:
                out[k] = _b
        elif kind == 'enum':
            # 表里没有的名字**一律丢掉** —— 不能让 vendor 拿到编不出来的型号（那是崩，不是"没反应"）
            if v in _PARAM_OPTS.get(k, ()):
                out[k] = v
        elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
            try:
                out[k] = float(v)
            except ValueError:
                pass
    # ---- ① 显示值 → 引擎值（方向翻转）----
    for k in list(out):
        if k in _PARAM_INV and abs(out[k]) > 1e-9:
            out[k] = 1.0 / out[k]
    # ---- ② 成对联动（主项在场就覆盖从项；单独给从项仍然放行，留给 A/B 用）----
    for k, mate in _PARAM_PAIR.items():
        if k in out:
            out[mate] = out[k]
    return out


class _Overrides:
    """临时覆盖 config（**用完还原**）。常驻服务是多线程的 ⇒ 加锁。"""

    def __init__(self, kv):
        self.kv = kv or {}

    def __enter__(self):
        self.old = {}
        _param_lock.acquire()
        for k, v in self.kv.items():
            self.old[k] = getattr(C, k)
            cur = self.old[k]
            # ⚠ 顺序要紧：`isinstance(True, int)` **也是 True** ⇒ bool 必须先判，
            #   否则开关会被写成 0/1 整数（下游 `bool(...)` 虽然还能用，但类型就脏了）。
            if isinstance(cur, bool):
                setattr(C, k, bool(v))
            elif isinstance(cur, str):
                setattr(C, k, str(v))
            elif isinstance(cur, int):
                setattr(C, k, int(round(v)))
            else:
                setattr(C, k, float(v))
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(C, k, v)
        _param_lock.release()
        return False


# ---- 前端要的「可调参数清单」（带范围）--------------------------------------
# 只列**真值得给人拧的**那几个 —— 别把 config 里 200 个常量全倒出来。
# `grp`   = 前端按这个分组画（真卷 / 脸 / 影调 / 质感）
# `spek`  = True 只在**真卷**模式下生效；False 只在**中性基准**下生效；None = 都生效。
#   ⚠ 真卷自带 H&D + 颗粒 + halation，我们的影调/空间层**让位**了
#     ⇒ 那几根在真卷下拧了**没反应**，前端要求「不生效的就别列出来」（SV 09-14）。
# `dv`    = **不写在这里**！由 `_param_defs()` 从 config 现读（见那个函数的注释）。
# `inv`   = True ⇒ 滑杆显示的是"引擎值的倒数"（人话方向翻过来，见「整张亮暗」）
# `pair`  = 从项名字：主项一动就把从项同步成同值（见「黑柔」的守恒）
# ★★ 09-15 SV 选「C」：13 根 → **23 根**。范围收窄的口径是"把用不到的长尾砍掉"，
#   方向统一成**右 = 强、左端 = 关掉/不动**（唯一例外是「整张亮暗」，它靠 `inv` 翻正）。
# ★ 「对齐大师」那一档都写进 `d` 里了 —— 这是 SV 定的规矩：每个菜单都要有"照大师那一栏"。
PARAMS = [
    # ========== 影调（★ 09-15 SV 定的右栏顺序：影调 → 真卷 → 物理/质感 → 脸）==========
    dict(k='ENTRY_SETTLE_SHIFT_EV', name='整张亮暗(总)', lo=-1.0, hi=1.5, step=0.05, grp='影调',
         d='在"听相机曝光"之上，整体再提亮 / 压暗（单位：档）。0 = 不动。'
           '★ 09-15 修：**任何机型都通电**。以前只有量过落点规律的机型才认这个数，'
           '别的机身上它被**静默丢掉**（拧了、点渲染都不动）；现在没量过的机身走全局落点，'
           '一样按这一档平移。'
           '⚠ 只对 **RAW** 有效（JPG 的相机曲线已经压过了，入口这一段不跑）。'
           '⚠ 大师的中灰是 58，我们够不到（那个数绑着别人的场景+曝光+冲扫）⇒ '
           '这是"朝那个方向偏"，不是"对齐到 58"'),
    dict(k='ENTRY_TOE',       name='暗部亮度',   lo=0.0,  hi=1.0,  step=0.02, grp='影调',
         # ⚠ 名字按**看得见的方向**取，别按引擎的措辞取：引擎里它叫 ENTRY_TOE = "最深处的
         #   增益下限"，1.0 = 关掉 = 暗部最亮 ⇒ 直接叫「入口黑位」会让人以为往右更黑（反的）。
         #   现在右 = 暗部更亮，和「整张亮暗(总)」同向。
         d='最暗那一段保留多少光。0 = 全压死（黑位最深）；1.00 = 完全不压（回到没有趾部）。'
           '出厂 0.32 是照作者线的黑位 7.5 定的。只动暗部，中灰和亮部一个像素不动。'
           '⚠ 只对 **RAW** 有效'),
    dict(k='CONTRAST',        name='明度对比',   lo=0.85, hi=1.20, step=0.01, grp='影调', spek=False,
         d='S 形对比（只动明暗、中灰不移动）。1.00 = 不动；越大画面越硬。真卷下不生效'),
    dict(k='CHROMA_S',        name='彩度',       lo=0.70, hi=1.40, step=0.01, grp='影调', spek=False,
         d='整张彩度倍率。1.00 = 不动。真卷下不生效 —— 那边的彩度归「整张浓淡」管'),
    dict(k='TONE_LIFT',       name='中高调抬起', lo=0,  hi=14,  step=0.5, grp='影调', spek=False,
         d='整张变亮（白点锚在 100 ⇒ 顺带带出高光肩部）。⚠ 实测它只改整体亮暗、不改形状'),
    dict(k='TONE_TOE',        name='趾部压深',   lo=0,  hi=2.0, step=0.05, grp='影调', spek=False,
         d='暗部相对中灰再压深多少。1.0 = 出厂值（按大师全体的相对形状解出来的最优）。'
           '它比「入口黑位」更靠下游、只管中间调以下'),
    dict(k='TONE_SHOULDER',   name='高光肩部',   lo=0,  hi=4,   step=0.05, grp='影调', spek=False,
         d='高光段整段下收多少。2.0 = 出厂值（大师的高光顶实测 97.0，肩部 2 正好对上）；'
           '0 = 开顶（白能真到白，代价是高光偏暖的胶片味会淡）'),

    # ========== 真卷（物理链：落点 / 印相曲线 / 预闪 / 扫描）==========
    dict(k='SPEK_PE_SHIFT',   name='整张亮暗',   lo=0.62, hi=1.43, step=0.01, grp='真卷', spek=True,
         inv=True,
         d='整张更亮还是更暗。1.00 = 不动，越大越亮。相机给多了曝光的片（闪光顶亮、脸发白）'
           '往左拉回来。⚠ 往右别拉到头，高光会先顶'),
    dict(k='SPEK_COUPLERS',   name='整张浓淡',   lo=0.0,  hi=0.5,  step=0.01, grp='真卷', spek=True,
         d='彩度（胶片层间抑制的强度）。越小越淡。0 = 关掉这道过程（画面彩度 7.57，作者线的靶 '
           '6.79，已经很贴）；1.0 = 出厂物理值（12.13，明显更艳）。它不动明暗对比'),
    dict(k='SPEK_MORPH_GAMMA', name='印相反差',  lo=1.00, hi=1.30, step=0.01, grp='真卷', spek=True,
         d='相纸曲线的陡度 —— 改的是对比的"形状"（不是加滤镜）。1.00 = 关掉；越大画面越硬、'
           '层次往亮部靠。⚠ 它一动，整张的落点也跟着动，要配着「整张亮暗」一起看'),
    dict(k='SPEK_MORPH_FAST',  name='印相·快层', lo=0.80, hi=1.40, step=0.02, grp='真卷', spek=True,
         d='只加在"快层"（颗粒最细那一层）上的额外陡度。1.00 = 不动。'
           '⚠ 它和「印相反差」是**相乘**的；只动这一根会让三个通道的曲线不同步（画面偏色），'
           '要试就把快慢两层给同一个数'),
    dict(k='SPEK_MORPH_SLOW',  name='印相·慢层', lo=0.80, hi=1.40, step=0.02, grp='真卷', spek=True,
         d='只加在"慢层"（颗粒最粗那一层）上的额外陡度。1.00 = 不动。⚠ 和快层成对用，理由同上'),
    dict(k='SPEK_MORPH_EXHAUST', name='显影疲劳', lo=0.0, hi=1.0,  step=0.02, grp='真卷', spek=True,
         d='显影液用旧了（局部耗尽）的效果：把每层曲线往一起拉，**中灰不动**。0 = 关。'
           '越大越"闷"、暗部层次越挤'),
    dict(k='SPEK_PREFLASH',    name='预闪',      lo=0.0,  hi=0.05, step=0.001, grp='真卷', spek=True,
         d='暗房技法：不放底片、只让灯透过片基先给相纸一点均匀曝光。0 = 关。'
           '⚠ **方向是反的** —— 实测 0.10 就让画面中位从 74.5 掉到 48.0、亮部 86.2→64.2'
           '（相纸多吃光 = 整张往下压）⇒ 这里只开到 0.05，一格一格试'),
    dict(k='SPEK_PREFLASH_Y_SHIFT', name='预闪偏黄', lo=-1.0, hi=1.0, step=0.02, grp='真卷', spek=True,
         d='预闪那束光偏黄多少（暖）。0 = 中性。⚠ 只有「预闪」不是 0 的时候才看得出来'),
    dict(k='SPEK_PREFLASH_M_SHIFT', name='预闪偏品红', lo=-1.0, hi=1.0, step=0.02, grp='真卷', spek=True,
         d='预闪那束光偏品红多少。0 = 中性。⚠ 只有「预闪」不是 0 的时候才看得出来'),
    dict(k='SPEK_SCANNER_LENS_BLUR', name='成片锐度', lo=0.0, hi=1.5, step=0.05, grp='真卷', spek=True,
         d='扫描端的锐化强度。0 = 不锐化（画面更软），0.60 = 出厂'),

    # ========== 质感（空间光学：柔光族 / 镜头 / 降噪 / 颗粒 / 黑柔 / 晕圈）==========
    dict(k='SPEK_DIFFUSION_STRENGTH', name='柔光', lo=0.0, hi=0.5, step=0.01, grp='质感', spek=True,
         d='柔光的强度（挂放大机时颗粒保持锐利）。0 = 关；0.25 ≈ 1/4 档。'
           '⚠ 大师的柔度是 11.19，我们关掉就已经 9.72 ⇒ 我们本来比大师更柔，'
           '想照大师对齐就拉到 0'),
    dict(k='SPEK_DIFFUSION_FAMILY', name='柔光型号', kind='enum', grp='质感', spek=True,
         opts=(('black_pro_mist', '黑柔（BPM）'), ('pro_mist', '白柔（Pro Mist）'),
               ('glimmerglass', '微光（Glimmerglass）'), ('cinebloom', '电影柔光（CineBloom）')),
         d='柔光的"牌子" —— 决定化开的形状、晕圈大小、纱雾轻重。'
           '★ 同一个档位**电影柔光比黑柔柔得多**：实测中尺度柔度（越小越柔）'
           '关 9.72 → 黑柔1/2 8.55 → 电影1/2 6.18；黑柔最保分辨率、电影柔光晕开最大、微光居中。'
           '⚠ 换型号画面立刻变（这是重新渲染一发的量级）'),
    dict(k='SPEK_DIFFUSION_ENLARGER', name='柔光挂放大机', kind='bool', grp='质感', spek=True,
         d='把柔光挂在**放大机**上（印相那一步、颗粒画出来之前）⇒ 光化开了但**颗粒还是锐的**'
           '（09-15 你选的就是这一支）'),
    dict(k='SPEK_DIFFUSION_CAMERA', name='柔光挂相机', kind='bool', grp='质感', spek=True,
         d='把柔光挂在**相机**上（RAW 那一端）⇒ **颗粒跟着一起柔**、整张更"化"。'
           '⚠ 两个都挂 = 两处各柔一遍，力度会叠起来'),
    dict(k='SPEK_DIFFUSION_SCALE', name='柔光尺度', lo=0.3, hi=3.0, step=0.1, grp='质感', spek=True,
         d='化开的范围有多大。1.0 = 出厂。越大 = 大范围柔（像隔一层玻璃）；越小 = 只柔细节'),
    dict(k='SPEK_CAMERA_LENS_BLUR_UM', name='镜头模糊', lo=0.0, hi=40.0, step=1.0, grp='质感',
         spek=True,
         d='镜头本身的像差（单位 μm，按出图尺寸换算成像素 ⇒ 出图越大它越明显）。'
           '10 = 出厂；0 = 一点都不加（画面最"数码"地锐）。'
           '⚠ 它和「成片锐度」是一糊一锐两头，两个一起拉会互相抵消'),
    dict(k='DENOISE_ENABLE', name='降噪', kind='bool', grp='质感',
         d='RAW 提亮之后暗部的色斑/噪点要不要收拾。默认开，只在"暗部 + 平坦区"下手、'
           '边缘和细节一个像素不动 ⇒ 关掉只会让暗部更脏，不会让细节更多'),
    dict(k='GRAIN_AMOUNT',    name='颗粒',       lo=0,  hi=0.06, step=0.002, grp='质感', spek=False,
         gate='GRAIN_ENABLE',
         d='颗粒强度。0 = 关。高光端本来就精确归零、暗部也会淡出 ⇒ 它主要作用在中间调。'
           '⚠ 名字前面那个勾 = 这一整层开不开（关掉 = 这层不跑，不是把强度拧到 0）'),
    dict(k='GRAIN_SIZE',      name='颗粒大小',   lo=0.6, hi=2.5, step=0.05, grp='质感', spek=False,
         d='颗粒的尺度（高斯半径 px @2048 长边）。小 = 细盐，大 = 粗砂'),
    dict(k='BLOOM_AMOUNT',    name='黑柔',       lo=0,  hi=0.20, step=0.005, grp='质感', spek=False,
         pair='BLOOM_SPREAD', gate='BLOOM_ENABLE',
         d='黑柔的强度（高光外溢）。0 = 关。⚠ 它和「化开」必须**成对相等**才能量守恒'
           '（加进去的光 = 扣掉的）—— 引擎已经自动同步，你只拧这一根就行，别去碰另一个。'
           '⚠ 名字前面那个勾 = 这一整层开不开'),
    dict(k='HALATION_AMOUNT', name='红橙晕圈',   lo=0,  hi=0.25, step=0.01, grp='质感', spek=False,
         gate='HALATION_ENABLE',
         d='高光边缘的红橙光晕（电影卷片基把红光散射回来）。0 = 关。'
           '⚠ 名字前面那个勾 = 这一整层开不开'),
    dict(k='HALATION_RADIUS', name='晕圈半径',   lo=8.0, hi=30.0, step=0.5, grp='质感', spek=False,
         d='红边的扩散半径。小 = 贴着亮边一条硬红边，大 = 糊开一大片'),
    dict(k='WHITE_MICRO',     name='白区层次',   lo=0,  hi=1.5, step=0.05, grp='质感',
         d='白衣 / 白墙那块的中尺度微反差。0 = 不动。1.00 = 出厂，'
           '**这就是"够得着的上限"** —— 大师的 4.69 追不到，那道差是内容差（人家的白是天空和阳光）'),

    # ========== 脸（两条路都生效：L3 肤色层是保留的）=========
    dict(k='FACE_SPAN_KMAX',  name='脸的层次',   lo=1.0,  hi=3.0,  step=0.05, grp='脸',
         gate='FACE_DEPTH_ENABLE',
         d='脸内部明暗最多拉开几倍。1.0 = 不动，越大越立体。2.0 = 作者线那一档（就是出厂值），'
           '再往上容易显脏。'
           '⚠ 名字前面那个勾 = 这道"收脸"整道关掉（连下面那根「脸的靶跨度」一起停）'),
    dict(k='FACE_TGT_SPAN',   name='脸的靶跨度', lo=20.0, hi=50.0, step=0.5, grp='脸',
         d='脸的明暗想拉到多开（配合上一根用）。35 = 作者线的下限，就是出厂值；'
           '调大 = 想要更立体的脸'),
    dict(k='SKIN_FLOOR_A',    name='脸的红绿',   lo=11.0, hi=20.0, step=0.1, grp='脸',
         d='脸的 a*（+ 偏红润 / − 偏绿）。**16.3 = 33 位大师脸的中间值**（照大师对齐选它）；'
           '出厂 14.5 是"作者线那档"，更淡'),
    dict(k='SKIN_FLOOR_B',    name='脸的黄蓝',   lo=12.0, hi=22.0, step=0.1, grp='脸',
         d='脸的 b*（+ 偏黄暖 / − 偏蓝冷）。**18.5 = 33 位大师脸的中间值**；出厂 16.5 更冷一点'),
    dict(k='SKIN_PROTECT_STRENGTH', name='肤色保护', lo=0.0, hi=1.0, step=0.02, grp='脸',
         d='风格层压彩度时，脸少降多少。0 = 不保护（脸跟着整张一起变淡），1 = 脸完全不掉色'),
    # ★★ 09-15 SV 选「D」：把"收脸"放出来，但**默认 0 = 一个像素都不动**。
    #   为什么单开一根而不是直接改默认：09-14 定的「只提不压」是**故意**的
    #   （两个方向都锚会把整张亮度分布拉散，实测 L50 由 41~68 → 35~84）
    #   ⇒ 做成滑杆 ⇒ 拖了才生效，**出厂结果逐位不变**。
    dict(k='ANCHOR_DOWN_GAIN', name='脸太亮收回', lo=0.0, hi=1.0, step=0.02, grp='脸',
         d='脸**比该有的亮度还亮**（发白）时，往靶收多少。**0.00 = 完全不动（出厂）**；'
           '1.00 = 完全收到靶 68 —— 那就是 33 位大师脸的中位 67.9，也是'
           '「脸的红绿 / 黄蓝」正在对齐的同一个数（实测一张中位 89 的脸 ⇒ 68.0）。'
           '⚠ 它是整张乘**同一个**增益（不分区）⇒ 脸回来了，背景也跟着暗一些，'
           '所以出厂没开。脸本来就偏暗的片**不受影响**（只收不回）。'
           '⚠ 别再拿「整张亮暗」去救发白的脸：那根在印相那一步，压在相纸曲线的平肩上'
           '（实测整张掉 11 L* 而脸只掉 0.5）。'),
]
_PARAM_KEYS = tuple(p['k'] for p in PARAMS)
_PARAM_INV = frozenset(p['k'] for p in PARAMS if p.get('inv'))
_PARAM_PAIR = {p['k']: p['pair'] for p in PARAMS if p.get('pair')}
# ★★ 09-15（B3）：这一行的 `kind` / `opts` / `gate` 全由 PARAMS 表驱动 ——
#   `_parse_params` 靠前两个决定"怎么解析"，前端靠全部三个决定"画什么控件"。
_PARAM_KIND = {p['k']: p.get('kind', 'num') for p in PARAMS}
_PARAM_OPTS = {p['k']: tuple(o[0] for o in p['opts']) for p in PARAMS if p.get('opts')}
# ⚠⚠ 「整层开关」那几根键（GRAIN_ENABLE / BLOOM_ENABLE / HALATION_ENABLE / FACE_DEPTH_ENABLE）
#    **只在 `gate=` 里出现过、自己不是一行参数** ⇒ 不补进来的话它们会落回默认的 'num'，
#    而 `isinstance(True, bool)` 会被数字那条闸挡掉 ⇒ **勾掉开关静默发不出去**（勾了没反应）。
_PARAM_KIND.update({p['gate']: 'bool' for p in PARAMS if p.get('gate')})


def _param_defs():
    """给前端的滑杆清单：给每一项补上 `dv` = **引擎此刻实际在用的值**。

    ★★ 为什么必须由引擎现读（不能在前端写死、也不能把数抄进 PARAMS 表里）：
      过去前端的初值是"区间正中间" `(lo+hi)/2`，而引擎用的是 config 的出厂值
      ⇒ **13 根滑杆里有 12 根，显示的数字和实际生效的对不上**：
        整张浓淡 显示 0.50 / 实际 0.00（彩度 9.40 vs 7.57，差一档半）；
        颗粒     显示 0.050 / 实际 0.024（翻倍）。只有「趾部压深」恰好对得上。
      ⚠ 画面本身没错（没拧过的键不参与覆盖，引擎照出厂走）—— **错的是那行字**。
      现在从 config 现读，以后改出厂值它自动跟上，永远不会再漂。
    ⚠ `inv` 的项要给**显示值**（= 1 / 引擎值），否则滑杆一跳就跳到倒数上去。
    ★ 09-15（B3）：`dv` 的类型跟着 `kind` 走 —— 数字给 float、整层开关（`kind='bool'`）
      给 bool、下拉（`kind='enum'`）给选项名；挂了 `gate` 的那几根再补一个 `gate_dv`。
      **控件的初值只能由引擎给**（前端不许自己编默认），这一条对三种控件都成立。
    """
    out = []
    for p in PARAMS:
        q = dict(p)
        cur = getattr(C, q['k'], None)
        # ⚠ bool **必须先判**：`isinstance(True, int)` 也成立 ⇒ 顺序反了开关的 dv 会变 1/0
        if isinstance(cur, bool):
            q['dv'] = bool(cur)
        elif isinstance(cur, (int, float)):
            cur = float(cur)
            if q.get('inv') and abs(cur) > 1e-9:
                cur = 1.0 / cur
            q['dv'] = cur
        elif isinstance(cur, str):
            q['dv'] = cur                       # 下拉（柔光型号）
        else:
            q['dv'] = None                      # 理论上不会发生，自检盯着
        # ★ 带「整层开关」的那几根：把开关**此刻的值**一起给前端（勾选框的初值）
        _g = q.get('gate')
        if _g:
            q['gate_dv'] = bool(getattr(C, _g, False))
        # ★★★ 09-15（SV 报「柔光下拉框为空」）：`opts` **必须在过 HTTP 之前**从
        #   PARAMS 表里的紧凑元组 `(('black_pro_mist','黑柔（BPM）'), …)`
        #   翻成对象 `[{'v': …, 't': …}, …]` 再发出去。
        #   病理：元组经 JSON 序列化变成**数组对** `[['black_pro_mist','黑柔（BPM）'], …]`，
        #        而前端读的是 `o.v` / `o.t` ⇒ 四个 `<option>` 的 value/text 全是 undefined
        #        ⇒ **下拉框看着是空的**（选项在，只是没字、值也是空的）。
        #        更阴的是：`<option>` 的**个数**还是 4 ⇒ 布局自检那条"4 支"的断言照样绿。
        #   ⇒ 形状只在**这一个边界**上统一：表里保持紧凑元组（人改起来短），
        #     出 HTTP 一律是对象（前端只认对象）。自检 `t_param_kinds` 钉着这个形状。
        if q.get('opts'):
            q['opts'] = [{'v': o[0], 't': o[1]} for o in q['opts']]
        out.append(q)
    return out


_CACHE_MAX = [DEFAULT_CACHE]
_WEB = [None]        # --web <dir>：要不要顺手 serve 一个静态前端（可选）


def serve(port=DEFAULT_PORT, host='127.0.0.1', cache=DEFAULT_CACHE, web=None):
    _CACHE_MAX[0] = int(cache)
    _WEB[0] = web
    srv = ThreadingHTTPServer((host, int(port)), _H)
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
    serve(a.port, a.host, a.cache, a.web)


if __name__ == '__main__':
    main()
