# -*- coding: utf-8 -*-
r"""_lib / cache.py —— **四层缓存**（L1 解码 · L2 场景 · L3 渲染 · L4 测量）。

## 为什么有它
同一组参数被反复重渲（一天 ~120 次渲染，多数是重复）。瓶颈不在算法，在"算过的不记得"。
四层键各管一段，**任何一段的输入变了，该段之后的键全部换**（= 不会"拧了没反应"）。

## 键（**这是本模块唯一该记的东西**）
```
L1 解码  key = sha256( raf 绝对路径 + mtime_ns + size + side + WB/config 指纹 + 引擎树指纹 )
L2 场景  key = sha256( L1key + 分割模型 sha + SCENE_* 配置 )
L3 渲染  key = sha256( L2key + **预设文件全文 sha** + canonical(overrides) + side
                       + 引擎树指纹 + config 指纹 + 渲染入口标签 )
L4 测量  key = sha256( 输出 u8 的 sha + measure 版本 + SV_CHEEK )
```
★ **L3 必须含「预设全文」与「overrides」** —— 少了这两项就是"PERSON_* 没进缓存键 ⇒
  拧了没反应"的同一类病（引擎侧 `config.CACHE_KEY_PREFIXES` 那条注释讲的就是这件事）。
★ 每层都额外带「引擎树指纹 + 工作树脏指纹」：spec 里只在 L3 写了，但**解码/场景也吃代码**
  （`io._resize` / `scene.classify`），少它会在改代码后照样命中旧图。

## 落盘
```
E:\ds\photo\_cache\L1\<key[:2]>\<key>.npz      解码后的场景线性 `lin`
E:\ds\photo\_cache\L2\<key[:2]>\<key>.json     场景判定 `scene.classify()` 的结果
E:\ds\photo\_cache\L3\<key[:2]>\<key>.jpg      成片
E:\ds\photo\_cache\L3\<key[:2]>\<key>.json     该张的 meta（含 `_LAST_PERSON_PE`）
E:\ds\photo\_cache\L4\<key[:2]>\<key>.json     `measure.measure()` 的读数
```

## 开关
* `SV_NOCACHE=1` —— **强制绕开全部四层**（"同参两渲"这类实验必须绕开）
* `SV_CACHE_L1=0` —— 只关 L1（L1 占盘最大）
* `SV_CACHE_ALLOW_BACKFILL=1` —— 允许命中"反推登记"（tier B）的条目（见 `_清单.md`）
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time

_PHOTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PHOTO not in sys.path:
    sys.path.insert(0, _PHOTO)
import _lib                                                                  # noqa: E402
import _lib.jsonsafe as _J                                                    # noqa: E402

ROOT = os.path.join(_lib.PHOTO, '_cache')
LAYERS = ('L1', 'L2', 'L3', 'L4')
# 渲染入口标签：`render_one` 的语义一变（换了入口 / 加了动态层）就改这个数
RENDER_ENTRY = 'render_one/person_pe/1'
_WB_PREFIX = ('PUBLIC_',)          # 入口白平衡（`PUBLIC_WB*`）；已在 config 指纹里，此处只作文档
_SCENE_EXTRA = ('SCENE_',)         # ★ `config.CACHE_KEY_PREFIXES` **没有** `SCENE_` ⇒ 这里显式补


# ---------------------------------------------------------------- 小工具 —— 开关 / 指纹

def enabled(layer=None):
    r"""缓存开关。`SV_NOCACHE=1` 关全部；`SV_CACHE_L1=0` 只关 L1。@returns {bool}"""
    if str(os.environ.get('SV_NOCACHE', '')).strip() in ('1', 'true', 'yes', 'on'):
        return False
    if layer == 'L1' and str(os.environ.get('SV_CACHE_L1', '1')).strip() in ('0', 'false'):
        return False
    return True


def allow_backfill():
    r"""是否允许命中"反推登记"的条目（tier B）。@returns {bool}"""
    return str(os.environ.get('SV_CACHE_ALLOW_BACKFILL', '')).strip() in ('1', 'true', 'yes', 'on')


def _jsonable(o):
    r"""把任意值压成**稳定可比**的 JSON 片段（dict 按键排序、list 保序、未知类型用 repr）。"""
    if isinstance(o, dict):
        return {str(k): _jsonable(o[k]) for k in sorted(o, key=lambda x: str(x))}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    return repr(o)


def digest(*parts):
    r"""把任意几个值压成 sha256 十六进制（**唯一**的键算法入口）。

    @returns {str} 64 位十六进制
    """
    blob = json.dumps(_jsonable(list(parts)), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def file_ident(path):
    r"""文件身份（**路径 + mtime_ns + size**）—— 内容相等但 mtime 变了也当"另一次"。

    ★ 为什么不直接 sha 文件：RAW 27 MB × 22 张 = 594 MB 读一遍要几秒，而 mtime+size 足够。
    @returns {dict} {path, mtime_ns, size}
    """
    p = os.path.abspath(path)
    st = os.stat(p)
    return dict(path=p, mtime_ns=int(st.st_mtime_ns), size=int(st.st_size))


def file_sha256(path):
    r"""文件全文 sha256（旧名字；**实现已合并到 `jsonsafe.file_sha256`**）。@returns {str}"""
    _lib.deprecation('cache.file_sha256', 'jsonsafe.file_sha256')
    return _J.file_sha256(path)


def _engine_cfg():
    r"""拿引擎 `config` 模块（惰性；拿不到就返回 None）。"""
    try:
        _lib.bootstrap_engine()
        from svFilm import config as C
        return C
    except Exception:                                                        # noqa: BLE001
        return None


def config_fp(cfg=None):
    r"""**config 指纹** = 引擎 `config.key_signature()` + 显式补的 `SCENE_*`。

    ★ 复用引擎那份声明式清单（`CACHE_KEY_PREFIXES`），**不重抄一遍** —— 重抄必漏。
    ★ 但引擎那份**漏了 `SCENE_`**（场景阈值会改 `scene.classify` 的结论 ⇒ 会改出图），
      所以这里补上；报告里把这条当"引擎侧待补"记了一笔。
    @returns {str} sha256
    """
    C = cfg or _engine_cfg()
    if C is None:
        return digest('no-config')
    sig = []
    try:
        sig.append(('key_signature', [list(map(str, kv)) for kv in C.key_signature()]))
    except Exception as e:                                                   # noqa: BLE001
        sig.append(('key_signature_err', type(e).__name__))
    extra = []
    for k in sorted(dir(C)):
        if k.startswith('_'):
            continue
        if any(k.startswith(p) for p in _SCENE_EXTRA):
            v = getattr(C, k)
            try:
                hash(v)
                extra.append((k, v))
            except TypeError:
                extra.append((k, repr(v)))
    sig.append(('scene_extra', extra))
    return digest(sig)


_HEAD = {}


def engine_head(refresh=False):
    r"""**引擎树指纹**（`svFilm/svFilm` + vendored `svFilm/_tools/spektrafilm` 的 tree sha）
    + 工作树脏指纹（**同一次进程只查一次**）。

    ★ 为什么不再用整仓 `HEAD`：以前任何一个提交（包括 `svFilm/引擎手册.md`、`迭代记录.md`
      这类**文档提交**）都会换掉 HEAD ⇒ 所有渲染缓存作废。08-18 实测：一轮升级 + 重标
      60+ 张全是冷渲，其中一半是文档提交造成的。现在只哈希**两棵会进渲染的树**：
        · `svFilm/svFilm`（代码 + config + data/presets）
        · `svFilm/_tools/spektrafilm`（vendored 引擎源码）
      文档/报告提交不再影响缓存；动引擎树/代码树照旧失效。
    ★ `dirty_code` 只算**被跟踪文件的改动**（`M`/`D`/`R`…，**不含 `??` 未跟踪**）：
      未跟踪的杂物（如 `*.bak_paperA`）不该让整个缓存失效；而未提交的引擎改动**必须**失效，
      否则"改了代码却命中旧图"正是本模块要防的那类病。
    @returns {dict} {head, dirty, dirty_code, sha16} ｜ `head` = 两棵树的 tree sha（`+` 连接）
    """
    if _HEAD and not refresh:
        return _HEAD
    repo = r'E:\svStudio'
    trees = ('svFilm/svFilm', 'svFilm/_tools/spektrafilm')
    head, dirty, dirty_code = 'unknown', '', ''
    try:
        parts = []
        for p in trees:
            r = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD:' + p],
                               capture_output=True, timeout=20)
            parts.append(r.stdout.decode('utf-8', 'replace').strip()
                         if r.returncode == 0 else 'unknown')
        head = '+'.join(parts)
        r2 = subprocess.run(['git', '-C', repo, 'status', '--porcelain', '--', *trees],
                            capture_output=True, timeout=20)
        if r2.returncode == 0:
            lines = [ln for ln in r2.stdout.decode('utf-8', 'replace').splitlines() if ln.strip()]
            dirty = '\n'.join(lines)
            dirty_code = '\n'.join(ln for ln in lines if not ln.startswith('??'))
    except Exception:                                                        # noqa: BLE001
        head, dirty, dirty_code = 'unknown', '', ''
    _HEAD.update(head=head, dirty=dirty, dirty_code=dirty_code,
                 sha16=digest(head, dirty_code)[:16])
    return _HEAD


_MODEL = {}


def model_fp():
    r"""分割/检测模型的 sha（**L2 键的第二项**）。@returns {str}"""

    if _MODEL:
        return _MODEL['sha']
    d = os.path.join(_lib.REPO, 'svFilm', '_models')
    parts = []
    for n in ('yunet.onnx', 'selfie_multiclass_256x256.tflite'):
        p = os.path.join(d, n)
        parts.append((n, _J.file_sha256(p) if os.path.isfile(p) else 'missing'))
    _MODEL['sha'] = digest(parts)
    _MODEL['files'] = parts
    return _MODEL['sha']


def preset_fp(stock):
    r"""**预设文件全文 sha**（L3 键的核心项之一）。@returns {dict} {name, path, sha, size}"""
    d = os.path.join(_lib.REPO, 'svFilm', 'data', 'presets')
    p = os.path.join(d, '%s.json' % stock)
    if not os.path.isfile(p):
        return dict(name=stock, path=p, sha='missing', size=0)
    return dict(name=stock, path=p, sha=_J.file_sha256(p),
                size=os.path.getsize(p))


def measure_fp():
    r"""**measure 版本**（L4 键的第二项）= `_lib.VERSION` + `measure.py` 全文 sha。@returns {str}"""
    p = os.path.join(_lib.ROOT, 'measure.py')
    return digest(_lib.VERSION, _J.file_sha256(p) if os.path.isfile(p) else 'missing')


_TGT = {}


def targets_fp():
    r"""`targets.json` 全文 sha（**L3 的附加项**）—— 轴层住在这份文件里，它一变出图就变。"""
    if _TGT:
        return _TGT['sha']
    p = os.path.join(_lib.REPO, 'svFilm', 'data', 'targets.json')
    _TGT['sha'] = _J.file_sha256(p) if os.path.isfile(p) else 'missing'
    _TGT['path'] = p
    return _TGT['sha']


def measure_version():
    r"""人读的 measure 版本串（`1.0@<sha12>`）。@returns {str}"""
    p = os.path.join(_lib.ROOT, 'measure.py')
    s = _J.file_sha256(p)[:12] if os.path.isfile(p) else 'missing'
    return '%s/%s@%s' % (_lib.VERSION, _lib.MEASURE_VERSION, s)


# ---------------------------------------------------------------- 四层的键

def key_l1(raf_path, side, cfg=None, wb=None):
    r"""**L1 解码键** = sha256(raf 路径+mtime+size + side + config 指纹 + 引擎树指纹 + **每预设 WB**)。

    ★ `wb` = `{white_balance, temperature, tint}`（`presets.wb_of`）；None ⇒ 全局（旧键）。
      每预设 WB 不同 ⇒ 各自的解码缓存，互不串。
    """
    _wb = None
    if wb:
        _wb = (str(wb.get('white_balance')), round(float(wb.get('temperature')), 3),
               round(float(wb.get('tint')), 5))
    return digest('L1', file_ident(raf_path), int(side), config_fp(cfg),
                  engine_head()['sha16'], _wb)


def key_l2(l1_key, cfg=None):
    r"""**L2 场景键** = sha256(L1 键 + 分割模型 sha + config 指纹（含 SCENE_）)。"""
    return digest('L2', l1_key, model_fp(), config_fp(cfg))


def key_l3(l2_key, stock, side, overrides=None, cfg=None, entry=RENDER_ENTRY, extras=None):
    r"""**L3 渲染键** = sha256(L2 键 + 预设全文 sha + canonical(overrides) + side
    + 引擎树指纹 + config 指纹 + 渲染入口 **+ targets.json 全文 sha + extras**)。

    ★ 预设要用**全文 sha**（不是 mtime、不是"字段指纹"）：手改一个数字、甚至只改注记，
      都要换键 —— 后者无害，前者漏了就正好是"拧了没反应"。
    ★ `overrides` 用 canonical（键排序、值规范化）⇒ 字典序不同的同一份 overrides 同键。
    ★ **附加项**（spec 里没写、但漏了必然错）：`targets.json` 全文 sha（轴层住那里）、
      `extras`（运行时开关，如 `person_metering`）。
    """
    return digest('L3', l2_key, preset_fp(stock)['sha'], int(side),
                  _jsonable(overrides or {}), engine_head()['sha16'],
                  config_fp(cfg), entry, targets_fp(), _jsonable(extras or {}))


def key_l4(out_sha, cfg=None):
    r"""**L4 测量键** = sha256(输出 sha + measure 版本 + `SV_CHEEK`)。"""
    return digest('L4', out_sha, measure_fp(),
                  (os.environ.get('SV_CHEEK') or 'skin').strip().lower())


# ---------------------------------------------------------------- 落盘

def path_of(layer, key, ext):
    r"""某层某个键的落盘路径：`_cache/<layer>/<key[:2]>/<key>.<ext>`。@returns {str}"""
    return os.path.join(ROOT, layer, key[:2], '%s.%s' % (key, ext))


def exists(layer, key, ext='json'):
    r"""该层该键在不在盘上。@returns {bool}"""
    return os.path.isfile(path_of(layer, key, ext))


def put_bytes(layer, key, data, ext):
    r"""原子写（`<path>.part` + `os.replace`）。@returns {str} 落盘路径"""
    p = path_of(layer, key, ext)
    d = os.path.dirname(p)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = p + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(data)
    os.replace(tmp, p)
    return p


def get_bytes(layer, key, ext):
    r"""读该层该键；没有 ⇒ None。@returns {bytes|None}"""
    p = path_of(layer, key, ext)
    if not os.path.isfile(p):
        return None
    with open(p, 'rb') as fh:
        return fh.read()


def put_json(layer, key, obj):
    r"""原子写 JSON。@returns {str}"""
    return put_bytes(layer, key, json.dumps(obj, ensure_ascii=False, indent=1,
                                            default=float).encode('utf-8'), 'json')


def get_json(layer, key):
    r"""读 JSON；没有 ⇒ None。@returns {dict|None}"""
    b = get_bytes(layer, key, 'json')
    if b is None:
        return None
    try:
        return json.loads(b.decode('utf-8'))
    except Exception:                                                        # noqa: BLE001
        return None


def put_file(layer, key, src_path, ext):
    r"""把一个**已存在的文件**搬进缓存（拷贝，不是移动）。@returns {str}"""
    with open(src_path, 'rb') as fh:
        return put_bytes(layer, key, fh.read(), ext)


def copy_out(layer, key, ext, dst):
    r"""把缓存里的文件拷到 `dst`（原子写）。@returns {str|None} 目标路径（没命中 ⇒ None）"""
    b = get_bytes(layer, key, ext)
    if b is None:
        return None
    d = os.path.dirname(dst)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = dst + '.part'
    with open(tmp, 'wb') as fh:
        fh.write(b)
    os.replace(tmp, dst)
    return dst


# ---------------------------------------------------------------- L1 解码载荷

def put_lin(key, lin):
    r"""存场景线性 `lin`（`npz`，**保 dtype**）。@returns {str}"""
    import numpy as np
    buf = io.BytesIO()
    np.savez(buf, lin=np.ascontiguousarray(lin))
    return put_bytes('L1', key, buf.getvalue(), 'npz')


def get_lin(key):
    r"""读场景线性 `lin`；没有 ⇒ None。@returns {ndarray|None}"""
    import numpy as np
    b = get_bytes('L1', key, 'npz')
    if b is None:
        return None
    try:
        with np.load(io.BytesIO(b)) as z:
            return z['lin']
    except Exception:                                                        # noqa: BLE001
        return None


def restore_sample(key):
    r"""从 L1 重建一个 `Sample`（`disp` 由 `color.l2s(lin)` 重算 —— 与 `io.load_raw` 同一步）。

    @returns {object|None} `svFilm.io.Sample`；没有缓存 ⇒ None
    """
    lin = get_lin(key)
    if lin is None:
        return None
    try:
        _lib.bootstrap_engine()
        import numpy as np
        from svFilm import color, io as SIO
        disp = np.clip(color.l2s(np.clip(np.asarray(lin, np.float64), 0.0, None)), 0.0, 1.0)
        return SIO.Sample(lin, disp, 'raw', '<cache:L1>')
    except Exception:                                                        # noqa: BLE001
        return None


# ---------------------------------------------------------------- L4（测量）

def cached_reading(path, det=None, side=900, use_cache=True):
    r"""**L4 测量缓存**：给一张成片文件 -> `measure.read_image()` 的 `reading`。

    L4 键 = sha256(**成片文件的 sha** + measure 版本 + `SV_CHEEK`)。
    ★ 只存 `reading`（= 判分要的全部标量）；`lab` / 掩膜不存（要它们就自己 `read_image`）。

    @returns {dict} `{reading, cache: 'L4 hit'|'miss'|'off', key_l4}`
    """
    import _lib.measure as M
    if not (use_cache and enabled()):
        r = M.read_image(path, det=det, side=side)
        return dict(reading=r['reading'], cache='off', key_l4=None,
                    cheek_n=r['cheek_n'], arm_n=r['arm_n'], nface=r['nface'])
    k = key_l4(_J.file_sha256(path))
    if enabled('L4'):
        hit = get_json('L4', k)
        if hit is not None:
            hit['cache'] = 'L4 hit'
            hit['key_l4'] = k
            return hit
    r = M.read_image(path, det=det, side=side)
    doc = dict(reading=r['reading'], cheek_n=r['cheek_n'], arm_n=r['arm_n'],
               nface=r['nface'], cheek_last=M.CHEEK_LAST(),
               sv_cheek=(os.environ.get('SV_CHEEK') or 'skin').strip().lower(),
               measure=measure_version(), side=int(side), src=os.path.abspath(path))
    try:
        put_json('L4', k, doc)
    except Exception as _e:                                                  # noqa: BLE001
        import sys as _s
        print('[cache 警告] L4 写盘失败（不影响读数）：%s' % _e, file=_s.stderr)
    doc['cache'] = 'miss'
    doc['key_l4'] = k
    return doc


# ---------------------------------------------------------------- 统计

def stats(layer=None, sample=0):
    r"""扫盘统计（文件数 / 字节；`sample>0` 时只扫每个前缀目录的前 `sample` 个）。

    @returns {dict} {layer: {files, mb, keys}}
    """
    out = {}
    for L in ((layer,) if layer else LAYERS):
        root = os.path.join(ROOT, L)
        n, b = 0, 0
        keys = set()
        if os.path.isdir(root):
            for d in sorted(os.listdir(root)):
                dd = os.path.join(root, d)
                if not os.path.isdir(dd):
                    continue
                fs = sorted(os.listdir(dd))
                if sample:
                    fs = fs[:sample]
                for f in fs:
                    p = os.path.join(dd, f)
                    n += 1
                    b += os.path.getsize(p)
                    keys.add(os.path.splitext(f)[0])
        out[L] = dict(files=n, mb=round(b / 1024.0 ** 2, 1), keys=len(keys))
    return out


def cache_dir_report():
    r"""一行自报（给报告用）：每层几个键、多少 MB。@returns {str}"""
    s = stats()
    return ' ｜ '.join('%s %d 键 / %.1f MB' % (L, s[L]['keys'], s[L]['mb']) for L in LAYERS)


def clear(layer=None):
    r"""删缓存（**只删 `_cache/` 下**；拒绝删根目录之外的任何东西）。@returns {int} 删了几个文件"""
    import shutil
    root = os.path.abspath(ROOT)
    if os.path.basename(root) != '_cache' or not root.startswith(os.path.abspath(_lib.PHOTO)):
        raise RuntimeError('拒绝清理：%r 不像 _lib.PHOTO 下的 _cache' % root)
    n = 0
    for L in ((layer,) if layer else LAYERS):
        d = os.path.join(root, L)
        if os.path.isdir(d):
            n += sum(len(fs) for _r, _d, fs in os.walk(d))
            shutil.rmtree(d)
    return n


def main(argv=None):
    r"""CLI：`python -m _lib.cache stats | keys | head | clear [L3]`。@returns {int}"""
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else 'stats'
    if cmd == 'stats':
        print('缓存根 = %s' % ROOT)
        s = stats()
        for L in LAYERS:
            print('  %-3s %6d 文件 / %8.1f MB / %5d 键' % (L, s[L]['files'], s[L]['mb'],
                                                          s[L]['keys']))
        print('  开关：SV_NOCACHE=%r SV_CACHE_L1=%r SV_CACHE_ALLOW_BACKFILL=%r'
              % (os.environ.get('SV_NOCACHE'), os.environ.get('SV_CACHE_L1'),
                 os.environ.get('SV_CACHE_ALLOW_BACKFILL')))
        return 0
    if cmd == 'head':
        h = engine_head(refresh=True)
        print('引擎树 = %s' % h['head'])
        print('工作树脏 = %r' % (h['dirty'][:200] or ''))
        print('模型 sha  = %s' % model_fp())
        print('config 指纹(前16) = %s' % config_fp()[:16])
        print('measure 版本 = %s' % measure_version())
        for n in ('鹿井预设', '滨田预设', 'xhs预设'):
            d = preset_fp(n)
            print('  预设 %-8s sha=%s  %d B' % (n, d['sha'][:16], d['size']))
        return 0
    if cmd == 'keys':
        # 打印一份"若现在渲会用什么键"（零渲染、零写盘）——调试"为什么没命中"用
        raf_dir = argv[1] if len(argv) > 1 else _lib.RAF_DIR
        stem = argv[2] if len(argv) > 2 else _lib.STEMS11[0]
        side = int(argv[3]) if len(argv) > 3 else 1080
        raf = os.path.join(raf_dir, stem + '.RAF')
        if not os.path.isfile(raf):
            print('缺 %s' % raf)
            return 2
        l1 = key_l1(raf, side)
        l2 = key_l2(l1)
        print('RAF  %s' % raf)
        print('L1   %s' % l1)
        print('L2   %s' % l2)
        for st in ('鹿井预设', '滨田预设', 'xhs预设'):
            print('L3   %-8s %s' % (st, key_l3(l2, st, side)))
        return 0
    if cmd == 'clear':
        n = clear(argv[1] if len(argv) > 1 else None)
        print('删了 %d 个文件' % n)
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
