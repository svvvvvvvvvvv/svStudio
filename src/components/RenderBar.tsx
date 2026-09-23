import { Box, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 调色台底栏 —— 只有一行状态提示。
 *
 * 渲染触发点只有两个：右栏「渲染」按钮、切进调色台。换风格 / 换图都不会自动出图。
 *
 * 留着它的价值：引擎起不来时把原因显示出来。不然右栏是空的、中间两栏也没图，
 * 光看界面看不出为什么。
 */
export function RenderBar() {
  const engineOk = useStore((s) => s.engineOk);
  const engineMsg = useStore((s) => s.engineMsg);
  const renderBusy = useStore((s) => s.renderBusy);
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);

  const hasPhoto = !!photos[cur];

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
        {!engineOk
          ? engineMsg || '引擎未启动'
          : renderBusy
            ? '出图中…'
            : hasPhoto
              ? '改完胶片风格 / 曝光风格，点右栏「渲染」出图'
              : '调色台只列已打星（★≥1）的片子 —— 先去选片台打星'}
      </Text>

      <Box style={{ flex: 1 }} />
    </Flex>
  );
}
