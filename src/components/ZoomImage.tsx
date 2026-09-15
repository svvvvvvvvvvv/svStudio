import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * ★★ 可缩放的大图（09-15 加，SV 选「B」）—— 调色台「验收皮肤」的尺子。
 *
 * ## 为什么要有它
 * 以前全仓库搜 `zoom / 缩放 / 1:1` **零命中** ⇒ 大图永远是"整张塞进窗口"。
 * 而**验收皮肤必须看 1:1**：颗粒粗细、磨皮够不够、对焦在不在眼睛上、鼻翼糊没糊，
 * 在"整张缩略"下根本看不出来（SV 的验收方式就是目测，目测的尺子就是放大倍率）。
 * 参照物：spektrafilm 的 GUI 有 `100% / 200% / 400% / 重置视图`，按钮上明确写着
 * 「1 个屏幕像素 = 1 个图像像素」。我们这里只做**两个档**（SV 定的）：
 * 「适应」（整张看构图）和「1:1」（看像素）。
 *
 * ## 口径（别改错，这是这个东西唯一容易骗人的地方）
 * 徽标上那个百分数 = **1 个屏幕像素对应几个图像像素**，口径和 spektrafilm 一致：
 *   `pct = 变换倍率 k × 基准倍率 base`，其中 `base = 实际画出来的宽 ÷ 图的原始宽`
 *   （`object-fit:contain` 会留黑边 ⇒ **不能用盒子的宽高**，得按长宽比算真正画出来的那张）。
 * ⇒ 点「1:1」之后徽标**一定**读作 `100%`，这是自检的探针（不是"差不多"）。
 *
 * ## 写法上的两个坑
 * ① `wheel` 必须用**原生监听 + `{passive:false}`**：React 的 `onWheel` 是 passive 的，
 *    在里面 `preventDefault()` 无效（页面会跟着滚）。
 * ② 缩放**锚在光标上**：`transform-origin` 固定 `center`，靠重算位移把光标下那一点钉住
 *    （`k2/k1` 那个式子是推导出来的：`c = p·k + d`，让 p 不动解 d）。
 */
const MAX_K = 8;
const MIN_K = 0.1;
const PAD = 8;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function ZoomImage({
  src,
  alt,
  pad = PAD,
}: {
  src: string;
  /** ⚠ 同时是 `img` 的 alt —— 布局自检靠 `alt === '调色后'` 认这一栏，别乱改 */
  alt: string;
  pad?: number;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [k, setK] = useState(1);
  const [off, setOff] = useState({ x: 0, y: 0 });
  const kRef = useRef(1);
  const offRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);

  // 盒子的尺寸（ResizeObserver）+ 图的原始尺寸 —— 用来算 base（见文件头「口径」）
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [nat, setNat] = useState({ w: 0, h: 0 });

  const apply = useCallback((nk: number, nd: { x: number; y: number }) => {
    kRef.current = nk;
    offRef.current = nd;
    setK(nk);
    setOff(nd);
  }, []);
  const reset = useCallback(() => apply(1, { x: 0, y: 0 }), [apply]);

  // 换图 ⇒ 回到「适应」（不然翻到下一张还停在上次的放大倍数上）
  useEffect(() => {
    reset();
  }, [src, reset]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const sync = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    sync();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [src]);

  /** `object-fit:contain` 下**真正画出来**的那张的宽 —— 不是盒子的宽（会留黑边） */
  const drawnW = (() => {
    const bw = box.w - pad * 2;
    const bh = box.h - pad * 2;
    if (bw <= 0 || bh <= 0 || !nat.w || !nat.h) return 0;
    const ar = nat.w / nat.h;
    return bw / bh > ar ? bh * ar : bw;
  })();
  /** 适应状态下 1 个图像像素占几个屏幕像素（<1 = 被缩过） */
  const base = drawnW > 0 && nat.w > 0 ? drawnW / nat.w : 1;
  const pct = Math.round(k * base * 100);

  const to1to1 = useCallback(() => {
    if (!(base > 0)) return;
    apply(clamp(1 / base, MIN_K, MAX_K), { x: 0, y: 0 });
  }, [apply, base]);

  // ---- 滚轮缩放（原生监听，见文件头坑①）----
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left - rect.width / 2;
      const cy = e.clientY - rect.top - rect.height / 2;
      const k1 = kRef.current;
      const d1 = offRef.current;
      const k2 = clamp(k1 * (e.deltaY < 0 ? 1.15 : 1 / 1.15), MIN_K, MAX_K);
      const r = k2 / k1;
      apply(k2, { x: cx - r * (cx - d1.x), y: cy - r * (cy - d1.y) });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [apply]);

  // ---- 拖动平移（只在放大之后）----
  // ⚠ 这里有两个坑，**都是自检逮出来的**，别再踩：
  //   ① **不要用 `setPointerCapture`**：把指针捕获到外层容器之后，浮在里面的
  //      「适应 / 1:1」按钮就再也收不到 click 了 —— 表现是"放大以后按钮全失灵"
  //      （布局自检 `[14]` 里那条"点「适应」应复位"就是这么红出来的）。
  //      改成：`pointerdown` 时判一下目标是不是按钮，是就放行。
  //   ② 平移的 `pointermove` 挂在 **`window`** 上，别挂在容器上 ——
  //      鼠标一快就滑出容器，挂在容器上会中途断掉。
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (kRef.current <= 1) return;
    if ((e.target as HTMLElement)?.tagName === 'BUTTON') return;  // 别把控件的点击吃掉
    dragRef.current = {
      x: e.clientX, y: e.clientY, ox: offRef.current.x, oy: offRef.current.y,
    };
    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      apply(kRef.current, { x: d.ox + (ev.clientX - d.x), y: d.oy + (ev.clientY - d.y) });
    };
    const up = () => {
      dragRef.current = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  };

  return (
    <div
      ref={wrapRef}
      data-zoom={k > 1.0001 ? 'zoomed' : 'fit'}
      data-zoom-pct={pct}
      onPointerDown={onPointerDown}
      onDoubleClick={() => (kRef.current > 1.0001 ? reset() : to1to1())}
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        cursor: k > 1.0001 ? 'grab' : 'default',
        touchAction: 'none',
      }}
    >
      <img
        ref={imgRef}
        src={src}
        alt={alt}
        draggable={false}
        onLoad={(e) =>
          setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
        }
        style={{
          position: 'absolute',
          top: pad,
          left: pad,
          width: `calc(100% - ${pad * 2}px)`,
          height: `calc(100% - ${pad * 2}px)`,
          objectFit: 'contain',
          display: 'block',
          transform: `translate(${off.x}px, ${off.y}px) scale(${k})`,
          transformOrigin: 'center center',
        }}
      />
      {/* 缩放控件：浮在右上角（不占标题栏，选片台那边没有标题栏也能用） */}
      <div
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '2px 4px',
          borderRadius: 999,
          background: 'rgba(0,0,0,.55)',
          color: '#fff',
          fontSize: 11,
          lineHeight: 1.6,
        }}
      >
        <ZBtn label="适应" active={k <= 1.0001} onClick={reset} />
        <ZBtn label="1:1" active={pct === 100} onClick={to1to1} />
        <span style={{ minWidth: 38, textAlign: 'right', paddingRight: 4 }}>{pct}%</span>
      </div>
    </div>
  );
}

function ZBtn({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-zoom-btn={label === '1:1' ? '1to1' : 'fit'}
      onClick={onClick}
      style={{
        border: 'none',
        borderRadius: 999,
        padding: '1px 8px',
        fontSize: 11,
        cursor: 'pointer',
        background: active ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.14)',
        color: active ? '#111' : '#fff',
      }}
    >
      {label}
    </button>
  );
}
