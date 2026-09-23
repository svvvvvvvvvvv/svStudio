import { Box, Button, Badge, Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { ratingKey, useStore } from '../store/useStore';

/**
 * 顶栏：切模式（选片台 / 调色台）+ 库路径 + 同步星级。
 * 所有按钮的 disabled 状态都由 store 派生 —— 不用再手写 updateArchiveBtn()。
 */
export function TopBar() {
  const mode = useStore((s) => s.mode);
  const setMode = useStore((s) => s.setMode);
  const sessionName = useStore((s) => s.sessionName);
  const sessionPath = useStore((s) => s.sessionPath);
  const goHome = useStore((s) => s.goHome);
  const ratings = useStore((s) => s.ratings);
  const photos = useStore((s) => s.photos);
  const busy = useStore((s) => s.busy);
  const setBusy = useStore((s) => s.setBusy);
  const showToast = useStore((s) => s.showToast);

  /** 同步星级：把当前目录里每张的星级与物理目录对齐 */
  const syncStars = async () => {
    if (!sessionName || !photos.length) return;
    setBusy(true, '同步星级…');
    try {
      const items = photos.map((p) => ({
        rel: p.rel,
        star: ratings[ratingKey(sessionName, p.rel)] || 0,
      }));
      const r = await API.archivePhotos({ dirPath: sessionPath, items });
      const nFail = r?.failed?.length || 0;
      showToast(`已同步 ${r?.done || 0} 张${nFail ? `，${nFail} 张失败` : ''}`);
    } catch (e) {
      showToast('同步失败：' + String(e));
    } finally {
      setBusy(false);
    }
  };

  /** 重置调色：清除调色输出与成片（原图/RAW 不动） */
  const resetColor = async () => {
    if (!sessionName) return;
    const ok = await API.confirmDialog({
      title: '重置调色',
      message: `确定要清除「${sessionName}」的调色输出与 2~5 星成片吗？`,
      detail: '原图与 RAW 不动，只清调色结果。换调色思路重跑前用。',
    });
    if (!ok) return;
    setBusy(true, '重置调色…');
    try {
      await API.resetColorGrade(sessionPath);
      showToast('已重置调色');
    } catch (e) {
      showToast('重置失败：' + String(e));
    } finally {
      setBusy(false);
    }
  };

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
          目录列表
        </Button>
      )}

      <Box style={{ flex: 1 }} />

      {/* 重置调色（危险操作，二次确认） */}
      {sessionName && (
        <Button size="1" variant="ghost" color="red" onClick={resetColor}>
          重置调色
        </Button>
      )}

      {/* 同步星级（物理归位）：把星级与目录同步 */}
      {sessionName && (
        <Button size="1" variant="soft" disabled={busy} onClick={syncStars}>
          同步星级
        </Button>
      )}

      {/* ★ 已打星 / 当前目录张数：直接从 ratings 派生，不用手动维护计数 */}
      <Badge color="gray">
        {photos.filter((p) => (ratings[ratingKey(sessionName, p.rel)] || 0) >= 1).length} /{' '}
        {photos.length}
      </Badge>
    </Flex>
  );
}
