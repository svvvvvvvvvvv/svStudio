import { useEffect, useRef, useState } from 'react';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { API, Photo } from '../api';
import { ratingKey, useStore, visiblePhotos } from '../store/useStore';
import { HoverPreview } from './HoverPreview';

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
    // ⚠ 返回的是 {url,ow,oh} 对象，不是字符串（09-15 裂图就是把对象塞给了 src）
    API.getThumb(sessionPath, p.rel, THUMB_W).then((t) => {
      if (alive && t?.url) setSrc(t.url);
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
          position: 'relative',   // ★ RAW 角标要挂在这上面
          height: 72,
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
        {/* ★ SV 09-15：缩略图要能一眼看出「这张有没有 RAW」
            （选片台打星时要知道哪些片后面能拿 RAW 重调） */}
        {p.hasRaw && (
          <span
            data-raw="1"
            title="有 RAW（RAF 等原始文件）"
            style={{
              position: 'absolute',
              right: 3,
              top: 3,
              fontSize: 9,
              lineHeight: '13px',
              padding: '0 4px',
              borderRadius: 3,
              background: 'rgba(0,0,0,.62)',
              color: '#fff',
              letterSpacing: 0.5,
              pointerEvents: 'none',
            }}
          >
            RAW
          </span>
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
  const hostRef = useRef<HTMLDivElement>(null);

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
      ref={hostRef}
      style={{
        position: 'relative',
        flex: '0 0 auto',
        /* 高度布局：6(上) + 格子(2边框+3pad+72图+2+13名+3) ≈ 95 + 滚动条独立区 14
           ⚠ 滚动条必须有自己的空间，不能贴着格子（09-15 SV：滚动栏压住缩略图/选中框） */
        height: 122,
        borderTop: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        padding: '6px 0 0',
        overflow: 'hidden',
      }}
    >
      {/* ★ 悬浮大预览（高频值走 useRef，零重渲染） */}
      <HoverPreview containerRef={hostRef} />
      {/* ★★ 底栏空的时候**必须说出为什么**（09-15 SV 报「底部栏没了」）。
          实测现场：调色台 + 一个一张星都没有的主题 ⇒ `visiblePhotos(..., forGrade=true)`
          返回空 ⇒ 整条底栏一片空白、一格缩略图都没有，**和"坏了"长得一模一样**
          （他不止看不到图，连"为什么没有"都无从判断）。
          ⇒ 空着不出声是本项目最忌的一类（技能 §0：「空」和「坏了」要能分清）。
          ⚠ 两种空因要分开说：调色台是"只列已打星"，选片台是"当前筛选下没有"。 */}
      {!list.length && (
        <div
          data-dock-empty={mode === 'grade' ? 'grade-no-star' : 'filtered'}
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 12,
            color: 'var(--text-faint)',
            pointerEvents: 'none',   // 不许挡住下面那条 Virtuoso 的滚动/点击
            textAlign: 'center',
            padding: '0 12px',
          }}
        >
          {mode === 'grade'
            ? '这个主题还没有打星的片 —— 调色台底栏只列 ★≥1，先去「选片台」打星'
            : '当前筛选下没有照片（换一个筛选看看）'}
        </div>
      )}
      <Virtuoso
        ref={virtuoso}
        horizontalDirection
        style={{ height: 'calc(100% - 14px)' }}
        totalCount={list.length}
        itemContent={(i) => {
          const gi = list[i];
          return (
            <div
              onClick={() => setCur(gi)}
              data-idx={gi}
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
