import { Button, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';

/**
 * 左侧图库目录栏。
 *
 * ★ 两件事（09-15 SV 定）：
 *  ① **固定显示**（图库目录应该固定在左侧，进主题后也不消失，随时切主题）。
 *     ⚠ 一开始是 `{sessionName && <SessionPane/>}` —— 那就是"空库 / 新库没有主题 ⇒
 *       左栏不显示"，于是**导入按钮永远点不到**（新库恰恰是最需要导入的时候）。
 *  ② 顶部加「导入照片」+「换图库」。
 *     换图库的通道（`pick-directory`）一直就有，只是**前端没人接**；
 *     首页那句「先点右上角「切换照片库」」指的是一个不存在的按钮。
 */
export function SessionPane() {
  const sessions = useStore((s) => s.sessions);
  const sessionName = useStore((s) => s.sessionName);
  const enter = useStore((s) => s.enterSession);
  const openImport = useStore((s) => s.openImport);
  const changeLibRoot = useStore((s) => s.changeLibRoot);
  const libRoot = useStore((s) => s.libRoot);
  const busy = useStore((s) => s.busy);
  const running = useStore((s) => s.importRunning);

  const pickLib = async () => {
    const p = await API.pickDirectory();
    if (p) await changeLibRoot(p);
  };

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
      {/* ---- 顶部动作：导入 / 换图库 ---- */}
      <Flex direction="column" gap="1" px="2" pb="2" data-lib-actions>
        <Button
          size="1"
          variant="soft"
          data-import-open
          disabled={busy || running}
          onClick={() => openImport()}
        >
          导入照片
        </Button>
        <Button size="1" variant="ghost" disabled={busy} onClick={pickLib}>
          换图库
        </Button>
      </Flex>

      <Text
        size="1"
        weight="bold"
        style={{ color: 'var(--text-dim)', letterSpacing: 1, padding: '0 12px 6px' }}
      >
        图库目录
      </Text>

      {sessions.length === 0 ? (
        <Text size="1" style={{ color: 'var(--text-faint)', padding: '0 12px', lineHeight: 1.7 }}>
          这个库里还没有主题。
          <br />
          插卡后点上面的「导入照片」。
        </Text>
      ) : (
        sessions.map((s) => {
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
        })
      )}

      {/* 库根：切错盘 / 看不着主题时，这一行是唯一的线索 */}
      <Text
        size="1"
        data-lib-root
        title={libRoot}
        style={{
          color: 'var(--text-faint)',
          padding: '10px 12px',
          marginTop: 'auto',
          fontSize: 10.5,
          lineHeight: 1.5,
          wordBreak: 'break-all',
        }}
      >
        {libRoot || '（未设置图库）'}
      </Text>
    </Flex>
  );
}
