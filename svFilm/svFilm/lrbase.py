# -*- coding: utf-8 -*-
r"""C2「LR 平基底片」导出 —— svFilm 只出胶片负片，印相/影调/分色交给 LR。

一条链：
    RAW → io.load（场景线性）→ presets.render_linear（scan_film，线性、不裁剪）
        → invert_negative（线性域倒数 + 逐通道分位归一 + 可选增益）
        → encode_srgb → 16-bit TIFF（带 sRGB ICC）
        → lr_meta（JSON / XMP / lrpreset）

★ 与 C1 的分界：C1 走 `pipeline.run`（印相 + 扫描 + clip）；C2 只跑 `render_linear`，
  不碰 `print_exposure` / `person_pe` / `_post_scan`。C1 路径一个字节不改。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config as C
from . import io, lr_meta, presets

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


def srgb_encode(x: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(x, np.float64), 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92,
                    1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def srgb_decode(x: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(x, np.float64), 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92,
                    np.power((x + 0.055) / 1.055, 2.4))


def invert_negative(neg: np.ndarray, *,
                    lo_q: float = 1.0, hi_q: float = 99.5,
                    gains: tuple[float, float, float] = (1.0, 1.0, 1.0),
                    headroom: float = 0.0) -> np.ndarray:
    r"""负片线性 → 正像线性。

    物理上负片透射光 `L = 10^-D`，正像密度 `D' = Dmax - D` ⇒ `L' ∝ 1/L`；
    这里用倒数 + 逐通道分位归一（研究原型；正式版应加灰卡/色卡的中性平衡）。

    `headroom` > 0 时把结果压进 `[h, 1-h]`，避免平基底片在 0/1 处硬裁切，
    给 LR 留可拉的高光/暗部余量。
    """
    a = np.asarray(neg, np.float64)
    if a.ndim != 3 or a.shape[-1] != 3:
        raise ValueError("neg 必须是 HxWx3")
    h = float(headroom)
    if not (0.0 <= h < 0.5):
        raise ValueError("headroom 必须在 [0, 0.5)")
    pos = 1.0 / np.maximum(a, 1e-6)
    out = np.empty_like(pos)
    for c in range(3):
        lo, hi = np.percentile(pos[..., c], [float(lo_q), float(hi_q)])
        v = (pos[..., c] - lo) / max(float(hi - lo), 1e-6)
        out[..., c] = h + np.clip(v, 0.0, 1.0) * (1.0 - 2.0 * h)
    out = np.clip(out, 0.0, 1.0)
    g = np.asarray(gains, np.float64).reshape(1, 1, 3)
    out = np.clip(out * g, 0.0, 1.0)
    return out


def save_tiff16(disp: np.ndarray, path: str | Path) -> str:
    r"""sRGB 编码 0..1 → 16-bit RGB TIFF（带 sRGB ICC）。"""
    if tifffile is None:
        raise RuntimeError("缺 tifffile：pip install tifffile（或改用 cv2 的 16-bit TIFF）")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    arr16 = np.round(arr * 65535.0).astype(np.uint16)
    icc = b""
    try:
        from PIL import ImageCms
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:  # pragma: no cover - 没有 ICC 也要能写
        icc = b""
    extratags = [(34675, "B", len(icc), icc, False)] if icc else None
    tmp = path.with_name(path.name + ".tmp.tif")
    tifffile.imwrite(tmp, arr16, photometric="rgb", compression="zlib",
                     extratags=extratags)
    os.replace(tmp, path)
    return str(path)


def read_srgb(path: str | Path) -> np.ndarray:
    r"""读 8-bit JPEG/PNG 或 16-bit TIFF，返回 float sRGB 0..1。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        if tifffile is None:
            raise RuntimeError("读 16-bit TIFF 需要 tifffile")
        a = tifffile.imread(str(path))
        if a.dtype == np.uint16:
            a = a.astype(np.float64) / 65535.0
        elif a.dtype == np.uint8:
            a = a.astype(np.float64) / 255.0
        else:
            a = np.asarray(a, np.float64)
        if a.ndim == 3 and a.shape[-1] > 3:
            a = a[..., :3]
        if a.ndim != 3 or a.shape[-1] != 3:
            raise ValueError("TIFF 不是 RGB: %r" % (a.shape,))
        return np.clip(a, 0.0, 1.0)
    from PIL import Image
    return np.asarray(Image.open(str(path)).convert("RGB"), np.float64) / 255.0


def _lab(rgb: np.ndarray) -> np.ndarray:
    lin = srgb_decode(rgb)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    t = xyz / white
    f = np.where(t > (6 / 29) ** 3, np.cbrt(t),
                 t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def _fit_channel(x: np.ndarray, y: np.ndarray, bins: int):
    qs = np.linspace(0.0, 1.0, bins + 1)
    edges = np.quantile(x, qs)
    xs, ys = [], []
    for i in range(len(edges) - 1):
        mask = (x >= edges[i]) & (x <= edges[i + 1])
        if int(mask.sum()) < 64:
            continue
        xs.append(float(np.median(x[mask])))
        ys.append(float(np.median(y[mask])))
    xs = np.asarray(xs)
    ys = np.maximum.accumulate(np.asarray(ys))
    keep = np.concatenate(([True], np.diff(xs) > 1e-9))
    return xs[keep], ys[keep]


def _sample_curve(xs: np.ndarray, ys: np.ndarray, points: int):
    if PchipInterpolator is None:
        raise RuntimeError("拟合基底曲线需要 scipy")
    grid = np.linspace(0.0, 255.0, points)
    f = PchipInterpolator(xs, ys, extrapolate=True)
    vals = np.clip(f(grid / 255.0) * 255.0, 0.0, 255.0)
    vals[0], vals[-1] = 0.0, 255.0
    vals = np.maximum.accumulate(vals)
    return [[int(round(x)), int(round(y))] for x, y in zip(grid, vals)]


def fit_base_curve(print_path: str | Path, flat_path: str | Path,
                   *, points: int = 16, bins: int = 32) -> dict:
    r"""拟合「平基底片（16-bit TIFF）→ 当前冲印成片」的逐通道曲线。

    输出 JSON 可直接给 `lrbase --base-curve` / `lr_meta`。
    ⚠ 正式标定要用**同内容、同像素**的一对图；C1 print 建议存 PNG/无损，不要 JPEG。
    """
    pr = read_srgb(print_path)
    fl = read_srgb(flat_path)
    if pr.shape != fl.shape:
        raise ValueError("尺寸不一致: print=%r flat=%r" % (pr.shape, fl.shape))
    curves = {}
    names = ("ToneCurvePV2012", "ToneCurvePV2012Red",
             "ToneCurvePV2012Green", "ToneCurvePV2012Blue")

    # 复合曲线：在线性亮度上拟合，再转回 sRGB 编码坐标
    y_pr = srgb_decode(pr) @ np.array([0.2126, 0.7152, 0.0722])
    y_fl = srgb_decode(fl) @ np.array([0.2126, 0.7152, 0.0722])
    xs, ys = _fit_channel(srgb_encode(y_fl), srgb_encode(y_pr), bins)
    curves[names[0]] = _sample_curve(xs, ys, points)
    for i, name in enumerate(names[1:]):
        xs, ys = _fit_channel(fl[..., i], pr[..., i], bins)
        curves[name] = _sample_curve(xs, ys, points)

    rec = np.empty_like(fl)
    for i, name in enumerate(names[1:]):
        pts = np.asarray(curves[name], np.float64) / 255.0
        rec[..., i] = np.interp(fl[..., i], pts[:, 0], pts[:, 1])
    d_l = _lab(rec)[..., 0] - _lab(pr)[..., 0]
    d_e = None
    if colour is not None:
        d_e = colour.delta_E(_lab(pr), _lab(rec), method="CIE 2000")
    return {
        "schema": "svfilm.lr.base_curve/1",
        "source": {"print": str(print_path), "flat": str(flat_path)},
        "space": "sRGB-encoded 0..255",
        "points_per_curve": int(points),
        "curves": curves,
        "fit": {
            "median_abs_dL": round(float(np.median(np.abs(d_l))), 3),
            "p95_abs_dL": round(float(np.percentile(np.abs(d_l), 95)), 3),
            "median_dE00": None if d_e is None else round(float(np.median(d_e)), 3),
            "p95_dE00": None if d_e is None else round(float(np.percentile(d_e, 95)), 3),
            "note": "同内容单对图拟合；正式标定用 16-bit 多图 + 色卡",
        },
    }


def save_curve_json(curve: dict, path: str | Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    import json
    tmp.write_text(json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def apply_curve(flat_srgb: np.ndarray, curve: dict) -> np.ndarray:
    r"""把基底曲线回放到平基底片上（只做 tone，不做 HSL/Color Grading）。

    用于 `lrbase-preview`：不开 LR 也能看“平基底 + 基底曲线”长什么样。
    """
    a = np.asarray(flat_srgb, np.float64)
    curves = curve.get("curves", {})
    out = np.empty_like(a)
    keys = ("ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")
    for i, key in enumerate(keys):
        pts = curves.get(key) or curves.get("ToneCurvePV2012")
        if not pts:
            out[..., i] = a[..., i]
            continue
        p = np.asarray(pts, np.float64) / 255.0
        out[..., i] = np.interp(a[..., i], p[:, 0], p[:, 1])
    return np.clip(out, 0.0, 1.0)


def save_jpeg(disp: np.ndarray, path: str | Path, quality: int = 95) -> str:
    from PIL import Image
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.clip(np.asarray(disp, np.float64), 0.0, 1.0)
    tmp = path.with_name(path.name + ".tmp.jpg")
    Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8)).save(
        tmp, format="JPEG", quality=int(quality), subsampling=0)
    os.replace(tmp, path)
    return str(path)


def render_flat(lin: np.ndarray, stock: str, cfg=C, *,
                lo_q: float = 1.0, hi_q: float = 99.5,
                gains: tuple[float, float, float] = (1.0, 1.0, 1.0),
                headroom: float = 0.0,
                overrides: dict | None = None) -> FlatResult:
    neg = presets.render_linear(lin, stock, cfg=cfg, overrides=overrides)
    pos = invert_negative(neg, lo_q=lo_q, hi_q=hi_q, gains=gains,
                          headroom=headroom)
    disp = srgb_encode(pos)
    return FlatResult(disp=disp, lin=pos, neg=neg, report={})


def _safe_stem(path: str | Path) -> str:
    return Path(path).stem


def run_one(path: str | Path, out_dir: str | Path | None = None, *,
            stock: str | None = None, max_side: int | None = None,
            src: str | None = None,
            lo_q: float = 1.0, hi_q: float = 99.5,
            gains: tuple[float, float, float] = (1.0, 1.0, 1.0),
            headroom: float = 0.01,
            base_curve: dict | None = None,
            master: dict | None = None,
            preset_name: str = "svFilm",
            write_preset: bool = True,
            overwrite: bool = True) -> dict:
    r"""单张：RAW/JPG → 平基底片 TIFF + JSON/XMP/lrpreset。"""
    t0 = time.perf_counter()
    path = Path(path)
    name = str(stock if stock is not None else C.STOCK)
    if not presets.has(name):
        raise KeyError("没有这个胶片风格: %s（可选：%s）" % (name, "、".join(presets.names())))
    wb = presets.wb_of(name) if presets.has(name) else None
    s = io.load(str(path), max_side if max_side is not None else C.MAX_SIDE,
                src=src, wb=wb)
    flat = render_flat(s.lin, name, C, lo_q=lo_q, hi_q=hi_q, gains=gains,
                       headroom=headroom)
    out_dir = Path(out_dir) if out_dir is not None else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(path) + "_svFilm"
    tif_path = out_dir / (stem + "_flat.tif")
    if tif_path.exists() and not overwrite:
        raise FileExistsError(tif_path)

    report = {
        "mode": "flat",
        "stock": name,
        "stock_label": presets.label_of(name)[0],
        "stock_desc": presets.label_of(name)[1],
        "camera": s.cam,
        "entry_bias_ev": (s.cam or {}).get("idt_bias_ev"),
        "fuji_dr": (s.cam or {}).get("fuji_dr"),
        "scene": None,
        "person_pe": None,
        "light_back": 0.0,
        "flat": {
            "lo_q": float(lo_q),
            "hi_q": float(hi_q),
            "gains": [float(v) for v in gains],
            "headroom": float(headroom),
            "bit_depth": 16,
            "color_space": "sRGB",
            "note": "scan_film 线性 → 倒数/分位归一 → sRGB 编码；未做印相",
        },
        "ms": (time.perf_counter() - t0) * 1000.0,
    }
    save_tiff16(flat.disp, tif_path)

    meta = lr_meta.build_metadata(report, tif_path, mode="flat",
                                  base_curve=base_curve, master=master,
                                  preset_name=preset_name)
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
