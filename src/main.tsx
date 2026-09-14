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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* Radix Themes：dark 外观 + 蓝色强调 + 大圆角 = 最接近苹果的那档配置 */}
    <Theme appearance="dark" accentColor="blue" grayColor="gray" radius="large" scaling="100%">
      <App />
    </Theme>
  </React.StrictMode>
);
