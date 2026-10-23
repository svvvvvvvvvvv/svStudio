# -*- coding: utf-8 -*-
"""把 E:\\lr_agent\\lrplugin\\SvFilmBridge.lrplugin 部署成 LR 能加载的插件。

背景（2026-10-21 实测）：
- 旧目录 Modules\\SvFilmBridge.lrplugin 被折腾到「已安装并正在运行、但 LrInitPlugin 永不执行」，
  换新目录 + 新 LrToolkitIdentifier 才恢复。部署目标固定为 SvFilmBridgeB.lrplugin。
- 插件文件改了必须重启 LR 才会生效（插件加载器不会热重载 init）。

用法:
    python deploy_plugin.py            # 只部署
    python deploy_plugin.py --restart  # 部署 + 重启 LR + 等桥就绪
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paths  # noqa: E402

SRC = str(paths.ROOT / "lrplugin" / "SvFilmBridge.lrplugin")
DST = str(paths.APPDATA / "Adobe" / "Lightroom" / "Modules" / "SvFilmBridgeB.lrplugin")
FILES = [
    "Info.lua",
    "PluginInit.lua",
    "BridgeServer.lua",
    "StartBridge.lua",
    "ApplyMetadata.lua",
    "ApplyAndExport.lua",
]


def deploy():
    if not os.path.isdir(DST):
        os.makedirs(DST)
    done = []
    for f in FILES:
        s = os.path.join(SRC, f)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(DST, f))
            done.append(f)
    return done


if __name__ == "__main__":
    print("deployed:", ", ".join(deploy()))
    if "--restart" in sys.argv:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from lr_boot import clear_console_log, clear_stale_lock, kill_lr, launch, wait_bridge

        print("killing lr ...", kill_lr())
        print("lock:", clear_stale_lock())
        # ★ 必须先删控制台日志：文件存在时 LR 启动要改写它，被拦就 __fastfail 自杀
        print("console log:", clear_console_log())
        launch()
        ok, detail = wait_bridge()
        print("BRIDGE OK" if ok else "BRIDGE FAIL", detail)
        sys.exit(0 if ok else 1)
