import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 调色台底栏（老版 #renderBar）：渲染按钮 + 自动出图 + 状态提示。
 * 渲染动作本身在 Viewer 的分屏里做（那才是显示结果的地方），
 * 这里只负责「手动点一下」和「自动出图开关」这两个意图。
 */
export function RenderBar() {
  const engineOk = useStore((s) => s.engineOk);
  const engineMsg = useStore((s) => s.engineMsg);
  const ensureEngine = useStore((s) => s.ensureEngine);
  const auto = useStore((s) => s.autoRender);
  const setAuto = useStore((s) => s.setAutoRender);
  const rendering = useStore((s) => s.rendering);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const setRendering = useStore((s) => s.setRendering);

  const hasPhoto = !!photos[cur];

  /** 手动渲染：先确保引擎在，再触发重渲染（改一下 rendering 让分屏重跑） */
  const doRender = async () => {
    if (!hasPhoto) return;
    setRendering(true);
    try {
      await ensureEngine();
    } finally {
      // 分屏那边靠 rendering 状态变化重新出图
      setTimeout(() => setRendering(false), 30);
    }
  };

  return (
    <Flex
      align="center"
      gap="3"
      px="3"
      py="2"
      style={{
        flex: '0 0 auto',
        borderTop: '1px solid var(--line)',
        background: 'var(--bg-panel)',
      }}
    >
      <Text size="1" style={{ color: 'var(--text-dim)' }}>
        {engineOk ? '调色台 · 选中一张后按「渲染」' : (engineMsg || '引擎未启动')}
      </Text>

      <Box style={{ flex: 1 }} />

      <label
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          fontSize: 11.5,
          color: 'var(--text-dim)',
          cursor: 'pointer',
        }}
      >
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => setAuto(e.target.checked)}
        />
        换图/换卷自动出图
      </label>

      <Button size="1" disabled={!hasPhoto || rendering} onClick={doRender}>
        {rendering ? '出图中…' : '渲染'}
      </Button>
    </Flex>
  );
}
