import { Box, Button, Badge, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 顶栏：切模式（选片台 / 调色台）+ 库路径 + 同步星级。
 * 所有按钮的 disabled 状态都由 store 派生 —— 不用再手写 updateArchiveBtn()。
 */
export function TopBar() {
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const sessionName = useStore((s) => s.sessionName);
  const goHome = useStore((s) => s.goHome);
  const ratings = useStore((s) => s.ratings);
  const photos = useStore((s) => s.photos);

  return (
    <Flex
      align="center"
      gap="3"
      px="4"
      py="2"
      style={{
        borderBottom: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        flex: '0 0 auto',
      }}
    >
      {/* 模式切换：苹果的分段控件（Segmented Control） */}
      <Flex
        style={{
          background: 'var(--bg)',
          borderRadius: 'var(--r-sm)',
          padding: 2,
          gap: 2,
        }}
      >
        {(
          [
            ['pick', '选片台'],
            ['grade', '调色台'],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            style={{
              border: 0,
              cursor: 'pointer',
              padding: '4px 14px',
              borderRadius: 4,
              fontSize: 12.5,
              color: mode === m ? 'var(--text)' : 'var(--text-dim)',
              background: mode === m ? 'var(--bg-hover)' : 'transparent',
              transition: 'background .15s',
            }}
          >
            {label}
          </button>
        ))}
      </Flex>

      <Text size="1" style={{ color: 'var(--text-dim)' }}>
        {sessionName || '—'}
      </Text>

      {sessionName && (
        <Button size="1" variant="ghost" onClick={goHome}>
          主题列表
        </Button>
      )}

      <Box style={{ flex: 1 }} />

      {/* ★ 待同步数量：直接从 ratings 派生，不用手动维护计数 */}
      <Badge color="gray">
        {Object.values(ratings).filter((v) => v >= 1).length} / {photos.length}
      </Badge>
    </Flex>
  );
}
