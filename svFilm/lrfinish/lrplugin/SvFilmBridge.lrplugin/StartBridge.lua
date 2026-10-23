-- 手动启动文件队列 bridge。
-- 用于 LrInitPlugin 在启动时没被触发（或上次 start 失败留下标记）的情况：
-- 文件 > 增效工具额外功能 > svFilm: Start bridge 点一次即可。
_G.__svfilm_bridge_running = nil

local Bridge = require 'BridgeServer'
Bridge.start()

local LOG = 'E:\\lr_agent\\lr_cmd\\plugin.log'
local f = io.open(LOG, 'a')
if f then
  f:write(os.date('%H:%M:%S ') .. 'manual start invoked\n')
  f:close()
end
