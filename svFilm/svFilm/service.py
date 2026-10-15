# -*- coding: utf-8 -*-
r"""svFilm 常驻 HTTP 服务 —— 第二条口子（给工作台 svStudio 调）。

解码（`io.load_raw`）占全链的大头 ⇒ **「常驻 + 缓存解码」是硬要求，不是优化选项**。**只绑
127.0.0.1**；零依赖（只用标准库 `http.server`）。出图三支（`/render` `/base` `/export`）回图片
字节（前端 `<img src>`，改参数 = 改 URL），其余回 JSON；`side` 不传 = 沿用已解码那份（`/export`
例外 = 原图全尺寸）。
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
from . import io, pipeline, presets

VERSION = 'svstudio-1'
DEFAULT_PORT = 8765
DEFAULT_SIDE = 700          # ★ 预览用 700（1080 屏根本显示不到 1000 px）
DEFAULT_CACHE = 40          # 最多缓存多少张

_cache = OrderedDict()      # id -> dict(sample=..., path=..., side=..., t=...)
_cache_lock = threading.Lock()
_next_id = [1]

# 第二层缓存：按「路径 + 尺寸 + 解码签名」缓存已解码 Sample。
_path_cache = OrderedDict()
_path_cache_lock = threading.Lock()
_PATH_CACHE_MAX = [24]

# 段缓存：留下「胶片出图」及其之前的产物（关掉 = 每次全跑，**逐位相同**）。
_STAGES = [pipeline.StageCache() if getattr(C, 'CACHE_ENABLE', False) else None]

# 解码阶段参数在 `io.load_raw` 里就跑掉了、算完才存进 Sample，而 Sample 被缓存留着 ⇒
# 不重新解码改它们**根本不生效** ⇒ 键里带「解码签名」（签名键显式列出，别用 `dir(C)` 扫描）。
_DECODE_SIG_KEYS = ('PUBLIC_WB', 'PUBLIC_WB_TEMPERATURE', 'PUBLIC_WB_TINT',
                    'PUBLIC_COLORSPACE')


def _decode_sig():
    return tuple((k, getattr(C, k, None)) for k in _DECODE_SIG_KEYS)


def _load_key(path, side):
    r"""键 = 路径 + 尺寸 + **解码签名**（`side=None` = 原图全尺寸，独立成键）。"""
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
    if side is None or str(side).strip() in ('', '0'):
        return None, None, None
    try:
        req = int(float(str(side).strip()))
    except (TypeError, ValueError):
        return None, 'side 不合法：%r' % (side,), None
    cap = int(getattr(C, 'MAX_SIDE', 2048) or 2048)
    return max(16, min(req, cap)), None, req


def _render_bytes(i, stock, side, fmt, quality):
    r"""出图：`side` 真的按请求走（不给 / 一致 ⇒ 用缓存；否则重新解码再跑链）。"""
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
    r = pipeline.run_from(s, stock=stock or None, cache=_STAGES[0])
    disp = np.clip(r.disp, 0.0, 1.0)
    arr = (disp * 255.0 + 0.5).astype(np.uint8)
    info = {'ms': round(r.report.get('ms', 0)),
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


def _export_one(i, out_path, stock, side, quality, src_path=None):
    r"""★ 导出**成片**（写盘走 `io.save`）。⚠ 默认**原图全尺寸** ⇒ 重新解码 + 重跑。"""
    row = _cache_get(i) if i else {}
    src = src_path or row.get('path')
    if not src:
        return None, {'error': '缺 src（原图路径）或 id（先 /load）'}
    if not out_path:
        return None, {'error': '缺 path（导出到哪）'}
    out_path = os.path.abspath(out_path)
    d = os.path.dirname(out_path)
    if not d or not os.path.isdir(d):
        return None, {'error': '目录不存在: %s' % d}
    # ★★ 导出尺寸上限 `C.EXPORT_MAX_SIDE`（原图全尺寸内存需求几十 GB）。
    _cap = getattr(C, 'EXPORT_MAX_SIDE', None)      # None = 不设上限
    if side is None:
        side, side_clamped = None, False
    else:
        side = int(side)
        side_clamped = bool(_cap) and side > int(_cap)
        if side_clamped:
            side = int(_cap)
    try:
        s, _ms = _load_one(src, side)
        r = pipeline.run_from(s, stock=stock or None, cache=None)
    except MemoryError:
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


def _stats_of(i, stock):
    row = _cache_get(i)
    if not row:
        return {'error': 'id 不在缓存里'}
    s = _ensure_decoded(i, row)           # ★ 解码阶段参数改了要重新解码
    r = pipeline.run_from(s, stock=stock or None, cache=_STAGES[0])
    from . import color
    lab = color.to_lab(np.clip(r.disp, 0, 1))
    L = lab[..., 0]
    Cc = np.sqrt(lab[..., 1] ** 2 + lab[..., 2] ** 2)
    sc = r.report.get('scene') or {}
    _l50 = round(float(np.median(L)), 1)
    return dict(ms=round(r.report.get('ms', 0)),
                stock=r.report.get('stock'),
                scene=sc.get('key'),
                L50=_l50,
                L50_measured=_l50,
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
        # ★★ 实际尺寸写进响应头（探针不用解 JPEG）。
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
                                       stage_cache=(_STAGES[0].stats() if _STAGES[0] else None)))
            if u.path == '/stocks':
                # ★ 从 `presets` 现读（写死名字会在风格删掉后 500）。
                return self._json([dict(name=n, label=presets.label_of(n)[0],
                                        desc=presets.label_of(n)[1], engine=True)
                                   for n in presets.names()])
            if u.path in ('/', '/index.html') and _WEB[0]:
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
                # 只列**用户指定的**目录（不自己翻盘）；按扩展名收，自然序排。
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
                b, info = _base_bytes(int(q.get('id') or 0),
                                      q.get('fmt') or 'jpg', q.get('q') or 92,
                                      q.get('side'))
                if b is None:
                    return self._json(info, 404)
                return self._img(b, info['mime'], info)
            if u.path == '/render':
                b, info = _render_bytes(int(q.get('id') or 0), q.get('stock'),
                                        q.get('side'),
                                        (q.get('fmt') or 'jpg').lower(),
                                        q.get('q') or 92)
                if b is None:
                    return self._json(info, 404)
                return self._img(b, info['mime'], info)
            if u.path == '/export':
                _sv = (q.get('side') or '').strip()
                info2, err2 = _export_one(int(q.get('id') or 0), q.get('path'),
                                          q.get('stock'),
                                          int(_sv) if _sv else None,
                                          q.get('q') or C.JPEG_QUALITY,
                                          src_path=q.get('src'))
                if info2 is None:
                    return self._json(err2, 404)
                return self._json(info2)
            if u.path == '/stats':
                return self._json(_stats_of(int(q.get('id') or 0), q.get('stock')))
            return self._json({'error': 'no such path', 'path': u.path}, 404)
        except Exception as e:                                    # noqa: BLE001
            return self._json({'error': '%s: %s' % (type(e).__name__, str(e)[:200])}, 500)



_CACHE_MAX = [DEFAULT_CACHE]
_WEB = [None]        # --web <dir>：要不要顺手 serve 一个静态前端（可选）


def _port_taken(port, host='127.0.0.1', timeout=2.0):
    r"""端口上**已经有人应答**了吗？

    ★ Windows 上 SO_REUSEADDR 是"**可以抢**"：第二个 `bind` **不报错** ⇒ 两个引擎同挂一个端口、
      请求落到哪个不确定 ⇒ 先探，有人应答就退出。"""
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
    allow_reuse_address = False


def serve(port=DEFAULT_PORT, host='127.0.0.1', cache=DEFAULT_CACHE, web=None):
    _CACHE_MAX[0] = int(cache)
    _WEB[0] = web
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
    # ★ `serve()` 返回非 0 = 没起来（端口已占用 / 绑不上）。
    rc = serve(a.port, a.host, a.cache, a.web)
    if rc:
        raise SystemExit(rc)


if __name__ == '__main__':
    main()
