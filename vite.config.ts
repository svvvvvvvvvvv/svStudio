import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// ⚠⚠ svStudio 是 Electron 用 `win.loadFile('renderer/index.html')` 加载的
//   ⇒ 走 **file:// 协议**。file:// 下有两个硬约束，配错就是白屏：
//   ① base 必须 './'（相对路径）；写 '/assets/x.js' 会解析成 file:///assets/x.js → 404
//   ② **ES module 会被 CORS 拦死**（<script type="module"> 在 file:// 下不执行）
//      ⇒ 必须输出 IIFE。
//
// ★ 为什么用 lib 模式而不是普通 html 入口：
//   普通模式下 Vite 只认 `<script type="module">`，如果写成普通 script 它会
//   **跳过打包**（实测产物 0.03 kB，空的）。lib 模式直接产一个 IIFE 文件，
//   index.html 由我们自己手写（见 renderer/index.html），不经过 Vite。
export default defineConfig({
  plugins: [react()],
  base: './',
  // ★★ lib 模式**不会**自动替换 process.env.NODE_ENV（普通模式才会），
  //   而渲染进程 nodeIntegration:false ⇒ process 不存在 ⇒ 一执行就崩、黑屏。
  //   必须在这里显式替换掉（09-15 黑屏就是这个）。
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  build: {
    outDir: 'renderer/dist',
    emptyOutDir: true,
    lib: {
      entry: 'src/main.tsx',
      formats: ['iife'],
      name: 'svStudioApp',
      fileName: () => 'index.js',
    },
    // CSS 单独成文（lib 模式下不内联），renderer/index.html 里手动引
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        assetFileNames: 'index[extname]',
        inlineDynamicImports: true,
      },
    },
  },
});
