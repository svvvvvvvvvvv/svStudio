import { Button, Flex, Text } from '@radix-ui/themes';
import { useState } from 'react';
import type { DragEvent } from 'react';
import { API } from '../api';
import { useStore } from '../store/useStore';

/**
 * 左侧「图库目录」栏 —— 09-15 SV 选「B」之后的模型：
 *
 *   ★★ **「图库根」这一层没有了**。这一栏列的就是「你加过的文件夹」。
 *      老模型是"先设一个大库、库底下分一次次拍摄"，在"我手上就是一个文件夹、
 *      照片直接躺在里面"的时候直接崩：库根自己列不出来（旧 `scanSessions` 只认它的子目录），
 *      而「加入目录」又因为"这就是库根自己"被挡掉 ⇒ 锁死，界面上还看不出来 ——
 *      这就是 SV 说的"点了没反应 / 干嘛还要目录"。
 *
 *   ★ 一个加进来的文件夹可能列出**好几条**（规则在 main.js 的 `extraSessions`）：
 *       · 它自己就是照片目录（有 JPG / 有星级桶）⇒ 它自己一条，不再往下拆；
 *       · 它自己是"只有 RAW"⇒ 自己一条（靠预览小图列图）＋ 底下有照片的子目录也各一条；
 *       · 它只是一层壳（比如整个照片库）⇒ 把底下**有照片的子目录**各列一条。
 *       ⇒ "把整个照片库拖进来"和"把一次拍摄拖进来"是同一件事，不用先设什么库根。
 *
 *   ★ **加进来的两条路**：点上面的「加入目录」，或者**把文件夹直接拖进这一栏**。
 *
 *   ★ 两条布局规矩（别退回去）：
 *    ① **固定显示**：图库目录固定在左侧，进目录后也不消失，随时切。
 *       ⚠ 一开始是 `{sessionName && <SessionPane/>}` —— "空的时候左栏不显示"，
 *         于是入口永远点不到（空的时候恰恰最需要它）。
 *    ② 顶部**只放「加入目录」这一个按钮**（09-15 SV 定案）。
 *
 *   ⚠ 条目上的「×」**只从列表移除，不删任何文件**（按钮 title 里必须写清 ——
 *     用户看到 × 的第一反应是"会不会删我文件"）。
 *
 *   ⚠ `data-session-ext` **不是**"库外"的意思（B 之后每条都来自"加过的文件夹"）——
 *     留着它是让布局自检能把"加进来的文件夹"和 mock 里那两条默认条目**区分开**。
 *     哪天想删它，要连 `_check/layout_check.mjs` 里 6 处 `[data-session-ext]` 一起改。
 */
export function SessionPane() {
  const sessions = useStore((s) => s.sessions);
  const sessionName = useStore((s) => s.sessionName);
  const enter = useStore((s) => s.enterSession);
  const addExtraRoot = useStore((s) => s.addExtraRootDir);
  const removeExtraRoot = useStore((s) => s.removeExtraRootDir);
  const busy = useStore((s) => s.busy);
  const toast = useStore((s) => s.showToast);

  /* 拖拽经过时给个边框提示 —— 不然"能不能拖"全靠猜 */
  const [dragOn, setDragOn] = useState(false);

  const addDir = async () => {
    const p = await API.pickDirectory();
    if (p) await addExtraRoot(p);
  };

  /* ★ 拖拽加入：整栏都是投放区。
     ⚠ `dragover` 必须 preventDefault，否则浏览器不认这是合法投放点（drop 根本不来）；
       顺带把"拖个文件进窗口 = 用浏览器打开它"那条默认行为也堵住。 */
  const onDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  };
  const onDrop = async (e: DragEvent) => {
    e.preventDefault();
    setDragOn(false);
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    /* ⚠ Electron 33 没有 `File.path` ⇒ 必须走 preload 里那个 `getPathForFile` */
    const p = API.getPathForFile(f);
    if (!p) {
      toast('拖进来的不是本地文件夹（从"此电脑"里拖试试）');
      return;
    }
    await addExtraRoot(p);
  };

  return (
    <Flex
      direction="column"
      data-session-pane
      onDragOver={onDragOver}
      onDragEnter={() => setDragOn(true)}
      onDragLeave={() => setDragOn(false)}
      onDrop={onDrop}
      style={{
        width: 190,
        flex: '0 0 auto',
        borderRight: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        overflow: 'auto',
        paddingTop: 6,
        outline: dragOn ? '2px dashed var(--accent)' : 'none',
        outlineOffset: -4,
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
          这一栏还是空的。
          <br />
          把装着照片的文件夹拖进来，
          <br />
          或者点上面的「加入目录」选一个
          <br />
          —— 原地读，不复制一份。
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
                title={gone ? `文件夹不在了：${s.path || s.name}` : s.path || s.name}
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
                  {gone && (
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
                      找不到
                    </span>
                  )}
                </div>
                {typeof s.count === 'number' && !gone && (
                  <div style={{ fontSize: 10.5, opacity: 0.6 }}>
                    {s.count} 张
                    {/* ★ 每张 RAW 都要先有一张预览小图（从 RAW 抠机内 JPG）；
                        还没生完的时候点进去会是空的 —— 先把原因写在张数旁边。 */}
                    {s.needsIndex ? ' · 预览待生成' : ''}
                  </div>
                )}
              </button>
              {/* ⚠ 「×」只给"加过的文件夹"（`external`）—— 它的语义是"从列表里去掉这一条"，
                  不是"删这个文件夹里的照片"。别的来源的条目（自检 mock 里那两条）不该有它。 */}
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
    </Flex>
  );
}
