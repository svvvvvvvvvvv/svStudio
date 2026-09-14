import { useEffect, useRef, useState } from 'react';
import { API, Photo } from '../api';
import { useStore } from '../store/useStore';

/**
 * 中间大图区。
 * ★ 关键：老代码在这里踩过坑 —— 选片台大图（#bigImgA/B）和调色台分屏（#splitView）
 *   **两套都是 position:absolute; inset:18px**，切模式只关一套就会"三层叠影"。
 *   React 下这个问题**不存在**：这里是"模式 → 渲染哪个组件"的二选一，
 *   不存在的那个根本不会被渲染进 DOM。
 */
export function Viewer() {
  const sessionPath = useStore((s) => s.sessionPath);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const mode = useStore((s) => s.mode);

  const p: Photo | undefined = photos[cur];

  if (mode === 'grade') return <SplitView />;

  if (!p) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-dim)',
          background: 'var(--bg)',
        }}
      >
        没有照片
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        padding: 18,
      }}
    >
      <img
        src={'file:///' + (sessionPath + '\\' + p.rel).replace(/\\/g, '/')}
        alt={p.name}
        style={{
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
          borderRadius: 'var(--r-md)',
        }}
      />
    </div>
  );
}

/**
 * 调色台分屏：左「原图」右「调色后」。
 * ⚠★ 两栏必须同口径才公平：左边用引擎 /base（缓存的 Sample.disp，恒等不调色），
 *    右边用 /render。**别让左栏去读原始 JPG** —— 尺寸和方向跟渲染结果不是一把尺子。
 */
function SplitView() {
  const sessionPath = useStore((s) => s.sessionPath);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const grade = useStore((s) => s.grade);
  const engineOk = useStore((s) => s.engineOk);

  const p: Photo | undefined = photos[cur];
  const [before, setBefore] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const idRef = useRef<string | null>(null);

  /** 装载当前图 → 引擎，拿 id */
  useEffect(() => {
    if (!p || !engineOk) return;
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const full = sessionPath + '\\' + p.rel;
        const r = await API.engineLoad([full]);
        if (!alive || !r?.id) return;
        idRef.current = r.id;
        const b = await API.engineBase(r.id);
        if (alive && b && (b as { bytes?: string }).bytes) {
          setBefore((b as { bytes: string }).bytes);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [p, sessionPath, engineOk]);

  /** 渲染（换参数/换卷/换基准都重跑） */
  useEffect(() => {
    if (!idRef.current || !engineOk) return;
    let alive = true;
    setLoading(true);
    API.engineRender(idRef.current, {
      stock: grade.stock,
      base: grade.base,
      params: grade.params || {},
    })
      .then((r) => {
        if (alive && r?.bytes) setAfter(r.bytes);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [grade, engineOk, before]);

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        gap: 8,
        padding: 18,
        background: 'var(--bg)',
      }}
    >
      <Pane title="原图" src={before} loading={loading} />
      <Pane title="调色后" src={after} loading={loading} />
      {!engineOk && (
        <div style={{ color: 'var(--text-dim)', alignSelf: 'center' }}>
          引擎未启动
        </div>
      )}
    </div>
  );
}

function Pane({
  title,
  src,
  loading,
}: {
  title: string;
  src: string | null;
  loading: boolean;
}) {
  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-panel)',
        borderRadius: 'var(--r-md)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          fontSize: 11,
          padding: '4px 10px',
          color: 'var(--text-dim)',
          borderBottom: '1px solid var(--line)',
        }}
      >
        {title}
      </div>
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 8,
        }}
      >
        {src ? (
          <img
            src={src}
            alt={title}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              objectFit: 'contain',
            }}
          />
        ) : (
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            {loading ? '出图中…' : '按「渲染」出图'}
          </span>
        )}
      </div>
    </div>
  );
}
