import { useEffect, useRef, useState } from 'react';
import { API, Photo, RenderOpts } from '../api';
import { useStore } from '../store/useStore';
import { FitImage } from './FitImage';

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
 * ★★ 三处大图（选片台一张 + 调色台两栏）**共用 `FitImage`**（一份实现，别再各写一套）。
 *   ⚠ 09-15 SV 拍板：「两图总用自适应，**删除其他的**」⇒ `FitImage` 里**没有**缩放，
 *     滚轮 / 1:1 / 双击切换 / 百分比徽标全删了，**别再顺手加回来**。
 *   ★ 看图的"姿势"改由下面那组 **A / A|B / B** 三档按钮管（照 Lightroom）。
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
      <FitImage
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
 * ★ 出图时机：**只有两个触发点** —— ① 右栏「渲染」按钮 ② 切进调色台。
 *   换图 / 换风格一律只改参数，不动画面。
 *
 * ★ 视图三档（照 Lightroom 的 `A` / `A|B` / `B`）：默认 `A|B`。
 *   `A` = 只看原图 · `A|B` = 左右对比 · `B` = 只看调色后。
 *   ❗**只换"怎么看"，不碰渲染**：切档不出图、不改参数、不清已出的那一张
 *     （两栏共用同一个 `after` dataURL ⇒ 从 `A|B` 切到 `B` 立刻就能看到，
 *      不会因为"少了左栏"重新渲染一发）。改了就变成"切个视图等半分钟"。
 *   ⚠ 用 `useState` 局部状态（不进 store）：这是临时的看图姿势，不该跟着配方落盘。
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
  /* ★★ 视图三档（默认 A|B = 左右对比）—— 见文件里 SplitView 的说明 */
  const [view, setView] = useState<ViewMode>('ab');

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
        /* 出图源 = `p.loadPath`（main.js 的 `attachLoadPath()` 算好的，永远指向根里的 RAW）。
           ⚠ 不许自己拼 `rel` —— 它是身份键（不带扩展名），不是路径。 */
        const full = p.loadPath || '';
        if (!full) {
          showToast('这张没有出图源（根目录里找不到对应的 RAW）');
          setLoading(false);
          return;
        }
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

  /* ---- 渲染：盯 `renderTick` ----
     四条护栏：
       ① 同时只跑一发（手快连点 / 反复切台子会连发两三次）；
       ② 60ms 防抖；
       ③ **跑完补发最新那一发** —— 忙的时候发来的不许丢（丢了画面就停在上一次）；
       ④ 出图中有可见反馈（旧图原地不动、也没有转圈 ⇒ 看着就像"没反应"）。 */
  const opts: RenderOpts = {
    /* 两个选择器都在这里进请求。少一个 ⇒ 界面选了、画面不动。 */
    stock: grade.stock,
    style: grade.style,
  };
  const wantOptsRef = useRef<RenderOpts>({});
  const doneTickRef = useRef(-1);
  const busyRef = useRef(false);
  const pumpRef = useRef<() => void>(() => {});
  /* **最新一发是谁**：异步回调里读不到新的 `renderTick`（闭包过期）⇒ 只能靠 ref 记住，
     跑完跟"我刚跑的是哪一发"比 —— 不相等说明参数又动过，得补发。 */
  const tickRef = useRef(renderTick);

  // 每次渲染记下「最新想要的参数」—— 异步回调里读 ref，避免闭包过期
  wantOptsRef.current = opts;
  tickRef.current = renderTick;

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
      /* 忙的时候发来的那一发**不许丢**：这一发在跑的过程中 tick 可能又涨了
         ⇒ 跑完补发"最新那一发"，否则画面停在上一次（参数是新的、画面是旧的）。
         ⚠ 失败的那一发不重试（`doneTickRef` 没更新、tick 也没变 ⇒ 不会自激）。 */
      if (tickRef.current !== tick) pumpRef.current();
    }
  };

  useEffect(() => {
    if (!engineOk || !imgId) return;
    // ⚠ 请求常常比装载先到（切台子那一刻图还没 load 完）⇒ 不能只认「变化」，
    //   要认「这一发还没出过」：imgId 到位后 effect 会再跑一次，那时才真正出图。
    if (doneTickRef.current === renderTick) return;
    /* 防抖 60ms：连点「渲染」/ 反复切台子会连发好几发，而每发 1 到几十秒。
       ⚠ 只防抖「发起」，不丢「结果」：合并交给上面的 pump（跑完补发最新那一发）。 */
    const t = window.setTimeout(() => pumpRef.current(), 60);
    return () => window.clearTimeout(t);
  }, [renderTick, engineOk, imgId]);

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg)',
        overflow: 'hidden',
      }}
    >
      {/* ★★ 视图三档（LR 的 A / A|B / B）：一整条放在两栏**上方**，别塞进某一栏 ——
          塞进栏里就会变成"每一栏各有一个"，切档时按钮位置跟着栏一起消失。 */}
      <div
        style={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '8px 18px 0',
        }}
      >
        {!engineOk && (
          <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>引擎未启动</span>
        )}
        <ViewSwitch value={view} onChange={setView} />
      </div>
      {/* ⚠ `data-view-mode` 是自检的探针（默认必须是 `ab`）—— 三档值 = a / ab / b */}
      <div
        data-view-mode={view}
        style={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          display: 'flex',
          gap: 8,
          padding: '8px 18px 18px',
          overflow: 'hidden',
        }}
      >
        {view !== 'b' && <Pane title="原图" src={before} loading={loading} busy={false} />}
        {/* 右边标题栏带上出图源的文件名，一眼看出喂的是哪张 RAW。
            ⚠ 这个标记放在 `note` 里，**不能塞进 title** —— title 同时是 img 的 alt，
              布局自检靠 alt === '调色后' 认这两栏。 */}
        {view !== 'a' && (
          <Pane
            title="调色后"
            note={p ? p.loadPath || undefined : undefined}
            src={after}
            loading={loading}
            busy={busy}
          />
        )}
      </div>
    </div>
  );
}

/* ★★ 视图三档（09-15 SV 定）：照 Lightroom 的 `A` / `A|B` / `B`。
   `A` = 只看原片；`A|B` = 左右对比（默认）；`B` = 只看调色后的效果、**单张铺满**。
   ❗只换"怎么看"，不碰渲染（切档不出图）。 */
type ViewMode = 'a' | 'ab' | 'b';

const VIEWS: { id: ViewMode; label: string; tip: string }[] = [
  { id: 'a', label: 'A', tip: '只看原片' },
  { id: 'ab', label: 'A|B', tip: '左右对比（左原片 / 右调色后）' },
  { id: 'b', label: 'B', tip: '只看调色后的效果（铺满）' },
];

function ViewSwitch({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (v: ViewMode) => void;
}) {
  return (
    <div
      /* ⚠ 自检靠这两个探针认档位（`data-view-switch` = 当前档） */
      data-view-switch={value}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        padding: 2,
        borderRadius: 'var(--r-sm)',
        border: '1px solid var(--line)',
        background: 'var(--bg-panel)',
      }}
    >
      {VIEWS.map((v) => {
        const on = v.id === value;
        return (
          <button
            key={v.id}
            type="button"
            title={v.tip}
            aria-pressed={on}
            /* ⚠ 自检靠这两个探针点按钮：`data-view-btn` = 档位，`data-view-active` = 亮着没 */
            data-view-btn={v.id}
            data-view-active={on ? '1' : '0'}
            onClick={() => onChange(v.id)}
            style={{
              border: 'none',
              borderRadius: 'calc(var(--r-sm) - 2px)',
              padding: '1px 9px',
              minWidth: 30,
              fontSize: 11,
              lineHeight: 1.7,
              cursor: 'pointer',
              background: on ? 'var(--accent)' : 'transparent',
              color: on ? '#fff' : 'var(--text-dim)',
            }}
          >
            {v.label}
          </button>
        );
      })}
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
          ★ 尺寸/适应那套在 `FitImage` 里（**没有缩放**，09-15 SV 定），见那个文件头。 */}
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
          <FitImage src={src} alt={title} pad={PANE_PAD} />
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
