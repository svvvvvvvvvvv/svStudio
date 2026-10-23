-- 探针 + 启动。先确认 LrInitPlugin 到底有没有被执行，再起 bridge。
local LrTasks = import 'LrTasks'
local LrFileUtils = import 'LrFileUtils'
local LrPathUtils = import 'LrPathUtils'

local PLUGIN_DIR = _PLUGIN and _PLUGIN.path or nil
local PROBE = (PLUGIN_DIR or 'C:/') .. '/init_probe.txt'
local LOG = 'E:/lr_agent/lr_cmd/plugin.log'

local function stamp()
  return tostring(os.date('%H:%M:%S'))
end

local function note(tag)
  local msg = stamp() .. ' ' .. tag .. '\n'
  LrTasks.pcall(function()
    LrFileUtils.writeFile(PROBE, msg)
  end)
  LrTasks.pcall(function()
    local f = io.open(LOG, 'a')
    if f then f:write(msg); f:close() end
  end)
end

note('init: enter, plugin=' .. tostring(PLUGIN_DIR))

_G.__svfilm_bridge_running = nil

local ok, err = LrTasks.pcall(function()
  local Bridge = require 'BridgeServer'
  Bridge.start()
end)
note(ok and 'init: start() called' or ('init: start() failed -> ' .. tostring(err)))
