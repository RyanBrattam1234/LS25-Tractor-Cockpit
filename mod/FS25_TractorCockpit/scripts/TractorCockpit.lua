-- ===========================================================================
--  FS25 Tractor Cockpit - Telemetrie-Bruecke (lesend) + Steuerkanal (Phase 4)
--  Kette: LS25 -> diese Mod -> Telemetriedatei/log.txt -> server.py -> Browser
--          Browser -> server.py -> Befehlsdatei -> diese Mod -> Fahrzeug
-- ===========================================================================
--  Ausgabekanaele (Telemetrie):
--
--  1) DATEI-KANAL (schnell, empfohlen)
--     <Spielprofil>/modSettings/FS25_TractorCockpit.txt
--     Wird alle FILE_INTERVAL_MS neu geschrieben (klein, kein Log-Wachstum).
--     Das gleiche Muster benutzt der Mod FS25_LiveMap in dieser Installation.
--
--  2) LOG-KANAL (Rueckfall und Protokoll)
--     TRACTORCOCKPIT|... in der LS25-log.txt. Laeuft der Datei-Kanal, wird nur
--     alle LOG_INTERVAL_MS_FILE_OK eine Zeile geschrieben, sonst jede Sekunde.
--
--  Eingangskanal (Steuerung, standardmaessig aktiv, aber nur mit Whitelist):
--     <Spielprofil>/modSettings/FS25_TractorCockpit_cmd.txt
--     Der Server schreibt genau eine Zeile:
--       TRACTORCMD|id=<nummer>|action=<name>|target=<index>|name=<geraetename>
--     Erlaubte Aktionen: lower, raise, toggleFold, turnOn, turnOff.
--     Es werden NUR diese benannten Aktionen ausgefuehrt - niemals freier Lua-
--     Code, niemals eine Zahl aus dem Netz. Jede Aktion wird vorher auf
--     Unterstuetzung durch das Geraet geprueft, und das Ergebnis steht als
--     cmdId/cmdResult/cmdMsg in der Telemetrie (echter Spielzustand).
--
--  Einheiten und verifizierte FS25-API (geprueft gegen die Spielskripte von
--  Farming Simulator 25 v1.24.0.0, sdk/debugger/gameSource.zip):
--    g_localPlayer:getCurrentVehicle()                -> Fahrzeug | nil
--    Vehicle:getName()                                -> Fahrzeugname
--    Vehicle:getLastSpeed()                           -> km/h
--    Motorized:getIsMotorStarted()                    -> true | false
--    Motorized:getMotorRpmReal()                      -> U/min
--    Motorized:getMotorLoadPercentage()               -> 0.0 .. 1.0
--    Motorized:getConsumerFillUnitIndex(FillType.*)   -> FillUnit-Index | nil
--    FillUnit:getFillUnitFillLevel/Capacity/FillType  -> Liter / FillType
--    VehicleMotor:getMinRpm()/getMaxRpm()             -> U/min
--    Vehicle:getOperatingTime()                       -> Millisekunden
--    AttacherJoints:getAttachedImplements()           -> Liste {object=...}
--    Vehicle:getIsLowered(), Foldable:getIsUnfolded()
--    TurnOnVehicle:getIsTurnedOn()
--    WorkArea:getIsWorkAreaActive(workArea)
--    FillUnit:getFillUnits()                          -> spec.fillUnits
--    getUserProfileAppPath()                          -> Spielprofilordner
--  Steuerung (alle Aufrufe so, wie das Spiel sie selbst benutzt):
--    AttacherJoints:handleLowerImplementEvent(implement, direction)
--    Foldable:setFoldDirection(direction) + getToggledFoldDirection()
--    TurnOnVehicle:setIsTurnedOn(isTurnedOn)
--    Vehicle:getAllowsLowering(), Foldable:getIsFoldAllowed()
-- ===========================================================================

TractorCockpit = {}
TractorCockpit.MOD_NAME = "TractorCockpit"
TractorCockpit.VERSION = "0.7.0"

-- --- Leistung / Datenrate -------------------------------------------------
-- Wie oft die Telemetriedatei neu geschrieben wird. 250 ms = 4x pro Sekunde
-- (FS25_LiveMap schreibt in dieser Installation alle 25 ms).
TractorCockpit.FILE_INTERVAL_MS = 250
-- Logzeile, wenn der Datei-Kanal laeuft: nur ein Lebenszeichen.
TractorCockpit.LOG_INTERVAL_MS_FILE_OK = 10000
-- Logzeile, wenn der Datei-Kanal nicht verfuegbar ist: voller Takt.
TractorCockpit.LOG_INTERVAL_MS_FALLBACK = 1000
-- Datei-Kanal komplett abschaltbar (dann nur log.txt).
TractorCockpit.FILE_ENABLED = true
TractorCockpit.FILE_NAME = "modSettings/FS25_TractorCockpit.txt"
-- Obergrenze fuer Anbaugeraete pro Zeile (begrenzt Rechenzeit und Zeilenlaenge).
TractorCockpit.MAX_IMPLEMENTS = 4
TractorCockpit.MAX_WORK_AREAS = 8

-- --- Steuerkanal (Phase 4) ------------------------------------------------
-- Auf false setzen, dann fuehrt die Mod keinerlei Befehle aus (harte Sperre
-- im Spiel selbst, unabhaengig davon, was der Server schickt).
TractorCockpit.CONTROL_ENABLED = true
-- FS25 erlaubt Mods kein io.open im Lesemodus ("only write mode ('w') is
-- allowed"). Deshalb ist die Befehlsdatei eine XML-Datei, die ueber die
-- XML-API des Spiels gelesen wird. Welcher Pfad wirklich lesbar ist, wird
-- beim Laden getestet (siehe detectCommandChannel).
TractorCockpit.COMMAND_FILE = "modSettings/FS25_TractorCockpit_cmd.xml"
TractorCockpit.COMMAND_POLL_MS = 250
-- Wenn die Datei fehlt oder nicht lesbar ist: erst nach dieser Pause wieder
-- versuchen, damit das Spiel-Log nicht zugemuellt wird.
TractorCockpit.COMMAND_READ_RETRY_MS = 5000
-- Wie lange ein Befehlsergebnis in der Telemetrie wiederholt wird.
TractorCockpit.COMMAND_RESULT_MS = 6000
-- Whitelist. Nur diese Namen werden ausgefuehrt.
TractorCockpit.COMMAND_ACTIONS = {
    lower = true,
    raise = true,
    toggleFold = true,
    turnOn = true,
    turnOff = true,
    -- Licht, Blinker und Rundumleuchte (Fahrzeug, target=0)
    lightDefaultOn = true,
    lightDefaultOff = true,
    lightHighBeamOn = true,
    lightHighBeamOff = true,
    lightWorkFrontOn = true,
    lightWorkFrontOff = true,
    lightWorkBackOn = true,
    lightWorkBackOff = true,
    lightNext = true,
    lightPrev = true,
    beaconOn = true,
    beaconOff = true,
    turnOff = true,
    turnLeft = true,
    turnRight = true,
    turnHazard = true
}

-- Lichtarten des Spiels (Konstanten aus Lights.lua, nicht hart kodiert).
TractorCockpit.LIGHT_ORDER = { "default", "highBeam", "workFront", "workBack" }
TractorCockpit.LIGHT_CONSTANTS = {
    default = "LIGHT_TYPE_DEFAULT",
    highBeam = "LIGHT_TYPE_HIGHBEAM",
    workFront = "LIGHT_TYPE_WORK_FRONT",
    workBack = "LIGHT_TYPE_WORK_BACK"
}

TractorCockpit.TURN_ORDER = { "off", "left", "right", "hazard" }
TractorCockpit.TURN_CONSTANTS = {
    off = "TURNLIGHT_OFF",
    left = "TURNLIGHT_LEFT",
    right = "TURNLIGHT_RIGHT",
    hazard = "TURNLIGHT_HAZARD"
}

-- Fahrzeugweite Aktionen: Licht, Lichtstufen, Rundumleuchte, Blinker.
TractorCockpit.VEHICLE_ACTIONS = {
    lightDefaultOn = { kind = "light", name = "default", value = true },
    lightDefaultOff = { kind = "light", name = "default", value = false },
    lightHighBeamOn = { kind = "light", name = "highBeam", value = true },
    lightHighBeamOff = { kind = "light", name = "highBeam", value = false },
    lightWorkFrontOn = { kind = "light", name = "workFront", value = true },
    lightWorkFrontOff = { kind = "light", name = "workFront", value = false },
    lightWorkBackOn = { kind = "light", name = "workBack", value = true },
    lightWorkBackOff = { kind = "light", name = "workBack", value = false },
    lightNext = { kind = "cycle", value = 1 },
    lightPrev = { kind = "cycle", value = -1 },
    beaconOn = { kind = "beacon", value = true },
    beaconOff = { kind = "beacon", value = false },
    turnOff = { kind = "turn", name = "off" },
    turnLeft = { kind = "turn", name = "left" },
    turnRight = { kind = "turn", name = "right" },
    turnHazard = { kind = "turn", name = "hazard" }
}

-- --- Laufzeitzustand ------------------------------------------------------
TractorCockpit.fileElapsedMs = 0
TractorCockpit.logElapsedMs = 0
TractorCockpit.filePath = nil
TractorCockpit.fileOk = false
TractorCockpit.fileChecked = false
TractorCockpit.fileErrorLogged = false
TractorCockpit.lastLine = nil

-- Steuerkanal
TractorCockpit.commandElapsedMs = 0
TractorCockpit.commandRetryMs = 0
TractorCockpit.commandReadErrors = 0
TractorCockpit.commandPathBase = nil
TractorCockpit.commandPath = nil
TractorCockpit.commandMode = nil
TractorCockpit.savegameDirectory = nil
TractorCockpit.lastCommandId = 0
TractorCockpit.lightCacheVehicle = nil
TractorCockpit.lightCacheTypes = nil
TractorCockpit.commandResultId = nil
TractorCockpit.commandResultState = nil
TractorCockpit.commandResultText = nil
TractorCockpit.commandResultMsLeft = 0

TractorCockpit.NO_VEHICLE_LINE = "TRACTORCOCKPIT|state=foot|name=No vehicle"
    .. "|speed=n/a|rpm=n/a|rpmMin=n/a|rpmMax=n/a|load=n/a|fuel=n/a|fuelCapacity=n/a"
    .. "|fuelType=n/a|engine=n/a|hours=n/a|x=n/a|y=n/a|z=n/a"

-- ---------------------------------------------------------------------------
-- Hilfsfunktionen
-- ---------------------------------------------------------------------------

local function cleanText(value)
    if value == nil then
        return "n/a"
    end
    local text = tostring(value)
    text = string.gsub(text, "[|\r\n]", "_")
    if text == "" then
        return "n/a"
    end
    return text
end

local function cleanValue(value)
    if value == nil then
        return "n/a"
    end
    if type(value) == "boolean" then
        return value and "1" or "0"
    end
    if type(value) == "number" then
        if value ~= value or value == math.huge or value == -math.huge then
            return "n/a"
        end
        return string.format("%.2f", value)
    end
    return cleanText(value)
end

local function addField(buffer, key, value)
    return buffer .. "|" .. key .. "=" .. cleanValue(value)
end

local function safeCall(object, methodName, ...)
    if object == nil then
        return nil
    end

    local method = object[methodName]
    if type(method) ~= "function" then
        return nil
    end

    local ok, a, b, c = pcall(method, object, ...)
    if ok then
        return a, b, c
    end
    return nil
end

-- ---------------------------------------------------------------------------
-- Fahrzeug und Messwerte
-- ---------------------------------------------------------------------------

function TractorCockpit:getControlledVehicle()
    if g_localPlayer ~= nil then
        local vehicle = safeCall(g_localPlayer, "getCurrentVehicle")
        if vehicle ~= nil then
            return vehicle
        end
    end

    if g_currentMission ~= nil and g_currentMission.controlledVehicle ~= nil then
        return g_currentMission.controlledVehicle
    end

    return nil
end

function TractorCockpit:getVehicleName(vehicle)
    local name = safeCall(vehicle, "getName")
    if name ~= nil and tostring(name) ~= "" then
        return name
    end
    if vehicle.configFileName ~= nil then
        return vehicle.configFileName
    end
    return "Fahrzeug"
end

function TractorCockpit:getMotor(vehicle)
    local motor = safeCall(vehicle, "getMotor")
    if motor ~= nil then
        return motor
    end
    if vehicle.spec_motorized ~= nil then
        return vehicle.spec_motorized.motor
    end
    return nil
end

-- Vehicle:getLastSpeed() liefert bereits km/h (Vehicle.lua: lastSpeed * 3600).
function TractorCockpit:getSpeedKmh(vehicle)
    local speed = safeCall(vehicle, "getLastSpeed")
    if type(speed) == "number" then
        return speed
    end
    if type(vehicle.lastSpeed) == "number" then
        return vehicle.lastSpeed * 3600
    end
    return nil
end

function TractorCockpit:getRpm(vehicle, motor)
    local rpm = safeCall(vehicle, "getMotorRpmReal")
    if rpm == nil then
        rpm = safeCall(motor, "getLastMotorRpm")
    end
    if rpm == nil and motor ~= nil then
        rpm = motor.lastMotorRpm
    end
    return rpm
end

function TractorCockpit:getRpmRange(motor)
    local minRpm = safeCall(motor, "getMinRpm")
    local maxRpm = safeCall(motor, "getMaxRpm")
    if minRpm == nil and motor ~= nil then
        minRpm = motor.minRpm
    end
    if maxRpm == nil and motor ~= nil then
        maxRpm = motor.maxRpm
    end
    return minRpm, maxRpm
end

-- Liefert Prozent (0..100) oder nil.
function TractorCockpit:getMotorLoadPercent(vehicle)
    local load = safeCall(vehicle, "getMotorLoadPercentage")
    if type(load) ~= "number" or load ~= load then
        return nil
    end
    if load < 0 then
        return 0
    end
    if load > 1 then
        return 100
    end
    return load * 100
end

function TractorCockpit:getEngineState(vehicle, motor)
    local state = safeCall(vehicle, "getIsMotorStarted")
    if state == nil then
        state = safeCall(motor, "getIsMotorStarted")
    end
    if state == nil and motor ~= nil then
        state = motor.isMotorStarted
    end
    return state
end

-- Kraftstoff ueber die Consumer-API des Spiels (Diesel, dann Elektro, dann Methan).
function TractorCockpit:getFuel(vehicle)
    local fuelTypes = {}
    if FillType ~= nil then
        fuelTypes = {
            { index = FillType.DIESEL, name = "diesel" },
            { index = FillType.ELECTRICCHARGE, name = "electric" },
            { index = FillType.METHANE, name = "methane" }
        }
    end

    for _, fuelType in ipairs(fuelTypes) do
        if fuelType.index ~= nil then
            local unitIndex = safeCall(vehicle, "getConsumerFillUnitIndex", fuelType.index)
            if unitIndex ~= nil then
                local level = safeCall(vehicle, "getFillUnitFillLevel", unitIndex)
                local capacity = safeCall(vehicle, "getFillUnitCapacity", unitIndex)
                if level ~= nil then
                    return level, capacity, fuelType.name
                end
            end
        end
    end

    if vehicle.spec_fillUnit ~= nil and vehicle.spec_fillUnit.fillUnits ~= nil then
        for _, unit in pairs(vehicle.spec_fillUnit.fillUnits) do
            if unit ~= nil and unit.fillLevel ~= nil and unit.fillType ~= nil and FillType ~= nil then
                if unit.fillType == FillType.DIESEL then
                    return unit.fillLevel, unit.capacity, "diesel"
                elseif unit.fillType == FillType.ELECTRICCHARGE then
                    return unit.fillLevel, unit.capacity, "electric"
                elseif unit.fillType == FillType.METHANE then
                    return unit.fillLevel, unit.capacity, "methane"
                end
            end
        end
    end

    return nil, nil, nil
end

-- Vehicle:getOperatingTime() liefert Millisekunden; die Anzeige im Spiel
-- rechnet ebenfalls mit ms / (1000 * 60 * 60) = Betriebsstunden.
function TractorCockpit:getOperatingHours(vehicle)
    local milliseconds = safeCall(vehicle, "getOperatingTime")
    if type(milliseconds) ~= "number" then
        milliseconds = vehicle.operatingTime
    end
    if type(milliseconds) ~= "number" or milliseconds ~= milliseconds then
        return nil
    end
    return milliseconds / (1000 * 60 * 60)
end

function TractorCockpit:getPosition(vehicle)
    if vehicle.rootNode == nil or type(getWorldTranslation) ~= "function" then
        return nil, nil, nil
    end

    local ok, x, y, z = pcall(getWorldTranslation, vehicle.rootNode)
    if ok then
        return x, y, z
    end
    return nil, nil, nil
end

-- ---------------------------------------------------------------------------
-- Anbaugeraete (Phase 3) - nur Werte, die es wirklich gibt
-- ---------------------------------------------------------------------------

-- Sammelt angehaengte Geraete (maximal 2 Ebenen tief, z. B. Traktor -> Gewicht
-- -> Anhaenger) und hoechstens MAX_IMPLEMENTS Eintraege.
function TractorCockpit:collectImplements(vehicle)
    local list = {}
    local seen = {}
    local queue = { { vehicle = vehicle, depth = 0 } }
    local head = 1

    while head <= #queue and #list < self.MAX_IMPLEMENTS do
        local item = queue[head]
        head = head + 1

        if item.depth < 2 then
            local attached = safeCall(item.vehicle, "getAttachedImplements")
            if type(attached) == "table" then
                for _, entry in ipairs(attached) do
                    local object = nil
                    if type(entry) == "table" then
                        object = entry.object
                    end
                    if object ~= nil and seen[object] == nil then
                        seen[object] = true
                        list[#list + 1] = object
                        queue[#queue + 1] = { vehicle = object, depth = item.depth + 1 }
                        if #list >= self.MAX_IMPLEMENTS then
                            break
                        end
                    end
                end
            end
        end
    end

    return list
end

-- true, wenn mindestens eine Arbeitszone aktiv ist; nil, wenn das Geraet
-- keine Arbeitszonen hat.
function TractorCockpit:getIsWorking(object)
    local spec = object.spec_workArea
    if type(spec) ~= "table" or type(spec.workAreas) ~= "table" then
        return nil
    end

    local count = #spec.workAreas
    if count == 0 then
        return nil
    end
    if count > self.MAX_WORK_AREAS then
        count = self.MAX_WORK_AREAS
    end

    local anyChecked = false
    for index = 1, count do
        local workArea = spec.workAreas[index]
        if workArea ~= nil then
            anyChecked = true
            local active = safeCall(object, "getIsWorkAreaActive", workArea)
            if active == true then
                return true
            end
        end
    end

    if anyChecked then
        return false
    end
    return nil
end

-- Groesste FillUnit des Geraets (bei Anhaengern der Hauptbehaelter).
function TractorCockpit:getPrimaryFill(object)
    local units = safeCall(object, "getFillUnits")
    if type(units) ~= "table" then
        return nil, nil, nil
    end

    local bestIndex, bestCapacity = nil, 0
    for index, unit in pairs(units) do
        if type(unit) == "table" and type(unit.capacity) == "number" and unit.capacity > bestCapacity then
            bestIndex, bestCapacity = index, unit.capacity
        end
    end

    if bestIndex == nil then
        return nil, nil, nil
    end

    local level = safeCall(object, "getFillUnitFillLevel", bestIndex)
    local capacity = safeCall(object, "getFillUnitCapacity", bestIndex)
    local fillTypeIndex = safeCall(object, "getFillUnitFillType", bestIndex)

    local fillTypeName = nil
    if fillTypeIndex ~= nil and g_fillTypeManager ~= nil then
        fillTypeName = safeCall(g_fillTypeManager, "getFillTypeTitleByIndex", fillTypeIndex)
        if fillTypeName == nil then
            fillTypeName = safeCall(g_fillTypeManager, "getFillTypeNameByIndex", fillTypeIndex)
        end
    end

    return level, capacity, fillTypeName
end

-- ---------------------------------------------------------------------------
-- Zeile aufbauen
-- ---------------------------------------------------------------------------

-- Was kann dieses Geraet? Grundlage fuer "nur bei unterstuetztem Geraet
-- freischalten": der Server und das Dashboard sperren Aktionen anhand dieser
-- Felder, das Spiel prueft beim Ausfuehren noch einmal selbst.
-- ---------------------------------------------------------------------------
-- Licht, Blinker und Rundumleuchte (Fahrzeug)
-- ---------------------------------------------------------------------------
-- Verwendet die Konstanten und Funktionen, die das Spiel selbst fuer seine
-- Lichttasten benutzt (Lights.lua): LIGHT_TYPE_*, TURNLIGHT_*,
-- setLightsTypesMask, setTurnLightState, setBeaconLightsVisibility.
-- Es wird nie ein Index hart kodiert.

local function joinNames(list)
    local text = ""
    for _, value in ipairs(list) do
        if text ~= "" then
            text = text .. ","
        end
        text = text .. value
    end
    return text
end

local function gameConstant(source, name)
    if type(source) ~= "table" or type(name) ~= "string" then
        return nil
    end
    local value = source[name]
    if type(value) ~= "number" then
        return nil
    end
    return value
end

function TractorCockpit:getLightType(name)
    return gameConstant(Lights, self.LIGHT_CONSTANTS[name])
end

function TractorCockpit:getTurnState(name)
    return gameConstant(Lights, self.TURN_CONSTANTS[name])
end

-- Welche Lichtarten hat dieses Fahrzeug wirklich? Einmal je Fahrzeug berechnet.
function TractorCockpit:getUsedLightTypes(vehicle)
    if self.lightCacheVehicle == vehicle and self.lightCacheTypes ~= nil then
        return self.lightCacheTypes
    end

    local used = {}
    local spec = vehicle.spec_lights
    if type(spec) == "table" then
        local function scanList(list)
            if type(list) ~= "table" then
                return
            end
            for _, light in ipairs(list) do
                if type(light) == "table" and type(light.lightTypes) == "table" then
                    for _, lightType in ipairs(light.lightTypes) do
                        if type(lightType) == "number" then
                            used[lightType] = true
                        end
                    end
                end
            end
        end

        if type(spec.staticLights) == "table" then
            for _, list in pairs(spec.staticLights) do
                scanList(list)
            end
        end
        if type(spec.realLights) == "table" then
            for _, profile in pairs(spec.realLights) do
                if type(profile) == "table" then
                    for _, list in pairs(profile) do
                        scanList(list)
                    end
                end
            end
        end
    end

    self.lightCacheVehicle = vehicle
    self.lightCacheTypes = used
    return used
end

function TractorCockpit:getLightsSpec(vehicle)
    local spec = vehicle.spec_lights
    if type(spec) ~= "table" or type(spec.lightsTypesMask) ~= "number" then
        return nil
    end
    if type(bit32) ~= "table" then
        return nil
    end
    return spec
end

-- Setzt oder loescht ein Lichtart-Bit - genau wie die Lichttaste des Spiels.
function TractorCockpit:setLightState(vehicle, lightName, wanted)
    local lightType = self:getLightType(lightName)
    if lightType == nil then
        return "unsupported", "Lichtart ist in dieser Spielversion nicht bekannt"
    end

    local spec = self:getLightsSpec(vehicle)
    if spec == nil then
        return "unsupported", "Fahrzeug hat keine Lichtsteuerung"
    end
    if safeCall(vehicle, "getCanToggleLight") ~= true then
        return "denied", "Licht kann gerade nicht geschaltet werden"
    end

    local used = self:getUsedLightTypes(vehicle)
    if used[lightType] ~= true then
        return "unsupported", "Dieses Fahrzeug hat kein " .. lightName .. "-Licht"
    end

    local mask = spec.lightsTypesMask
    local bit = 2 ^ lightType
    local newMask
    if wanted then
        newMask = bit32.bor(mask, bit)
    else
        newMask = bit32.band(mask, bit32.bnot(bit))
    end

    if newMask == mask then
        return "ok", wanted and "war bereits eingeschaltet" or "war bereits aus"
    end

    safeCall(vehicle, "setLightsTypesMask", newMask)
    return "ok", wanted and "eingeschaltet" or "ausgeschaltet"
end

function TractorCockpit:setBeaconState(vehicle, wanted)
    if type(vehicle.setBeaconLightsVisibility) ~= "function"
        or type(vehicle.getBeaconLightsVisibility) ~= "function" then
        return "unsupported", "Fahrzeug hat keine Rundumleuchte"
    end
    if safeCall(vehicle, "getCanToggleLight") ~= true then
        return "denied", "Licht kann gerade nicht geschaltet werden"
    end
    if safeCall(vehicle, "getBeaconLightsVisibility") == wanted then
        return "ok", wanted and "war bereits an" or "war bereits aus"
    end
    safeCall(vehicle, "setBeaconLightsVisibility", wanted)
    return "ok", wanted and "Rundumleuchte an" or "Rundumleuchte aus"
end

function TractorCockpit:setTurnState(vehicle, turnName)
    local state = self:getTurnState(turnName)
    if state == nil then
        return "unsupported", "Blinkerzustand ist in dieser Spielversion nicht bekannt"
    end
    if type(vehicle.setTurnLightState) ~= "function" then
        return "unsupported", "Fahrzeug hat keine Blinker"
    end
    if safeCall(vehicle, "getCanToggleLight") ~= true then
        return "denied", "Blinker kann gerade nicht geschaltet werden"
    end
    if safeCall(vehicle, "getTurnLightState") == state then
        return "ok", "war bereits so"
    end
    safeCall(vehicle, "setTurnLightState", state)
    local text = ({ off = "Blinker aus", left = "Blinker links", right = "Blinker rechts",
                    hazard = "Warnblinker" })[turnName] or "Blinker"
    return "ok", text
end

function TractorCockpit:appendLights(line, vehicle)
    local spec = self:getLightsSpec(vehicle)
    if spec == nil then
        return line
    end

    local mask = spec.lightsTypesMask
    local used = self:getUsedLightTypes(vehicle)

    line = line .. "|canToggleLight="
        .. ((safeCall(vehicle, "getCanToggleLight") == true) and "1" or "0")

    local canList = {}
    local onList = {}
    for _, name in ipairs(self.LIGHT_ORDER) do
        local lightType = self:getLightType(name)
        if lightType ~= nil and used[lightType] == true then
            canList[#canList + 1] = name
            if bit32.band(mask, 2 ^ lightType) ~= 0 then
                onList[#onList + 1] = name
            end
        end
    end

    line = line .. "|lightCan=" .. (joinNames(canList) ~= "" and joinNames(canList) or "none")
    line = line .. "|lightOn=" .. (joinNames(onList) ~= "" and joinNames(onList) or "none")

    if type(vehicle.getBeaconLightsVisibility) == "function" then
        line = line .. "|beaconCan=1"
        line = line .. "|beacon=" .. cleanValue(safeCall(vehicle, "getBeaconLightsVisibility"))
    end

    local turnState = safeCall(vehicle, "getTurnLightState")
    if type(turnState) == "number" then
        local turnName = "unknown"
        for _, name in ipairs(self.TURN_ORDER) do
            if self:getTurnState(name) == turnState then
                turnName = name
                break
            end
        end
        line = line .. "|turnCan=1|turnLight=" .. cleanText(turnName)
    end

    return line
end

function TractorCockpit:getControlCapabilities(object)
    local canLower = false
    local attacherVehicle = safeCall(object, "getAttacherVehicle")
    if attacherVehicle ~= nil and type(attacherVehicle.handleLowerImplementEvent) == "function" then
        canLower = safeCall(object, "getAllowsLowering") == true
    end

    -- Das Spiel prueft Function-Felder genau so (siehe AttacherJoints.lua).
    local canFold = type(object.setFoldDirection) == "function"
        and type(object.getToggledFoldDirection) == "function"
    local canOn = type(object.setIsTurnedOn) == "function"
        and type(object.getIsTurnedOn) == "function"

    return canLower, canFold, canOn
end

function TractorCockpit:appendImplements(line, vehicle)
    local implements = self:collectImplements(vehicle)
    if #implements == 0 then
        return line
    end

    line = line .. "|implCount=" .. #implements

    for index, object in ipairs(implements) do
        local prefix = "|impl" .. index
        line = line .. prefix .. "Name=" .. cleanText(self:getVehicleName(object))

        -- Nur Felder anhaengen, die das Geraet wirklich liefert.
        local typeName = object.typeName
        if type(typeName) == "string" and typeName ~= "" then
            line = line .. prefix .. "Type=" .. cleanText(typeName)
        end

        local lowered = safeCall(object, "getIsLowered")
        if lowered ~= nil then
            line = line .. prefix .. "Lowered=" .. cleanValue(lowered)
        end

        local unfolded = safeCall(object, "getIsUnfolded")
        if unfolded ~= nil then
            line = line .. prefix .. "Unfolded=" .. cleanValue(unfolded)
        end

        local turnedOn = safeCall(object, "getIsTurnedOn")
        if turnedOn ~= nil then
            line = line .. prefix .. "On=" .. cleanValue(turnedOn)
        end

        local working = self:getIsWorking(object)
        if working ~= nil then
            line = line .. prefix .. "Working=" .. cleanValue(working)
        end

        local fill, capacity, fillTypeName = self:getPrimaryFill(object)
        if fill ~= nil then
            line = line .. prefix .. "Fill=" .. cleanValue(fill)
        end
        if capacity ~= nil then
            line = line .. prefix .. "FillCapacity=" .. cleanValue(capacity)
        end
        if fillTypeName ~= nil then
            line = line .. prefix .. "FillType=" .. cleanText(fillTypeName)
        end

        -- Faehigkeiten: daran haengen die Steuerknoepfe im Dashboard.
        local canLower, canFold, canOn = self:getControlCapabilities(object)
        line = line .. prefix .. "CanLower=" .. cleanValue(canLower)
        line = line .. prefix .. "CanFold=" .. cleanValue(canFold)
        line = line .. prefix .. "CanOn=" .. cleanValue(canOn)
    end

    return line
end

function TractorCockpit:buildLine(vehicle)
    local motor = self:getMotor(vehicle)
    local minRpm, maxRpm = self:getRpmRange(motor)
    local fuel, fuelCapacity, fuelType = self:getFuel(vehicle)
    local x, y, z = self:getPosition(vehicle)

    local line = "TRACTORCOCKPIT"
    line = addField(line, "state", "vehicle")
    line = addField(line, "name", self:getVehicleName(vehicle))
    line = addField(line, "speed", self:getSpeedKmh(vehicle))
    line = addField(line, "rpm", self:getRpm(vehicle, motor))
    line = addField(line, "rpmMin", minRpm)
    line = addField(line, "rpmMax", maxRpm)
    line = addField(line, "load", self:getMotorLoadPercent(vehicle))
    line = addField(line, "fuel", fuel)
    line = addField(line, "fuelCapacity", fuelCapacity)
    line = addField(line, "fuelType", fuelType)
    line = addField(line, "engine", self:getEngineState(vehicle, motor))
    line = addField(line, "hours", self:getOperatingHours(vehicle))
    line = addField(line, "x", x)
    line = addField(line, "y", y)
    line = addField(line, "z", z)

    line = self:appendLights(line, vehicle)

    return self:appendImplements(line, vehicle)
end

function TractorCockpit:buildCurrentLine()
    local vehicle = self:getControlledVehicle()
    if vehicle == nil then
        return self:appendCommandResult(self.NO_VEHICLE_LINE)
    end
    return self:appendCommandResult(self:buildLine(vehicle))
end

-- ---------------------------------------------------------------------------
-- Steuerkanal (Phase 4): Befehlsdatei lesen, pruefen, ausfuehren
-- ---------------------------------------------------------------------------
-- WICHTIG (verifiziert an der Spielmeldung "io.open, only write mode ('w') is
-- allowed"): FS25 erlaubt Mods das Schreiben von Dateien, aber NICHT das Lesen
-- ueber io.open. Der einzige erlaubte Leseweg ist die XML-API des Spiels
-- (XMLFile.load). Die Befehlsdatei ist deshalb eine XML-Datei, und der Mod
-- prueft beim Laden, welcher Pfad sich wirklich lesen laesst, statt es zu raten.
--
-- Ausgefuehrt werden ausschliesslich benannte Aktionen aus COMMAND_ACTIONS.
-- Es gibt keinen Weg, darueber Lua-Code oder freie Funktionsnamen auszufuehren.

function TractorCockpit:getCommandCandidates()
    local list = {}
    if self.commandPathBase ~= nil then
        list[#list + 1] = self.commandPathBase .. self.COMMAND_FILE
    end
    if self.savegameDirectory ~= nil and self.savegameDirectory ~= "" then
        list[#list + 1] = self.savegameDirectory .. "/FS25_TractorCockpit_cmd.xml"
    end
    return list
end

function TractorCockpit:loadCommandXml(path)
    if path == nil or type(XMLFile) ~= "table" or type(XMLFile.load) ~= "function" then
        return nil
    end
    local ok, xmlFile = pcall(XMLFile.load, "tcCommandRead", path)
    if not ok or xmlFile == nil then
        return nil
    end
    return xmlFile
end

function TractorCockpit:releaseXml(xmlFile)
    if xmlFile == nil then
        return
    end
    pcall(function()
        xmlFile:delete()
    end)
end

function TractorCockpit:createCommandXml(path, id)
    if path == nil or type(XMLFile) ~= "table" or type(XMLFile.create) ~= "function" then
        return false
    end
    local ok, result = pcall(function()
        local xmlFile = XMLFile.create("tcCommandWrite", path, "tractorcockpit")
        if xmlFile == nil then
            return false
        end
        xmlFile:setInt("tractorcockpit#id", id)
        xmlFile:save()
        xmlFile:delete()
        return true
    end)
    return ok and result == true
end

-- Prueft einen Pfad: notfalls anlegen (Schreiben ist erlaubt) und dann lesen.
function TractorCockpit:testCommandPath(path)
    local xmlFile = self:loadCommandXml(path)
    if xmlFile ~= nil then
        self:releaseXml(xmlFile)
        return true
    end

    if not self:createCommandXml(path, 0) then
        return false
    end

    xmlFile = self:loadCommandXml(path)
    if xmlFile == nil then
        return false
    end
    self:releaseXml(xmlFile)
    return true
end

function TractorCockpit:detectCommandChannel()
    local candidates = self:getCommandCandidates()
    if #candidates == 0 then
        self.commandMode = "none"
        return false
    end

    for _, path in ipairs(candidates) do
        if self:testCommandPath(path) then
            self.commandPath = path
            self.commandMode = "xml"
            print("TractorCockpit: Steuerkanal bereit -> " .. path)
            return true
        end
        print("TractorCockpit: Steuerkanal-Pfad nicht lesbar -> " .. path)
    end

    self.commandMode = "none"
    print("TractorCockpit: kein lesbarer Steuerkanal gefunden - Steuerung bleibt aus (nur Anzeige).")
    return false
end

function TractorCockpit:readCommandId(path)
    local xmlFile = self:loadCommandXml(path)
    if xmlFile == nil then
        return nil
    end
    local id = xmlFile:getInt("tractorcockpit#id", 0)
    self:releaseXml(xmlFile)
    return id
end

function TractorCockpit:executeCommand(id, action, target, expectedName)
    local vehicle = self:getControlledVehicle()
    if vehicle == nil then
        return "error", "kein Fahrzeug gesteuert"
    end

    -- Fahrzeugweite Aktionen (Licht, Blinker, Rundumleuchte) zuerst.
    local vehicleState, vehicleText = self:executeVehicleAction(vehicle, action)
    if vehicleState ~= nil then
        return vehicleState, vehicleText
    end

    local implements = self:collectImplements(vehicle)
    local object = implements[target]
    if object == nil then
        return "error", "Geraet " .. tostring(target) .. " nicht gefunden"
    end

    local name = self:getVehicleName(object)
    if type(expectedName) == "string" and expectedName ~= "" and expectedName ~= name then
        return "targetChanged", "am Platz " .. tostring(target) .. " haengt jetzt " .. tostring(name)
    end

    if action == "lower" or action == "raise" then
        if safeCall(object, "getAllowsLowering") ~= true then
            return "unsupported", "Absenken ist fuer dieses Geraet nicht freigegeben"
        end
        local attacherVehicle = safeCall(object, "getAttacherVehicle")
        if attacherVehicle == nil or type(attacherVehicle.handleLowerImplementEvent) ~= "function" then
            return "unsupported", "kein Hebewerk gefunden"
        end
        -- Genau der Aufruf, den das Spiel selbst benutzt
        -- (AttacherJoints:handleLowerImplementEvent).
        safeCall(attacherVehicle, "handleLowerImplementEvent", object, action == "lower")
        return "ok", action == "lower" and "Senken ausgeloest" or "Heben ausgeloest"
    end

    if action == "toggleFold" then
        if type(object.setFoldDirection) ~= "function" or type(object.getToggledFoldDirection) ~= "function" then
            return "unsupported", "Geraet ist nicht klappbar"
        end
        if safeCall(object, "getIsFoldAllowed") == false then
            return "denied", "Klappen ist gerade nicht erlaubt"
        end
        local direction = safeCall(object, "getToggledFoldDirection")
        if type(direction) ~= "number" or direction == 0 then
            return "error", "Klapprichtung nicht ermittelbar"
        end
        safeCall(object, "setFoldDirection", direction)
        return "ok", direction > 0 and "Aufklappen ausgeloest" or "Zuklappen ausgeloest"
    end

    if action == "turnOn" or action == "turnOff" then
        if type(object.setIsTurnedOn) ~= "function" or type(object.getIsTurnedOn) ~= "function" then
            return "unsupported", "Geraet hat kein Ein/Aus"
        end
        local wanted = (action == "turnOn")
        if safeCall(object, "getIsTurnedOn") == wanted then
            return "ok", wanted and "war bereits eingeschaltet" or "war bereits aus"
        end
        safeCall(object, "setIsTurnedOn", wanted)
        return "ok", wanted and "Einschalten ausgeloest" or "Ausschalten ausgeloest"
    end

    return "unsupported", "Aktion ist nicht freigegeben"
end

-- Fuehrt fahrzeugweite Aktionen aus. Rueckgabe nil, wenn die Aktion keine ist.
function TractorCockpit:executeVehicleAction(vehicle, action)
    local info = self.VEHICLE_ACTIONS[action]
    if info == nil then
        return nil, nil
    end

    if info.kind == "light" then
        return self:setLightState(vehicle, info.name, info.value)
    end

    if info.kind == "cycle" then
        if type(vehicle.setNextLightsState) ~= "function" then
            return "unsupported", "Fahrzeug hat keine Lichtstufen"
        end
        if safeCall(vehicle, "getCanToggleLight") ~= true then
            return "denied", "Licht kann gerade nicht geschaltet werden"
        end
        safeCall(vehicle, "setNextLightsState", info.value)
        return "ok", info.value > 0 and "Lichtstufe weiter" or "Lichtstufe zurueck"
    end

    if info.kind == "beacon" then
        return self:setBeaconState(vehicle, info.value)
    end

    if info.kind == "turn" then
        return self:setTurnState(vehicle, info.name)
    end

    return "unsupported", "Aktion nicht umgesetzt"
end

function TractorCockpit:setCommandResult(id, state, text)    self.commandResultId = id
    self.commandResultState = state
    self.commandResultText = text
    self.commandResultMsLeft = self.COMMAND_RESULT_MS
end

function TractorCockpit:appendCommandResult(line)
    if self.commandMode ~= nil then
        line = line .. "|cmdMode=" .. cleanText(self.commandMode)
    end
    if self.commandPath ~= nil then
        line = line .. "|cmdPath=" .. cleanText(self.commandPath)
    end
    if self.commandReadErrors > 0 then
        line = line .. "|cmdReadErrors=" .. self.commandReadErrors
    end

    if self.commandResultMsLeft <= 0 or self.commandResultId == nil then
        return line
    end
    return line
        .. "|cmdId=" .. cleanValue(self.commandResultId)
        .. "|cmdResult=" .. cleanText(self.commandResultState)
        .. "|cmdMsg=" .. cleanText(self.commandResultText)
end

function TractorCockpit:processCommandFile()
    if self.commandMode ~= "xml" or self.commandPath == nil then
        return
    end

    local xmlFile = self:loadCommandXml(self.commandPath)
    if xmlFile == nil then
        -- Datei weg oder gerade nicht lesbar. Gedrosselt neu anlegen, damit
        -- das Spiel-Log nicht mit Meldungen zugemuellt wird.
        if self.commandRetryMs <= 0 then
            self.commandRetryMs = self.COMMAND_READ_RETRY_MS
            self.commandReadErrors = self.commandReadErrors + 1
            if self:createCommandXml(self.commandPath, 0) then
                self.commandReadErrors = 0
            end
        end
        return
    end
    self.commandReadErrors = 0

    local id = xmlFile:getInt("tractorcockpit#id", 0)
    local action = xmlFile:getString("tractorcockpit#action")
    local target = xmlFile:getInt("tractorcockpit#target", 0)
    local name = xmlFile:getString("tractorcockpit#name")
    self:releaseXml(xmlFile)

    if id == nil or id <= 0 or id <= self.lastCommandId then
        return
    end
    self.lastCommandId = id

    if action == nil or self.COMMAND_ACTIONS[action] ~= true then
        self:setCommandResult(id, "unsupported", "Aktion nicht in der Whitelist")
        self:createCommandXml(self.commandPath, id)
        print("TractorCockpit: Befehl " .. tostring(id) .. " abgelehnt (" .. tostring(action) .. ")")
        return
    end

    local state, text = self:executeCommand(id, action, target, name)
    self:setCommandResult(id, state, text)
    -- Datei selbst zuruecksetzen (Schreiben ist erlaubt): so kann derselbe
    -- Befehl nie zweimal laufen, auch wenn der Server gerade nicht antwortet.
    self:createCommandXml(self.commandPath, id)
    print("TractorCockpit: Befehl " .. tostring(id) .. " " .. action .. " -> " .. tostring(state)
        .. " (" .. tostring(text) .. ")")
end

-- ---------------------------------------------------------------------------
-- Ausgabekanaele
-- ---------------------------------------------------------------------------

function TractorCockpit:initFileChannel()
    if self.fileChecked then
        return
    end
    self.fileChecked = true

    if type(io) ~= "table" or type(io.open) ~= "function" then
        print("TractorCockpit: io.open nicht verfuegbar - nutze nur die Logdatei, Steuerung inaktiv.")
        return
    end
    if type(getUserProfileAppPath) ~= "function" then
        print("TractorCockpit: getUserProfileAppPath fehlt - nutze nur die Logdatei, Steuerung inaktiv.")
        return
    end

    local ok, base = pcall(getUserProfileAppPath)
    if not ok or type(base) ~= "string" or base == "" then
        print("TractorCockpit: Spielprofilordner nicht ermittelbar - nutze nur die Logdatei.")
        return
    end

    local lastChar = string.sub(base, -1)
    if lastChar ~= "/" and lastChar ~= "\\" then
        base = base .. "/"
    end

    -- Kandidaten fuer den Steuerkanal. Welcher davon lesbar ist, stellt
    -- detectCommandChannel() fest (FS25 erlaubt io.open nur zum Schreiben).
    self.commandPathBase = base

    if not self.FILE_ENABLED then
        print("TractorCockpit: Datei-Kanal deaktiviert (FILE_ENABLED=false).")
        return
    end

    self.filePath = base .. self.FILE_NAME
    self.fileOk = true
    print("TractorCockpit: Datei-Kanal aktiv -> " .. self.filePath)
end

function TractorCockpit:writeFile(line)
    if not self.fileOk or self.filePath == nil then
        return
    end

    local ok, success = pcall(function()
        local file = io.open(self.filePath, "w")
        if file == nil then
            return false
        end
        file:write(line)
        file:write("\n")
        file:close()
        return true
    end)

    if ok and success == true then
        return
    end

    self.fileOk = false
    if not self.fileErrorLogged then
        self.fileErrorLogged = true
        print("TractorCockpit: Schreiben in " .. tostring(self.filePath)
            .. " fehlgeschlagen - nutze nur die Logdatei.")
    end
end

-- ---------------------------------------------------------------------------
-- Mod-Event-Listener
-- ---------------------------------------------------------------------------

function TractorCockpit:loadMap(name)
    self:initFileChannel()

    -- Spielstandordner als zweiter Kandidat fuer den Steuerkanal.
    if g_currentMission ~= nil and g_currentMission.missionInfo ~= nil then
        local directory = g_currentMission.missionInfo.savegameDirectory
        if type(directory) == "string" and directory ~= "" then
            self.savegameDirectory = directory
        end
    end

    if self.CONTROL_ENABLED then
        if self:detectCommandChannel() then
            -- Ein Befehl aus einer frueheren Sitzung darf nicht ausgefuehrt werden.
            local existing = self:readCommandId(self.commandPath)
            if type(existing) == "number" and existing > 0 then
                self.lastCommandId = existing
                print("TractorCockpit: alter Befehl " .. existing .. " in der Befehlsdatei wird ignoriert.")
            end
        end
    else
        self.commandMode = "off"
        print("TractorCockpit: Steuerung per Konstante abgeschaltet (CONTROL_ENABLED=false).")
    end

    print("TractorCockpit " .. self.VERSION
        .. ": geladen (Telemetrie + Steuerung"
        .. (self.commandMode == "xml" and ", aktiv" or ", inaktiv") .. ").")
end

function TractorCockpit:deleteMap()
    print("TractorCockpit: Spielstand beendet.")
end

function TractorCockpit:update(dt)
    -- Einmalig pruefen, ob der schnelle Datei-Kanal benutzt werden kann.
    self:initFileChannel()

    -- Steuerkanal laeuft unabhaengig vom Telemetrie-Takt.
    if self.commandRetryMs > 0 then
        self.commandRetryMs = self.commandRetryMs - dt
    end
    self.commandElapsedMs = self.commandElapsedMs + dt
    if self.commandElapsedMs >= self.COMMAND_POLL_MS then
        self.commandElapsedMs = 0
        self:processCommandFile()
    end
    if self.commandResultMsLeft > 0 then
        self.commandResultMsLeft = self.commandResultMsLeft - dt
    end

    self.fileElapsedMs = self.fileElapsedMs + dt
    self.logElapsedMs = self.logElapsedMs + dt

    local logInterval = self.fileOk and self.LOG_INTERVAL_MS_FILE_OK or self.LOG_INTERVAL_MS_FALLBACK
    -- Ohne Datei-Kanal wird nur einmal pro Sekunde gerechnet und geschrieben.
    local fileDue = self.fileOk and self.fileElapsedMs >= self.FILE_INTERVAL_MS
    local logDue = self.lastLine == nil or self.logElapsedMs >= logInterval

    if not fileDue and not logDue then
        return
    end

    local line = self:buildCurrentLine()
    self.lastLine = line

    if fileDue then
        self.fileElapsedMs = 0
        self:writeFile(line)
    end

    if logDue then
        self.logElapsedMs = 0
        print(line)
    end
end

addModEventListener(TractorCockpit)
