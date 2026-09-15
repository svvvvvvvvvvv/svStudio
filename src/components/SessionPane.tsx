import { Button, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';

/**
 * 左侧图库目录栏。
 *
 * ★ 三件事（09-15 SV 定）：
 *  ① **固定显示**（图库目录应该固定在左侧，进主题后也不消失，随时切主题）。
 *     ⚠ 一开始是 `{sessionName && <SessionPane/>}` —— 那就是"空库 / 新库没有主题 ⇒
 *       左栏不显示"，于是**导入按钮永远点不到**（新库恰恰是最需要导入的时候）。
 *  ② 顶部加「导入照片」+「换图库」。
 *     换图库的通道（`pick-directory`）一直就有，只是**前端没人接**；
 *     首页那句「先点右上角「切换照片库」」指的是一个不存在的按钮。
 *  ③ ★ **「加入目录…」**（09-15 SV：*"如果一张照片已经在我的电脑中，我可以通过加这个目录
 *     让这个目录出现在图片库那一栏中"*）—— 把硬盘上**已有的**照片文件夹挂进这个列表。
 *
 * ★★ ②③ 是两件**不同**的事，别混：
 *   | | 导入照片 | 加入目录 |
 *   |---|---|---|
 *   | 干什么 | **复制**进照片库、按「日期_主题_地点」建档 | 把已有目录**就地**列进来 |
 *   | 动原文件吗 | 不动（只读源） | 不动（原地读） |
 *   | 原目录删了 | 库里的副本还在 | 那条就从列表消失（会留一条「找不到」） |
 *   | 用在哪 | 卡 / U 盘里的新片 | 已经在硬盘上的片子 |
 *
 * ⚠ 库外条目上的「×」**只从列表移除，不删任何文件**（按钮 title 里必须写清 ——
 *   用户看到 × 的第一反应是"会不会删我文件"）。
 */
export function SessionPane() {
  const sessions = useStore((s) => s.sessions);
  const sessionName = useStore((s) => s.sessionName);
  const enter = useStore((s) => s.enterSession);
  const openImport = useStore((s) => s.openImport);
  const changeLibRoot = useStore((s) => s.changeLibRoot);
  const addExtraRoot = useStore((s) => s.addExtraRootDir);
  const removeExtraRoot = useStore((s) => s.removeExtraRootDir);
  const libRoot = useStore((s) => s.libRoot);
  const busy = useStore((s) => s.busy);
  const running = useStore((s) => s.importRunning);

  const pickLib = async () => {
    const p = await API.pickDirectory();
    if (p) await changeLibRoot(p);
  };

  const addDir = async () => {
    const p = await API.pickDirectory();
    if (p) await addExtraRoot(p);
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
      {/* ---- 顶部动作：导入 / 加入目录 / 换图库 ---- */}
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
        <Button
          size="1"
          variant="ghost"
          data-add-dir
          disabled={busy}
          onClick={addDir}
          title="把硬盘上已有的照片文件夹挂进这个列表（原地读，不复制一份）"
        >
          加入目录
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
          插卡后点上面的「导入照片」；
          <br />
          片子已经在硬盘上，就点「加入目录」。
        </Text>
      ) : (
        sessions.map((s) => {
          const on = s.name === sessionName;
          const gone = !!s.missing;
          return (
            <Flex
              key={s.name}
              align="stretch"
              style={{
                background: on ? 'var(--bg-hover)' : 'transparent',
                borderLeft: on ? '3px solid var(--accent)' : '3px solid transparent',
                transition: 'background .12s',
              }}
            >
              <button
                data-session={s.name}
                data-session-ext={s.external ? '1' : undefined}
                data-session-gone={gone ? '1' : undefined}
                disabled={gone}
                onClick={() => enter(s.name)}
                title={gone ? `目录不在了：${s.path || ''}` : s.path || s.name}
                style={{
                  flex: 1,
                  minWidth: 0,
                  border: 0,
                  textAlign: 'left',
                  cursor: gone ? 'not-allowed' : 'pointer',
                  padding: '7px 12px',
                  fontSize: 12.5,
                  color: gone ? 'var(--text-faint)' : on ? 'var(--text)' : 'var(--text-dim)',
                  background: 'transparent',
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
                  {/* 库外要一眼看得出（库里的主题和"就地读的外部目录"混在一起最容易搞错） */}
                  {s.external && (
                    <span
                      data-session-badge
                      style={{
                        marginLeft: 6,
                        fontSize: 9.5,
                        opacity: 0.7,
                        border: '1px solid var(--line)',
                        borderRadius: 3,
                        padding: '0 3px',
                      }}
                    >
                      {gone ? '找不到' : '库外'}
                    </span>
                  )}
                </div>
                {typeof s.count === 'number' && !gone && (
                  <div style={{ fontSize: 10.5, opacity: 0.6 }}>{s.count} 张</div>
                )}
              </button>
              {s.external && (
                <button
                  data-session-del={s.rootDir || s.path || s.name}
                  disabled={busy || running}
                  title={'从列表移除（不删任何文件）\n' + (s.rootDir || s.path || '')}
                  onClick={() => {
                    const d = s.rootDir || s.path;
                    if (d) removeExtraRoot(d);
                  }}
                  style={{
                    border: 0,
                    background: 'transparent',
                    color: 'var(--text-faint)',
                    cursor: 'pointer',
                    padding: '0 8px',
                    fontSize: 13,
                    lineHeight: 1,
                    flex: '0 0 auto',
                  }}
                >
                  ×
                </button>
              )}
            </Flex>
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
