# -*- coding: utf-8 -*-
r"""_lib / render.py —— 渲染编排（分片 · 6 路并行 · 内存闸门 · 自报应有/实得 · 原子写）。

来源 = `amount三链_1013\a0_orchestrate.py` + `a1_worker.py`（**已验证可用**：6 路 66 张）。

## 纪律（`AGENTS` §三）
```
★ 并行度 6 路（**内存是瓶颈不是 CPU**：单进程峰值 1.75~2.8 GB @2048）
★ 每路一个进程 + 分片 ｜ **串行启动、并行运行**
★ 每路自报「**应有 N / 实得 M**」——**不信退出码**，直接数母版文件
★ 启动前查可用内存 > 4 GB（防 OOM）｜ 原子写 ｜ 无残留 `.part` / `.tmp`
★ **不捕获子进程输出**（stdout/stderr 直接落文件句柄，不走 pipe）
★ 同一批必须同口径（别一半串行一半并行）
```

## 主函数（签名）
```
avail_mb()                                    -> int      可用物理内存 MB
mem_gate(mem_gate_mb=4096, timeout_s=900)     -> bool     等内存闸门（超时 False）
per_shard_mb_for(side, floor_mb=1200)         -> int      口径（长边 px）-> 每路预估占用 MB
plan_parallel(n_want, per_shard_mb=2800, reserve_mb=4096, avail=None) -> dict
budget_report(plan)                           -> str      「按 X 路启动（per_shard_mb=Y，可用 Z MB）」
atomic_save(im, path, fmt, **kw)              -> str      原子写图（.part + os.replace）
count_dir(d, exts=('.png',))                  -> int      数母版（跳过 .part/.tmp）
scan_residue(root)                            -> list     残留 .part/.tmp 清单
save_json_atomic(obj, path, indent=1)         -> str
run_shards(worker=..., plan=..., arm_of=..., expect=..., root=..., per_shard_mb=2800, ...) -> dict
load_sample(stem, raf_dir, side)              -> Sample|None  ★ 解码一次，多臂复用
render_one(stem=..., raf_dir=..., side=..., stock=..., overrides=..., out_dir=..., sample=...) -> dict
cached_render(stem=..., raf_dir=..., side=..., stock=..., overrides=..., out_dir=...) -> dict
                                              ★★ **四层缓存版**（命中就拷 jpg + 回放 meta）
```
★ **`cached_render`（10-18 加）= 生产入口的推荐写法**：键的算法全在 `_lib/cache.py`，
  这里只做"查 -> 命中就回放 / 没中就渲完再存"。`SV_NOCACHE=1` 绕过全部四层。
★ `retarget` 这类"同一张图 × 多个预设"的活：**一个进程里先把 sample 备好**再循环调
  `cached_render(..., sample=s)` —— 省掉重复解码（RAW 解码 ~2.3 s @2048）。
★ **`sample=` = 省解码**（10-14 加）：`presets.render()` 只吃场景线性 `lin` ⇒
   **同一张图渲 N 个臂只需解码 1 次**（实测 RAW 解码 ~2.3 s @2048）。
   ★ 分片口径随之变成 **按图分片**（一个进程包一张图的全部臂），别按臂分。
   ⚠ 没传 `sample=` 时行为**逐字不变**（老脚本不受影响）；`meta` 里多了
     `sample_reused` 与 `dt_render`（渲染本身耗时，解码另计）。

## ★ 每路内存对照表（判**能起几路**用，不是"启动前可用够就行"）
```
★ 判据：`avail_mb() > n_shards x per_shard_mb + reserve_mb(默认 4096)`
  不够就 **自动降路数**，并自报「按 X 路启动（per_shard_mb=Y，可用 Z MB）」
```
| 长边 | 每路预估 MB | 32 GB 机（可用 ~20 GB）可起 |
|---|---|---|
| **2048**（生产口径）| **~2800** | **6 路** |
| **6264** | **~8000** | **1~2 路** |
| 其余口径 | `per_shard_mb_for(side)` = `2178 + 1.484e-4 x side^2` | 按公式估 |

★ 表里两个点（2048 -> 2800 / 6264 -> 8000）是**实测**；中间的公式是这两点的**线性拟合**
  （内存**不是**纯按像素数走 —— 像素 9.4 倍，内存只 2.9 倍），**别拿它外推到 6264 以上**。
★★ 实测教训（10-14）：长边 **6264** 上按 6 路起了 4 路 -> **全部 OOM**（每路实测约 8 GB）。
  根因 = `mem_gate` 只看「启动前可用内存」，**不看「每路要多少」** -> 现在两个都看。

## 用法（专题脚本一般只写十几行）
```python
import _lib.render as R
R.run_shards(worker=r'...\a1_worker.py', plan=[0,1,2,3,4,5],
             arm_of=lambda sh: SHARD_ARM[sh], expect=11,
             root=r'...\render', cwd=HERE, here=HERE,
             summary='_a0_summary.json', logfile='_a0_log.txt')
```
⚠ 本模块**顶层不 import 引擎**（numpy/PIL 都不用）—— 编排器要能在任何环境起。
   `render_one()` 里的引擎 import 是惰性的。
"""
from __future__ import annotations

import ctypes
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
import _lib.cache as _cache                                                  # noqa: E402

PY = _lib.PY
MEM_GATE_MB = 4096
N_PARALLEL = 6
PER_SHARD_MB = 2800                 # ★ 每路预估占用（生产口径 2048 实测）
MEM_RESERVE_MB = 4096               # ★ 安全余量：给系统 / 别的进程留的
# ★ 实测两点的对照表（长边 -> 每路 MB）；其余口径见 per_shard_mb_for()
PER_SHARD_MB_BY_SIDE = {2048: 2800, 6264: 8000}


class _MEMSTATUSEX(ctypes.Structure):
    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]


def avail_mb():
    r"""可用物理内存（MB）。Windows `GlobalMemoryStatusEx`。@returns {int}"""
    m = _MEMSTATUSEX()
    m.dwLength = ctypes.sizeof(_MEMSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullAvailPhys // (1024 * 1024))


def mem_gate(mem_gate_mb=MEM_GATE_MB, timeout_s=900, poll_s=5, log=print):
    r"""★ 启动前闸门：可用内存 > `mem_gate_mb` 才放行。

    @returns {bool} True = 放行；False = 超时（调用方自己决定跳不跳）
    """
    waited = 0
    while avail_mb() < mem_gate_mb:
        log('  [等内存] 可用 %d MB < %d，等 %d s（已等 %d s）'
            % (avail_mb(), mem_gate_mb, poll_s, waited))
        time.sleep(poll_s)
        waited += poll_s
        if waited > timeout_s:
            log('  x 等内存超时，放弃')
            return False
    return True


def per_shard_mb_for(side, floor_mb=1200):
    r"""★ 口径（长边 px）-> **每路预估占用 MB**（判路数用）。

    两个**实测**锚点（`PER_SHARD_MB_BY_SIDE`）：`2048 -> 2800` / `6264 -> 8000`；
    中间用这两点的**线性拟合**（对 `side^2`）：`mb = 2178 + 1.484e-4 x side^2`。

    ⚠ 内存**不是**纯按像素数走（像素 9.4 倍，内存只 2.9 倍）=> **别外推到 6264 以上**。
    @param side {int} 长边像素
    @param floor_mb {int} 下限（小图也给个非零预算）
    @returns {int} 每路 MB
    """
    side = int(side)
    if side in PER_SHARD_MB_BY_SIDE:
        return int(PER_SHARD_MB_BY_SIDE[side])
    s0, m0 = 2048, PER_SHARD_MB_BY_SIDE[2048]
    s1, m1 = 6264, PER_SHARD_MB_BY_SIDE[6264]
    slope = float(m1 - m0) / float(s1 * s1 - s0 * s0)
    mb = m0 + slope * (side * side - s0 * s0)
    return int(max(int(floor_mb), round(mb)))


def plan_parallel(n_want, per_shard_mb=PER_SHARD_MB, reserve_mb=MEM_RESERVE_MB, avail=None):
    r"""★ 按「**每路预估占用 x 路数**」算实际能起几路（不够就**自动降路数**）。

    判据：`avail_mb() > n x per_shard_mb + reserve_mb`（不是只看"启动前可用"）。
    * 预算不够时**至少起 1 路**（比一路不起强，启动前 `mem_gate` 再兜一层）。
    * `per_shard_mb=None/0/负数` => **关掉预算**（退回旧行为：只看 `mem_gate`）。

    @param n_want {int} 计划路数（= `len(plan)`）
    @param per_shard_mb {int|None} 每路预估 MB（见 `per_shard_mb_for`）
    @param reserve_mb {int} 安全余量 MB
    @param avail {int|None} 可用 MB（None = 现测；**给值只为自检可复现**）
    @returns {dict} n_want/n_eff/avail_mb/per_shard_mb/reserve_mb/budget_mb/need_mb/cut/gated
    """
    n_want = max(0, int(n_want))
    avail = avail_mb() if avail is None else int(avail)
    reserve_mb = int(reserve_mb or 0)
    if not per_shard_mb or int(per_shard_mb) <= 0 or n_want == 0:
        return dict(n_want=n_want, n_eff=n_want, avail_mb=avail, per_shard_mb=per_shard_mb,
                    reserve_mb=reserve_mb, budget_mb=avail - reserve_mb,
                    need_mb=n_want * int(per_shard_mb or 0) + reserve_mb, cut=0, gated=False)
    per_shard_mb = int(per_shard_mb)
    budget = avail - reserve_mb
    n_max = budget // per_shard_mb
    if n_max < 1:
        n_max = 1
    n_eff = int(min(n_want, n_max))
    return dict(n_want=n_want, n_eff=n_eff, avail_mb=avail, per_shard_mb=per_shard_mb,
                reserve_mb=reserve_mb, budget_mb=int(budget),
                need_mb=n_eff * per_shard_mb + reserve_mb, cut=n_want - n_eff, gated=True)


def budget_report(plan):
    r"""★ 内存自报一行：「**按 X 路启动（per_shard_mb=Y，可用 Z MB）**」。

    @param plan {dict} `plan_parallel()` 的返回
    @returns {str} 自报串（降路数时另附「计划 N 路 -> 降为 M 路」）
    """
    if not plan.get('gated'):
        return '按 %d 路启动（内存预算 = 关，可用 %d MB）' % (plan['n_eff'], plan['avail_mb'])
    s = '按 %d 路启动（per_shard_mb=%d，可用 %d MB）' % (
        plan['n_eff'], plan['per_shard_mb'], plan['avail_mb'])
    if plan['cut']:
        s += ' ｜ **计划 %d 路 -> 降为 %d 路**（安全余量 %d MB ｜ 每路预算 %d MB）' % (
            plan['n_want'], plan['n_eff'], plan['reserve_mb'], plan['per_shard_mb'])
    return s


def atomic_save(im, path, fmt, **kw):
    r"""原子写图：写 `<path>.part` 再 `os.replace`。@returns {str} 落盘路径"""
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + '.part'
    im.save(tmp, format=fmt, **kw)
    os.replace(tmp, path)
    return path


def save_json_atomic(obj, path, indent=1):
    r"""原子写 JSON（旧名字；**实现已合并到 `jsonsafe.write_json_atomic`**）。@returns {str}"""
    _lib.deprecation('render.save_json_atomic', 'jsonsafe.write_json_atomic')
    return _J.write_json_atomic(obj, path, indent=indent)


def count_dir(d, exts=('.png',)):
    r"""数一个目录里的母版（跳过 `.part` / `.tmp`）。@returns {int}"""
    if not os.path.isdir(d):
        return 0
    if isinstance(exts, str):
        exts = (exts,)
    n = 0
    for f in os.listdir(d):
        if f.endswith('.part') or f.endswith('.tmp'):
            continue
        if any(f.endswith(e) for e in exts):
            n += 1
    return n


def scan_residue(root):
    r"""扫残留 `.part` / `.tmp`。@returns {list} 路径清单（应为空）"""
    left = []
    for r, _d, fs in os.walk(root):
        for f in fs:
            if f.endswith('.part') or f.endswith('.tmp'):
                left.append(os.path.join(r, f))
    return left


# ---------------------------------------------------------------- 编排器

def run_shards(worker, plan, arm_of, expect, root, here=None, cwd=None,
               summary='_summary.json', logfile='_orchestrate_log.txt',
               mem_gate_mb=MEM_GATE_MB, per_shard_mb=PER_SHARD_MB,
               reserve_mb=MEM_RESERVE_MB,
               py=PY, stagger_s=3.0, poll_s=20.0,
               extra_args=(), ext_map=(('.png',), ('.jpg',)), tag='',
               env_extra=None, dry=False, threads_per_shard=None):
    r"""★ 编排器：**串行启动、并行运行**，每路自报「应有 N / 实得 M」。

    ★ **路数由"每路预估占用"判**（不是只看"启动前可用内存"）：
      判据 = `avail_mb() > n x per_shard_mb + reserve_mb`；不够就**自动降路数**，
      并自报「按 X 路启动（per_shard_mb=Y，可用 Z MB）」。降路数后 `plan` 尾部的分片不跑。

    @param worker {str} worker 脚本绝对路径（命令行收 `--shard <id>`）
    @param plan {list[int]} 要跑的 shard id
    @param arm_of {callable} shard -> 臂名（输出目录名 `root/<arm>`）
    @param expect {int|dict} 每路应有张数（int = 每路相同；dict = 按臂）
    @param root {str} 输出根目录（下面每臂一个子目录）
    @param here {str|None} 落 summary/log 的目录（默认 = worker 所在目录）
    @param cwd {str|None} 子进程工作目录
    @param summary / logfile {str} 落盘文件名（None = 不落）
    @param mem_gate_mb {int} 启动前内存闸门（每路启动前各查一次）
    @param per_shard_mb {int|None} ★ **每路预估占用 MB**（`per_shard_mb_for(side)`；
        None/0 = 关预算，退回旧行为）
    @param reserve_mb {int} ★ 安全余量 MB（默认 4096）
    @param extra_args {tuple} 追加给 worker 的命令行参数
    @param ext_map {tuple} 两档扩展名，用于"实得"双读数
    @returns {dict} summary（wall_sec / rows / total_expect / total_got / residue / ok
        / per_shard_mb / reserve_mb / n_planned / n_effective / cut_shards / avail_at_preflight_mb）
    """
    here = here or os.path.dirname(os.path.abspath(worker))
    cwd = cwd or here
    LOG = []

    def log(s=''):
        print(s, flush=True)
        LOG.append(s)

    want_plan = list(plan)
    # ★★ 每路线程数（10-15 修）：引擎热点是 numba.prange，默认吃满核数；
    #   起 N 路 ⇒ 必须把每路压到 `核数 // N`，否则 N x 核数 个线程抢 核数 个核。
    #   调用方可用 `threads_per_shard` 覆盖（0 = 明确要求"不设"）。
    pl = plan_parallel(len(want_plan), per_shard_mb=per_shard_mb, reserve_mb=reserve_mb)
    want = want_plan[:pl['n_eff']]
    if threads_per_shard is None:
        threads = max(1, (os.cpu_count() or 8) // max(1, len(want)))
    else:
        threads = int(threads_per_shard)
    env = _lib.env_for_engine(threads=threads or None)
    if env_extra:
        env.update(env_extra)
    t0 = time.time()
    log('=' * 96)
    log('编排启动 %s ｜ 可用内存 %d MB ｜ 计划 %d 路 ｜ 每路应有 %s 张 ｜ %s'
        % (time.strftime('%H:%M:%S'), avail_mb(), len(want_plan), expect, tag))
    log('线程预算：每路 %s 线程（%d 核 / %d 路）｜ %s'
        % (threads or '默认', os.cpu_count() or 0, len(want),
           'OMP/NUMBA/MKL 均已设' if threads else '未设（numba 默认 = 全核）'))
    log('内存预算：' + budget_report(pl))
    if pl['cut']:
        log('  ⚠ 内存不够：计划 %d 路只起 %d 路，**本次不跑的 shard = %s**'
            % (pl['n_want'], pl['n_eff'], want_plan[pl['n_eff']:]))
    log('=' * 96)
    procs = []
    for sh in want:
        arm = arm_of(sh)
        d = os.path.join(root, arm)
        os.makedirs(d, exist_ok=True)
        if not mem_gate(mem_gate_mb, log=log):
            log('  x 跳过 shard %s' % sh)
            continue
        av = avail_mb()
        so = open(os.path.join(d, '_stdout.log'), 'a', encoding='utf-8', errors='replace')
        se = open(os.path.join(d, '_stderr.log'), 'a', encoding='utf-8', errors='replace')
        cmd = [py, '-X', 'utf8', worker, '--shard', str(sh)] + list(extra_args)
        if dry:
            log('  [dry] %s' % ' '.join(cmd))
            so.close()
            se.close()
            continue
        p = subprocess.Popen(cmd, env=env, stdout=so, stderr=se,
                             stdin=subprocess.DEVNULL, cwd=cwd)
        procs.append(dict(sh=sh, arm=arm, pid=p.pid, p=p, av=av, so=so, se=se))
        log('  [启动] shard%-3s %-10s pid=%-7d 启动前可用 %d MB' % (sh, arm, p.pid, av))
        time.sleep(stagger_s)
    if dry:
        return dict(dry=True, plan=want, plan_ran=list(want), plan_planned=list(want_plan),
                    n_planned=pl['n_want'], n_effective=pl['n_eff'], cut_shards=pl['cut'],
                    per_shard_mb=pl['per_shard_mb'], reserve_mb=pl['reserve_mb'],
                    avail_at_preflight_mb=pl['avail_mb'], budget_line=budget_report(pl))

    log('-' * 96)
    log('%d 路全部启动（启动总耗时 %.1f s），等待完成 ...' % (len(procs), time.time() - t0))
    while any(pr['p'].poll() is None for pr in procs):
        done = sum(1 for pr in procs if pr['p'].poll() is not None)
        got = sum(count_dir(os.path.join(root, pr['arm']), ext_map[0]) for pr in procs)
        log('  [%s] 已退出 %d/%d 路 ｜ 可用 %d MB ｜ 母版已落 %d ｜ 已用 %.1f min'
            % (time.strftime('%H:%M:%S'), done, len(procs), avail_mb(), got,
               (time.time() - t0) / 60))
        time.sleep(poll_s)

    for pr in procs:
        pr['p'].wait()
        pr['rc'] = pr['p'].returncode
        pr['so'].close()
        pr['se'].close()
    el = time.time() - t0
    # ★ 10-15 加宽（**向后兼容**）：`expect` 现在支持三种写法 ——
    #   · int          = 每路同数（旧契约）
    #   · dict         = **先按 shard id 查，再按臂名查**（旧契约是只按臂名）
    #   · callable     = `f(shard_id) -> int`（分片大小不一时用它）
    #   为什么加：分片天然不整除时（如 11 张分 6 路 = 2/2/2/2/2/1），旧两种写法都表达不了
    #   ⇒ 调用方要么被迫把 `expect` 写成假的，要么自己另写一套编排（那就重复造轮子了）。
    if callable(expect):
        e_of = lambda a, sh=None: int(expect(sh))
    elif isinstance(expect, int):
        e_of = lambda a, sh=None: int(expect)
    elif isinstance(expect, dict):
        e_of = lambda a, sh=None: int(expect.get(sh) if sh in expect else expect.get(a, 0))
    else:
        # ★ 修（10-18）：原来非 int/dict 的 `expect`（如 list）会走到 `expect.get(...)`
        #   ⇒ `AttributeError: 'list' object has no attribute 'get'`，且是在**全部渲完之后**
        #   在汇总那一步炸（现场只剩一堆成片和一个异常）。现在按"每路同数"处理并告警。
        import sys as _sys
        print('[run_shards 警告] expect 类型 %s 不认识（只认 int/dict/callable）⇒ 按元素数 %d 处理'
              % (type(expect).__name__, len(expect) if hasattr(expect, '__len__') else 0),
              file=_sys.stderr)
        e_of = lambda a, sh=None: len(expect) if hasattr(expect, '__len__') else 0
    rows, te, tg = [], 0, 0
    for pr in procs:
        e = e_of(pr['arm'], pr['sh'])
        g0 = count_dir(os.path.join(root, pr['arm']), ext_map[0])
        g1 = (count_dir(os.path.join(root, pr['arm']), ext_map[1])
              if len(ext_map) > 1 else None)
        te += e
        tg += g0
        rows.append(dict(shard=pr['sh'], arm=pr['arm'], pid=pr['pid'], rc=pr['rc'],
                         expect=e, got=g0, got2=g1, avail_at_start_mb=pr['av']))
        log('  shard%-3s %-10s rc=%-3d 应有 %d / 实得 %d%s  %s'
            % (pr['sh'], pr['arm'], pr['rc'], e, g0,
               ('' if g1 is None else ' / %d' % g1),
               'OK' if (g0 >= e and (g1 is None or g1 >= e)) else '**缺**'))
    log('合计：应有 %d / 实得 %d  %s' % (te, tg, 'OK 一张不缺' if tg >= te else '**有缺失**'))
    left = scan_residue(root)
    log('残留 .part / .tmp = %d 个 %s' % (len(left), left[:5]))
    ok = (tg >= te and not left and all(r['rc'] == 0 for r in rows))
    doc = dict(wall_sec=round(el, 1), rows=rows, total_expect=te, total_got=tg,
               n_parallel=len(procs), mem_gate_mb=mem_gate_mb, residue=left,
               root=root, ok=bool(ok), tag=tag,
               # ★ 路数预算自报（谁被降掉了都记着）
               per_shard_mb=pl['per_shard_mb'], reserve_mb=pl['reserve_mb'],
               n_planned=pl['n_want'], n_effective=pl['n_eff'], cut_shards=pl['cut'],
               avail_at_preflight_mb=pl['avail_mb'],
               plan_complete=bool(pl['cut'] == 0),
               plan_planned=list(want_plan), plan_ran=list(want))
    if summary:
        _J.write_json_atomic(doc, os.path.join(here, summary))
    if logfile:
        with io.open(os.path.join(here, logfile), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write('\n'.join(LOG) + '\n')
    log('墙钟 %.1f s = %.2f min ｜ ok=%s' % (el, el / 60, ok))
    return doc


# ---------------------------------------------------------------- 单张（引擎路径）

def load_sample(stem, raf_dir, side, raf_ext='.RAF', wb=None):
    r"""★ **解码一张**（RAW/JPG -> `Sample`），供**同一张图的多个臂复用**。

    为什么有它：`presets.render()` 收的是**场景线性 `lin`**，所以"同一张图渲 N 个臂"
    只需要解码 1 次。实测 RAW 解码 ~2.3 s @2048，6 个臂 = 白扔 11.5 s/张。
    ★ 用法：`s = R.load_sample(...)` 然后 `R.render_one(..., sample=s)` 重复调。
    ★ `wb` = 该预设自己的白平衡（`presets.wb_of(stock)`）；None ⇒ 全局 `PUBLIC_WB*`。
    ⚠ `Sample` 是**只读**的吗：`presets.render` 只读 `lin`（`np.clip(s.lin,...)`），
      不改它 => 多臂复用安全。**但别跨进程/跨线程复用**（引擎 import 是惰性的，见模块头）。

    @returns {object|None} `svFilm.io.Sample`（失败 => None，调用方回退到"自己解码"）
    """
    try:
        _lib.bootstrap_engine()
        from svFilm import io as SIO
        return SIO.load(os.path.join(raf_dir, stem + raf_ext), side, wb=wb)
    except Exception as _e:                                                  # noqa: BLE001
        # ★ 修（10-18）：原来 `except Exception: return None` 是**纯静默**——
        #   调用方只看到 None，于是"再解码一次"（白付 2.3 s），而且现场没有任何线索。
        #   现在：记进 `LAST_SAMPLE_ERR` + stderr 一行，返回值不变（契约不动）。
        global LAST_SAMPLE_ERR
        LAST_SAMPLE_ERR = dict(path=os.path.join(raf_dir, stem + raf_ext), side=side,
                               err='%s: %s' % (type(_e).__name__, str(_e)[:200]))
        import sys as _sys
        print('[render 警告] load_sample 失败（调用方将自行解码）：%s' % LAST_SAMPLE_ERR['err'],
              file=_sys.stderr)
        return None


LAST_SAMPLE_ERR = None


def sample_for(stem, raf_dir, side, raf_ext='.RAF', cache=True, wb=None):
    r"""★ **拿一个 sample：优先 L1 缓存（免解码），否则现解码并存 L1**。

    @param wb {dict|None} 该预设的 WB（进入 L1 键；None = 全局）
    @returns {tuple} `(sample|None, info|None)`；`info` 里写明走了哪条路（`l1_hit` / `decode`）
    """
    raf = os.path.join(raf_dir, stem + raf_ext)
    l1 = None
    if cache and _cache.enabled('L1') and os.path.isfile(raf):
        l1 = _cache.key_l1(raf, side, wb=wb)
        s = _cache.restore_sample(l1)
        if s is not None:
            return s, dict(l1=l1, how='l1_hit')
    if not os.path.isfile(raf):
        return None, dict(l1=l1, how='missing_source', path=raf)
    s = load_sample(stem, raf_dir, side, raf_ext, wb=wb)
    if s is not None and l1 is not None:
        try:
            _cache.put_lin(l1, s.lin)
        except Exception as _e:                                              # noqa: BLE001
            import sys as _sys
            print('[render 警告] L1 写盘失败（不影响本次渲染）：%s' % _e, file=_sys.stderr)
    return s, dict(l1=l1, how='decode')


def render_one(stem, raf_dir, side, stock, overrides=None, out_dir=None,
               cfg=None, person_metering=True, raf_ext='.RAF', save_png=True,
               save_jpg=True, png_level=6, jpg_quality=95, sample=None):
    r"""渲一张（**生产路径**：`targets.scene_engine` 整份起底 -> `presets.render(overrides=)`）。

    @param stem {str} 文件名（不含扩展名）
    @param raf_dir {str} RAW 目录
    @param side {int} 长边（2048 = 生产口径）
    @param stock {str} 预设名
    @param overrides {dict|None} **整份起底**的 targets（不是叠加）；None = 用生产现值
    @param out_dir {str|None} 落盘目录（None = 只算不落）
    @returns {dict} arm/stem/stock/scene/shape/dt/png_bytes/jpg_bytes/cov
    """
    import numpy as np
    from PIL import Image
    _lib.bootstrap_engine()
    from svFilm import config as C, io as SIO, person as P, presets, scene, targets
    if cfg is not None:
        C = cfg
    t0 = time.time()
    C.PERSON_METERING = bool(person_metering)        # 生产口径
    _wb = presets.wb_of(stock) if presets.has(stock) else None
    s = sample if sample is not None else SIO.load(
        os.path.join(raf_dir, stem + raf_ext), side, wb=_wb)
    meta_load = dict(sample_reused=bool(sample is not None))
    cov = -1.0
    try:
        m, fc = P.person_face(s.disp, with_cloth=True)
        cl = P.last_cloth()
        cov = float(np.mean(np.asarray(m) > 0.5))
    except Exception:                                                        # noqa: BLE001
        m, fc, cl = None, None, None
    sc = scene.classify(s.disp, C, lin=s.lin, person=m, face=fc, cloth=cl)
    tg = dict(targets.scene_engine(sc, stock=stock, cfg=C))   # 整份起底
    if overrides:
        tg.update(overrides)
    # ★★★ 动态光晕（用户 10-17 拍板 B 臂）：`halation_strength` ← **`deg_source`**
    #   · 物理：光晕来自高光溢出 ⇒ 光源越直射/越强，光晕越重；现在是整卷常数
    #   · 为什么不用 `deg_back`：我们 11 张实测 back 中位 **0.000**（7 张为 0）；
    #     `deg_source` 中位 0.339、**11 张全非零**（见 滨田_1017/deg_dist.py）
    #   · 形式：`base + deg_source * K * (TABLE - base)`，K=0.5；TABLE = 消费卷弱抗晕的引擎表值
    #   · 只对 `滨田预设` 生效（鹿井那条未拍板）；显式传了 halation 的 overrides 优先（本条让位）
    #   ⚠ 目前只在这个**分析入口**生效；要进生产入口，得把 `deg_source` 传进
    #     `presets.render_person_pe`（pipeline.py 那边已经算出了它，见 pipeline.py:145-151）
    if stock == '滨田预设' and not any('halation_strength' in k for k in (tg or {})):
        _dsrc = 0.0
        try:
            _dsrc = float((((sc or {}).get('raw') or {}).get('light') or {}).get('deg_source') or 0.0)
        except Exception:                                                        # noqa: BLE001
            _dsrc = 0.0
        if _dsrc > 0:
            _b = [1.5, 0.5, 0.0]
            _t = [8.0, 2.0, 0.0]
            tg['film_render.halation.halation_strength'] = [
                round(_b[i] + _dsrc * 0.5 * (_t[i] - _b[i]), 3) for i in range(3)]
    # ★★★ 10-17：**必须走生产入口** `render_person_pe`（「以人为本」的动态印相曝光）
    #   · `pipeline.py:185` 用的就是它 ⇒ 用 `presets.render` 渲出来的图**不含那笔**（会低估）
    #   · 力度 = 光位：`sc['raw']['light']['deg_back']`（拿不到 ⇒ 0.0 = 顺光档，只保下限）
    #   · ⚠ `PERSON_PE_DYNAMIC=False` 时它**逐位退回** `render()` ⇒ 关掉也安全
    _back = 0.0
    try:
        _back = float((((sc or {}).get('raw') or {}).get('light') or {}).get('deg_back') or 0.0)
    except Exception:                                                            # noqa: BLE001
        _back = 0.0
    t_render = time.time()
    e = presets.render_person_pe(np.clip(s.lin, 0.0, None), stock, C, overrides=tg, back=_back)
    u8 = (np.clip(e, 0, 1) * 255 + 0.5).astype(np.uint8)
    # ★ 10-18：**把"第二遍印相到底做了什么"记进 meta**（缓存回放时它就是唯一证据）。
    #   键名 `_LAST_PERSON_PE` 与引擎里那个全局同名（一眼能对上）；`person_pe` 是同一份的别名。
    _ppe = None
    try:
        _ppe = dict(getattr(presets, '_LAST_PERSON_PE', None) or {})
    except Exception:                                                            # noqa: BLE001
        _ppe = None
    import hashlib as _hl
    meta = dict(stem=stem, stock=stock, scene=sc.get('key'), shape=list(u8.shape),
                cov=round(cov, 4), dt=round(time.time() - t0, 1),
                dt_render=round(time.time() - t_render, 1),
                overrides={k: tg[k] for k in (overrides or {})},
                _LAST_PERSON_PE=_ppe, person_pe=_ppe,
                out_sha=_hl.sha256(np.ascontiguousarray(u8).tobytes()).hexdigest(),
                person_mask_ok=bool(m is not None), side=int(side))
    meta.update(meta_load)
    if out_dir:
        im = Image.fromarray(u8)
        if save_png:
            meta['png_bytes'] = int(os.path.getsize(
                atomic_save(im, os.path.join(out_dir, stem + '.png'), 'PNG',
                            compress_level=png_level)))
        if save_jpg:
            meta['jpg_bytes'] = int(os.path.getsize(
                atomic_save(im, os.path.join(out_dir, stem + '.jpg'), 'JPEG',
                            quality=jpg_quality, subsampling=0)))
    meta['u8'] = u8 if not out_dir else None
    return meta


# ---------------------------------------------------------------- 四层缓存版

def cached_render(stem, raf_dir, side, stock, overrides=None, out_dir=None,
                  cfg=None, person_metering=True, raf_ext='.RAF',
                  save_png=False, save_jpg=True, png_level=6, jpg_quality=95,
                  sample=None, use_cache=True, stats=None):
    r"""★★ **四层缓存版 `render_one`**：命中就把成片拷到 `out_dir` + 回放 meta（含 `_LAST_PERSON_PE`）。

    流程（键的算法全在 `_lib/cache.py`，这里只管"查 / 存"）：
    ```
    L1  有缓存 => 从 `lin` 重建 sample（免解码）；没有 => 解码后写 L1
    L2  `scene.classify()` 的结果按 (L1, 模型 sha) 存一份 —— 仅作可观测（渲染本身要用 sample）
    L3  命中 => 拷 `<key>.jpg` 到 out_dir + 读回 `<key>.json`；没中 => `render_one()` 后写 L3
    L4  留给 `cached_measure()`（本函数不算读数）
    ```

    @param sample {object|None} 调用方已备好的 sample（**同一张图渲多个预设时传它**，省解码）
    @param use_cache {bool} False = 直接走 `render_one`（等价 `SV_NOCACHE=1`，但只影响本次）
    @param stats {dict|None} 传一个 dict 进来，函数会累加 `hit/miss/decode`（做命中率统计用）
    @returns {dict} meta（多两个键：`cache` = `L3 hit` / `miss` / `off`，`key_l3`）
    """
    st = stats if stats is not None else {}
    for k in ('hit', 'miss', 'decode', 'l1_hit'):
        st.setdefault(k, 0)
    _lib.bootstrap_engine()
    from svFilm import presets as _P
    _wb = _P.wb_of(stock) if _P.has(stock) else None
    raf = os.path.join(raf_dir, stem + raf_ext)
    on = bool(use_cache) and _cache.enabled()
    if not on:
        s = sample
        if s is None:
            # ★ 关缓存时**仍然只解码一次**（否则一条命里渲两支预设会解码两次 ——
            #   `SV_NOCACHE=1` 的本意是"绕开缓存"，不是"多付一次解码"）
            s, _info = sample_for(stem, raf_dir, side, raf_ext, cache=False, wb=_wb)
        r = render_one(stem, raf_dir, side, stock, overrides=overrides, out_dir=out_dir,
                       cfg=cfg, person_metering=person_metering, raf_ext=raf_ext,
                       save_png=save_png, save_jpg=save_jpg, png_level=png_level,
                       jpg_quality=jpg_quality, sample=s)
        r['cache'] = 'off'
        r['key_l3'] = None
        r['_sample'] = s
        st['miss'] = st.get('miss', 0) + 1
        return r
    if not os.path.isfile(raf):
        raise IOError('缓存层找不到源文件：%s' % raf)
    l1 = _cache.key_l1(raf, side)
    l2 = _cache.key_l2(l1, cfg)
    l3 = _cache.key_l3(l2, stock, side, overrides, cfg,
                       extras=dict(person_metering=bool(person_metering)))
    st['key_l1'], st['key_l2'], st['key_l3'] = l1, l2, l3
    # ---- L3 命中：拷成片 + 回放 meta
    meta = _cache.get_json('L3', l3)
    if meta is not None and _cache.exists('L3', l3, 'jpg'):
        if out_dir:
            _cache.copy_out('L3', l3, 'jpg', os.path.join(out_dir, stem + '.jpg'))
            meta['jpg_bytes'] = int(os.path.getsize(os.path.join(out_dir, stem + '.jpg')))
            if save_png and _cache.exists('L3', l3, 'png'):
                _cache.copy_out('L3', l3, 'png', os.path.join(out_dir, stem + '.png'))
        meta['u8'] = None
        meta['cache'] = 'L3 hit'
        meta['key_l3'] = l3
        meta['from_cache'] = True
        meta['_sample'] = None
        st['hit'] = st.get('hit', 0) + 1
        return meta
    # ---- L1：拿 sample（优先缓存）
    s = sample
    if s is None:
        s, sinfo = sample_for(stem, raf_dir, side, raf_ext, cache=True, wb=_wb)
        if sinfo.get('how') == 'l1_hit':
            st['l1_hit'] = st.get('l1_hit', 0) + 1
        else:
            st['decode'] = st.get('decode', 0) + 1
        if s is None:
            raise IOError('解码失败：%s' % raf)
    # ---- L3 未中：渲
    r = render_one(stem, raf_dir, side, stock, overrides=overrides, out_dir=out_dir,
                   cfg=cfg, person_metering=person_metering, raf_ext=raf_ext,
                   save_png=save_png, save_jpg=save_jpg, png_level=png_level,
                   jpg_quality=jpg_quality, sample=s)
    r['cache'] = 'miss'
    r['key_l3'] = l3
    r['from_cache'] = False
    r['_sample'] = s          # ★ 交给调用方复用（**不进缓存 json**，见下）
    # ---- 写 L3（成片 + meta）；不写 `u8`
    try:
        if save_jpg:
            src = os.path.join(out_dir, stem + '.jpg') if out_dir else None
            if src and os.path.isfile(src):
                _cache.put_file('L3', l3, src, 'jpg')
            elif r.get('u8') is not None:
                import numpy as _np
                from PIL import Image as _I
                _cache.put_bytes('L3', l3,
                                 _jpeg_bytes(_I.fromarray(r['u8']), jpg_quality), 'jpg')
        if save_png and out_dir:
            src = os.path.join(out_dir, stem + '.png')
            if os.path.isfile(src):
                _cache.put_file('L3', l3, src, 'png')
        m = {k: v for k, v in r.items() if k not in ('u8', '_sample')}
        m['cache'] = 'miss'
        m['key_l3'] = l3
        m['cached_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        _cache.put_json('L3', l3, m)
        # L2：把这条场景判定存一份（可观测用；**不参与命中判定** —— 渲染必须拿到 sample 才能跑）
        try:
            _cache.put_json('L2', l2, dict(l2=l2, scene_key=r.get('scene'),
                                           stem=stem, side=int(side),
                                           shape=r.get('shape'), cov=r.get('cov')))
        except Exception:                                                    # noqa: BLE001
            pass
    except Exception as _e:                                                  # noqa: BLE001
        import sys as _sys
        print('[render 警告] L3 写盘失败（成片已落 out_dir，不影响本次结果）：%s' % _e,
              file=_sys.stderr)
    st['miss'] = st.get('miss', 0) + 1
    return r


def _jpeg_bytes(im, quality=95):
    r"""把 PIL 图压成 JPEG 字节（与 `atomic_save(..., 'JPEG', subsampling=0)` 同参数）。"""
    import io as _io
    buf = _io.BytesIO()
    im.save(buf, format='JPEG', quality=quality, subsampling=0)
    return buf.getvalue()


def shard_main(argv=None, stems=None, arms=None, raf_dir=None, side=2048,
               out_root=None, res_dir=None, arm_name=None, overrides_of=None,
               raf_ext='.RAF'):
    r"""worker 模板：`--shard <id>` -> 渲一个分片 + 自报「应有 N / 实得 M」+ 落 `shard_<id>.json`。

    用法（专题 worker 一般只写 3~5 行）：
    ```python
    import _lib.render as R
    if __name__ == '__main__':
        raise SystemExit(R.shard_main(stems=STEMS, arms=ARMS, raf_dir=RAF_DIR,
                                      side=2048, out_root=os.path.join(HERE,'render'),
                                      res_dir=os.path.join(HERE,'_par')))
    ```
    `arms` = {臂名: (stock, overrides|None)}；`overrides_of(arm)` 可用来自定义。
    @returns {int} 退出码（0 = 脚本没崩；缺张由编排器数母版核对）
    """
    import argparse
    import traceback
    ap = argparse.ArgumentParser()
    ap.add_argument('--shard', type=int, required=True)
    ap.add_argument('--arm', default=None)
    ap.add_argument('--tag', default=None)
    a = ap.parse_args(argv)
    arm = a.arm if a.arm else (arm_name(a.shard) if arm_name else None)
    if arm is None:
        raise SystemExit('shard_main: 必须给 --arm 或 arm_name')
    plan = arms[arm] if isinstance(arms, dict) else None
    stock, ov = (plan if isinstance(plan, tuple) else (plan, None))
    if overrides_of:
        ov = overrides_of(arm)
    out_dir = os.path.join(out_root, arm)
    os.makedirs(out_dir, exist_ok=True)
    res = dict(shard=a.shard, arm=arm, stock=stock, expect=len(stems), got=0,
               sec=None, per=[], failed=[], side=side, raf_dir=raf_dir,
               overrides=ov or {})
    print('[shard%d] %s stock=%s 应有 %d 张 ｜ 启动前可用 %d MB'
          % (a.shard, arm, stock, len(stems), avail_mb()), flush=True)
    t0 = time.time()
    for i, stem in enumerate(stems, 1):
        try:
            if not os.path.isfile(os.path.join(raf_dir, stem + raf_ext)):
                raise IOError('缺源 %s' % stem)
            r = render_one(stem, raf_dir, side, stock, overrides=ov, out_dir=out_dir,
                           raf_ext=raf_ext)
            r.pop('u8', None)
            res['per'].append(r)
            res['got'] += 1
            print('  [%s] %2d/%d %-9s %5.1fs scene=%s'
                  % (arm, i, res['expect'], stem, r['dt'], r['scene']), flush=True)
        except Exception as ex:                                              # noqa: BLE001
            res['failed'].append(dict(stem=stem, err='%s: %s' % (type(ex).__name__, ex)))
            print('  [%s] %2d/%d %-9s FAIL %s' % (arm, i, res['expect'], stem, ex), flush=True)
            traceback.print_exc()
    res['sec'] = round(time.time() - t0, 1)
    if res_dir:
        _J.write_json_atomic(res, os.path.join(res_dir, 'shard_%d.json' % a.shard))
    print('SELFPORT %s 应有 %d / 实得 %d 失败 %d 耗时 %.1fs'
          % (arm, res['expect'], res['got'], len(res['failed']), res['sec']), flush=True)
    return 0
