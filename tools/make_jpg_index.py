#!/usr/bin/env python
"""给一个照片目录生成**预览索引**（缩略图缓存）—— 每张 RAW 抠一张机内 JPG。

为什么需要它
------------
工作台**一张片 = 一个 RAW**（身份键 = 文件名去扩展名，大写）。
预览 / 缩略图 / 大图 / EXIF **一律从这个 RAW 里抠相机自带的那张机内 JPG** ——
**JPG 不是一种片，只是从 RAW 里取出来的一段现成的图**，
所以目录里有没有独立的 JPG 文件，跟能不能看图**完全无关**（这就是这个脚本存在的全部理由）。

这个脚本把每张 RAW 的机内 JPG 抠出来、缩到长边 1600 存进缓存目录。
出图（渲染）用的**永远是源目录里那个 RAW 原件**（工作台直接在源目录里找，不靠缓存）。

    源目录（只读）                     缓存目录（随便删）
    D:\\示例库\\某次拍摄\\           %APPDATA%\\svstudio\\extpreview\\某次拍摄@a1b2c3d4\\
      DSCF0001.RAF      ────抠内嵌JPG──▶   DSCF0001.JPG   （长边 1600，带 EXIF）
      DSCF0002.RAF                        DSCF0002.JPG

★ 为什么用「内嵌机内 JPG」而不是自己解码渲染：
  抠它只是**从 RAW 文件里取现成的一段**，实测 3~4 ms/张；真解码一张 4400 万像素要几秒。
  一张 5 MB 的内嵌 JPG 缩到 1600 长边后约 300 KB ⇒ 700 张的目录约 200 MB 缓存。

★ **源目录一个字节都不改**：全程只读（`open(..., 'rb')` 由 rawpy 管），只往 `--out` 里写。

用法
----
  python make_jpg_index.py --src "D:\\示例库\\某次拍摄" --out "%APPDATA%\\svstudio\\extpreview\\xxx"
  python make_jpg_index.py --src ... --out ... --max-side 1600 --quality 88 --force

退出码：0 = 跑完（个别文件失败会计数、不影响整体）；2 = 起不来（源目录/输出目录有问题）。
末尾会打一行 `KS_EXT_INDEX_OK {...}`，工作台就是靠它拿结果的（不要改这行的格式）。
"""
import argparse
import io
import json
import os
import sys
import time

try:
    # line_buffering 保证输出重定向到管道时进度能实时刷出来（工作台靠它转进度条）
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
except Exception:
    pass

RAW_EXT = {'.raf', '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng'}
JPG_EXT = {'.jpg', '.jpeg'}

KS = 'KS_EXT_INDEX_OK'


def stem_of(name):
    return os.path.splitext(name)[0].upper()


def ext_of(name):
    return os.path.splitext(name)[1].lower()


def fmt(b):
    if b >= 2 ** 30:
        return '%.2f GB' % (b / 2 ** 30)
    if b >= 2 ** 20:
        return '%.1f MB' % (b / 2 ** 20)
    if b >= 2 ** 10:
        return '%.0f KB' % (b / 2 ** 10)
    return '%d B' % b


def pick_raws(src):
    """源目录里**所有** RAW（按 stem 去重，同名多形态只留一个）。

    ⚠★ 09-15 之前这里是"跳过有同名 JPG 的 RAW"（省事）。现在不行了：
      预览**一律从 RAW 抠** ⇒ 每张 RAW 都必须有索引，
      否则那张片在界面上就是**没图**的 —— 而"目录里有没有 JPG"正是要清掉的那个变量。
    """
    names = os.listdir(src)
    raws, seen = [], set()
    for n in sorted(names):
        if ext_of(n) not in RAW_EXT:
            continue
        s = stem_of(n)
        if s in seen:
            continue
        seen.add(s)
        raws.append(n)
    return raws


def extract_one(src_path, out_path, max_side, quality, force):
    """抠内嵌机内 JPG → 缩放 → 写 out_path。返回写出的字节数。

    ⚠ 子进程里读 RAW 偶发 `LibRawIOError`（libraw 对某些文件的句柄/缓存状态敏感）
      ⇒ 失败就**重开一次**再试（串行 + 一次重试；别并行，rawpy 在多进程里更不稳）。
    """
    import rawpy  # 放在函数里 import：缺依赖时报错信息能落在具体这一张上
    from PIL import Image, ImageOps

    last = None
    for attempt in (1, 2):
        try:
            with rawpy.imread(src_path) as raw:
                th = raw.extract_thumb()
            data = getattr(th, 'data', None)
            if data is None:
                raise RuntimeError('这台的相机没留内嵌 JPG（rawpy 也说没有缩略图）')
            if th.format == rawpy.ThumbFormat.JPEG:
                im = Image.open(io.BytesIO(data))
            elif th.format == rawpy.ThumbFormat.BITMAP:
                im = Image.fromarray(data)
            else:
                raise RuntimeError('不认得的内嵌图格式：%r' % (th.format,))

            # ★ 方向必须先转正再缩（否则竖拍片会躺着）。EXIF 里那条 Orientation 也要跟着改掉，
            #   不然后面看图的软件会**再**转一次 = 转两圈。
            exif_bytes = None
            try:
                exif_bytes = im.info.get('exif')
            except Exception:
                exif_bytes = None
            try:
                im = ImageOps.exif_transpose(im)
                exif_bytes = im.info.get('exif', exif_bytes)
            except Exception:
                pass

            im = im.convert('RGB')
            # ★ max_side = 0 ⇒ 不缩、保持机内 JPG 原尺寸（归档要全尺寸的直出件）
            if max_side and max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)

            kw = {}
            if exif_bytes:
                kw['exif'] = exif_bytes
            tmp = out_path + '.tmp'
            im.save(tmp, 'JPEG', quality=quality, optimize=True, **kw)
            os.replace(tmp, out_path)      # ★ 先写临时名再原子换名：中途被杀不会留半张坏图
            return os.path.getsize(out_path)
        except Exception as e:             # noqa: BLE001 —— 单张失败不该炸掉整个目录
            last = e
            if attempt == 2:
                break
            continue
    raise last


def run_one(args):
    """单文件模式：从一张 RAW 抠机内 JPG 写到 `--out-file`。

    ★ 归档「初筛1星」要一份**全尺寸**的相机直出 ⇒ 传 `--max-side 0`。
    退出码 0 = 写出来了；1 = 没写出来（原因打在最后一行）。"""
    src = os.path.abspath(args.one)
    dst = os.path.abspath(args.out_file or '')
    if not dst:
        print('[错误] --one 必须配 --out-file')
        return 2
    if not os.path.isfile(src):
        print('[错误] 这个 RAW 不在：%s' % src)
        return 1
    try:
        os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
        sz = extract_one(src, dst, args.max_side, args.quality, args.force)
    except Exception as e:                 # noqa: BLE001
        print('[失败] 抠不出来：%s（%s）' % (os.path.basename(src), e))
        return 1
    print('OK %s  %s' % (dst, fmt(sz)))
    return 0


def main():
    ap = argparse.ArgumentParser(description='为照片目录生成 JPG 预览索引（从 RAW 抠机内 JPG；源目录只读）')
    ap.add_argument('--src', help='源目录（只读，不会被动）')
    ap.add_argument('--out', help='索引输出目录（缓存，可以整个删）')
    ap.add_argument('--one', help='单文件模式：只处理这一张 RAW（要配 --out-file）')
    ap.add_argument('--out-file', help='单文件模式：写出的 JPEG 路径')
    ap.add_argument('--max-side', type=int, default=1600,
                    help='长边上限，默认 1600；**0 = 不缩、保持机内 JPG 原尺寸**')
    ap.add_argument('--quality', type=int, default=88, help='JPEG 质量，默认 88')
    ap.add_argument('--force', action='store_true', help='已存在的也重做')
    args = ap.parse_args()

    if args.one:
        return run_one(args)
    if not args.src or not args.out:
        print('[错误] 要么 --src + --out（整个目录），要么 --one + --out-file（单张）')
        return 2

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    if not os.path.isdir(src):
        print('[错误] 源目录不存在：%s' % src)
        return 2

    try:
        raws = pick_raws(src)
    except OSError as e:
        print('[错误] 读不了源目录：%s（%s）' % (src, e))
        return 2

    print('源目录  : %s' % src)
    print('索引目录: %s' % out)
    if not raws:
        print('[跳过] 这个目录里没有 RAW —— 工作台只认 RAW，这个目录是空的')
        print('%s %s' % (KS, json.dumps(
            {'src': src, 'out': out, 'n': 0, 'skip': 0, 'fail': 0, 'bytes': 0, 'ms': 0},
            ensure_ascii=False)))
        return 0
    print('RAW     : %d 张   （长边 %d，质量 %d）' % (len(raws), args.max_side, args.quality))

    try:
        os.makedirs(out, exist_ok=True)
    except OSError as e:
        print('[错误] 建不了索引目录：%s（%s）' % (out, e))
        return 2

    # ⚠ 09-15 起**不写 `_src.txt` 了**：那是"工作台 path 指向缓存"时代的出图源凭据。
    #   现在工作台的 path 就是源目录、出图直接在源目录里找 RAW ⇒ 这个目录是**纯缩略图缓存**，
    #   整个删掉也不影响出图（下次自动重做）。
    todo = []
    skip = 0
    for n in raws:
        dst = os.path.join(out, os.path.splitext(n)[0] + '.JPG')
        if os.path.exists(dst) and not args.force:
            skip += 1
            continue
        todo.append((n, dst))

    print('\n开始生成预览…（源目录只读，不会被动一个字节）')
    t0 = time.time()
    ok = 0
    total = 0
    fail = []
    for i, (n, dst) in enumerate(todo, 1):
        try:
            sz = extract_one(os.path.join(src, n), dst, args.max_side, args.quality, args.force)
            ok += 1
            total += sz
            if i % 5 == 0 or i == len(todo):
                print('  %d/%d  已生成 %s  (%s)' % (i, len(todo), os.path.basename(dst), fmt(sz)))
        except Exception as e:             # noqa: BLE001
            fail.append(n)
            print('  [失败] %s : %s' % (n, e))

    ms = int((time.time() - t0) * 1000)
    print('\n完成：生成 %d 张，跳过(已有) %d 张，失败 %d 张，耗时 %.1f 秒'
          % (ok, skip, len(fail), ms / 1000.0))
    print('缓存约 %s（源目录一个字节没动；这个目录可以整个删，下次会自动重做）' % fmt(total))
    if fail:
        print('失败的（工作台里这几张列不出来）：%s' % ', '.join(fail[:10]))
    print('%s %s' % (KS, json.dumps(
        {'src': src, 'out': out, 'n': ok, 'skip': skip, 'fail': len(fail),
         'bytes': total, 'ms': ms, 'failed': fail[:20]}, ensure_ascii=False)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
