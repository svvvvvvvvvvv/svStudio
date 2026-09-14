import { useEffect, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { API } from '../api';
import { ratingKey, useStore, visiblePhotos } from '../store/useStore';
import type { Filter } from '../store/useStore';

/**
 * 星级条。
 * ★ React 化收益：老代码要 updateDockBadge + renderStars + renderDock 三处同步，
 *   现在只改 store 里一个 ratings，用到它的地方自动更新。
 */
export function Stars({ big = false }: { big?: boolean }) {
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const sessionName = useStore((s) => s.sessionName);
  const ratings = useStore((s) => s.ratings);
  const rate = useStore((s) => s.rate);

  const p = photos[cur];
  const v = p ? ratings[ratingKey(sessionName, p.name)] || 0 : 0;

  return (
    <Flex gap="1" align="center">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          onClick={() => rate(v === n ? 0 : n)}
          title={v === n ? '再点一次清星（0）' : `打 ${n} 星`}
          style={{
            border: 0,
            background: 'transparent',
            cursor: 'pointer',
            fontSize: big ? 26 : 18,
            lineHeight: 1,
            padding: 2,
            color: n <= v ? 'var(--c-yellow)' : 'var(--text-faint)',
          }}
        >
          {n <= v ? '★' : '☆'}
        </button>
      ))}
    </Flex>
  );
}

/** 筛选 chips（选片台专用；调色台不显示 —— 那边定死只收 ★≥1） */
export function FilterChips() {
  const filter = useStore((s) => s.filter);
  const setFilter = useStore((s) => s.setFilter);
  const photos = useStore((s) => s.photos);
  const ratings = useStore((s) => s.ratings);
  const sessionName = useStore((s) => s.sessionName);

  const opts: [Filter, string][] = [
    ['all', '全部'],
    ['unrated', '未评'],
    ['1', '1★'],
    ['2', '2★'],
    ['3', '3★'],
    ['4', '4★'],
    ['5', '5★'],
  ];

  return (
    <Flex gap="1" align="center" wrap="wrap">
      {opts.map(([k, label]) => {
        const n = visiblePhotos(photos, sessionName, ratings, k).length;
        const on = filter === k;
        return (
          <button
            key={k}
            onClick={() => setFilter(k)}
            style={{
              border: on ? '1px solid var(--accent)' : '1px solid var(--line)',
              background: on ? 'rgba(10,132,255,.12)' : 'transparent',
              color: on ? 'var(--accent)' : 'var(--text-dim)',
              borderRadius: 'var(--r-pill)',
              padding: '3px 11px',
              fontSize: 11.5,
              cursor: 'pointer',
            }}
          >
            {label} <span style={{ opacity: 0.65 }}>{n}</span>
          </button>
        );
      })}
    </Flex>
  );
}

/** EXIF 条（只读，异步懒加载） */
export function ExifBar() {
  const photos = useStore((s) => s.photos);
  const cur = useStore((s) => s.cur);
  const sessionPath = useStore((s) => s.sessionPath);

  const p = photos[cur];
  const [txt, setTxt] = useState('');

  useEffect(() => {
    if (!p) return setTxt('');
    let alive = true;
    setTxt('读取中…');
    API.getExif(sessionPath, p.rel).then((e) => {
      if (!alive) return;
      if (!e) return setTxt('—');
      const bits = [e.camera, e.lens, e.iso ? `ISO${e.iso}` : '', e.fnum ? `f/${e.fnum}` : '', e.ss, e.fl]
        .filter(Boolean);
      setTxt(bits.join(' · ') || '—');
    });
    return () => {
      alive = false;
    };
  }, [p, sessionPath]);

  return (
    <Text
      size="1"
      style={{
        color: 'var(--text-dim)',
        padding: '4px 12px',
        borderTop: '1px solid var(--line)',
        flex: '0 0 auto',
      }}
    >
      {txt}
    </Text>
  );
}
