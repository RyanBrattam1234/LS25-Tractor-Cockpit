// Tractor Cockpit V1 - Dashboard mit Tabs.
// Holt die Telemetrie vom lokalen Python-Server, steuert Licht/Blinker/Geräte
// und zeigt den Verlauf aus der SQLite-Datenbank. Keine Abhängigkeiten.

const $ = (id) => document.getElementById(id);

const FUEL_UNITS = { diesel: "Liter", electric: "kWh", methane: "kg" };
const DEFAULT_RPM_MAX = 2500;
const CHANNEL_NAMES = { file: "Telemetriedatei (schnell)", log: "Logdatei (Rückfall)" };
const HISTORY_MINUTES = 30;
const HISTORY_EVERY_MS = 5000;
const COMMAND_EVERY_MS = 5000;

const LIGHT_ORDER = ["default", "highBeam", "workFront", "workBack"];
const LIGHT_LABELS = {
  default: "Abblendlicht",
  highBeam: "Fernlicht",
  workFront: "Arbeitslicht vorn",
  workBack: "Arbeitslicht hinten",
};
const LIGHT_ACTIONS = {
  default: { on: "lightDefaultOn", off: "lightDefaultOff" },
  highBeam: { on: "lightHighBeamOn", off: "lightHighBeamOff" },
  workFront: { on: "lightWorkFrontOn", off: "lightWorkFrontOff" },
  workBack: { on: "lightWorkBackOn", off: "lightWorkBackOff" },
};
const TURN_ORDER = ["off", "left", "right", "hazard"];
const TURN_LABELS = { off: "Aus", left: "Links", right: "Rechts", hazard: "Warnblinker" };
const TURN_ACTIONS = { off: "turnOff", left: "turnLeft", right: "turnRight", hazard: "turnHazard" };
const CONFIRM_TEXT = {
  "lowered:true": "bestätigt: gesenkt",
  "lowered:false": "bestätigt: angehoben",
  "on:true": "bestätigt: eingeschaltet",
  "on:false": "bestätigt: ausgeschaltet",
  "unfolded:true": "bestätigt: aufgeklappt",
  "unfolded:false": "bestätigt: geklappt",
  "beacon:true": "bestätigt: Rundumleuchte an",
  "beacon:false": "bestätigt: Rundumleuchte aus",
  "turnLight:off": "bestätigt: Blinker aus",
  "turnLight:left": "bestätigt: Blinker links",
  "turnLight:right": "bestätigt: Blinker rechts",
  "turnLight:hazard": "bestätigt: Warnblinker",
};
const EXPECTED_TEXT = {
  lower: "lowered:true", raise: "lowered:false", turnOn: "on:true", turnOff: "on:false",
  beaconOn: "beacon:true", beaconOff: "beacon:false",
  turnLeft: "turnLight:left", turnRight: "turnLight:right",
  turnHazard: "turnLight:hazard", turnOff2: "turnLight:off",
};

// --- Zustand --------------------------------------------------------------
let ageBase = null;
let ageBaseAt = null;
let staleLimit = 15;
let pollMs = 1000;
let pollTimer = null;
let commandPending = false;
let controlEnabled = false;
let controlReady = false;
let activeTab = "cockpit";
let lastSeries = null;
let lastReading = {};

// --- Kleine Helfer --------------------------------------------------------
function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value, digits = 0) {
  const parsed = num(value);
  if (parsed === null) return "--";
  return parsed.toLocaleString("de-DE", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function setText(id, text) {
  const element = $(id);
  if (element) element.textContent = text;
}

function setBar(id, percent) {
  const element = $(id);
  if (!element) return;
  const value = num(percent);
  element.style.width = Math.max(0, Math.min(100, value === null ? 0 : value)) + "%";
}

function toggleClass(id, name, active) {
  const element = $(id);
  if (element) element.classList.toggle(name, Boolean(active));
}

function setPill(id, text, kind) {
  const element = $(id);
  if (!element) return;
  element.textContent = text;
  element.className = "pill" + (kind ? " " + kind : "");
}

function setDot(state) {
  const dot = $("dot");
  if (dot) dot.className = "dot " + state;
}

function formatAge(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "--";
  if (seconds < 1.5) return "jetzt";
  if (seconds < 90) return "vor " + Math.round(seconds) + " s";
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return "vor " + minutes + " min";
  const hours = Math.floor(minutes / 60);
  return "vor " + hours + " h " + (minutes % 60) + " min";
}

function formatBytes(value) {
  const parsed = num(value);
  if (parsed === null) return "--";
  if (parsed < 1024) return Math.round(parsed) + " B";
  if (parsed < 1024 * 1024) return (parsed / 1024).toFixed(1).replace(".", ",") + " KB";
  return (parsed / (1024 * 1024)).toFixed(1).replace(".", ",") + " MB";
}

function formatClock(epochSeconds) {
  const parsed = num(epochSeconds);
  if (parsed === null) return "--";
  return new Date(parsed * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function currentAge() {
  if (ageBase === null || ageBaseAt === null) return null;
  return ageBase + (Date.now() - ageBaseAt) / 1000;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// --- Tabs -----------------------------------------------------------------
function activateTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((button) => {
    const on = button.dataset.tab === name;
    button.classList.toggle("is-active", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === "tab-" + name);
  });
  try { localStorage.setItem("tc.tab", name); } catch (error) { /* egal */ }

  if (name === "history") {
    if (lastSeries) requestAnimationFrame(() => drawHistory($("historyChart"), lastSeries.points || []));
    loadHistory();
  }
  if (name === "control") loadCommandLog();
}

function initTabs() {
  const buttons = Array.from(document.querySelectorAll(".tab"));
  buttons.forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      const index = buttons.indexOf(button);
      if (event.key === "ArrowRight") {
        buttons[(index + 1) % buttons.length].focus();
        buttons[(index + 1) % buttons.length].click();
      } else if (event.key === "ArrowLeft") {
        const previous = (index - 1 + buttons.length) % buttons.length;
        buttons[previous].focus();
        buttons[previous].click();
      }
    });
  });

  let stored = null;
  try { stored = localStorage.getItem("tc.tab"); } catch (error) { stored = null; }
  const known = buttons.map((button) => button.dataset.tab);
  activateTab(stored && known.includes(stored) ? stored : "cockpit");
}

// --- Anbaugeräte ----------------------------------------------------------
function addBadge(parent, onText, offText, value) {
  if (value !== true && value !== false) return;
  parent.appendChild(element("span", "badge " + (value ? "on" : "off"), value ? onText : offText));
}

function renderImplements(list) {
  const container = $("implements");
  if (!container) return;

  const implements_ = Array.isArray(list) ? list : [];
  container.textContent = "";
  setText("tabDeviceCount", String(implements_.length));
  setPill("implPill", implements_.length === 0 ? "KEINE"
    : implements_.length + (implements_.length === 1 ? " GERÄT" : " GERÄTE"),
    implements_.length === 0 ? "muted" : "");

  if (implements_.length === 0) {
    container.appendChild(element("p", "subtle", "Kein Gerät angehängt."));
    return;
  }

  implements_.forEach((item) => {
    const card = element("div", "impl");
    const head = element("div", "impl-head");
    head.appendChild(element("strong", null, item.name || "Gerät"));
    if (item.type) head.appendChild(element("span", "badge muted", item.type));
    card.appendChild(head);

    const badges = element("div", "badges");
    addBadge(badges, "gesenkt", "angehoben", item.lowered);
    addBadge(badges, "aufgeklappt", "geklappt", item.unfolded);
    addBadge(badges, "eingeschaltet", "aus", item.on);
    addBadge(badges, "arbeitet", "arbeitet nicht", item.working);
    if (badges.childElementCount > 0) card.appendChild(badges);

    if (num(item.fill) !== null) {
      const fill = element("div", "impl-fill");
      const capacity = num(item.fillCapacity);
      fill.appendChild(element("span", null,
        fmt(item.fill, 0) + (capacity ? " / " + fmt(capacity, 0) : "") + " L"
        + (item.fillType ? " · " + item.fillType : "")));
      if (capacity > 0) {
        const track = element("div", "track");
        const bar = element("span");
        bar.style.width = Math.max(0, Math.min(100, (num(item.fill) / capacity) * 100)) + "%";
        track.appendChild(bar);
        fill.appendChild(track);
      }
      card.appendChild(fill);
    }
    container.appendChild(card);
  });
}

// --- Licht ----------------------------------------------------------------
function lightStateActive(lights, name) {
  return (lights.on || []).includes(name);
}

function controlBlocked(control) {
  return !controlEnabled || !controlReady || commandPending;
}

function renderLights(lights, control) {
  const container = $("lightButtons");
  const cycleRow = $("lightCycleRow");
  const turnRow = $("turnButtons");
  const summary = $("lightSummary");
  if (!container) return;

  container.textContent = "";
  if (cycleRow) cycleRow.textContent = "";
  if (turnRow) turnRow.textContent = "";
  if (summary) summary.textContent = "";
  lastReading.lights = lights || null;

  if (!lights) {
    setPill("lightPill", "KEINE DATEN", "muted");
    setPill("lightPillCockpit", "—", "muted");
    setPill("turnPill", "—", "muted");
    setText("lightHint", "Dieses Fahrzeug meldet keine Lichtdaten (ältere Mod-Version?).");
    if (summary) summary.appendChild(element("p", "subtle", "Keine Lichtdaten."));
    return;
  }

  const capabilities = lights.capabilities || [];
  const blocked = controlBlocked(control) || lights.canToggle !== true;
  const reason = !controlEnabled ? "Steuerung ist aus (--enable-control)."
    : !controlReady ? "Kein lesbarer Steuerkanal (siehe Diagnose)."
    : lights.canToggle !== true ? "Licht kann gerade nicht geschaltet werden (nicht eingestiegen oder Helfer fährt)."
    : commandPending ? "Ein Befehl läuft noch." : "";

  // Einzelne Lichter
  let anyLight = false;
  LIGHT_ORDER.forEach((name) => {
    if (!capabilities.includes(name)) return;
    anyLight = true;
    const on = lightStateActive(lights, name);
    const button = element("button", "light-button" + (on ? " is-on" : ""),
      LIGHT_LABELS[name] + (on ? " · an" : " · aus"));
    button.type = "button";
    button.disabled = blocked;
    button.addEventListener("click", () => {
      const actions = LIGHT_ACTIONS[name];
      sendCommand(on ? actions.off : actions.on, 0);
    });
    container.appendChild(button);
  });

  if (!anyLight) {
    container.appendChild(element("p", "subtle", "Dieses Fahrzeug hat keine schaltbaren Scheinwerfer gemeldet."));
  }

  // Lichtstufen (wie die Lichttaste im Spiel)
  if (capabilities.includes("toggle") && cycleRow) {
    const previous = element("button", "control-button", "Stufe zurück");
    previous.type = "button";
    previous.disabled = blocked;
    previous.addEventListener("click", () => sendCommand("lightPrev", 0));
    const next = element("button", "control-button", "Stufe weiter");
    next.type = "button";
    next.disabled = blocked;
    next.addEventListener("click", () => sendCommand("lightNext", 0));
    cycleRow.appendChild(previous);
    cycleRow.appendChild(next);
  }

  // Rundumleuchte
  if (capabilities.includes("beacon")) {
    const on = lights.beacon === true;
    const button = element("button", "light-button" + (on ? " is-on" : ""),
      "Rundumleuchte" + (on ? " · an" : " · aus"));
    button.type = "button";
    button.disabled = blocked;
    button.addEventListener("click", () => sendCommand(on ? "beaconOff" : "beaconOn", 0));
    if (cycleRow) cycleRow.appendChild(button);
  }

  // Blinker
  if (turnRow && capabilities.includes("turn")) {
    TURN_ORDER.forEach((mode) => {
      const on = (lights.turnLight || "off") === mode;
      const button = element("button", "light-button" + (on ? " is-on" : ""), TURN_LABELS[mode]);
      button.type = "button";
      button.disabled = blocked;
      button.addEventListener("click", () => sendCommand(TURN_ACTIONS[mode], 0));
      turnRow.appendChild(button);
    });
    setPill("turnPill", (TURN_LABELS[lights.turnLight] || "—").toUpperCase(),
      (lights.turnLight && lights.turnLight !== "off") ? "" : "muted");
  } else {
    setPill("turnPill", "KEINE", "muted");
  }

  setText("lightHint", reason
    || "Nur Lampen, die dieses Fahrzeug wirklich hat. Der Zustand kommt aus dem Spiel.");
  const activeCount = LIGHT_ORDER.filter((name) => capabilities.includes(name) && lightStateActive(lights, name)).length;
  setPill("lightPill", lights.canToggle === true ? "BEREIT" : "GESPERRT",
    lights.canToggle === true ? "" : "muted");
  setPill("lightPillCockpit", activeCount === 0 ? "AUS" : activeCount + " AN",
    activeCount === 0 ? "muted" : "");

  // Kurzübersicht im Cockpit
  if (summary) {
    const rows = element("div", "badges");
    LIGHT_ORDER.forEach((name) => {
      if (!capabilities.includes(name)) return;
      const on = lightStateActive(lights, name);
      rows.appendChild(element("span", "badge " + (on ? "on" : "off"),
        LIGHT_LABELS[name] + (on ? "" : " (aus)")));
    });
    if (capabilities.includes("beacon")) {
      const on = lights.beacon === true;
      rows.appendChild(element("span", "badge " + (on ? "on" : "off"),
        "Rundumleuchte" + (on ? "" : " (aus)")));
    }
    if (capabilities.includes("turn")) {
      const mode = lights.turnLight || "off";
      rows.appendChild(element("span", "badge " + (mode !== "off" ? "on" : "off"),
        "Blinker: " + (TURN_LABELS[mode] || mode)));
    }
    if (rows.childElementCount === 0) {
      summary.appendChild(element("p", "subtle", "Keine Lichtdaten."));
    } else {
      summary.appendChild(rows);
    }
  }
}

// --- Steuerung ------------------------------------------------------------
function renderControl(data) {
  const control = data.control || {};
  controlEnabled = Boolean(control.enabled);
  controlReady = control.mode === "xml";
  commandPending = Boolean(control.pending);

  const list = $("controls");
  if (!list) return;

  if (!controlEnabled) {
    setPill("controlPill", "GESPERRT", "muted");
    setText("controlHint", "Steuerung ist aus. Server mit --enable-control starten, dann "
      + "erscheinen hier die Knöpfe. Vorher Spielstand sichern: tools\\backup_savegame.ps1");
    list.textContent = "";
    setText("controlStatus", "");
    renderLights(data.lights, control);
    return;
  }

  if (!controlReady) {
    setPill("controlPill", "KEIN KANAL", "warn");
    setText("controlHint", "Die Mod meldet keinen lesbaren Befehlsweg. FS25 erlaubt Mods nur das "
      + "Schreiben von Dateien; gelesen wird über die XML-API des Spiels. Im log.txt steht unter "
      + "„Steuerkanal“, welche Pfade getestet wurden.");
    list.textContent = "";
    renderLights(data.lights, control);
    renderCommandStatus(control);
    return;
  }

  setPill("controlPill", "AKTIV", "warn");
  setText("controlHint", "Ein Befehl nach dem anderen, nur für Geräte, die es unterstützen. "
    + "Der Zustand kommt aus dem Spiel, nicht aus dem Klick."
    + (control.path ? " · " + control.path : ""));

  const implements_ = Array.isArray(data.implements) ? data.implements : [];
  list.textContent = "";
  setPill("devicePill", implements_.length === 0 ? "KEINE" : implements_.length + " GERÄTE",
    implements_.length === 0 ? "muted" : "");

  if (implements_.length === 0) {
    list.appendChild(element("p", "subtle", "Kein Gerät angehängt – nichts zu bedienen."));
  }

  implements_.forEach((item, index) => {
    const row = element("div", "control-row");
    row.appendChild(element("strong", null, (index + 1) + " · " + (item.name || "Gerät")));

    const buttons = element("div", "control-buttons");
    if (item.canLower) {
      const doLower = item.lowered !== true;
      addControlButton(buttons, doLower ? "Senken" : "Heben", doLower ? "lower" : "raise", index + 1);
    }
    if (item.canFold) {
      addControlButton(buttons, item.unfolded === true ? "Klappen" : "Aufklappen", "toggleFold", index + 1);
    }
    if (item.canOn) {
      const doOn = item.on !== true;
      addControlButton(buttons, doOn ? "Einschalten" : "Ausschalten", doOn ? "turnOn" : "turnOff", index + 1);
    }
    if (buttons.childElementCount === 0) {
      buttons.appendChild(element("span", "subtle", "keine Steuerfunktion erkannt"));
    }
    row.appendChild(buttons);
    list.appendChild(row);
  });

  renderLights(data.lights, control);
  renderCommandStatus(control);
}

function addControlButton(parent, label, action, target) {
  const button = element("button", "control-button", label);
  button.type = "button";
  button.disabled = commandPending;
  button.addEventListener("click", () => sendCommand(action, target));
  parent.appendChild(button);
}

function setStatus(text, name) {
  const element_ = $("controlStatus");
  if (!element_) return;
  element_.className = "control-status" + (name ? " " + name : "");
  element_.textContent = text;
}

function renderCommandStatus(control) {
  if (control.pending) {
    setStatus("Befehl " + control.pending.id + " läuft: " + control.pending.action
      + " auf „" + (control.pending.name || "Fahrzeug") + "“ …", "pending");
    return;
  }
  const last = control.last;
  if (!last) {
    setStatus("Noch kein Befehl gesendet.", "");
    return;
  }

  const head = "Befehl " + last.id + ": " + last.action + " auf „" + (last.name || "Fahrzeug") + "“ – ";
  if (last.status !== "ok") {
    setStatus(head + (last.message || last.status), "error");
    return;
  }
  if (last.confirmed === true) {
    let text = CONFIRM_TEXT[EXPECTED_TEXT[last.action]] || "bestätigt vom Spiel";
    if (last.action === "toggleFold") text = "bestätigt: Klappzustand geändert";
    if (last.action === "lightNext" || last.action === "lightPrev") text = "ausgelöst (Lichtstufe)";
    setStatus(head + text, "ok");
    return;
  }
  if (last.confirmed === false) {
    setStatus(head + (last.message || "ausgelöst") + " – Spielzustand noch nicht bestätigt", "warn");
    return;
  }
  setStatus(head + (last.message || "ausgelöst"), "");
}

async function sendCommand(action, target) {
  commandPending = true;
  document.querySelectorAll(".control-button, .light-button").forEach((button) => {
    button.disabled = true;
  });
  setStatus("Befehl wird gesendet: " + action + " …", "pending");

  try {
    const response = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ action: action, target: target }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.ok !== true) {
      setStatus(body.error || ("Server hat den Befehl abgelehnt (HTTP " + response.status + ")"), "error");
    }
  } catch (error) {
    setStatus("Server nicht erreichbar – Befehl nicht gesendet.", "error");
  }

  await poll();
  scheduleNextPoll(300);
}

// --- Befehlsprotokoll -----------------------------------------------------
const COMMAND_STATUS_TEXT = {
  ok: "ok",
  error: "Fehler",
  unsupported: "nicht unterstützt",
  denied: "vom Spiel abgelehnt",
  timeout: "keine Rückmeldung",
  targetChanged: "Gerät gewechselt",
  pending: "läuft",
};

function renderCommandLog(commands) {
  const container = $("commandLog");
  if (!container) return;
  container.textContent = "";

  if (!Array.isArray(commands) || commands.length === 0) {
    container.appendChild(element("p", "subtle", "Noch keine Befehle im Protokoll."));
    return;
  }

  commands.slice().reverse().forEach((entry) => {
    const row = element("div", "log-row");
    row.appendChild(element("span", "log-time", formatClock(entry.ts)));
    row.appendChild(element("span", "log-text",
      entry.action + " · " + (entry.name || "Ziel " + entry.target)));
    const status = entry.status || "?";
    row.appendChild(element("span", "log-state " + (status === "ok" && entry.confirmed !== false ? "ok" : "warn"),
      (COMMAND_STATUS_TEXT[status] || status)
      + (entry.confirmed === true ? " · bestätigt" : entry.confirmed === false ? " · unbestätigt" : "")));
    container.appendChild(row);
  });
}

async function loadCommandLog() {
  try {
    const response = await fetch("/api/commands?limit=12", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const body = await response.json();
    renderCommandLog(body.commands);
  } catch (error) {
    /* Protokoll ist Zusatzinfo */
  }
}

// --- Verlauf --------------------------------------------------------------
function clearCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = (canvas.clientWidth || 600);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(220 * ratio);
  const context = canvas.getContext("2d");
  if (context) context.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (context) context.clearRect(0, 0, width, 220);
}

function drawHistory(canvas, points) {
  if (!canvas) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 600;
  const height = 220;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  canvas.style.height = height + "px";

  const context = canvas.getContext("2d");
  if (!context) return;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  if (!points || points.length < 2) return;

  const pad = { left: 44, right: 50, top: 14, bottom: 22 };
  const plotWidth = Math.max(10, width - pad.left - pad.right);
  const plotHeight = Math.max(10, height - pad.top - pad.bottom);

  let speedMax = 0;
  let rpmMax = 0;
  points.forEach((point) => {
    speedMax = Math.max(speedMax, num(point.speedMax) || num(point.speed) || 0);
    rpmMax = Math.max(rpmMax, num(point.rpmMax) || num(point.rpm) || 0);
  });
  speedMax = Math.max(10, Math.ceil(speedMax / 10) * 10);
  rpmMax = Math.max(1000, Math.ceil(rpmMax / 500) * 500);

  context.strokeStyle = "#2b3b30";
  context.fillStyle = "#93a397";
  context.font = "10px Inter, Segoe UI, sans-serif";
  context.lineWidth = 1;
  for (let step = 0; step <= 2; step += 1) {
    const y = pad.top + (plotHeight / 2) * step;
    context.beginPath();
    context.moveTo(pad.left, y);
    context.lineTo(pad.left + plotWidth, y);
    context.stroke();
    context.textAlign = "right";
    context.fillText(fmt(speedMax - (speedMax / 2) * step, 0), pad.left - 6, y + 3);
    context.textAlign = "left";
    context.fillText(fmt(rpmMax - (rpmMax / 2) * step, 0), pad.left + plotWidth + 6, y + 3);
  }

  const first = num(points[0].t) || 0;
  const last = num(points[points.length - 1].t) || (first + 1);
  const xOf = (index) => pad.left + (plotWidth * index) / (points.length - 1);
  const yOf = (value, maximum) =>
    pad.top + plotHeight - (plotHeight * Math.max(0, Math.min(value, maximum))) / maximum;

  const drawSeries = (key, maximum, color) => {
    context.strokeStyle = color;
    context.lineWidth = 2;
    context.beginPath();
    let started = false;
    points.forEach((point, index) => {
      const value = num(point[key]);
      if (value === null) return;
      const x = xOf(index);
      const y = yOf(value, maximum);
      if (!started) { context.moveTo(x, y); started = true; } else { context.lineTo(x, y); }
    });
    if (started) context.stroke();
  };

  drawSeries("rpm", rpmMax, "#f0b35c");
  drawSeries("speed", speedMax, "#a5e34d");

  context.fillStyle = "#93a397";
  context.textAlign = "left";
  context.fillText(formatClock(first), pad.left, height - 6);
  context.textAlign = "right";
  context.fillText(formatClock(last), pad.left + plotWidth, height - 6);
  context.textAlign = "center";
  context.fillStyle = "#a5e34d";
  context.fillText("km/h", pad.left + plotWidth / 2, pad.top - 2);
  context.fillStyle = "#f0b35c";
  context.fillText("U/min", pad.left + plotWidth / 2, pad.top + plotHeight + 14);
}

function renderHistory(series) {
  const canvas = $("historyChart");
  if (!canvas) return;
  lastSeries = series;

  setPill("historyPill", series && series.minutes ? Math.round(series.minutes) + " MIN" : HISTORY_MINUTES + " MIN", "muted");

  if (!series || series.available !== true) {
    setText("historyStats", "Datenbank nicht verfügbar.");
    setText("historyNote", series && series.error ? "Fehler: " + series.error : "Kein Verlauf.");
    clearCanvas(canvas);
    return;
  }

  const summary = series.summary || {};
  const hoursDriven = num(summary.hoursDriven);
  setText("historyStats", [
    fmt(summary.samples, 0) + " Messungen",
    "max " + fmt(summary.speedMax, 1) + " km/h",
    "max " + fmt(summary.rpmMax, 0) + " U/min",
    series.cached ? "aus Zwischenspeicher" : null,
  ].filter(Boolean).join(" · "));

  setText("histSamples", fmt(summary.samples, 0));
  setText("histSpeed", fmt(summary.speedMax, 1) + " km/h");
  setText("histRpm", fmt(summary.rpmMax, 0) + " U/min");
  setText("histHours", hoursDriven === null ? "--" : fmt(hoursDriven, 2) + " h");

  setText("historyNote", (series.points || []).length < 2
    ? "Noch zu wenig Daten für ein Diagramm – ein paar Minuten fahren."
    : "Ein Punkt je " + fmt(series.bucketSeconds, 0) + " s Werten (Ø Geschwindigkeit grün, Ø Drehzahl orange).");

  drawHistory(canvas, series.points || []);
}

async function loadHistory() {
  try {
    const response = await fetch("/api/history?minutes=" + HISTORY_MINUTES + "&buckets=120", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    renderHistory(await response.json());
  } catch (error) {
    setText("historyNote", "Verlauf nicht abrufbar (Server nicht erreichbar).");
  }
}

// --- Hauptanzeige ---------------------------------------------------------
function renderPerformance(performance, storage) {
  const perf = performance || {};
  const sampler = perf.samplerTotal;
  const read = perf.samplerRead;
  const store = perf.samplerStore;
  const ms = (entry) => entry ? fmt(entry.avgMs, 2) + " / " + fmt(entry.maxMs, 2) + " ms" : "--";

  setText("perfSample", ms(sampler));
  setText("perfRead", ms(read));
  setText("perfStore", ms(store));
  setText("perfPayload", formatBytes(perf.payloadBytes));
  setText("perfRequests", perf.counters ? fmt(perf.counters.apiRequests, 0) : "--");

  const db = storage || {};
  if (db.enabled === false) {
    setText("diagDb", "aus");
    setText("perfRows", "--");
  } else if (db.available === true) {
    setText("diagDb", formatBytes(db.sizeBytes));
    setText("perfRows", fmt(db.rows, 0) + (db.skippedSamples ? " (übersprungen: " + fmt(db.skippedSamples, 0) + ")" : ""));
  } else {
    setText("diagDb", "nicht verfügbar" + (db.error ? ": " + db.error : ""));
    setText("perfRows", "--");
  }
}

function renderDiagnostics(data) {
  const control = data.control || {};
  setText("diagChannel", CHANNEL_NAMES[data.channel] || "keine Daten");
  setText("diagLog", data.logPath || "--");
  setText("diagSource", data.logPathSource || "--");
  const growth = num(data.logGrowthPerHour);
  setText("diagLogSize", formatBytes(data.logSizeBytes));
  setText("diagRate", growth === null ? "Messung läuft…" : "+" + formatBytes(growth) + " pro Stunde");
  const serverTime = num(data.serverTime);
  setText("diagServer", serverTime === null ? "--" : new Date(serverTime * 1000).toLocaleTimeString("de-DE"));

  setText("modPill", data.modSeen ? "MOD SCHREIBT" : "KEINE MOD-ZEILE");
  toggleClass("modPill", "muted", !data.modSeen);

  setText("diagCmdMode", control.mode || "keine Meldung");
  setPill("cmdModePill", control.mode === "xml" ? "BEREIT" : "KEIN KANAL",
    control.mode === "xml" ? "" : "muted");
  setText("diagCmdPath", control.path || "--");
  setText("diagCmdReadErrors", fmt(control.readErrors, 0));
  setText("diagCmdWriteErrors", fmt(control.writeErrors, 0));

  renderPerformance(data.performance, data.storage);
}

function render(data) {
  lastReading = data;
  const connected = Boolean(data.connected);
  const stale = Boolean(data.stale);
  const hasData = num(data.updatedAt) !== null;

  ageBase = num(data.ageSeconds);
  ageBaseAt = Date.now();
  const limit = num(data.staleSeconds);
  if (limit !== null && limit > 0) staleLimit = limit;

  if (connected) {
    setDot("online");
    setText("connectionText", "Telemetrie aktiv");
  } else if (stale) {
    setDot("warn");
    setText("connectionText", "Daten veraltet");
  } else if (hasData) {
    setDot("warn");
    setText("connectionText", "Keine frischen Daten");
  } else {
    setDot("offline");
    setText("connectionText", "Keine Telemetrie");
  }
  document.body.classList.toggle("stale", stale || (hasData && !connected));

  const vehicleLabel = data.vehicle || (data.state === "foot" ? "Kein Fahrzeug gesteuert" : "Warte auf LS25");
  setText("vehicleName", vehicleLabel);
  setText("topVehicle", data.vehicle || "—");
  setText("message", data.message || "");
  setText("speed", fmt(data.speed, 1));

  const rpm = num(data.rpm);
  const rpmMin = num(data.rpmMin);
  const rpmMax = num(data.rpmMax);
  setText("rpm", fmt(rpm, 0));
  let rpmPercent = 0;
  if (rpm !== null) {
    const low = rpmMin === null ? 0 : rpmMin;
    const high = rpmMax !== null && rpmMax > low ? rpmMax : Math.max(DEFAULT_RPM_MAX, rpm);
    if (high > low) rpmPercent = ((rpm - low) / (high - low)) * 100;
  }
  setBar("rpmBar", rpmPercent);
  const load = num(data.load);
  setText("rpmFoot", "Motorlast: " + (load === null ? "--" : fmt(load, 0) + " %")
    + " · Skala " + fmt(rpmMin === null ? 0 : rpmMin, 0) + "–" + fmt(rpmMax === null ? DEFAULT_RPM_MAX : rpmMax, 0));

  const fuel = num(data.fuel);
  const capacity = num(data.fuelCapacity);
  const unit = FUEL_UNITS[data.fuelType] || "Liter";
  setText("fuel", fmt(fuel, 1));
  setText("fuelUnit", unit);
  setBar("fuelBar", fuel !== null && capacity !== null && capacity > 0 ? (fuel / capacity) * 100 : 0);
  if (fuel === null) {
    setText("fuelCapacity", "Füllstand nicht verfügbar");
  } else if (capacity !== null && capacity > 0) {
    setText("fuelCapacity", fmt((fuel / capacity) * 100, 0) + " % von " + fmt(capacity, 1) + " " + unit);
  } else {
    setText("fuelCapacity", "Kapazität unbekannt");
  }

  const engine = data.engine;
  setText("engine", engine === true ? "Läuft" : engine === false ? "Aus" : "Unbekannt");
  toggleClass("engine", "on", engine === true);
  toggleClass("engine", "off", engine === false);
  setText("engineFoot", engine === null || engine === undefined
    ? "getIsMotorStarted() lieferte keinen Wert"
    : "getIsMotorStarted() der Spiel-Engine");

  const hours = num(data.hours);
  setText("hours", fmt(hours, 1));
  setText("hoursFoot", hours === null ? "Nicht verfügbar" : "getOperatingTime()");

  setText("x", fmt(data.x, 1));
  setText("y", fmt(data.y, 1));
  setText("z", fmt(data.z, 1));
  const driving = data.state === "vehicle";
  setPill("posPill", driving ? "LIVE" : "KEIN FAHRZEUG", driving ? "" : "muted");

  renderImplements(data.implements);
  renderControl(data);
  renderDiagnostics(data);

  const recommended = num(data.pollMs);
  if (recommended !== null && recommended >= 100) pollMs = recommended;
  setText("diagPoll", pollMs + " ms");

  const updatedAt = num(data.updatedAt);
  setText("updated", updatedAt === null
    ? "Warte auf Daten…"
    : "Letzte Spielzeile " + new Date(updatedAt * 1000).toLocaleTimeString("de-DE"));

  tick();
}

function tick() {
  const age = currentAge();
  setText("diagAge", formatAge(age));

  const pill = $("agePill");
  if (pill) {
    if (age === null) {
      pill.hidden = true;
    } else {
      pill.hidden = false;
      pill.textContent = formatAge(age);
      pill.classList.toggle("fresh", age <= staleLimit);
      pill.classList.toggle("stale", age > staleLimit);
    }
  }
  if (age !== null && age > staleLimit) document.body.classList.add("stale");
}

async function poll() {
  try {
    const response = await fetch("/api/telemetry", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (error) {
    setDot("offline");
    setText("connectionText", "Server nicht erreichbar");
    setText("message", "Python-Server nicht erreichbar. Im Projektordner starten: python server\\server.py");
    document.body.classList.add("stale");
  }
}

function scheduleNextPoll(delay) {
  if (pollTimer !== null) clearTimeout(pollTimer);
  const wait = typeof delay === "number" ? delay : (commandPending ? 300 : pollMs);
  pollTimer = setTimeout(async () => {
    if (document.hidden) {
      scheduleNextPoll(1000);
      return;
    }
    await poll();
    scheduleNextPoll();
  }, wait);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    poll();
    scheduleNextPoll();
    if (activeTab === "history") loadHistory();
    if (activeTab === "control") loadCommandLog();
  }
});

window.addEventListener("resize", () => {
  if (activeTab === "history" && lastSeries) drawHistory($("historyChart"), lastSeries.points || []);
});

initTabs();
poll();
scheduleNextPoll();
setInterval(tick, 500);
setInterval(() => { if (!document.hidden && activeTab === "history") loadHistory(); }, HISTORY_EVERY_MS);
setInterval(() => { if (!document.hidden && activeTab === "control") loadCommandLog(); }, COMMAND_EVERY_MS);

try {
  if ("wakeLock" in navigator) navigator.wakeLock.request("screen").catch(() => {});
} catch (error) {
  /* absichtlich ignoriert */
}
