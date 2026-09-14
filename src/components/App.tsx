import { useEffect, useRef } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';
import { TopBar } from './TopBar';
import { HomeView } from './HomeView';
import { Dock } from './Dock';
import { GradePanel } from './GradePanel';

/**
 * 主布局：顶栏 + [左：会话列表] + [中：大图/分屏] + [右：参数] + 底栏
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
  const sessionPath = useStore((s) => s.sessionPath);
  const setCur = useStore((s) => s.setCur);
  const rate = useStore((s) => s.rate);

  const virtuoso = useRef<import('react-virtuoso').VirtuosoHandle>(null);

  useEffect(() => {
    loadSessions();
    loadEngine();
  }, [loadSessions, loadEngine]);

  /* 键盘：←/→ 翻页，1~5 打星（老代码 bindUI 里那套） */
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

  const curPhoto = photos[cur];

  return (
    <Flex direction="column" style={{ height: '100%' }}>
      <TopBar />

      <Flex style={{ flex: 1, minHeight: 0 }}>
        {/* 主区 */}
        <Box style={{ flex: 1, minWidth: 0, display: 'flex' }}>
          {!sessionName ? (
            <Box style={{ flex: 1, overflow: 'auto' }}>
              <HomeView />
            </Box>
          ) : (
            <Flex
              direction="column"
              style={{ flex: 1, minWidth: 0 }}
            >
              {/* 中间：大图 / 分屏 */}
              <Box
                style={{
                  flex: 1,
                  minHeight: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'var(--bg)',
                  padding: 18,
                  position: 'relative',
                }}
              >
                {curPhoto ? (
                  <img
                    src={
                      'file:///' +
                      (sessionPath + '\\' + curPhoto.rel).replace(/\\/g, '/')
                    }
                    alt={curPhoto.name}
                    style={{
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                      borderRadius: 'var(--r-md)',
                    }}
                  />
                ) : (
                  <Text style={{ color: 'var(--text-dim)' }}>没有照片</Text>
                )}
              </Box>

              <Dock virtuoso={virtuoso} />
            </Flex>
          )}
        </Box>

        {/* 右栏：调色台才显示 */}
        {mode === 'grade' && sessionName && <GradePanel />}
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
