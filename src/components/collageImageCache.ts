/** 拼图台的图片加载缓存：同一张图按 (key, maxSide) 只解码一次。 */
import { API } from '../api';
import type { CollageItem } from '../store/useStore';
import type { PaintImage } from './collagePaint';

export interface LoadedImage extends PaintImage {
  key: string;
}

interface Entry extends LoadedImage { maxSide: number }

const CACHE = new Map<string, Entry>();
const PENDING = new Map<string, Promise<LoadedImage>>();
const ERRORS = new Map<string, string>();

export const cacheKey = (item: CollageItem, maxSide: number) =>
  (item.key || '') + '|' + maxSide;

export function peek(item: CollageItem, maxSide: number): LoadedImage | null {
  const e = CACHE.get(cacheKey(item, maxSide));
  return e ? { img: e.img, ow: e.ow, oh: e.oh, key: e.key } : null;
}

export function errorOf(item: CollageItem, maxSide: number): string | null {
  return ERRORS.get(cacheKey(item, maxSide)) || null;
}

/** 加载一张图；返回的是**显示方向正确**的 PNG dataURL → HTMLImageElement。 */
export function load(item: CollageItem, maxSide: number): Promise<LoadedImage> {
  const ck = cacheKey(item, maxSide);
  const hit = CACHE.get(ck);
  if (hit) return Promise.resolve({ img: hit.img, ow: hit.ow, oh: hit.oh, key: hit.key });
  const running = PENDING.get(ck);
  if (running) return running;

  const job = (async (): Promise<LoadedImage> => {
    const r = await API.collageReadImage({
      path: item.path,
      sessionPath: item.sessionPath,
      rel: item.rel,
      maxSide
    });
    if (!r.ok || !r.url) throw new Error(r.error || '读图失败');
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const im = new Image();
      im.onload = () => resolve(im);
      im.onerror = () => reject(new Error('浏览器解不出这张图'));
      im.src = r.url as string;
    });
    const out: LoadedImage = {
      img, ow: r.ow || img.width, oh: r.oh || img.height, key: item.key
    };
    CACHE.set(ck, { ...out, maxSide });
    ERRORS.delete(ck);
    return out;
  })()
    .catch((e) => {
      ERRORS.set(ck, String(e && e.message || e));
      throw e;
    })
    .finally(() => PENDING.delete(ck));

  PENDING.set(ck, job);
  return job;
}

/** 清空某个来源的缓存（换目录时没必要，但换图后重名文件变了可以调） */
export function clearAll() {
  CACHE.clear();
  ERRORS.clear();
}
