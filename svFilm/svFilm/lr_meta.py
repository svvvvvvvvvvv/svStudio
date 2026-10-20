# -*- coding: utf-8 -*-
r"""C2 元数据：把 svFilm 的 report + 平基底片，写成 Lightroom 能消费的文件。

产物（默认与 TIFF 同目录）：
  <stem>.svfilm.json      全量 recipe + 最终 LR develop settings + 溯源
  <stem>.xmp              crs: photo sidecar（RAW 可读；JPEG/TIFF 需插件/嵌入）
  <stem>.lrpreset.xmp     可导入 LR 的 develop preset
  _svfilm_manifest.json   整批清单（CLI 批量时写）

★ 设计要点（与 `E:\lr_agent\调研_svFilm_Lightroom分工.md` 一致）：
  1. LR 只有一条 tone curve ⇒ 基底曲线与大师曲线在**生成端** compose，不靠 preset 叠加；
  2. `lr.settings` 是**最终扁平表**；`components` 只作溯源，插件不要按顺序二次套；
  3. `baked` 告诉 LR 哪些层已经烘进像素（颗粒/光晕必须 GrainAmount=0）；
  4. 不写 catalog、不启动 LR、不修改原图。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

X_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CRS_NS = "http://ns.adobe.com/camera-raw-settings/1.0/"
SVF_NS = "https://svfilm.local/ns/1.0/"

ET.register_namespace("x", X_NS)
ET.register_namespace("rdf", RDF_NS)
ET.register_namespace("crs", CRS_NS)
ET.register_namespace("svf", SVF_NS)

# 只写确定存在的 Camera Raw 键；未知键会静默失败。
KNOWN_SETTINGS = {
    "Exposure2012", "Contrast2012", "Highlights2012", "Shadows2012",
    "Whites2012", "Blacks2012", "Texture", "Clarity2012", "Dehaze",
    "Vibrance", "Saturation",
    "ToneCurveName2012", "ToneCurvePV2012",
    "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue",
    "HueAdjustmentRed", "HueAdjustmentOrange", "HueAdjustmentYellow",
    "HueAdjustmentGreen", "HueAdjustmentAqua", "HueAdjustmentBlue",
    "HueAdjustmentPurple", "HueAdjustmentMagenta",
    "SaturationAdjustmentRed", "SaturationAdjustmentOrange",
    "SaturationAdjustmentYellow", "SaturationAdjustmentGreen",
    "SaturationAdjustmentAqua", "SaturationAdjustmentBlue",
    "SaturationAdjustmentPurple", "SaturationAdjustmentMagenta",
    "LuminanceAdjustmentRed", "LuminanceAdjustmentOrange",
    "LuminanceAdjustmentYellow", "LuminanceAdjustmentGreen",
    "LuminanceAdjustmentAqua", "LuminanceAdjustmentBlue",
    "LuminanceAdjustmentPurple", "LuminanceAdjustmentMagenta",
    "ColorGradeShadowHue", "ColorGradeShadowSat",
    "ColorGradeMidtoneHue", "ColorGradeMidtoneSat",
    "ColorGradeHighlightHue", "ColorGradeHighlightSat",
    "ColorGradeBlending", "ColorGradeBalance",
    "GrainAmount", "GrainSize", "GrainFrequency",
    "Sharpness", "SharpenRadius", "SharpenDetail", "SharpenEdgeMasking",
    "LuminanceSmoothing", "ColorNoiseReduction",
    "PostCropVignetteAmount",
    "SplitToningShadowHue", "SplitToningShadowSaturation",
    "SplitToningHighlightHue", "SplitToningHighlightSaturation",
    "SplitToningBalance",
}


COLOR_RESET_KEYS: dict[str, Any] = {}
for _color in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"):
    for _prefix in ("HueAdjustment", "SaturationAdjustment", "LuminanceAdjustment"):
        COLOR_RESET_KEYS[_prefix + _color] = 0
for _key in ("ColorGradeShadowHue", "ColorGradeShadowSat",
             "ColorGradeMidtoneHue", "ColorGradeMidtoneSat",
             "ColorGradeHighlightHue", "ColorGradeHighlightSat",
             "ColorGradeBlending", "ColorGradeBalance",
             "SplitToningShadowHue", "SplitToningShadowSaturation",
             "SplitToningHighlightHue", "SplitToningHighlightSaturation",
             "SplitToningBalance", "Vibrance", "Saturation"):
    COLOR_RESET_KEYS[_key] = 0


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _curve_points(raw: Any) -> list[list[int]]:
    if isinstance(raw, dict):
        raw = raw.get("curves", raw).get("ToneCurvePV2012") or raw.get("points")
    pts: list[list[int]] = []
    for item in raw or []:
        if isinstance(item, str):
            x, y = item.split(",", 1)
            pts.append([int(float(x)), int(float(y))])
        else:
            pts.append([int(item[0]), int(item[1])])
    if len(pts) < 2:
        raise ValueError("curve 至少需要两个点")
    if pts[0][0] != 0 or pts[-1][0] != 255:
        raise ValueError("curve 必须从 x=0 开始、到 x=255 结束")
    if any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
        raise ValueError("curve 的 x 必须严格递增")
    if any(not (0 <= v <= 255) for p in pts for v in p):
        raise ValueError("curve 点必须在 0..255")
    return pts


def compose(inner: list[list[int]] | None,
            outer: list[list[int]] | None) -> list[list[int]]:
    """outer(inner(x))，坐标 0..255，按 outer 的 x 网格重新采样。"""
    if not inner:
        return outer or [[0, 0], [255, 255]]
    if not outer:
        return inner

    def interp(pts: list[list[int]], v: float) -> float:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= v <= x1:
                if x1 == x0:
                    return float(y0)
                return y0 + (y1 - y0) * (v - x0) / (x1 - x0)
        return float(pts[-1][1] if v > pts[-1][0] else pts[0][1])

    out: list[list[int]] = []
    for x, _ in outer:
        t = interp(inner, float(x))
        z = interp(outer, t)
        out.append([x, int(round(max(0.0, min(255.0, z))))])
    out[0][1] = 0
    out[-1][1] = 255
    return out


def _alt(parent: ET.Element, tag: str, value: str) -> None:
    node = ET.SubElement(parent, f"{{{CRS_NS}}}{tag}")
    alt = ET.SubElement(node, f"{{{RDF_NS}}}Alt")
    li = ET.SubElement(alt, f"{{{RDF_NS}}}li")
    li.set("{http://www.w3.org/XML/1998/namespace}lang", "x-default")
    li.text = value


def _seq(parent: ET.Element, tag: str, points: list[list[int]]) -> None:
    node = ET.SubElement(parent, f"{{{CRS_NS}}}{tag}")
    seq = ET.SubElement(node, f"{{{RDF_NS}}}Seq")
    for x, y in points:
        li = ET.SubElement(seq, f"{{{RDF_NS}}}li")
        li.text = f"{x}, {y}"


def _xmp_root() -> tuple[ET.Element, ET.Element]:
    root = ET.Element(f"{{{X_NS}}}xmpmeta", {f"{{{X_NS}}}xmptk": "svFilm LR bridge"})
    rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
    desc = ET.SubElement(rdf, f"{{{RDF_NS}}}Description")
    desc.set(f"{{{RDF_NS}}}about", "")
    return root, desc


def build_metadata(report: dict, image: str | Path, *,
                   mode: str = "flat",
                   base_curve: dict | None = None,
                   master: dict | None = None,
                   preset_name: str = "svFilm",
                   grain_owner: str = "svfilm",
                   halation_owner: str = "svfilm") -> dict:
    """`flat`：只烘负片介质；`print_neutral`：再烘中性相纸；`full`：连大师层一起烘。

    v2 契约：`owners` 声明每一层归谁；`baked` 只描述**已经烘进像素**的东西。
    `master` 参数仍是“LR 侧的 master settings”，不是 svFilm 的大师层。
    """
    if mode not in ("flat", "print_neutral", "full"):
        raise ValueError("mode 只能是 flat / print_neutral / full")
    medium_baked = mode in ("print_neutral", "full")
    master_baked = mode == "full"

    curves: dict[str, list[list[int]]] = {
        "ToneCurvePV2012": [[0, 0], [255, 255]],
        "ToneCurvePV2012Red": [[0, 0], [255, 255]],
        "ToneCurvePV2012Green": [[0, 0], [255, 255]],
        "ToneCurvePV2012Blue": [[0, 0], [255, 255]],
    }
    comps: dict[str, Any] = {}
    if mode == "flat" and base_curve:
        for key, pts in base_curve.get("curves", {}).items():
            if key in KNOWN_SETTINGS:
                curves[key] = _curve_points(pts)
        comps["film_base"] = {
            "source": "fit_base_curve",
            "fit": base_curve.get("fit"),
        }

    if master and master.get("curves"):
        for key, pts in master["curves"].items():
            if key not in KNOWN_SETTINGS:
                continue
            outer = _curve_points(pts)
            inner = curves.get(key) or curves.get("ToneCurvePV2012")
            curves[key] = compose(inner, outer)
        comps["master_tone"] = {"source": master.get("source", "master")}

    settings: dict[str, Any] = dict(COLOR_RESET_KEYS)
    settings.update({
        "ToneCurveName2012": "Custom",
        "Exposure2012": 0.0,
        "Contrast2012": 0,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": 0,
        "Blacks2012": 0,
        # 颗粒/光晕/柔光已经烘进像素
        "GrainAmount": 0,
        "GrainSize": 0,
        "GrainFrequency": 0,
    })
    for key, pts in curves.items():
        settings[key] = pts

    if master:
        for key, value in (master.get("settings") or {}).items():
            if key in KNOWN_SETTINGS:
                settings[key] = value
        comps["master_color"] = {
            "source": master.get("source", "master"),
            "keys": sorted((master.get("settings") or {}).keys()),
        }

    flat_settings = {k: v for k, v in settings.items() if not isinstance(v, list)}
    image = Path(image)
    return {
        "schema": "svfilm.lr/2",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "source": {
            "stock": report.get("stock"),
            "stock_label": report.get("stock_label"),
            "stock_desc": report.get("stock_desc"),
            "camera": (report.get("camera") or {}).get("model"),
            "scene_key": ((report.get("scene") or {}).get("key")),
            "person_pe": report.get("person_pe"),
            "light_back": report.get("light_back"),
            "render_ms": report.get("ms"),
            "flat": report.get("flat"),
        },
        "image": {
            "path": str(image),
            "name": image.name,
            "bytes": image.stat().st_size,
            "sha256": sha256_file(image),
        },
        "baked": {
            "tone_curve": medium_baked,
            "color_mix": medium_baked,
            "grain": grain_owner == "svfilm",
            "halation": halation_owner == "svfilm",
            "softness": True,
            "medium_tone": medium_baked,
            "medium_color": medium_baked,
            "master_tone": master_baked,
            "master_color": master_baked,
            "note": ("full：介质+大师都烘死；print_neutral：只烘中性介质，大师归 LR；"
                     "flat：只烘负片介质，相纸/大师归 LR"),
        },
        "owners": {
            "medium_stock": "svfilm",
            "medium_paper": "svfilm" if medium_baked else "lr",
            "medium_scan": "svfilm",
            "grain": grain_owner,
            "halation": halation_owner,
            "master_tone": "svfilm" if master_baked else "lr",
            "master_color": "svfilm" if master_baked else "lr",
            "wb": "svfilm" if master_baked else "lr",
            "crop": "lr",
            "lens": "lr",
            "retouch": "lr",
            "output": "lr",
        },
        "film": report.get("film") or {
            "stock": report.get("stock"),
            "paper": (report.get("film") or {}).get("paper") if isinstance(report.get("film"), dict) else None,
            "scan": "spektrafilm/scan_film",
        },
        "master": {
            "baked": master_baked,
            "source": (master or {}).get("source"),
        },
        "lr": {
            "preset_name": preset_name,
            "settings": settings,
            "flat_settings": flat_settings,
            "components": comps,
            "apply_via": "plugin_or_mcp",
        },
        "provenance": {
            "engine": "spektrafilm",
            "disclaimer": "settings 为最终扁平表；components 仅作溯源，不要按顺序二次套用",
        },
    }


def write_json(meta: dict, path: str | Path) -> str:
    path = Path(path)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def write_sidecar_xmp(meta: dict, path: str | Path) -> str:
    path = Path(path)
    root, desc = _xmp_root()
    for key, value in meta["lr"]["flat_settings"].items():
        desc.set(f"{{{CRS_NS}}}{key}", str(value))
    for key, pts in meta["lr"]["settings"].items():
        if isinstance(pts, list):
            _seq(desc, key, pts)
    desc.set(f"{{{SVF_NS}}}schema", meta["schema"])
    desc.set(f"{{{SVF_NS}}}mode", meta["mode"])
    desc.set(f"{{{SVF_NS}}}preset", meta["lr"]["preset_name"])
    desc.set(f"{{{SVF_NS}}}bakedTone", str(meta["baked"]["tone_curve"]).lower())
    desc.set(f"{{{SVF_NS}}}bakedColor", str(meta["baked"]["color_mix"]).lower())
    desc.set(f"{{{SVF_NS}}}masterTone", str(meta["baked"].get("master_tone", False)).lower())
    desc.set(f"{{{SVF_NS}}}owners", json.dumps(meta.get("owners") or {}, ensure_ascii=False))
    ET.indent(root, space=" ")
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    return str(path)


def write_preset_xmp(meta: dict, path: str | Path, group: str = "svFilm") -> str:
    path = Path(path)
    root, desc = _xmp_root()
    attrs = {
        "PresetType": "Normal",
        "SupportsAmount": "False",
        "SupportsColor": "True",
        "SupportsMonochrome": "False",
        "SupportsHighDynamicRange": "False",
        "SupportsNormalDynamicRange": "True",
        "SupportsSceneReferred": "True",
        "SupportsOutputReferred": "True",
        "CameraModelRestriction": "",
        "Copyright": "",
        "ContactInfo": "",
        "Version": "15.0",
        "ConvertToGrayscale": "False",
        "Treatment": "Color",
    }
    for key, value in attrs.items():
        desc.set(f"{{{CRS_NS}}}{key}", value)
    for key, value in meta["lr"]["flat_settings"].items():
        desc.set(f"{{{CRS_NS}}}{key}", str(value))
    for key, pts in meta["lr"]["settings"].items():
        if isinstance(pts, list):
            _seq(desc, key, pts)
    desc.set(f"{{{CRS_NS}}}UUID", str(uuid.uuid4()).upper())
    desc.set(f"{{{CRS_NS}}}Cluster", group)
    _alt(desc, "Name", meta["lr"]["preset_name"])
    _alt(desc, "ShortName", meta["lr"]["preset_name"][:32])
    _alt(desc, "Group", group)
    ET.indent(root, space=" ")
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    return str(path)


def write_manifest(items: list[dict], path: str | Path,
                   *, mode: str = "flat") -> str:
    path = Path(path)
    data = {
        "schema": "svfilm.manifest/1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "items": items,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Lua 侧：给 LR 插件直接 dofile 的元数据（避免在 Lua 里塞 JSON 解析器）
# ---------------------------------------------------------------------------
def _lua_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(int(v))
    if v is None:
        return "nil"
    if isinstance(v, str):
        s = (v.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r"))
        return '"' + s + '"'
    raise TypeError("unsupported lua scalar: %r" % (v,))


def _lua_table(obj: Any, indent: int = 1) -> str:
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if isinstance(obj, dict):
        lines = ["{"]
        for k, v in obj.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
                raise ValueError("Lua 表键必须是合法标识符: %r" % (k,))
            lines.append("%s%s = %s," % (inner, k, _lua_table(v, indent + 1)))
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(obj, (list, tuple)):
        return "{" + ", ".join(_lua_scalar(v) for v in obj) + "}"
    return _lua_scalar(obj)


def write_lua(meta: dict, path: str | Path) -> str:
    r"""写 `<stem>.svfilm.lua`：LR 插件 `dofile` 后拿 `settings` 直接 apply。

    曲线在 Lua 侧转成 SDK 常用的**扁平列表** `[x,y,x,y,...]`；JSON 仍保留 `[[x,y],...]`。
    """
    path = Path(path)
    settings = dict(meta["lr"]["flat_settings"])
    for key, pts in meta["lr"]["settings"].items():
        if isinstance(pts, list):
            flat: list[int] = []
            for x, y in pts:
                flat.extend([int(x), int(y)])
            settings[key] = flat
    payload = {
        "schema": meta["schema"],
        "mode": meta["mode"],
        "preset_name": meta["lr"]["preset_name"],
        "settings": settings,
    }
    text = "return " + _lua_table(payload) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def write_lr_txt(meta: dict, path: str | Path) -> str:
    r"""写 `<stem>.svfilm.lr.txt`：给 LR 插件**逐行 key=value** 读，不依赖 JSON/Lua 解析。

    曲线 = 逗号分隔的扁平列表 `x,y,x,y,...`；插件自己转成 SDK 表。
    """
    path = Path(path)
    settings = dict(meta["lr"]["flat_settings"])
    for key, pts in meta["lr"]["settings"].items():
        if isinstance(pts, list):
            flat: list[int] = []
            for x, y in pts:
                flat.extend([int(x), int(y)])
            settings[key] = ",".join(str(v) for v in flat)
    lines = [
        "# svFilm LR metadata v1",
        "schema=" + str(meta["schema"]),
        "mode=" + str(meta["mode"]),
        "preset_name=" + str(meta["lr"]["preset_name"]).replace("\n", " "),
        "# settings",
    ]
    for key, value in settings.items():
        lines.append("%s=%s" % (key, value))
    text = "\n".join(lines) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


