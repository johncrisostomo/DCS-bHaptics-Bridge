-- DCS-bHapticsBridge-Export.lua
--
-- Streams ownship telemetry from DCS World to the dcs-bhaptics-bridge
-- companion app over UDP on 127.0.0.1. Nothing leaves the local machine.
--
-- Install: copy this file to  %USERPROFILE%\Saved Games\DCS\Scripts\
-- and add this line at the end of Export.lua in the same folder:
--   dofile(lfs.writedir() .. [[Scripts\DCS-bHapticsBridge-Export.lua]])
--
-- Any export hooks that were already defined (SRS, Tacview, ...) are
-- chained and keep working.

local HOST = "127.0.0.1"
local PORT = 41231
local INTERVAL = 0.05 -- 20 Hz

-- Capture whatever hooks other scripts defined before us.
local prevStart = LuaExportStart
local prevStop = LuaExportStop
local prevBefore = LuaExportBeforeNextFrame
local prevAfter = LuaExportAfterNextFrame
local prevActivity = LuaExportActivityNextEvent

local socket = nil
local udp = nil
local nextSend = 0

local function try(f)
  if f == nil then return nil end
  local ok, v = pcall(f)
  if ok then return v end
  return nil
end

local function num(v, decimals)
  if type(v) ~= "number" then v = 0 end
  return string.format("%." .. (decimals or 3) .. "f", v)
end

local function field(t, a, b)
  -- Defensive table read: field(t, "x") or positional fallback field(t, 1, "shells")
  if type(t) ~= "table" then return nil end
  if b ~= nil then
    if t[b] ~= nil then return t[b] end
    return t[a]
  end
  return t[a]
end

local function collectPacket()
  local self = try(LoGetSelfData)
  if self == nil or self.Name == nil then
    return "DBB1;ac=0"
  end

  local p = { "DBB1", "ac=1" }
  local function add(k, v) p[#p + 1] = k .. "=" .. v end

  add("name", tostring(self.Name):gsub("[;=%s]", "_"))
  add("t", num(try(function() return LoGetModelTime() end), 2))

  local acc = try(LoGetAccelerationUnits)
  add("gx", num(field(acc, "x")))
  add("gy", num(field(acc, "y")))
  add("gz", num(field(acc, "z")))

  add("ias", num(try(LoGetIndicatedAirSpeed), 2))
  add("tas", num(try(LoGetTrueAirSpeed), 2))
  add("agl", num(try(LoGetAltitudeAboveGroundLevel), 2))
  add("asl", num(try(LoGetAltitudeAboveSeaLevel), 1))
  add("vv", num(try(LoGetVerticalVelocity), 2))
  add("aoa", num(try(LoGetAngleOfAttack), 4))
  add("mach", num(try(LoGetMachNumber), 3))

  local eng = try(LoGetEngineInfo)
  local rpm = field(eng, "RPM")
  add("rpm1", num(field(rpm, 1), 1))
  add("rpm2", num(field(rpm, 2), 1))
  add("fuel", num(field(eng, "fuel_internal"), 0))

  local mech = try(LoGetMechInfo)
  add("gear", num(field(field(mech, "gear"), "value")))
  add("flaps", num(field(field(mech, "flaps"), "value")))
  add("sbrake", num(field(field(mech, "speedbrakes"), "value")))
  add("canopy", num(field(field(mech, "canopy"), "value")))

  -- Draw arguments (community-documented, corroborated by DCS-BIOS/FlyPT):
  -- strut compression 1 = nose, 4 = right, 6 = left; burner 29 = left, 28 = right.
  local draw = nil
  if type(LoGetAircraftDrawArgumentValue) == "function" then draw = LoGetAircraftDrawArgumentValue end
  add("wow1", num(try(function() return draw(1) end)))
  add("wow4", num(try(function() return draw(4) end)))
  add("wow6", num(try(function() return draw(6) end)))
  add("ab1", num(try(function() return draw(29) end)))
  add("ab2", num(try(function() return draw(28) end)))

  local payload = try(LoGetPayloadInfo)
  local cannon = field(payload, "Cannon")
  add("shells", tostring(math.floor(field(cannon, 1, "shells") or 0)))
  local total = 0
  local counts = {}
  local stations = field(payload, "Stations")
  if type(stations) == "table" then
    for _, st in pairs(stations) do
      local c = 0
      if type(st) == "table" and type(st.count) == "number" then c = st.count end
      counts[#counts + 1] = tostring(c)
      total = total + c
    end
  end
  add("stn", tostring(total))
  add("sta", table.concat(counts, ","))

  local snares = try(LoGetSnares)
  add("chaff", tostring(math.floor(field(snares, "chaff", nil) or field(snares, 1) or 0)))
  add("flare", tostring(math.floor(field(snares, "flare", nil) or field(snares, 2) or 0)))

  local mcp = try(LoGetMCPState)
  local warns = {}
  if type(mcp) == "table" then
    for k, v in pairs(mcp) do
      if v == true then warns[#warns + 1] = k end
    end
  end
  table.sort(warns)
  add("warn", #warns > 0 and table.concat(warns, ",") or "-")

  return table.concat(p, ";")
end

local function sendPacket()
  if udp == nil then return end
  local ok, packet = pcall(collectPacket)
  if ok and packet then
    pcall(function() udp:send(packet) end)
  end
end

function LuaExportStart()
  if prevStart then pcall(prevStart) end
  local ok = pcall(function()
    package.path = package.path .. ";" .. lfs.currentdir() .. "/LuaSocket/?.lua"
    package.cpath = package.cpath .. ";" .. lfs.currentdir() .. "/LuaSocket/?.dll"
    socket = require("socket")
    udp = socket.udp()
    udp:settimeout(0)
    udp:setpeername(HOST, PORT)
  end)
  if not ok then
    udp = nil
  end
  nextSend = 0
end

function LuaExportStop()
  if udp ~= nil then
    pcall(function() udp:send("DBB1;ac=0") end)
    pcall(function() udp:close() end)
    udp = nil
  end
  if prevStop then pcall(prevStop) end
end

function LuaExportBeforeNextFrame()
  if prevBefore then pcall(prevBefore) end
end

function LuaExportAfterNextFrame()
  if prevAfter then pcall(prevAfter) end
end

function LuaExportActivityNextEvent(t)
  local tNext = t + INTERVAL
  if prevActivity then
    local ok, prevNext = pcall(prevActivity, t)
    if ok and type(prevNext) == "number" and prevNext > t and prevNext < tNext then
      tNext = prevNext
    end
  end
  if t >= nextSend then
    sendPacket()
    nextSend = t + INTERVAL
  end
  return tNext
end
