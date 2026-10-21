# -*- coding: utf-8 -*-
r"""命令行入口。

  python -m svFilm.cli one <img> -o out.jpg          # 单张成片（JPEG）
  python -m svFilm.cli dir <in> -o <out> [--jobs 4]  # 批处理成片
  python -m svFilm.cli lrbase <RAW...> -o <out>      # 给 LR 的 16-bit TIFF + 元数据（生产档）
  python -m svFilm.cli stocks                         # 列出胶片风格

★ 2026-10-21 清理：`flat` / `print_neutral` / `lrbase-fit` / `lrbase-preview` / `calib` /
  `service` / `selftest` 全部删除 —— 它们都在"用形状旋钮追位置目标"那条老路上。
  现在只有一条生产链：**介质在 sfFilm 烘死，落点/彩度/冷暖归 LR**。
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys

from . import config as C, io, presets


def auto_jobs(max_side):
    r"""`--jobs 0`（缺省）时的并发数 —— **按图幅定，不是按核数**。

    实测（4 张 @2048，32 核 / 31.8 GB）：jobs=1 → 113 s，2 → 61 s，4 → **33 s**，
    **6 → 33 s（不再增长）** ⇒ 瓶颈是**内存带宽**，4 之后再加进程纯属互相挤。
    """
    try:
        n = int(max_side) if max_side else 0
    except (TypeError, ValueError):
        n = 0
    if n <= 0 or n >= 5000:
        return 2
    if n <= 1400:
        return 6
    if n <= 2600:
        return 4
    return 3


def _fmt_probe(res):
    import numpy as _np
    from . import color
    _l50 = float(_np.median(color.to_lab(_np.clip(res.disp, 0.0, 1.0))[..., 0]))
    return ('%-16s %-4s %-14s  落点L* %5.1f  %5.0fms' % (
        res.sample.name, res.sample.kind,
        (res.report.get('stock') or 'config默认'), _l50, res.report.get('ms', 0)))


def cmd_probe(args):
    from . import pipeline
    for p in _expand(args.inputs, args.recursive):
        print(_fmt_probe(pipeline.run(p, src=args.src, max_side=args.max_side,
                                      stock=args.stock)))
    return 0


def cmd_one(args):
    from . import pipeline
    res = pipeline.run(args.input, src=args.src, max_side=args.max_side,
                       stock=args.stock)
    out = args.out or (os.path.splitext(args.input)[0] + '_svFilm.jpg')
    res.save(out)
    print(res.summary())
    print('-> ' + out)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(res.report, f, ensure_ascii=False, indent=1)
    return 0


def _worker(t):
    from . import pipeline
    src, max_side, outdir, p, stock = t
    name = os.path.splitext(os.path.basename(p))[0]
    suffix = '_svFilm' + (('_' + stock) if stock else '')
    out = os.path.join(outdir, name + suffix + '.jpg')
    if os.path.abspath(out) == os.path.abspath(p):
        return (p, None, '', '输出会覆盖输入，已跳过')
    try:
        res = pipeline.run(p, src=src, max_side=max_side, stock=stock)
        res.save(out)
        return (p, out, res.summary(), None)
    except Exception as e:                                  # noqa: BLE001
        return (p, None, '', '%s: %s' % (type(e).__name__, e))


def _pair_resolve(paths, pair_dir):
    r"""给一批 JPG，去 pair_dir 里找**同名 RAW**（找不到就保留原文件）。"""
    out = []
    for p in paths:
        try:
            k = io.kind_of(p)
        except ValueError:
            k = 'std'
        if k == 'raw':
            out.append(p)
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        hit = None
        for e in sorted(io.RAW_EXT):
            for ee in (e, e.upper()):
                cand = os.path.join(pair_dir, stem + ee)
                if os.path.exists(cand):
                    hit = cand
                    break
            if hit:
                break
        out.append(hit or p)
    return out


def cmd_dir(args):
    from . import io as _io
    ps = _expand(args.inputs, True, pattern=args.glob)
    if args.pair:
        ps = _pair_resolve(ps, args.pair)
    if args.raw_only:
        ps = [p for p in ps if _raw_ok(_io, p)]
    seen, uniq = set(), []
    for p in ps:
        k = os.path.abspath(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    ps = uniq[:args.limit] if args.limit else uniq
    if not ps:
        print('没找到输入文件')
        return 2
    os.makedirs(args.out, exist_ok=True)
    jobs = max(1, int(args.jobs))
    print('共 %d 个文件，并发 %d' % (len(ps), jobs))
    sys.stdout.flush()

    tasks = [(args.src, args.max_side, args.out, p, args.stock) for p in ps]
    done = fail = 0
    if jobs == 1:
        for t in tasks:
            p, out, s, err = _worker(t)
            done += err is None
            fail += err is not None
            print(('OK  ' + s) if err is None else ('FAIL ' + p + ' ' + err))
            sys.stdout.flush()
    else:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
            for p, out, s, err in ex.map(_worker, tasks):
                done += err is None
                fail += err is not None
                print(('OK  ' + s) if err is None else ('FAIL ' + p + ' ' + err))
                sys.stdout.flush()
    print('完成 %d / 失败 %d  -> %s' % (done, fail, os.path.abspath(args.out)))
    return 0 if fail == 0 else 1


def _raw_ok(io_mod, p):
    try:
        return io_mod.kind_of(p) == 'raw'
    except ValueError:
        return False


def _lrbase_task(t):
    r"""lrbase 单张 worker（进程池要 top-level）。"""
    from . import lrbase
    p, out_dir, stock, max_side, src, master, preset_name, write_preset = t
    try:
        r = lrbase.run_one(p, out_dir=out_dir, stock=stock, max_side=max_side, src=src,
                           master=master, preset_name=preset_name,
                           write_preset=write_preset)
        return (p, r, None)
    except Exception as e:                                  # noqa: BLE001
        return (p, None, '%s: %s' % (type(e).__name__, e))


def cmd_lrbase(args):
    r"""RAW → 16-bit TIFF + LR 元数据（**完整印相，固定印相曝光**）。"""
    from . import lr_meta, lrbase
    ps = _expand(args.inputs, True)
    if args.raw_only:
        ps = [p for p in ps if _raw_ok(io, p)]
    if args.limit:
        ps = ps[:args.limit]
    if not ps:
        print('没找到输入文件（--raw-only 时只收 RAW）')
        return 2

    master = None
    if args.master:
        try:
            with open(args.master, 'r', encoding='utf-8') as f:
                master = json.load(f)
        except (OSError, ValueError) as e:
            print('读 master 失败: %s' % e)
            return 2

    out_dir = args.out
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    preset_name = args.preset_name or ('svFilm %s' % (args.stock or C.STOCK))
    tasks = [(p, out_dir or os.path.dirname(os.path.abspath(p)), args.stock,
              args.max_side, args.src, master, preset_name, not args.no_preset)
             for p in ps]

    jobs = max(1, int(args.jobs)) if int(args.jobs) > 0 else auto_jobs(args.max_side)
    # ★ 并发不许超过张数（多余 worker 光 import 就 2 s/个）
    jobs = min(jobs, max(1, len(tasks)))
    print('lrbase 共 %d 张，并发 %d；输出 %s' % (len(ps), jobs, out_dir or '（各自目录）'))
    sys.stdout.flush()
    done, fail, items = 0, 0, []
    if jobs == 1:
        for t in tasks:
            p, r, err = _lrbase_task(t)
            if err is None:
                done += 1
                items.append(r)
                print('OK  %s  -> %s' % (os.path.basename(p), r['flat']))
            else:
                fail += 1
                print('FAIL %s  %s' % (p, err))
            sys.stdout.flush()
    else:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
            for p, r, err in ex.map(_lrbase_task, tasks):
                if err is None:
                    done += 1
                    items.append(r)
                    print('OK  %s  -> %s' % (os.path.basename(p), r['flat']))
                else:
                    fail += 1
                    print('FAIL %s  %s' % (p, err))
                sys.stdout.flush()

    if items and out_dir:
        manifest = os.path.join(out_dir, '_svfilm_manifest.json')
        lr_meta.write_manifest(items, manifest, mode='print_full')
        print('manifest -> %s' % manifest)
    print('完成 %d / 失败 %d' % (done, fail))
    return 0 if fail == 0 else 1


def cmd_stocks(args):
    """列出所有胶片风格。"""
    print('%-16s %-16s %s' % ('胶片风格', '中文名', '一句话'))
    print('-' * 92)
    for n in presets.names():
        lb, ds = presets.label_of(n)
        flag = ' ←' if (args.stock or C.STOCK) == n else ''
        print('%-16s %-16s %s%s' % (n, lb, ds, flag))
    print('-' * 92)
    print('用法：--stock <风格名>；不指定 = config.STOCK（当前 %s）' % (C.STOCK or 'None'))
    return 0


def _expand(inputs, recursive, pattern='*.jpg'):
    out = []
    for p in inputs:
        if os.path.isdir(p):
            pats = ['*.RAF', '*.raf', '*.ARW', '*.arw', '*.CR2', '*.CR3', '*.NEF', '*.DNG',
                    '*.JPG', '*.jpg', '*.JPEG', '*.jpeg', '*.PNG', '*.png']
            for pat in pats:
                out.extend(sorted(_glob.glob(os.path.join(p, '**', pat), recursive=True)
                                  if recursive else _glob.glob(os.path.join(p, pat))))
        elif any(ch in p for ch in '*?['):
            out.extend(sorted(_glob.glob(p, recursive=recursive)))
        else:
            out.append(p)
    from . import paths
    seen, uniq = set(), []
    for p in out:
        k = os.path.abspath(p).lower()
        if k in seen:
            continue
        # 自家产出树整棵跳过（否则第二次跑目录会把上一轮的成片当素材再处理一遍）。
        if paths.TOP.lower() in [s.lower() for s in os.path.abspath(p).split(os.sep)]:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def build_parser():
    ap = argparse.ArgumentParser('svFilm', description='数码仿胶片管线')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('--src', default=None, choices=[None, 'auto', 'raw', 'jpg'])
        p.add_argument('--max-side', type=int, default=None, dest='max_side')
        p.add_argument('--stock', default=None,
                       help='胶片风格：%s；不指定 = config.STOCK' % '/'.join(presets.names()))

    ap.add_argument('--version', action='version', version='svFilm ' + C.VERSION)

    p = sub.add_parser('lrbase', help='RAW → 16-bit TIFF + LR 元数据（生产档）')
    p.add_argument('inputs', nargs='+')
    p.add_argument('-o', '--out', default=None, help='输出目录（缺省 = 各自原目录）')
    p.add_argument('--jobs', type=int, default=0,
                   help='并发进程数；0=自动（按图幅定，见 auto_jobs）')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--raw-only', action='store_true', dest='raw_only', default=True,
                   help='目录/通配符里只收 RAW（默认开）')
    p.add_argument('--allow-jpg', action='store_false', dest='raw_only',
                   help='允许 JPG 输入（默认只收 RAW）')
    p.add_argument('--master', default=None, help='大师设置 JSON（可选，只进元数据）')
    p.add_argument('--preset-name', default=None, dest='preset_name')
    p.add_argument('--no-preset', action='store_true', dest='no_preset')
    common(p)
    p.set_defaults(func=cmd_lrbase)

    p = sub.add_parser('stocks', help='列出所有胶片风格')
    p.add_argument('--stock', default=None)
    p.set_defaults(func=cmd_stocks)

    p = sub.add_parser('probe', help='只分析不写文件')
    p.add_argument('inputs', nargs='+')
    p.add_argument('--recursive', action='store_true')
    common(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser('one', help='处理单张')
    p.add_argument('input')
    p.add_argument('-o', '--out', default=None)
    p.add_argument('--json', default=None, help='把报告写成 json')
    common(p)
    p.set_defaults(func=cmd_one)

    p = sub.add_parser('dir', help='批处理目录')
    p.add_argument('inputs', nargs='+')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--jobs', type=int, default=4, help='重活建议 <=4（内存）')
    p.add_argument('--glob', default='*.jpg', help='占位，实际按内置扩展名表扫描')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--raw-only', action='store_true', dest='raw_only',
                   help='只要 RAW（目录里 RAW+JPG 混放时用）')
    p.add_argument('--pair', default=None, metavar='DIR',
                   help='给 JPG 去这个目录找同名 RAW 来跑（选片目录只有 JPG 时用）')
    common(p)
    p.set_defaults(func=cmd_dir)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
