# -*- coding: utf-8 -*-
"""拼图台专用：把任意格式（JPG/PNG/BMP/TIFF…）读成一张**显示方向正确**的 PNG。

为什么不用 Electron 的 nativeImage：实测 Windows 下它对 TIFF/BMP 返回 0x0，
而拼图台要能吃刚转出来的 TIFF 扫描件。PIL 还能顺手做 exif_transpose，
把竖拍相机「物理横像素 + Orientation=6」的图摆正（这一步不摆正，
拼出来就是躺着的，而且预览和导出会不一致）。

用法：
  python collage_read.py --src <原图> --out <输出png> --max-side 0
  --max-side 0 = 原尺寸；>0 = 长边缩到该值（仅预览用，导出不走这条）

输出固定一行：KS_COLLAGE_OK {"w":..,"h":..,"bytes":..}
失败：      KS_COLLAGE_ERR {"error":"..."}
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image, ImageOps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-side', type=int, default=0)
    a = ap.parse_args()

    try:
        # ★ 大图（4653×3200 扫描件）要放开 PIL 的 DecompressionBomb 限制
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(a.src) as raw:
            # TIFF 可能多页，只取第一页
            try:
                raw.seek(0)
            except Exception:
                pass
            im = ImageOps.exif_transpose(raw)
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')
            elif im.mode == 'L':
                im = im.convert('RGB')
            w, h = im.size
            ow, oh = w, h          # 缩放前的原始像素（预览据此算 gap/pad 的缩放比）
            if a.max_side and max(w, h) > a.max_side:
                k = a.max_side / max(w, h)
                nw, nh = max(1, round(w * k)), max(1, round(h * k))
                im = im.resize((nw, nh), Image.LANCZOS)
                w, h = nw, nh
            os.makedirs(os.path.dirname(a.out), exist_ok=True)
            # compress_level=1：大图编码快很多，PNG 仍是无损
            im.save(a.out, 'PNG', compress_level=1)
        print('KS_COLLAGE_OK ' + json.dumps(
            {'w': w, 'h': h, 'ow': ow, 'oh': oh, 'bytes': os.path.getsize(a.out)},
            ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print('KS_COLLAGE_ERR ' + json.dumps({'error': str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
