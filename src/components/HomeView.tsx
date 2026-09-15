import { Button, Callout, Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 主题（会话）列表页 —— 老代码的 homeView。
 * 数据全部来自 store，点一下 enterSession 即可，不用再手写 renderSessions() 拼 DOM。
 */
export function HomeView() {
  const sessions = useStore((s) => s.sessions);
  const enter = useStore((s) => s.enterSession);
  const busy = useStore((s) => s.busy);

  if (!sessions.length) {
    return (
      <Flex align="center" justify="center" style={{ height: '100%' }}>
        <Callout.Root color="gray">
          <Callout.Text>
            这个库里还没有主题。照片已经在硬盘上，就点左侧「加入目录」
            把它那个文件夹挂进来（原地读，不复制）；想换库就点左下角「换图库」。
          </Callout.Text>
        </Callout.Root>
      </Flex>
    );
  }

  return (
    <Flex direction="column" gap="2" p="4" style={{ overflow: 'auto' }}>
      <Text size="1" style={{ color: 'var(--text-dim)' }}>
        共 {sessions.length} 个主题
      </Text>
      {sessions.map((s) => (
        <Button
          key={s.name}
          variant="soft"
          size="2"
          disabled={busy}
          onClick={() => enter(s.name)}
          style={{ justifyContent: 'flex-start' }}
        >
          {s.name}
          {typeof s.count === 'number' && (
            <Text size="1" style={{ color: 'var(--text-dim)', marginLeft: 6 }}>
              {s.count} 张
            </Text>
          )}
        </Button>
      ))}
    </Flex>
  );
}
