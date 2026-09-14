import { useEffect, useRef, useState } from 'react';
import { API, Photo, RenderOpts } from '../api';
import { useStore } from '../store/useStore';

/**
 * 中间大图区。
 * ★ 关键：老代码在这里踩过坑 —— 选片台大图（#bigImgA/B）和调色台分屏（#splitView）
 *   **两套都是 position:absolute; inset:18px**，切模式只关一套就会"三层叠影"。
 *   React 下这个问题**不存在**：这里是"模式 → 渲染哪个组件"的二选一，
 *   不存在的那个根本不会被渲染进 DOM。
 *
 * ★★ 图片尺寸的写法（09-15 改，**别退回 maxWidth/maxHeight:100%**）：
 *   `max-width/max-height: 100%` 里的百分比**依赖父元素高度是否"确定"**，
 *   一旦某条 flex 链上高度不确定，百分比就被当成 none ⇒ 图按原尺寸渲染 ⇒
 *   溢出后被外层 `overflow:hidden` **裁掉**（SV 报的「小窗下两张图被裁」）。
 *   现在改成：父容器 `position:relative`，图 `position:absolute` + `calc(100% - 2*pad)`
 *   + `object-fit:contain` —— 尺寸**确定**、比例**一定保持**、永远不裁。
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
        position: 'relative',
        flex: 1,
        minHeight: 0,      // ⚠ 不给 0 会被 img 撑爆，底下的条全被挤出视口
        overflow: 'hidden',
        background: 'var(--bg)',
      }}
    >
      <img
        src={'file:///' + (sessionPath + '\\' + p.rel).replace(/\\/g, '/')}
        alt={p.name}
        style={{
          position: 'absolute',
          top: 18,
          left: 18,
          width: 'calc(100% - 36px)',
          height: 'calc(100% - 36px)',
          objectFit: 'contain',
          display: 'block',
        }}
      />
    </div>
  );
}

/**
 * 调色台分屏：左「原图」右「调色后」。
 * ⚠★ 两栏必须同口径才公平：左边用引擎 /base（缓存的 Sample.disp，恒等不调色），
 *    右边用 /render。**别让左栏去读原始 JPG** —— 尺寸和方向跟渲染结果不是一把尺子。
 *
 * ★★ 出图时机（SV 09-15 定）：**只有两个触发点**
 *   ① 右栏「胶片卷」下面的「渲染」按钮  ② 切进/进入调色台
 *   换图 / 换卷 / 换基准 / 拉滑杆 —— **一律只改参数，不动画面**。
 *   （以前是"任何改动都自动出图"，拖一次滑杆能瞬间打出几十发 6~15 s / 2~3 GB 的渲染
 *    互相抢占，最后那张反而迟迟不出来，看着就像"点了没反应"。）
 */
function SplitView() {
  const sessionPath = useStore((s) => s.sessionPath);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const grade = useStore((s) => s.grade);
  const engineOk = useStore((s) => s.engineOk);
  const ensureEngine = useStore((s) => s.ensureEngine);
  const renderTick = useStore((s) => s.renderTick);      // 「请渲染」次数
  const setRenderBusy = useStore((s) => s.setRenderBusy);
  const showToast = useStore((s) => s.showToast);

  const p: Photo | undefined = photos[cur];
  const [before, setBefore] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [imgId, setImgId] = useState<string | null>(null);

  /** 装载当前图 → 引擎，拿 id（引擎没起就自己拉起来） */
  useEffect(() => {
    if (!p) return;
    let alive = true;
    setLoading(true);
    setBefore(null);
    setAfter(null);
    setImgId(null);
    (async () => {
      try {
        if (!(await ensureEngine())) return;
        const full = sessionPath + '\\' + p.rel;
        const r = await API.engineLoad([full]);
        // ★ main.js 给的是 { ok, items:[{id,path,ms}|{path,error}] } —— 不是 { id }。
        const item = r?.items?.[0];
        if (!alive) return;
        if (!item?.id) {
          if (item?.error) showToast('装载失败：' + item.error);
          else if (r?.error) showToast('装载失败：' + r.error);
          return;
        }
        const id = String(item.id);
        setImgId(id);       // ★ 先落 id —— 渲染那条 effect 靠它启动
        // ★ 原图栏也是 { ok, image }（data:URL），不是 { bytes }
        const b = await API.engineBase(id);
        if (alive && b?.image) setBefore(b.image);
        else if (alive && b?.error) showToast('原图取不到：' + b.error);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [p, sessionPath, engineOk, ensureEngine, showToast]);

  /* ---- 渲染：只在 renderTick 变化时出图（不再自动出图，所以也不需要防抖） ----
     仍保留两条护栏：
       ① 同时只跑一发（手快连点 / 反复切台子会连发两三次）；
       ② 出图中有明显反馈（旧图原地不动 + 没有转圈 ⇒ 看着就像"没反应"）。 */
  const opts: RenderOpts = {
    stock: grade.stock,
    base: grade.base,
    params: grade.params || {},
  };
  const wantOptsRef = useRef<RenderOpts>({});
  const doneTickRef = useRef(-1);
  const busyRef = useRef(false);
  const pumpRef = useRef<() => void>(() => {});

  // 每次渲染记下「最新想要的参数」—— 异步回调里读 ref，避免闭包过期
  wantOptsRef.current = opts;

  pumpRef.current = async () => {
    const id = imgId;
    const tick = renderTick;
    if (!id) return;
    if (busyRef.current) return;                     // 有一发在跑，让它先完
    if (doneTickRef.current === tick) return;        // 这一发已经出过了
    busyRef.current = true;
    setBusy(true);
    setRenderBusy(true);
    try {
      if (!(await ensureEngine())) return;
      const r = await API.engineRender(id, wantOptsRef.current);
      if (r?.image) {
        setAfter(r.image);
        doneTickRef.current = tick;
      } else if (r?.error) {
        showToast('渲染失败：' + r.error);
      }
    } catch (e) {
      showToast('渲染失败：' + String(e));
    } finally {
      busyRef.current = false;
      setBusy(false);
      setRenderBusy(false);
    }
  };

  useEffect(() => {
    if (!engineOk || !imgId) return;
    // ⚠ 请求常常比装载先到（切台子那一刻图还没 load 完）⇒ 不能只认「变化」，
    //   要认「这一发还没出过」：imgId 到位后 effect 会再跑一次，那时才真正出图。
    if (doneTickRef.current === renderTick) return;
    pumpRef.current();
  }, [renderTick, engineOk, imgId]);

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: 'flex',
        gap: 8,
        padding: 18,
        background: 'var(--bg)',
        overflow: 'hidden',
      }}
    >
      <Pane title="原图" src={before} loading={loading} busy={false} />
      <Pane title="调色后" src={after} loading={loading} busy={busy} />
      {!engineOk && (
        <div style={{ color: 'var(--text-dim)', alignSelf: 'center' }}>
          引擎未启动
        </div>
      )}
    </div>
  );
}

const PANE_PAD = 8;

function Pane({
  title,
  src,
  loading,
  busy,
}: {
  title: string;
  src: string | null;
  loading: boolean;
  busy: boolean;
}) {
  return (
    <div
      style={{
        position: 'relative',
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-panel)',
        borderRadius: 'var(--r-md)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          flexShrink: 0,      // 标题栏不许被压
          fontSize: 11,
          padding: '4px 10px',
          color: 'var(--text-dim)',
          borderBottom: '1px solid var(--line)',
        }}
      >
        {title}
      </div>
      {/* ★ 图区：绝对定位 + calc 尺寸 + object-fit:contain
          —— 不依赖"父高是否确定"，所以不会出现"图比面板大、被 overflow 裁掉"。
          仍然保留 minHeight:0（防 flex 子项不肯收缩这类老问题）。 */}
      <div
        style={{
          position: 'relative',
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        {src ? (
          <img
            src={src}
            alt={title}
            style={{
              position: 'absolute',
              top: PANE_PAD,
              left: PANE_PAD,
              width: `calc(100% - ${PANE_PAD * 2}px)`,
              height: `calc(100% - ${PANE_PAD * 2}px)`,
              objectFit: 'contain',
              display: 'block',
            }}
          />
        ) : (
          <span
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
              color: 'var(--text-faint)',
            }}
          >
            {loading ? '出图中…' : '按「渲染」出图'}
          </span>
        )}
      </div>
      {/* ★ 出图中的反馈：以前旧图原地不动、没有任何提示 ⇒ 看着就像"点了没反应" */}
      {busy && (
        <span
          style={{
            position: 'absolute',
            right: 8,
            bottom: 8,
            fontSize: 11,
            padding: '2px 9px',
            borderRadius: 999,
            background: 'rgba(0,0,0,.6)',
            color: '#fff',
            pointerEvents: 'none',
          }}
        >
          出图中…
        </span>
      )}
    </div>
  );
}
