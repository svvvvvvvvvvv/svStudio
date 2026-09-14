import { Box, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 调色台底栏 —— **只剩状态提示**。
 *
 * ★ 09-15 SV 定的策略：
 *   ①「渲染」按钮搬到了右栏「胶片卷」下面（那才是手会停的地方）；
 *   ② 换图/换卷/换基准/拉滑杆**都不再自动出图**，所以「换图/换卷自动出图」那个勾也没意义了，删掉。
 *   现在只有两个触发点：右栏「渲染」按钮、切进调色台。
 *
 * 这一条留着的价值：引擎起不来时把原因显示出来（不然右栏是空的、中间两栏也没图，看不出为啥）。
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
              ? '改完卷 / 基准 / 参数，点右栏「渲染」出图'
              : '调色台只列已打星（★≥1）的片子 —— 先去选片台打星'}
      </Text>

      <Box style={{ flex: 1 }} />
    </Flex>
  );
}
