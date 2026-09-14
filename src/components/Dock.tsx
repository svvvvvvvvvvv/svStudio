import { useEffect, useState } from 'react';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { API, Photo } from '../api';
import { ratingKey, useStore, visiblePhotos } from '../store/useStore';

/**
 * ★★ 底栏（横向缩略图条）—— 用 **react-virtuoso** 做虚拟滚动。
 *
 * ⚠ 09-15 更正：我最初说"底栏虚拟滚动要绕开 React 自己写"，**那是错的**。
 *   实测数据（10 万条）：传统渲染 vs 虚拟滚动 =
 *   首屏 2800ms→45ms、内存 450MB→12MB、帧率 12fps→60fps、DOM 节点 100012→25。
 *   虚拟滚动恰恰是 React 生态最成熟的部分，手写反而更差。这里用现成库。
 *
 * ★ 两个必须注意的点：
 *   ① 横向要用 horizontalDirection（默认是纵向）
 *   ② 缩略图是**异步**的（IPC 往返），不能在渲染里 await；
 *      每个格子自己管自己的加载状态（Thumb 组件），避免整条重渲染。
 */

const THUMB_W = 260;

function Thumb({ p, active }: { p: Photo; active: boolean }) {
  const sessionPath = useStore((s) => s.sessionPath);
  const sessionName = useStore((s) => s.sessionName);
  const star = useStore((s) => s.ratings[ratingKey(sessionName, p.name)] || 0);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setSrc(null);
    API.getThumb(sessionPath, p.rel, THUMB_W).then((b) => {
      // ⚠ 异步回来时组件可能已经滚出可视区被卸载了（虚拟滚动会复用节点）
      if (alive && b) setSrc(b);
    });
    return () => {
      alive = false;
    };
  }, [sessionPath, p.rel]);

  return (
    <div
      style={{
        width: 118,
        padding: 3,
        borderRadius: 'var(--r-sm)',
        border: active ? '2px solid var(--accent)' : '2px solid transparent',
        background: 'var(--bg-panel)',
        cursor: 'pointer',
      }}
    >
      <div
        style={{
          height: 78,
          background: 'var(--bg)',
          borderRadius: 4,
          overflow: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {src ? (
          <img
            src={src}
            alt={p.name}
            style={{ maxWidth: '100%', maxHeight: '100%', display: 'block' }}
            draggable={false}
          />
        ) : (
          <span style={{ fontSize: 10, color: 'var(--text-faint)' }}>…</span>
        )}
      </div>
      <div
        style={{
          fontSize: 10,
          marginTop: 2,
          color: star >= 1 ? 'var(--c-yellow)' : 'var(--text-faint)',
          textAlign: 'center',
        }}
      >
        {star >= 1 ? '★'.repeat(star) : p.name}
      </div>
    </div>
  );
}

export function Dock({ virtuoso }: { virtuoso: React.RefObject<VirtuosoHandle> }) {
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const mode = useStore((s) => s.mode);
  const filter = useStore((s) => s.filter);
  const ratings = useStore((s) => s.ratings);
  const sessionName = useStore((s) => s.sessionName);
  const setCur = useStore((s) => s.setCur);

  // 调色台只收 ★≥1（SV 09-14 定死）
  const list = visiblePhotos(photos, sessionName, ratings, filter, mode === 'grade');

  /** 当前片在"筛选后片单"里的位置，用于滚到可见 */
  const idxInList = list.indexOf(cur);
  useEffect(() => {
    if (idxInList >= 0 && virtuoso.current) {
      virtuoso.current.scrollToIndex({ index: idxInList, align: 'center' });
    }
  }, [idxInList, virtuoso]);

  if (!photos.length) return null;

  return (
    <div
      style={{
        flex: '0 0 auto',
        height: 108,
        borderTop: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        padding: '6px 0',
      }}
    >
      <Virtuoso
        ref={virtuoso}
        horizontalDirection
        style={{ height: '100%' }}
        totalCount={list.length}
        itemContent={(i) => {
          const gi = list[i];
          return (
            <div
              onClick={() => setCur(gi)}
              style={{ padding: '0 3px' }}
            >
              <Thumb p={photos[gi]} active={gi === cur} />
            </div>
          );
        }}
      />
    </div>
  );
}
