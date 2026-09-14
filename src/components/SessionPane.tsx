import { Flex, Text } from '@radix-ui/themes';
import { useStore } from '../store/useStore';

/**
 * 左侧图库目录栏 —— ★ 固定显示（SV 09-15：图库目录应该固定在左侧）。
 * 老代码就是这个布局（sessionPane 一直在左），我第二步漏搬了。
 * 进入主题后也不消失，随时切主题。
 */
export function SessionPane() {
  const sessions = useStore((s) => s.sessions);
  const sessionName = useStore((s) => s.sessionName);
  const enter = useStore((s) => s.enterSession);

  return (
    <Flex
      direction="column"
      style={{
        width: 190,
        flex: '0 0 auto',
        borderRight: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        overflow: 'auto',
        paddingTop: 6,
      }}
    >
      <Text
        size="1"
        weight="bold"
        style={{ color: 'var(--text-dim)', letterSpacing: 1, padding: '0 12px 6px' }}
      >
        图库目录
      </Text>
      {sessions.map((s) => {
        const on = s.name === sessionName;
        return (
          <button
            key={s.name}
            onClick={() => enter(s.name)}
            title={s.name}
            style={{
              border: 0,
              textAlign: 'left',
              cursor: 'pointer',
              padding: '7px 12px',
              fontSize: 12.5,
              color: on ? 'var(--text)' : 'var(--text-dim)',
              background: on ? 'var(--bg-hover)' : 'transparent',
              borderLeft: on ? '3px solid var(--accent)' : '3px solid transparent',
              transition: 'background .12s',
            }}
          >
            <div
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {s.name}
            </div>
            {typeof s.count === 'number' && (
              <div style={{ fontSize: 10.5, opacity: 0.6 }}>{s.count} 张</div>
            )}
          </button>
        );
      })}
    </Flex>
  );
}
