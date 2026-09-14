import { useEffect, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { useStore } from '../store/useStore';
import { Stars } from './Stars';

/**
 * 选片台右栏 —— 当前照片的参数信息（只读）。
 * 老版的 paramPane（选片台参数栏），React 重写时补上（09-15 SV：选中图片右侧参数栏没出来）。
 */
export function PickPanel() {
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const sessionPath = useStore((s) => s.sessionPath);

  const p = photos[cur];
  const [exif, setExif] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (!p) return;
    let alive = true;
    setExif(null);
    API.getExif(sessionPath, p.rel).then((e) => alive && setExif(e));
    return () => {
      alive = false;
    };
  }, [p, sessionPath]);

  if (!p) return null;

  /* ★ 全部 EXIF 都列（SV 09-15：把所有的相机信息全部都列出来）。
     键名对应 main.js humanizeExif() 的实际产出（别猜 —— 猜错过一轮）。 */
  const LABEL: Record<string, string> = {
    camera: '相机',
    lens: '镜头',
    focal: '焦距',
    aperture: '光圈',
    shutter: '快门',
    iso: '感光度',
    bias: '曝光补偿',
    flash: '闪光灯',
    wb: '白平衡',
    k: '色温',
    time: '拍摄时间',
    size: '原图尺寸',
  };
  const rows: [string, string][] = [];
  for (const [k, label] of Object.entries(LABEL)) {
    const v = exif?.[k];
    if (v !== undefined && v !== null && v !== '') rows.push([label, String(v)]);
  }
  if (p.hasRaw) rows.push(['RAW', '有（RAF）']);
  if (p.archived) rows.push(['归档', '已归档']);

  return (
    <Flex
      direction="column"
      style={{
        width: 240,
        flex: '0 0 auto',
        borderLeft: '1px solid var(--line)',
        background: 'var(--bg-panel)',
        overflow: 'auto',
        padding: 12,
        gap: 10,
      }}
    >
      {/* 文件名 + 星级 */}
      <div>
        <Text
          size="1"
          style={{
            display: 'block',
            color: 'var(--text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {p.name}
        </Text>
        <div style={{ marginTop: 4 }}>
          <Stars />
        </div>
      </div>

      {/* 参数表 */}
      <Flex direction="column" gap="1">
        {rows.length === 0 && (
          <Text size="1" style={{ color: 'var(--text-faint)' }}>
            读取参数中…
          </Text>
        )}
        {rows.map(([k, v]) => (
          <Flex key={k} justify="between" align="baseline" gap="2">
            <Text size="1" style={{ color: 'var(--text-dim)', flex: '0 0 auto' }}>
              {k}
            </Text>
            <Text
              size="1"
              style={{
                textAlign: 'right',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {v}
            </Text>
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
}
