import { useEffect, useRef, useState } from 'react';
import { API, Photo, RenderOpts } from '../api';
import { useStore } from '../store/useStore';
import { ZoomImage } from './ZoomImage';

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
 *
 * ★★ 缩放（09-15 SV 选「B」）：两处大图**共用 `ZoomImage`**（一份实现两条入口，
 *   别再各写一套）—— 滚轮缩放 + 「适应 / 1:1」两档 + 双击切换。
 *   为什么要有：验收皮肤必须看 1:1（颗粒、磨皮、对焦在眼睛上，缩略图里看不出来）。
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
      {/* ⚠ 选片台读的是**原始文件**（`rel` 那条路）—— 这是 SV 定的「选片台优先读 jpg 展示」，
          出图源那件事只对**调色台**生效，别顺手改这里。 */}
      <ZoomImage
        src={'file:///' + (sessionPath + '\\' + p.rel).replace(/\\/g, '/')}
        alt={p.name}
        pad={18}
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
        /* ★★ 出图源 = **同名 RAW 优先**（SV 09-15：「工作台本来就要优先用 raw」）。
           main.js 的 `attachLoadPath()` 已经把该用哪个文件算好了（没有 RAW 才回落到 JPG）。
           ⚠ 这里**不要**自己拼 `rel` —— 那是身份键，不是出图源。
           ⚠ 必须走 RAW：入口那一段（零点/成形/趾部/护栏）只在 `io.load_raw` 里跑，
             喂 JPG 的话「整张亮暗(总)」「暗部亮度」永远是死的。 */
        const full = p.loadPath || sessionPath + '\\' + p.rel;
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
    /* ★★ 相纸（09-15 SV 选「C」）：**必须带上** —— 少了这一行，界面选了纸、出图还是旧纸，
       而且画面不变 ⇒ 用户以为"这张纸没效果"（真因是根本没发出去）。
       空串/undefined 时引擎按"这一卷的配套纸"处理（`spektra.resolve_paper` 兜底）。 */
    paper: grade.paper,
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
      {/* ★ 标题栏标出「这张是用什么出的图」—— 只有 JPG 的主题（没有 RAF）入口那两根
          滑杆（整张亮暗(总)／暗部亮度）是不生效的，以前界面上完全看不出来。
          ⚠ 这个标记放在 `note` 里，**不能塞进 title** —— title 同时是 img 的 alt，
            布局自检靠 alt === '调色后' 认这两栏。 */}
      <Pane
        title="调色后"
        note={p ? (p.loadIsRaw ? 'RAW 出图' : 'JPG 出图（无 RAW）') : undefined}
        src={after}
        loading={loading}
        busy={busy}
      />
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
  note,
  src,
  loading,
  busy,
}: {
  title: string;
  /** 标题右边的小字（例：出图源是 RAW 还是 JPG）。⚠ 别塞进 `title`：那是 img 的 alt */
  note?: string;
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
          display: 'flex',
          gap: 8,
          alignItems: 'baseline',
        }}
      >
        <span>{title}</span>
        {note && <span style={{ color: 'var(--text-faint)' }}>{note}</span>}
      </div>
      {/* ★ 图区：绝对定位 + calc 尺寸 + object-fit:contain
          —— 不依赖"父高是否确定"，所以不会出现"图比面板大、被 overflow 裁掉"。
          仍然保留 minHeight:0（防 flex 子项不肯收缩这类老问题）。
          ★ 缩放交给 `ZoomImage`（滚轮 + 适应/1:1 + 双击），口径见那个文件头。 */}
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
          <ZoomImage src={src} alt={title} pad={PANE_PAD} />
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
