import { useEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { VirtuosoHandle } from 'react-virtuoso';
import { useStore } from '../store/useStore';
import { API } from '../api';
import { TopBar } from './TopBar';
import { HomeView } from './HomeView';
import { Dock } from './Dock';
import { GradePanel } from './GradePanel';
import { Viewer } from './Viewer';
import { Stars, FilterChips, ExifBar } from './Stars';
import { SessionPane } from './SessionPane';
import { PickPanel } from './PickPanel';
import { RenderBar } from './RenderBar';

/**
 * 主布局：顶栏 + [中：大图/分屏 + 底栏] + [右：参数]
 *
 * ★ 老代码最大的结构性问题在这里被解决：
 *   选片台大图和调色台分屏**两套都是 absolute+inset**，老代码切模式时只关掉一套，
 *   结果出「三层叠影」。React 下不存在这个问题 —— 二选一渲染，
 *   不显示的那套压根不会进 DOM。
 */
export function App() {
  const loadSessions = useStore((s) => s.loadSessions);
  const loadEngine = useStore((s) => s.loadEngine);
  const ensureEngine = useStore((s) => s.ensureEngine);
  const sessionName = useStore((s) => s.sessionName);
  const mode = useStore((s) => s.mode);
  const busy = useStore((s) => s.busy);
  const busyText = useStore((s) => s.busyText);
  const toast = useStore((s) => s.toast);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const setCur = useStore((s) => s.setCur);
  const rate = useStore((s) => s.rate);

  const virtuoso = useRef<VirtuosoHandle>(null);

  useEffect(() => {
    loadSessions();
    loadEngine();
  }, [loadSessions, loadEngine]);

  /* 库外·纯 RAW 目录生成预览小图：进度是**主进程推的事件**，不是 invoke 的返回值 ——
     一次几百张要转几十秒，等返回值才刷新的话遮罩上一直是刚开头那行字（看着像卡死）。 */
  useEffect(() => API.onExtIndexProgress((line) => useStore.getState().setBusyText(line)), []);

  /* 批量出片：进度同样是主进程**推**过来的（几百张要跑几十分钟，等返回值界面像死机）。 */
  useEffect(
    () =>
      API.onExportBatchProgress((o) => {
        const s = useStore.getState();
        if (!o) return;
        if (o.phase === 'end') {
          s.setBatchText('');
        } else if (o.phase === 'one') {
          s.setBatchText(`${o.i}/${o.n} 已出 ${o.done} 张${o.failed ? ` · 失败 ${o.failed}` : ''}`);
        } else {
          s.setBatchText(`${o.i}/${o.n} 正在出 ${o.name || ''}`);
        }
      }),
    []
  );

  /* ★ 进调色台：① 先把引擎拉起来（开机那刻通常还没起，不拉的话卷/基准/滑杆全是空的）
     ② 再自动出一次图 —— 这是 SV 09-15 定的两个渲染触发点之一，另一个是右栏的「渲染」按钮。
     ⚠ 除此之外**任何操作都不自动出图**（换卷/换基准/拉滑杆/换图都只改参数，不动画面）。 */
  useEffect(() => {
    if (mode !== 'grade') return;
    (async () => {
      await ensureEngine();
      useStore.getState().requestRender();
    })();
  }, [mode, ensureEngine]);

  /* 键盘：←/→ 翻页，1~5 打星，0 清星 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!photos.length) return;
      if (e.key === 'ArrowLeft') setCur(Math.max(0, cur - 1));
      else if (e.key === 'ArrowRight')
        setCur(Math.min(photos.length - 1, cur + 1));
      else if (e.key >= '1' && e.key <= '5') rate(Number(e.key));
      else if (e.key === '0') rate(0);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [photos.length, cur, setCur, rate]);

  return (
    <Flex direction="column" style={{ height: '100%' }}>
      <TopBar />

      <Flex style={{ flex: 1, minHeight: 0 }}>
        {/* ★ 左侧图库目录：**常显**（SV 09-15）——
            进目录后不消失（随时切目录），空库/新库时也必须在：
            不然「加入目录」这个入口在新库上永远点不到（而新库恰恰最需要它）。 */}
        <SessionPane />

        {/* 主区：大图/分屏 + 底栏
            ⚠ minHeight:0 必须加 —— 嵌套 flex 的子项默认 min-height:auto，
              会被大图内容撑爆，把底栏/星级挤出视口（09-15 SV 实测踩到） */}
        <Flex direction="column" style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
          {!sessionName ? (
            <Box style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
              <HomeView />
            </Box>
          ) : (
            <>
              <Viewer />

              {/* 星级 + 筛选（调色台不显示筛选：那边定死只收 ★≥1） */}
              <Flex
                align="center"
                gap="3"
                px="3"
                py="2"
                style={{
                  borderTop: '1px solid var(--line)',
                  background: 'var(--bg-panel)',
                  flex: '0 0 auto',
                }}
              >
                <Stars big />
                <Box style={{ flex: 1 }} />
                {mode === 'pick' && <FilterChips />}
              </Flex>

              <ExifBar />
              <Dock virtuoso={virtuoso} />
              {mode === 'grade' && <RenderBar />}
            </>
          )}
        </Flex>

        {/* 右栏：选片台 = 照片参数（只读）；调色台 = 调色面板 */}
        {sessionName && mode === 'pick' && <PickPanel />}
        {sessionName && mode === 'grade' && <GradePanel />}
      </Flex>

      {/* busy 遮罩 */}
      {busy && (
        <Box
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 50,
          }}
        >
          <Text>{busyText || '处理中…'}</Text>
        </Box>
      )}

      {/* toast */}
      {toast && (
        <Box
          style={{
            position: 'fixed',
            bottom: 24,
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'var(--bg-hover)',
            padding: '8px 16px',
            borderRadius: 'var(--r-md)',
            zIndex: 60,
            fontSize: 12.5,
          }}
        >
          {toast}
        </Box>
      )}

    </Flex>
  );
}
