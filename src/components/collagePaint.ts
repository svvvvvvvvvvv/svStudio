/**
 * 拼图台的**唯一绘制实现**。
 *
 * 预览和导出都调这一个函数，只是喂进去的图尺寸不同（预览长边 1600、导出原图），
 * gap/pad/圆角按 `unitW / fullUnitW` 自动同步缩放 ⇒ 所见即所得，
 * 不存在"预览一套、导出另一套"的问题。
 *
 * 默认（side=0、fit=contain、图与格子同尺寸）**1:1 原始像素绘制，不重采样**，
 * 配合 PNG 输出就是逐像素无损。只有用户主动选了「导出长边」或「裁切填满」才会重采样/裁切。
 *
 * ★ 参数收 canvas 而不是 ctx：函数自己按布局把 width/height 设好再画，
 *   调用方不需要预先知道成品尺寸（预览一块可见 canvas、导出一块离屏 canvas，完全同构）。
 */
import type { CollageLayout, CollageOptions } from '../store/useStore';
import { getLayout } from '../store/collageLayouts';

export interface PaintImage {
  img: HTMLImageElement;
  /** 缩放前的原始像素（用来算预览缩放比） */
  ow: number;
  oh: number;
}

export interface PaintInfo {
  w: number; h: number;
  /** 基准长边（未乘 side） */
  baseW: number; baseH: number;
}

const near = (a: number, b: number) => Math.abs(a - b) < 1e-4;

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  const rr = Math.max(0, Math.min(r, Math.min(w, h) / 2));
  if (rr <= 0 || typeof (ctx as any).roundRect !== 'function') ctx.rect(x, y, w, h);
  else (ctx as any).roundRect(x, y, w, h, rr);
  ctx.closePath();
}

/** 把一张图按 contain / cover 画进指定矩形 */
function drawInto(
  ctx: CanvasRenderingContext2D,
  im: PaintImage,
  x: number, y: number, w: number, h: number,
  fit: 'contain' | 'cover'
) {
  const iw = im.img.naturalWidth || im.img.width;
  const ih = im.img.naturalHeight || im.img.height;
  if (!iw || !ih) return;
  const k = fit === 'cover' ? Math.max(w / iw, h / ih) : Math.min(w / iw, h / ih);
  const dw = iw * k, dh = ih * k;
  ctx.drawImage(im.img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh);
}

/** 只算尺寸，不画（右栏显示「成品尺寸」用） */
export function measureCollage(
  imgs: (PaintImage | null)[],
  layout: CollageLayout,
  opts: CollageOptions,
  side = 0
): PaintInfo {
  const cells = getLayout(layout).cells;
  const xs = Array.from(new Set(cells.flatMap((c) => [c[0], c[0] + c[2]]))).sort((a, b) => a - b);
  const ys = Array.from(new Set(cells.flatMap((c) => [c[1], c[1] + c[3]]))).sort((a, b) => a - b);
  const cols = Math.max(1, xs.length - 1);
  const rows = Math.max(1, ys.length - 1);
  const have = imgs.filter((x): x is PaintImage => !!x);
  const fullW = have.length ? Math.max(...have.map((x) => x.ow || x.img.width)) : 1200;
  const unitW = have.length ? Math.max(...have.map((x) => x.img.width)) : 1200;
  const unitH = have.length ? Math.max(...have.map((x) => x.img.height)) : 800;
  const scale = unitW / Math.max(1, fullW);
  const gap = Math.max(0, opts.gap) * scale;
  const pad = Math.max(0, opts.pad) * scale;
  const W = cols * unitW + (cols - 1) * gap + 2 * pad;
  const H = rows * unitH + (rows - 1) * gap + 2 * pad;
  const k = side > 0 ? side / Math.max(W, H) : 1;
  return {
    w: Math.max(1, Math.round(W * k)),
    h: Math.max(1, Math.round(H * k)),
    baseW: W, baseH: H
  };
}

/**
 * @param canvas 目标 canvas（函数自己设 width/height）
 * @param imgs   与布局格子一一对应，空格为 null
 * @param side   0 = 原尺寸；>0 = 把成品长边压到该值（仅导出时用，会重采样）
 */
export function paintCollage(
  canvas: HTMLCanvasElement,
  imgs: (PaintImage | null)[],
  layout: CollageLayout,
  opts: CollageOptions,
  side = 0,
  preview = false
): PaintInfo {
  const def = getLayout(layout);
  const cells = def.cells;
  const n = cells.length;

  /* ---- 1. 从归一化矩形反推行列网格 ---- */
  const xs = Array.from(new Set(cells.flatMap((c) => [c[0], c[0] + c[2]]))).sort((a, b) => a - b);
  const ys = Array.from(new Set(cells.flatMap((c) => [c[1], c[1] + c[3]]))).sort((a, b) => a - b);
  const cols = Math.max(1, xs.length - 1);
  const rows = Math.max(1, ys.length - 1);
  const idx = (arr: number[], v: number) => {
    const i = arr.findIndex((x) => near(x, v));
    return i < 0 ? 0 : i;
  };

  /* ---- 2. 单位格尺寸：以「原图像素」为基准，预览自动按图的实际像素缩小 ---- */
  const have = imgs.filter((x): x is PaintImage => !!x);
  const fullW = have.length ? Math.max(...have.map((x) => x.ow || x.img.width)) : 1200;
  const unitW = have.length ? Math.max(...have.map((x) => x.img.width)) : 1200;
  const unitH = have.length ? Math.max(...have.map((x) => x.img.height)) : 800;
  const scale = unitW / Math.max(1, fullW);
  const gap = Math.max(0, opts.gap) * scale;
  const pad = Math.max(0, opts.pad) * scale;
  const radius = Math.max(0, opts.radius) * scale;

  const W = cols * unitW + (cols - 1) * gap + 2 * pad;
  const H = rows * unitH + (rows - 1) * gap + 2 * pad;
  const k = side > 0 ? side / Math.max(W, H) : 1;
  const outW = Math.max(1, Math.round(W * k));
  const outH = Math.max(1, Math.round(H * k));

  canvas.width = outW;
  canvas.height = outH;
  const ctx = canvas.getContext('2d');
  if (!ctx) return { w: outW, h: outH, baseW: W, baseH: H };

  ctx.save();
  ctx.setTransform(k, 0, 0, k, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';

  /* ---- 3. 背景 ---- */
  ctx.fillStyle = opts.bg || '#ffffff';
  ctx.fillRect(0, 0, W, H);

  const boxOf = (c: [number, number, number, number]) => {
    const col = idx(xs, c[0]);
    const row = idx(ys, c[1]);
    const colSpan = Math.max(1, idx(xs, c[0] + c[2]) - col);
    const rowSpan = Math.max(1, idx(ys, c[1] + c[3]) - row);
    return {
      x: pad + col * (unitW + gap),
      y: pad + row * (unitH + gap),
      w: colSpan * unitW + (colSpan - 1) * gap,
      h: rowSpan * unitH + (rowSpan - 1) * gap
    };
  };

  /* ---- 4. 滑动对比：两张叠满，中间可拖分割线 ---- */
  if (layout === 'compare') {
    const box = boxOf(cells[0]);
    ctx.save();
    roundRect(ctx, box.x, box.y, box.w, box.h, radius);
    ctx.clip();
    ctx.fillStyle = opts.bg || '#ffffff';
    ctx.fillRect(box.x, box.y, box.w, box.h);
    if (imgs[0]) drawInto(ctx, imgs[0], box.x, box.y, box.w, box.h, opts.fit);
    const cut = Math.max(0, Math.min(1, opts.split));
    if (imgs[1]) {
      ctx.save();
      ctx.beginPath();
      ctx.rect(box.x + box.w * cut, box.y, box.w * (1 - cut), box.h);
      ctx.clip();
      drawInto(ctx, imgs[1], box.x, box.y, box.w, box.h, opts.fit);
      ctx.restore();
      const lx = box.x + box.w * cut;
      ctx.strokeStyle = 'rgba(255,255,255,.95)';
      ctx.lineWidth = Math.max(2, 3 * scale);
      ctx.beginPath(); ctx.moveTo(lx, box.y); ctx.lineTo(lx, box.y + box.h); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.95)';
      ctx.beginPath(); ctx.arc(lx, box.y + box.h / 2, Math.max(9, 13 * scale), 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#1c1c1e';
      ctx.beginPath(); ctx.arc(lx, box.y + box.h / 2, Math.max(3, 4 * scale), 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
    ctx.restore();
    return { w: outW, h: outH, baseW: W, baseH: H };
  }

  /* ---- 5. 常规网格：逐格画 ---- */
  for (let i = 0; i < n; i++) {
    const box = boxOf(cells[i]);
    const im = imgs[i];
    ctx.save();
    roundRect(ctx, box.x, box.y, box.w, box.h, radius);
    ctx.clip();
    if (im) {
      ctx.fillStyle = opts.bg || '#ffffff';
      ctx.fillRect(box.x, box.y, box.w, box.h);
      drawInto(ctx, im, box.x, box.y, box.w, box.h, opts.fit);
    } else if (preview) {
      /* ★ 预览里空格必须**说出为什么**：不是坏了，是这一格还没放图。
         ⚠ 导出时（preview=false）**不画**这些提示，否则没放满的组
         会把「第 N 格 · 点这里选片」一起印进成品。 */
      ctx.fillStyle = 'rgba(120,120,128,.10)';
      ctx.fillRect(box.x, box.y, box.w, box.h);
      ctx.strokeStyle = 'rgba(120,120,128,.35)';
      ctx.lineWidth = Math.max(1, scale * 1.5);
      ctx.setLineDash([8 * scale, 6 * scale]);
      ctx.strokeRect(box.x + 4 * scale, box.y + 4 * scale,
        box.w - 8 * scale, box.h - 8 * scale);
      ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(120,120,128,.75)';
      const fs = Math.max(13, 26 * scale);
      ctx.font = `600 ${fs}px -apple-system, "PingFang SC", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(`第 ${i + 1} 格 · 点这里选片`, box.x + box.w / 2, box.y + box.h / 2);
    } else {
      ctx.fillStyle = opts.bg || '#ffffff';
      ctx.fillRect(box.x, box.y, box.w, box.h);
    }
    ctx.restore();

    /* 标签（A/B/C…）—— 默认关，保持画面干净；空格不标 */
    if (opts.label && im) {
      const s = Math.max(12, 22 * scale);
      ctx.fillStyle = 'rgba(0,0,0,.62)';
      roundRect(ctx, box.x + 8 * scale, box.y + 8 * scale, s * 1.9, s * 1.5, 4 * scale);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = `600 ${s}px -apple-system, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String.fromCharCode(65 + i), box.x + 8 * scale + s * 0.95, box.y + 8 * scale + s * 0.78);
    }
  }
  ctx.restore();
  return { w: outW, h: outH, baseW: W, baseH: H };
}
