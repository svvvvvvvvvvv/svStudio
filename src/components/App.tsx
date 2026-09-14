import { useEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import type { VirtuosoHandle } from 'react-virtuoso';
import { useStore } from '../store/useStore';
import { TopBar } from './TopBar';
import { HomeView } from './HomeView';
import { Dock } from './Dock';
import { GradePanel } from './GradePanel';
import { Viewer } from './Viewer';
import { Stars, FilterChips, ExifBar } from './Stars';
import { SessionPane } from './SessionPane';
import { PickPanel } from './PickPanel';

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
        {/* ★ 左侧图库目录：固定显示（进主题后也不消失，SV 09-15） */}
        {sessionName && <SessionPane />}

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
