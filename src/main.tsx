import '@radix-ui/themes/styles.css';
import './theme/global.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import { Theme } from '@radix-ui/themes';
import { App } from './components/App';
import { applyThemeVars } from './theme/apple';

/* 把我们那套苹果风变量写进 :root（给图墙/分屏/悬浮预览这些自定义部分用；
   Radix 组件自己走 <Theme>） */
applyThemeVars();

/* ---------------- ★★ 渲染层日志（09-15 新增） ----------------
   黑屏时 SV 不用截图 —— 所有未捕获错误 + 启动打点都写到
   E:\工作目录\_debug\_logs\svstudio_render.log，
   助理直接读文件定位。 */
const t0 = Date.now();
function log(line: string) {
  try {
    const dt = ((Date.now() - t0) / 1000).toFixed(2);
    (window as any).api?.logLine?.(`+${dt}s ${line}`);
  } catch { /* 日志失败不影响主流程 */ }
}

log('boot: 脚本开始执行');

window.addEventListener('error', (e) => {
  log(`ERROR: ${e.message} @ ${e.filename?.split('/').pop()}:${e.lineno}`);
});
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  log(`REJECT: ${r && r.stack ? r.stack.split('\n')[0] : String(r)}`);
});
const origErr = console.error.bind(console);
console.error = (...a: unknown[]) => {
  log('console.error: ' + a.map((x) => (x instanceof Error ? x.message : String(x))).join(' '));
  origErr(...a);
};

log(`环境探测: api=${!!(window as any).api} React=${React.version}`);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* Radix Themes：dark 外观 + 蓝色强调 + 大圆角 = 最接近苹果的那档配置 */}
    <Theme appearance="dark" accentColor="blue" grayColor="gray" radius="large" scaling="100%">
      <App />
    </Theme>
  </React.StrictMode>
);

log('boot: createRoot 已挂载（如果界面还是黑的，错误会出现在上面的 ERROR 行）');
