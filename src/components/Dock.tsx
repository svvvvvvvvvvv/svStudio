import { useEffect, useRef, useState } from 'react';
import { Virtuoso, VirtuosoHandle } from 'react-virtuoso';
import { API, Photo } from '../api';
import { ratingKey, photoGroupIndex, useStore, visiblePhotos } from '../store/useStore';
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

function Thumb({ p, gi }: { p: Photo; gi: number }) {
  /* ★★ **选中框自己订阅 `cur`**，不靠父组件传 `active`。
     原因：底栏是 react-virtuoso，它只在 `totalCount` 变化时重渲 item
     ⇒ `cur` 变了 `itemContent` 不会重跑 ⇒ **左键点缩略图，大图换了但框不动**；
     右键会走别的路径触发重渲，所以看起来"右键正常、左键不正常"。 */
  const cur = useStore((s) => s.cur);
  const active = gi === cur;
  const sessionPath = useStore((s) => s.sessionPath);
  const sessionName = useStore((s) => s.sessionName);
  /* ★ 拼图组角标：这张片归哪组了，一眼看得出（任何台都显示）。
     ⚠ 角标自己订阅 groups —— 不能靠父组件传，Virtuoso 的 itemContent
     只在 totalCount 变化时重跑（和 cur 那个坑同源）。 */
  const groups = useStore((s) => s.collageGroups);
  const groupNo = photoGroupIndex(groups, (sessionPath || '') + '||' + p.rel);
  const star = useStore((s) => s.ratings[ratingKey(sessionName, p.rel)] || 0);
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
        /* 选中要**一眼看出来**：4px 边（未选中用 transparent 占位，布局不跳）+ 外发光。 */
        border: active ? '4px solid var(--accent)' : '4px solid transparent',
        boxShadow: active ? '0 0 10px 1px var(--accent)' : 'none',
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
        {/* ★ 已加入拼图组的标记 */}
        {groupNo >= 0 && (
          <span
            data-collage-group={groupNo + 1}
            title={`这张片已加入拼图组 ${groupNo + 1}`}
            style={{
              position: 'absolute', top: 2, right: 2,
              background: 'var(--accent)', color: '#fff',
              fontSize: 9, lineHeight: 1.4, padding: '0 4px',
              borderRadius: 3, pointerEvents: 'none', whiteSpace: 'nowrap'
            }}>
            组{groupNo + 1}
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
  const sessionPath = useStore((s) => s.sessionPath);
  const setCur = useStore((s) => s.setCur);
  const fillFromDock = useStore((s) => s.collageFillFromDock);
  const hostRef = useRef<HTMLDivElement>(null);

  /* ★ 拼图台：底栏就是「选片来源」—— 点一张填进当前选中的格子。
     ⚠ 这里**不按星级/筛选过滤**（拼图台不该被选片台的星级挡住），
        也不走 setCur（那会把大图换掉，但拼图台没有大图）。 */
  const list = mode === 'collage'
    ? photos.map((_, i) => i)
    : visiblePhotos(photos, sessionName, ratings, filter, mode === 'grade');

  /** 当前片在"筛选后片单"里的位置，用于滚到可见 */
  const idxInList = list.indexOf(cur);
  useEffect(() => {
    if (idxInList >= 0 && virtuoso.current) {
      virtuoso.current.scrollToIndex({ index: idxInList, align: 'center', behavior: 'auto' });
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
          实测现场：调色台 + 一个一张星都没有的目录 ⇒ `visiblePhotos(..., forGrade=true)`
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
            ? '这个目录还没有打星的片 —— 调色台底栏只列 ★≥1，先去「选片台」打星'
            : mode === 'collage'
              ? '这个目录里没有照片 —— 也可以用右栏「从文件添加…」，或直接把图片拖进画布'
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
              onClick={() => {
                if (mode === 'collage') {
                  fillFromDock({
                    key: (sessionPath || '') + '||' + photos[gi].rel,
                    name: photos[gi].name,
                    sessionPath,
                    rel: photos[gi].rel
                  });
                } else {
                  setCur(gi);
                }
              }}
              data-idx={gi}
              style={{ padding: '0 3px' }}
            >
              <Thumb p={photos[gi]} gi={gi} />
            </div>
          );
        }}
      />
    </div>
  );
}
