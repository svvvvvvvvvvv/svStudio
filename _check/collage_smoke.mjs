/* eslint-disable no-console */
/**
 * 拼图台端到端回归自检（自包含，不依赖外部测试图）。
 *
 * 真实 Edge + 注入 mock api，验证 11-04 SV 定的「搭配形式」：
 *   底栏点图入组 → 缩略图打「组 N」角标 → 加到别的组会撤销旧组分配
 *   → 点一次导出把所有非空组一起出 → 每组文件名按该组图名拼 → PNG 逐像素无损。
 *
 * 测试图由浏览器当场画成纯色 200×100（不内嵌 base64，避免长串损坏造成假红）。
 * 用法：node _check/collage_smoke.mjs（没装 Edge 时跳过）
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright-core';

const ROOT = path.resolve('.');
const PAGE = path.join(ROOT, 'renderer', 'index.html');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const OUT = path.join(os.tmpdir(), 'svstudio-collage-smoke.png');

let pass = 0, fail = 0;
const ok = (n, d = '') => { pass++; console.log(`  ok   ${n}${d ? '   ' + d : ''}`); };
const bad = (n, d = '') => { fail++; console.log(`  FAIL ${n}${d ? '   ' + d : ''}`); };
const check = (n, c, d = '') => (c ? ok(n, d) : bad(n, d));

const MOCK_HEAD = `
const mkPng = (r, g, b) => {
  const c = document.createElement('canvas');
  c.width = 200; c.height = 100;
  const x = c.getContext('2d');
  x.fillStyle = 'rgb(' + r + ',' + g + ',' + b + ')';
  x.fillRect(0, 0, 200, 100);
  return c.toDataURL('image/png');
};
const URL_A = mkPng(220, 40, 40);
const URL_B = mkPng(40, 80, 220);
`;

const mock = MOCK_HEAD + `
window.__saves = [];
window.api = {
  logLine: async () => true,
  getConfig: async () => ({ lastSession: '测试目录', lastCur: 0, lastMode: 'pick', ratings: {} }),
  setConfig: async () => ({}),
  pickDirectory: async () => null,
  getPathForFile: () => '',
  scanSessions: async () => [{ name: '测试目录', path: 'D:/Photos' }],
  listPhotos: async () => [
    { name: 'A 001 (1).jpg', rel: 'A_001_1', dir: 'D:/Photos' },
    { name: 'B 002.tif',     rel: 'B002',    dir: 'D:/Photos' },
    { name: 'C 003.jpg',     rel: 'C003',    dir: 'D:/Photos' }
  ],
  getThumb: async () => ({ url: URL_A, ow: 200, oh: 100 }),
  getExif: async () => null,
  saveRatings: async () => true,
  writeRatingsJson: async () => ({ ok: true, count: 0 }),
  readRatingsJson: async () => ({ ok: true, ratings: {} }),
  onRatingsJsonChanged: () => {},
  archivePhotos: async () => ({}), confirmDialog: async () => true,
  resetColorGrade: async () => ({}),
  engineHealth: async () => ({ ok: false }), engineStart: async () => ({ ok: false }),
  engineStocks: async () => ({ items: [] }), engineLoad: async () => ({ ok: false }),
  engineBase: async () => ({ ok: false }), engineRender: async () => ({ ok: false }),
  getGrade: async () => null, setGrade: async () => true,
  exportImage: async () => ({ ok: false }), exportBatch: async () => ({ ok: false }),
  exportBatchCancel: async () => true, onExportBatchProgress: () => () => {},
  extIndex: async () => ({ ok: true, n: 0 }), onExtIndexProgress: () => () => {},
  collagePickFiles: async () => [],
  collageReadImage: async (o) => {
    const k = String((o && o.rel) || (o && o.path) || '');
    if (k.indexOf('A_001') >= 0 || k.indexOf('A 001') >= 0)
      return { ok: true, url: URL_A, w: 200, h: 100, ow: 200, oh: 100 };
    if (k.indexOf('B002') >= 0 || k.indexOf('B 002') >= 0)
      return { ok: true, url: URL_B, w: 200, h: 100, ow: 200, oh: 100 };
    return { ok: true, url: URL_A, w: 200, h: 100, ow: 200, oh: 100 };
  },
  collageSave: async (o) => {
    window.__saves.push(o);
    return { ok: true, path: 'D:/Photos/Collage/' + o.name + '.png',
             dir: 'D:/Photos/Collage', name: o.name + '.png',
             bytes: (o.dataUrl || '').length };
  }
};
`;

/** 在浏览器里把一张 PNG dataURL 解出来，逐像素比对上下两格（也验证空格不留占位文字） */
async function pixelsOf(page, dataUrl, upRgb, downRgb) {
  return page.evaluate(async ({ u, upRgb, downRgb }) => {
    const im = new Image();
    await new Promise((res, rej) => { im.onload = res; im.onerror = rej; im.src = u; });
    const c = document.createElement('canvas');
    c.width = im.width; c.height = im.height;
    const x = c.getContext('2d'); x.drawImage(im, 0, 0);
    const d = x.getImageData(0, 0, c.width, c.height).data;
    const px = (xx, yy) => { const i = (yy * c.width + xx) * 4; return [d[i], d[i + 1], d[i + 2]]; };
    const same = (x0, y0, w, h, rgb) => {
      for (let yy = y0; yy < y0 + h; yy++)
        for (let xx = x0; xx < x0 + w; xx++) {
          const p = px(xx, yy);
          if (p[0] !== rgb[0] || p[1] !== rgb[1] || p[2] !== rgb[2]) return false;
        }
      return true;
    };
    return {
      w: c.width, h: c.height,
      corner: px(0, 0), gapPx: px(108, 112),
      up: same(8, 8, 200, 100, upRgb),
      down: same(8, 116, 200, 100, downRgb)
    };
  }, { u: dataUrl, upRgb, downRgb });
}

console.log('\n=== svStudio 拼图台（分组搭配）端到端自检 ===\n');
if (!fs.existsSync(EDGE)) { console.log('  跳过：本机没找到 Edge'); process.exit(0); }
if (!fs.existsSync(PAGE)) { console.log('  跳过：renderer/index.html 不存在'); process.exit(0); }

const run = async () => {
  const browser = await chromium.launch({ executablePath: EDGE, headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e)));
  await page.addInitScript({ content: mock });
  await page.goto('file:///' + PAGE.replace(/\\/g, '/'));
  await page.waitForTimeout(900);

  /* ---- ① 切台 + 底栏有片 ---- */
  const tab = page.locator('button', { hasText: /^拼图台$/ });
  check('顶栏有「拼图台」第三个台', (await tab.count()) === 1);
  await tab.first().click();
  await page.waitForTimeout(350);
  check('底栏缩略图在（有目录也能选片）', (await page.locator('[data-idx]').count()) >= 3);
  let txt = await page.locator('body').innerText();
  check('组条在', txt.includes('拼图组') && txt.includes('组 1'));
  check('空状态说清为什么（不是白屏）', txt.includes('是空的'));
  check('布局模板条在（9 个模板）', txt.includes('九宫格') && txt.includes('滑动对比'));
  check('右栏参数齐全', txt.includes('间距与边框') && txt.includes('画质'));
  check('默认 PNG 无损', txt.includes('PNG 无损'));

  /* ---- ② 从底栏点两张进组 1 ---- */
  await page.locator('[data-idx="0"]').first().click();
  await page.waitForTimeout(250);
  await page.locator('[data-idx="1"]').first().click();
  await page.waitForTimeout(700);
  txt = await page.locator('body').innerText();
  check('组 1 放进了 2 张', /组 1 已放 2\/2 格/.test(txt), (txt.match(/组 1[^\n]*/) || [''])[0]);
  check('★ 底栏缩略图打了「组 1」角标',
    (await page.locator('[data-collage-group="1"]').count()) === 2,
    `${await page.locator('[data-collage-group="1"]').count()} 个`);
  const size = await page.locator('canvas').first().evaluate((c) => [c.width, c.height]);
  check('预览画布尺寸 = 200+2×8 / 100×2+8+16', size[0] === 216 && size[1] === 224,
    `${size[0]}×${size[1]}`);

  /* ---- ③ 新建组 2，把第 1 张改分到组 2 ⇒ 组 1 自动撤掉 ---- */
  await page.locator('button', { hasText: /^\+ 新组$/ }).first().click();
  await page.waitForTimeout(300);
  txt = await page.locator('body').innerText();
  check('新建组 2 并自动切过去', txt.includes('组 2') && /组 2 是空的/.test(txt));
  await page.locator('[data-idx="0"]').first().click();
  await page.waitForTimeout(700);
  const g1 = await page.locator('[data-collage-group="1"]').count();
  const g2 = await page.locator('[data-collage-group="2"]').count();
  check('★ 一张片只属于一个组（加到组 2 就撤掉组 1 的分配）',
    g1 === 1 && g2 === 1, `组1角标 ${g1} 个 / 组2角标 ${g2} 个`);
  txt = await page.locator('body').innerText();
  check('组条上两组各有 1 张', /共 2 组 · 已分配 2 张/.test(txt),
    (txt.match(/共 2 组[^\n]*/) || [''])[0]);
  check('点导出会一起出 2 张', /点导出会一起出 2 张/.test(txt));

  /* ---- ④ 一次导出 = 两组各出一张 ---- */
  const expBtn = page.locator('button', { hasText: /导出全部/ });
  check('导出按钮写明「导出全部 N 个拼图组」', (await expBtn.count()) === 1);
  await expBtn.first().click();
  await page.waitForTimeout(1800);
  const saves = await page.evaluate(() => window.__saves || []);
  check('★★ 一次点击把两个组都导出了', saves.length === 2, `${saves.length} 次`);
  const names = saves.map((s) => String(s.name || '')).sort();
  check('★ 每组文件名 = 该组图名（不是布局名/时间戳）',
    names.includes('B_002') && names.includes('A_001_1'), names.join(' , '));
  check('★ 文件名无特殊符号',
    names.every((x) => /^[A-Za-z0-9_\-\u4e00-\u9fa5]+$/.test(x)), names.join(' , '));
  check('★ 导出目录 = 源图同目录（不弹保存框）',
    saves.every((s) => s.dir === 'D:/Photos'), saves.map((s) => s.dir).join(' , '));
  check('★ 两组都产出 PNG',
    saves.every((s) => String(s.dataUrl || '').startsWith('data:image/png;base64,')));

  /* ---- ⑤ 逐像素：组 1 = 上格空/下格蓝，组 2 = 上格红/下格空 ---- */
  if (saves.length === 2) {
    const byName = Object.fromEntries(saves.map((s) => [s.name, s.dataUrl]));
    const white = [255, 255, 255], red = [220, 40, 40], blue = [40, 80, 220];
    const p1 = await pixelsOf(page, byName['B_002'], white, blue);
    check('★ 组 1：图区与源图逐像素相同（无损）', p1.down && p1.w === 216 && p1.h === 224,
      `${p1.w}×${p1.h}`);
    check('★ 没放满的组：空格只留背景，不把占位文字印进成品', p1.up,
      '上格应整块纯白');
    check('四周留白 / 缝隙是纯白',
      p1.corner.join(',') === '255,255,255' && p1.gapPx.join(',') === '255,255,255');
    const p2 = await pixelsOf(page, byName['A_001_1'], red, white);
    check('★ 组 2：红图在上格、空格纯白且无损', p2.up && p2.down);
    fs.writeFileSync(OUT, Buffer.from(saves[0].dataUrl.split(',')[1], 'base64'));
  }
  check('整轮没有未捕获报错', errs.length === 0, errs.slice(0, 2).join(' | '));
  await browser.close();

  /* ---- ⑥ 主进程落盘逻辑真跑一遍（不靠 mock）---- */
  const mainSrc = fs.readFileSync(path.join(ROOT, 'main.js'), 'utf8');
  const start = mainSrc.indexOf("ipcMain.handle('collage-save'");
  const end = mainSrc.indexOf('\n});', start);
  const handlerSrc = start > 0 && end > start ? mainSrc.slice(start, end + 4) : '';
  const nameFn = (mainSrc.match(/function safeCollageName\(s\) \{[\s\S]*?\n\}/) || [''])[0];
  const subConst = (mainSrc.match(/const COLLAGE_SUBDIR = '[^']+';/) || [''])[0];
  const diskRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'svstudio-collage-save-'));
  const handlers = {};
  check('能抽出主进程 collage-save 处理函数', !!handlerSrc && !!nameFn && !!subConst);
  if (handlerSrc && nameFn && subConst && fs.existsSync(OUT)) {
    const forbiddenDialog = { showSaveDialog: () => { throw new Error('不该弹保存框'); } };
    new Function('ipcMain', 'path', 'fs', 'app', 'dialog', 'Buffer',
      subConst + '\n' + nameFn + '\n' + handlerSrc)(
        { handle: (n, h) => { handlers[n] = h; } },
        path, fs, { getPath: () => diskRoot }, forbiddenDialog, Buffer
      );
    const pngBuf = fs.readFileSync(OUT);
    const payload = {
      dataUrl: 'data:image/png;base64,' + pngBuf.toString('base64'),
      name: 'A 001 (1)_and_B#2.JPG',
      dir: diskRoot, w: 216, h: 224
    };
    const r1 = await handlers['collage-save'](null, payload);
    const r2 = await handlers['collage-save'](null, payload);
    check('★ 主进程不弹框、自动建 Collage 子目录并写盘',
      !!r1.ok && fs.existsSync(path.join(diskRoot, 'Collage', 'A_001_1_and_B_2.png')),
      r1.path || r1.error || '(没返回)');
    check('★ 文件名里的空格/括号/# 全被清洗',
      !!r1.ok && r1.name === 'A_001_1_and_B_2.png', r1.name || '');
    check('★ 重名自动加 _2，不覆盖上一张',
      !!r2.ok && r2.name === 'A_001_1_and_B_2_2.png', r2.name || '');
    const back = fs.readFileSync(path.join(diskRoot, 'Collage', 'A_001_1_and_B_2.png'));
    check('★ 写盘的 PNG 字节与导出数据完全一致', back.equals(pngBuf),
      `${back.length} vs ${pngBuf.length}`);
  }

  console.log(`\n--------------------------------------------------\n${fail ? fail + ' 项失败' : '全部通过'} / 共 ${pass + fail} 项\n--------------------------------------------------`);
  process.exit(fail ? 1 : 0);
};

run().catch((e) => { console.error('SMOKE_FAIL', e); process.exit(1); });
