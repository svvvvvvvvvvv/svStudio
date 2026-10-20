# -*- coding: utf-8 -*-
r"""命令行入口。

  python -m svFilm.cli probe <img...> | one <img> -o out.jpg | dir <in> -o <out> [--jobs 4]
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys

from . import config as C, io, presets
from . import lr_meta


def _fmt_probe(res):
    # ★ 落点 L* **直接从成片量**。
    import numpy as _np
    from . import color
    _l50 = float(_np.median(color.to_lab(_np.clip(res.disp, 0.0, 1.0))[..., 0]))
    _sc = res.report.get('scene') or {}
    return ('%-16s %-4s %-14s  落点L* %5.1f  场景 %-18s  %5.0fms' % (
                res.sample.name, res.sample.kind,
                (res.report.get('stock') or 'config默认'),
                _l50,
                (_sc.get('key') or '-'),
                res.report.get('ms', 0)))


def cmd_probe(args):
    from . import pipeline
    for p in _expand(args.inputs, args.recursive):
        res = pipeline.run(p, src=args.src, max_side=args.max_side, stock=args.stock)
        print(_fmt_probe(res))
    return 0


def cmd_calib(args):
    r"""量机型表用的基线增益：拿 RAW 与"同名 JPG"对齐中灰。"""
    import glob as _g
    import os as _os

    import numpy as np
    from . import color, io

    pairs = []
    for p in _expand(args.inputs, True):
        if io.kind_of(p) != 'raw':
            continue
        jpg = None
        for e in ('.JPG', '.jpg', '.JPEG', '.jpeg'):
            if _os.path.exists(_os.path.splitext(p)[0] + e):
                jpg = _os.path.splitext(p)[0] + e
                break
        if jpg:
            pairs.append((p, jpg))
    if not pairs:
        print('没找到 RAW+JPG 同名对')
        return 2

    print('%-16s %-14s %8s %8s %8s' % ('file', 'model', 'rawY50', 'jpgY50', 'gain'))
    gains = []
    for rp, jp in pairs:
        a = io.load(rp, max_side=1024, src='raw')
        b = io.load(jp, max_side=1024, src='jpg')
        ym = float(np.percentile(color.Y_of(a.lin), 50))
        jm = float(np.percentile(color.s2l(b.disp), 50))
        g = jm / max(ym, 1e-9)
        gains.append(g)
        md = a.cam.get('model') or '?'
        print('%-16s %-14s %8.4f %8.4f %8.2f' % (_os.path.basename(rp), md, ym, jm, g))
    med = float(np.median(gains))
    print('-' * 60)
    print('中位增益 %.3f  →  建议 baseline_ev = %+.2f' % (med, float(np.log2(med))))
    print('（挑曝光标准的样张；同一批里有人为欠曝的图会把数字拉偏）')
    return 0


def cmd_one(args):
    from . import pipeline
    res = pipeline.run(args.input, src=args.src, max_side=args.max_side, stock=args.stock)
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
    # 输出名带 _svFilm 后缀：**不能叫 <名字>.jpg**（会和"相机直出同名 JPG"撞名）。
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
        keep = []
        for p in ps:
            try:
                if _io.kind_of(p) == 'raw':
                    keep.append(p)
            except ValueError:
                pass
        ps = keep
    seen, uniq = set(), []
    for p in ps:
        k = os.path.abspath(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    ps = uniq
    if args.limit:
        ps = ps[:args.limit]
    if not ps:
        print('没找到输入文件')
        return 2
    os.makedirs(args.out, exist_ok=True)
    jobs = max(1, int(args.jobs))

    print('共 %d 个文件，并发 %d' % (len(ps), jobs))
    sys.stdout.flush()

    from . import pipeline
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


def cmd_lrbase_fit(args):
    r"""拟合「平基底片 → 当前冲印」的逐通道曲线（C2 基底曲线）。"""
    from . import lrbase
    try:
        curve = lrbase.fit_base_curve(args.print_img, args.flat_img,
                                      points=args.points, bins=args.bins)
    except Exception as e:                                  # noqa: BLE001
        print('拟合失败: %s: %s' % (type(e).__name__, e))
        return 1
    out = args.out or 'base_curve.json'
    lrbase.save_curve_json(curve, out)
    print('-> %s' % out)
    print('fit: %s' % json.dumps(curve.get('fit'), ensure_ascii=False))
    return 0


def _lrbase_task(t):
    r"""C2 单张 worker（进程池要 top-level）。"""
    from . import lrbase
    (p, out_dir, stock, max_side, src, lo, hi, gains, headroom,
     base_curve, master, preset_name, write_preset) = t
    try:
        r = lrbase.run_one(
            p, out_dir=out_dir, stock=stock, max_side=max_side, src=src,
            lo_q=lo, hi_q=hi, gains=gains, headroom=headroom,
            base_curve=base_curve, master=master,
            preset_name=preset_name, write_preset=write_preset)
        return (p, r, None)
    except Exception as e:                                  # noqa: BLE001
        return (p, None, '%s: %s' % (type(e).__name__, e))


def cmd_lrbase(args):
    r"""C2：出平基底片（16-bit TIFF）+ LR 元数据；不跑印相。"""
    from . import lrbase
    ps = _expand(args.inputs, True)
    if args.raw_only:
        keep = []
        for p in ps:
            try:
                if io.kind_of(p) == 'raw':
                    keep.append(p)
            except ValueError:
                pass
        ps = keep
    if args.limit:
        ps = ps[:args.limit]
    if not ps:
        print('没找到输入文件（--raw-only 时只收 RAW）')
        return 2

    try:
        base_curve = None
        if args.base_curve:
            with open(args.base_curve, 'r', encoding='utf-8') as f:
                base_curve = json.load(f)
        master = None
        if args.master:
            with open(args.master, 'r', encoding='utf-8') as f:
                master = json.load(f)
    except (OSError, ValueError) as e:
        print('读 base-curve/master 失败: %s' % e)
        return 2

    try:
        gains = tuple(float(v) for v in str(args.gains).replace('，', ',').split(','))
        if len(gains) != 3:
            raise ValueError
    except ValueError:
        print('--gains 要三个数，例如 1,1,1')
        return 2

    out_dir = args.out
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    preset_name = args.preset_name or ('svFilm %s flat' % (args.stock or C.STOCK))
    tasks = []
    for p in ps:
        od = out_dir or os.path.dirname(os.path.abspath(p))
        tasks.append((p, od, args.stock, args.max_side, args.src,
                      args.lo, args.hi, gains, args.headroom,
                      base_curve, master, preset_name, not args.no_preset))

    jobs = max(1, int(args.jobs))
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
        lr_meta.write_manifest(items, manifest, mode='flat')
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
    ap = argparse.ArgumentParser('svFilm', description='数码仿胶片管线（重写版）')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('--src', default=None, choices=[None, 'auto', 'raw', 'jpg'])
        p.add_argument('--max-side', type=int, default=None, dest='max_side')
        p.add_argument('--stock', default=None,
                       help='胶片风格：%s；不指定 = config.STOCK' % '/'.join(presets.names()))

    ap.add_argument('--version', action='version', version='svFilm ' + C.VERSION)

    p = sub.add_parser('lrbase-fit', help='拟合 C2 基底曲线（平基底片 → 冲印成片）')
    p.add_argument('--print', required=True, dest='print_img', help='当前冲印成片（JPEG/PNG）')
    p.add_argument('--flat', required=True, dest='flat_img', help='平基底片（16-bit TIFF）')
    p.add_argument('--out', default=None, help='输出 curve JSON')
    p.add_argument('--points', type=int, default=16, help='曲线控制点数（默认 16）')
    p.add_argument('--bins', type=int, default=32, help='分位分箱数（默认 32）')
    p.set_defaults(func=cmd_lrbase_fit)

    p = sub.add_parser('lrbase', help='C2：平基底片 16-bit TIFF + LR 元数据（不跑印相）')
    p.add_argument('inputs', nargs='+')
    p.add_argument('-o', '--out', default=None, help='输出目录（缺省 = 各自原目录）')
    p.add_argument('--jobs', type=int, default=1, help='并发进程数（重活建议 <=4）')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--raw-only', action='store_true', dest='raw_only', default=True,
                   help='目录/通配符里只收 RAW（默认开）')
    p.add_argument('--allow-jpg', action='store_false', dest='raw_only',
                   help='允许 JPG 输入（默认只收 RAW）')
    p.add_argument('--lo', type=float, default=1.0, help='黑点分位 %%（默认 1.0）')
    p.add_argument('--hi', type=float, default=99.5, help='白点分位 %%（默认 99.5）')
    p.add_argument('--headroom', type=float, default=0.01,
                   help='平基底片留白（0 = 硬裁到 0/1；默认 0.01）')
    p.add_argument('--gains', default='1,1,1', help='逐通道增益，例如 1.02,1.0,0.98')
    p.add_argument('--base-curve', default=None, dest='base_curve',
                   help='lrbase-fit 产出的 JSON（缺省 = identity）')
    p.add_argument('--master', default=None, help='大师设置 JSON（可选）')
    p.add_argument('--preset-name', default=None, dest='preset_name')
    p.add_argument('--no-preset', action='store_true', dest='no_preset')
    common(p)
    p.set_defaults(func=cmd_lrbase)

    # ⚠ **不许写死预设条数**（写死的数会过期）⇒ 运行时数出来。
    p = sub.add_parser('stocks', help='列出所有胶片风格（%d 条预设）' % len(presets.names()))
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

    p = sub.add_parser('calib', help='量机型表要填的基线增益（需 RAW+JPG 同名对）')
    p.add_argument('inputs', nargs='+')
    p.set_defaults(func=cmd_calib)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
