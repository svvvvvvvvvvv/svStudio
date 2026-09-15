import { Button, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';

/**
 * 左侧图库目录栏。
 *
 * ★★ 09-15 SV 定案：**只有一条路** ——
 *   照片已经在硬盘上，就把它那个目录「加入目录」挂进这个列表（**原地读、不复制**）。
 *   早先那套「导入照片」（插卡 / U 盘 → 复制进库 → 按「日期_主题_地点」建档）**已整条删掉**：
 *   多一条复制路径就多一份"两边规则慢慢长歪"的风险，而 SV 的用法本来就是「RAW 在哪就在哪看」。
 *
 * ★ 两条布局规矩（别退回去）：
 *  ① **固定显示**：图库目录固定在左侧，进主题后也不消失，随时切主题。
 *     ⚠ 一开始是 `{sessionName && <SessionPane/>}` —— "空库 / 新库没有主题 ⇒ 左栏不显示"，
 *       于是入口永远点不到（新库恰恰最需要它）。
 *  ② 顶部**只放「加入目录」这一个按钮**。「换图库」降到最底下那一行小字里
 *     —— 它跟"当前库根是哪个"本来就是同一件事，摆一起才对。
 *
 * ⚠ 库外条目上的「×」**只从列表移除，不删任何文件**（按钮 title 里必须写清 ——
 *   用户看到 × 的第一反应是"会不会删我文件"）。
 */
export function SessionPane() {
  const sessions = useStore((s) => s.sessions);
  const sessionName = useStore((s) => s.sessionName);
  const enter = useStore((s) => s.enterSession);
  const changeLibRoot = useStore((s) => s.changeLibRoot);
  const addExtraRoot = useStore((s) => s.addExtraRootDir);
  const removeExtraRoot = useStore((s) => s.removeExtraRootDir);
  const libRoot = useStore((s) => s.libRoot);
  const busy = useStore((s) => s.busy);

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
      {/* ---- 顶部动作：**只有「加入目录」这一个按钮**（09-15 SV 定案） ---- */}
      <Flex direction="column" gap="1" px="2" pb="2" data-lib-actions>
        <Button
          size="1"
          variant="soft"
          data-add-dir
          disabled={busy}
          onClick={addDir}
          title="把硬盘上已有的照片文件夹挂进这个列表（原地读，不复制一份）"
        >
          加入目录
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
          照片已经在硬盘上，就点上面的「加入目录」，
          <br />
          把它那个文件夹挂进这一栏（原地读，不复制）。
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
                title={
                  gone
                    ? `目录不在了：${s.srcDir || s.path || ''}`
                    : s.rawOnly
                      ? `源目录：${s.srcDir || ''}\n（这个目录只有 RAW，工作台靠它的预览小图列图）`
                      : s.path || s.name
                }
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
                  <div style={{ fontSize: 10.5, opacity: 0.6 }}>
                    {s.count} 张
                    {/* ★ "只有 RAW"的目录要靠预览小图才能列出来；还没生成的时候
                        点进去会是空的 —— 先把原因写在张数旁边，别让人疑心戏撞。 */}
                    {s.needsIndex ? ' · 预览待生成' : ''}
                  </div>
                )}
              </button>
              {s.external && (
                <button
                  data-session-del={s.rootDir || s.path || s.name}
                  disabled={busy}
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

      {/* 库根 + 换图库：切错盘 / 看不着主题时，这一行是唯一的线索。
          「换图库」放这儿而不放顶栏 —— 它和"当前库根是哪个"本来就是同一件事。 */}
      <Flex
        align="center"
        gap="1"
        px="2"
        data-lib-root-row
        style={{ marginTop: 'auto', paddingTop: 10, paddingBottom: 10 }}
      >
        <Text
          size="1"
          data-lib-root
          title={libRoot}
          style={{
            color: 'var(--text-faint)',
            flex: 1,
            minWidth: 0,
            fontSize: 10.5,
            lineHeight: 1.5,
            wordBreak: 'break-all',
          }}
        >
          {libRoot || '（未设置图库）'}
        </Text>
        <Button
          size="1"
          variant="ghost"
          data-change-lib
          disabled={busy}
          onClick={pickLib}
          title="换一个照片库根目录（左栏列出来的就是它的子文件夹）"
          style={{ flex: '0 0 auto', fontSize: 10.5 }}
        >
          换图库
        </Button>
      </Flex>
    </Flex>
  );
}
