# -*- coding: utf-8 -*-
"""C2「LR 平基底片」导出 —— svFilm 只出胶片负片，印相/影调/分色交给 LR。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config as C
from . import io, lr_meta, pipeline, presets

try:
    import tifffile
except Exception:  # pragma: no cover - 只在导出 TIFF 时需要
    tifffile = None

try:
    import colour
except Exception:  # pragma: no cover - 只在拟合报告误差时需要
    colour = None

try:
    from scipy.interpolate import PchipInterpolator
except Exception:  # pragma: no cover - 只在拟合基底曲线时需要
    PchipInterpolator = None


@dataclass
class FlatResult:
    disp: np.ndarray          # sRGB 编码 0..1（给 TIFF / 预览）
    lin: np.ndarray           # 正像线性（反转/归一后）
    neg: np.ndarray           # 负片线性（render_linear 原样）
    report: dict








def save_tiff16(disp: np.ndarray, path: str | Path, *, icc: bool = False) -> str:
    """sRGB 编码 0..1 → 16-bit RGB TIFF。"""
    if tifffile is None:
        raise RuntimeError("缺 tifffile：pip install tifffile（或改用 cv2 的 16-bit TIFF）")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    arr16 = np.round(arr * 65535.0).astype(np.uint16)
    extratags = None
    if icc:
        try:
            from PIL import ImageCms
            prof = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            extratags = [(34675, "B", len(prof), prof, False)]
        except Exception:  # pragma: no cover - 没有 ICC 也要能写
            extratags = None
    tmp = path.with_name(path.name + ".tmp.tif")
    tifffile.imwrite(tmp, arr16, photometric="rgb", compression="zlib",
                     extratags=extratags)
    os.replace(tmp, path)
    return str(path)


















def render_print_full(sample, stock: str, cfg=C, *,
                      path: str | Path | None = None) -> FlatResult:
    """film_print_full：跑完整印相（**固定**印相曝光，来自预设），大师层归 LR。"""
    res = pipeline.run_from(sample, cfg=cfg, stock=stock,
                            path=str(path) if path is not None else None)
    disp = np.asarray(res.disp, np.float64)
    return FlatResult(disp=disp, lin=disp, neg=None, report=dict(res.report or {}))



def _safe_stem(path: str | Path) -> str:
    return Path(path).stem


def run_one(path: str | Path, out_dir: str | Path | None = None, *,
            stock: str | None = None, max_side: int | None = None,
            src: str | None = None,
            master: dict | None = None,
            preset_name: str = "svFilm",
            write_preset: bool = True,
            overwrite: bool = True,
            grain_owner: str = "svfilm",
            halation_owner: str = "svfilm") -> dict:
    """单张：RAW → 16-bit TIFF + JSON/XMP/lrpreset。"""
    if grain_owner == "lr" or halation_owner == "lr":
        raise ValueError("颗粒/光晕在 sfFilm 里烘进像素（介质的一部分），不能声明归 LR")
    t0 = time.perf_counter()
    path = Path(path)
    name = str(stock if stock is not None else C.STOCK)
    if not presets.has(name):
        raise KeyError("没有这个胶片风格: %s（可选：%s）" % (name, "、".join(presets.names())))
    wb = presets.wb_of(name) if presets.has(name) else None
    s = io.load(str(path), max_side if max_side is not None else C.MAX_SIDE,
                src=src, wb=wb)

    flat = render_print_full(s, name, C, path=path)
    suffix = "_full.tif"
    note = "完整印相（**逐图印相落点**，见 print 段）；颗粒/光晕/颜色已烘"
    out_dir = Path(out_dir) if out_dir is not None else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(path) + "_svFilm"
    tif_path = out_dir / (stem + suffix)
    if tif_path.exists() and not overwrite:
        raise FileExistsError(tif_path)

    _r = flat.report or {}
    report = {
        "mode": "print_full",
        "stock": name,
        "stock_label": presets.label_of(name)[0],
        "stock_desc": presets.label_of(name)[1],
        "camera": s.cam,
        "entry_bias_ev": (s.cam or {}).get("idt_bias_ev"),
        "fuji_dr": (s.cam or {}).get("fuji_dr"),
        # ★★ 记账：这里是**实际**印相曝光（B 方案由 render_key 逐图解出），不是标称值。
        "print_exposure": _r.get("print_exposure", presets.pe_of(name)),
        "print_exposure_nominal": _r.get("print_exposure_nominal", presets.pe_of(name)),
        "print_exposure_ev": _r.get("print_exposure_ev"),
        "key_p50": _r.get("key_p50"),
        "key_white": _r.get("key_white"),
        "key_black": _r.get("key_black"),
        "key_hit_bound": _r.get("key_hit_bound"),
        "key_both_dead": _r.get("key_both_dead"),
        "film": {
            "stock": name,
            "paper": presets.label_of(name)[1],
            "scan": "spektrafilm/scan_film",
            "grain_owner": grain_owner,
            "halation_owner": halation_owner,
        },
        "ms": (time.perf_counter() - t0) * 1000.0,
    }
    save_tiff16(flat.disp, tif_path)

    meta = lr_meta.build_metadata(report, tif_path, mode="print_full",
                                  master=master,
                                  preset_name=preset_name,
                                  grain_owner=grain_owner,
                                  halation_owner=halation_owner)
    json_path = out_dir / (stem + ".svfilm.json")
    xmp_path = out_dir / (stem + ".xmp")
    lua_path = out_dir / (stem + ".svfilm.lua")
    lrtxt_path = out_dir / (stem + ".svfilm.lr.txt")
    preset_path = out_dir / (stem + ".lrpreset.xmp")
    lr_meta.write_json(meta, json_path)
    lr_meta.write_sidecar_xmp(meta, xmp_path)
    lr_meta.write_lua(meta, lua_path)
    lr_meta.write_lr_txt(meta, lrtxt_path)
    if write_preset:
        lr_meta.write_preset_xmp(meta, preset_path)
    return {
        "input": str(path),
        "flat": str(tif_path),
        "json": str(json_path),
        "xmp": str(xmp_path),
        "lua": str(lua_path),
        "lrtxt": str(lrtxt_path),
        "preset": str(preset_path) if write_preset else None,
        "shape": [int(v) for v in flat.disp.shape],
        "ms": report["ms"],
        "sha256": meta["image"]["sha256"],
    }
