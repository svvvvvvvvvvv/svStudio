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
  const [dim, setDim] = useState<{ ow: number; oh: number } | null>(null);

  useEffect(() => {
    if (!p) return;
    let alive = true;
    setExif(null);
    setDim(null);
    API.getExif(sessionPath, p.rel).then((e) => alive && setExif(e));
    API.getThumbMeta(sessionPath, p.rel).then((d) => alive && setDim(d));
    return () => {
      alive = false;
    };
  }, [p, sessionPath]);

  if (!p) return null;

  const rows: [string, string][] = [];
  const add = (k: string, v: unknown) => {
    if (v !== undefined && v !== null && v !== '') rows.push([k, String(v)]);
  };
  add('相机', exif?.Make && exif?.Model ? `${exif.Make} ${exif.Model}` : exif?.Model || exif?.camera);
  add('镜头', exif?.LensModel || exif?.lens);
  add('焦距', exif?.FocalLength || exif?.fl);
  add('光圈', exif?.FNumber ? `f/${exif.FNumber}` : exif?.fnum ? `f/${exif.fnum}` : '');
  add('快门', exif?.ExposureTime || exif?.ss);
  add('ISO', exif?.ISO || exif?.iso);
  add('拍摄时间', exif?.DateTimeOriginal);
  if (dim?.ow) add('原图尺寸', `${dim.ow} × ${dim.oh}`);
  add('RAW', p.hasRaw ? '有（RAF）' : '无');
  if (p.archived) add('归档', '已归档');

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
