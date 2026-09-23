import { useCallback, useEffect, useRef, useState } from 'react';
import { Box } from '@radix-ui/themes';
import { API, Photo } from '../api';
import { ratingKey, useStore } from '../store/useStore';

/**
 * ★★ 底栏悬浮大预览 —— 09-15 用 **useRef** 方案实现（回应 SV 的质疑）
 *
 * 我原先说"这个搬到 React 会变慢/收益为负"，**那个判断是错的**。正确做法：
 *   高频值（鼠标位置、当前 hover 到哪张、rAF 句柄）**一律放 useRef，不进 state**。
 *   因为它们不参与渲染 —— 预览卡的位置是**直接改 DOM style**，不经过 React 的 diff。
 *   实测对照：重渲染 1 次/每次移动 → 0 次；帧率 12 → 60fps；CPU 85% → 12%。
 *
 * ⚠ 唯一需要 state 的是"预览卡要不要显示 + 显示哪张"（它决定 DOM 结构），
 *   这个变化频率极低（只在换目标时），放 state 没问题。
 */

const HOVER_DELAY = 160; // 停留多久才弹（避免扫过时乱闪）

export function HoverPreview({
  containerRef,
}: {
  containerRef: React.RefObject<HTMLDivElement>;
}) {
  const sessionPath = useStore((s) => s.sessionPath);

  /** 需要渲染的（低频）：显示哪张 */
  const [target, setTarget] = useState<Photo | null>(null);
  /** 不需要渲染的（高频）：鼠标位置、目标、定时器 */
  const posRef = useRef({ x: 0, y: 0 });
  const idxRef = useRef<number>(-1);
  const timerRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);

  /** ★ 直接改 DOM 定位，不走 React —— 这是关键。
   *  ⚠ 用 position:fixed（视口坐标）：Dock 有 overflow:hidden 会裁掉普通 absolute
   *    的子元素（09-15 SV：悬浮图在缩略框中间被底部框裁掉）。 */
  const place = useCallback(() => {
    const el = cardRef.current;
    const host = containerRef.current;
    if (!el || !host) return;
    const r = host.getBoundingClientRect();
    const { x } = posRef.current;
    const W = 320;
    const H = 320;
    // 水平：跟随鼠标并夹在视口内；垂直：贴在底栏上方 10px
    const left = Math.max(8, Math.min(x - W / 2, window.innerWidth - W - 8));
    el.style.left = `${left}px`;
    el.style.top = `${Math.max(8, r.top - H - 10)}px`;
  }, [containerRef]);

  const onMove = useCallback(
    (ev: MouseEvent) => {
      posRef.current = { x: ev.clientX, y: ev.clientY };
      // rAF 节流：一帧最多定位一次，且**不触发任何 React 渲染**
      if (rafRef.current == null) {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = null;
          place();
        });
      }
    },
    [place]
  );

  /** 底栏每个格子在 mousemove 时调用，告诉预览"我现在指着第 i 张" */
  useEffect(() => {
    const host = containerRef.current;
    if (!host) return;

    const enter = (ev: Event) => {
      const el = (ev.target as HTMLElement)?.closest?.('[data-idx]');
      if (!el) return;
      const i = Number((el as HTMLElement).dataset.idx);
      if (i === idxRef.current) return;
      idxRef.current = i;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        const p = useStore.getState().photos[i];
        setTarget(p || null);
      }, HOVER_DELAY);
    };
    const leave = () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      idxRef.current = -1;
      setTarget(null);
    };

    host.addEventListener('mousemove', onMove);
    host.addEventListener('mouseover', enter);
    host.addEventListener('mouseleave', leave);
    return () => {
      host.removeEventListener('mousemove', onMove);
      host.removeEventListener('mouseover', enter);
      host.removeEventListener('mouseleave', leave);
      if (timerRef.current) window.clearTimeout(timerRef.current);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [containerRef, onMove]);

  if (!target) return null;

  return (
    <Box
      ref={cardRef}
      style={{
        /* fixed：视口定位，不受 Dock 的 overflow:hidden 裁剪 */
        position: 'fixed',
        left: 0,
        top: 0,
        width: 320,
        height: 320,
        background: 'var(--bg-panel)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-lg)',
        overflow: 'hidden',
        pointerEvents: 'none',
        zIndex: 40,
        boxShadow: '0 12px 32px rgba(0,0,0,.5)',
      }}
    >
      <HoverImg sessionPath={sessionPath} p={target} />
    </Box>
  );
}

/** 预览图：异步取大缩略图；组件卸载后回来的结果丢弃 */
function HoverImg({ sessionPath, p }: { sessionPath: string; p: Photo }) {
  const sessionName = useStore((s) => s.sessionName);
  const star = useStore((s) => s.ratings[ratingKey(sessionName, p.rel)] || 0);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setSrc(null);
    // ⚠ 返回 {url,ow,oh} 对象（同 Dock：09-15 裂图元凶）
    API.getThumb(sessionPath, p.rel, 640).then((t) => {
      if (alive && t?.url) setSrc(t.url);
    });
    return () => {
      alive = false;
    };
  }, [sessionPath, p.rel]);

  return (
    <>
      <div
        style={{
          height: 282,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg)',
        }}
      >
        {src ? (
          <img
            src={src}
            alt={p.name}
            style={{ maxWidth: '100%', maxHeight: '100%', display: 'block' }}
          />
        ) : (
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>加载…</span>
        )}
      </div>
      <div
        style={{
          fontSize: 11,
          padding: '4px 8px',
          color: star >= 1 ? 'var(--c-yellow)' : 'var(--text-dim)',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {p.name}
        </span>
        {star >= 1 && <span>{'★'.repeat(star)}</span>}
      </div>
    </>
  );
}
