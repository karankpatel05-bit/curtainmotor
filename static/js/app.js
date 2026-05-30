/**
 * app.js  –  Curtain OS Dashboard
 * Communicates with dashboard_server.py via:
 *   - Socket.IO  (real-time state push, same pattern as navis-LLM)
 *   - REST POST  /api/command, /api/camera, /api/config
 */

// ── Socket.IO connection ──────────────────────────────────────
const socket = io();

// ── DOM refs ──────────────────────────────────────────────────
const connDot        = document.getElementById('connDot');
const connLabel      = document.getElementById('connLabel');
const statePill      = document.getElementById('statePill');
const statePillText  = document.getElementById('statePillText');
const curtainLeft    = document.getElementById('curtainLeft');
const curtainRight   = document.getElementById('curtainRight');
const lastCmdInfo    = document.getElementById('lastCmdInfo');
const btnOpen        = document.getElementById('btnOpen');
const btnStop        = document.getElementById('btnStop');
const btnClose       = document.getElementById('btnClose');
const cameraToggle   = document.getElementById('cameraToggle');
const cameraSub      = document.getElementById('cameraSub');
const gestureVal     = document.getElementById('gestureVal');
const cooldownFill   = document.getElementById('cooldownFill');
const cooldownPct    = document.getElementById('cooldownPct');
const udpTarget      = document.getElementById('udpTarget');
const udpStatus      = document.getElementById('udpStatus');
const udpStatusText  = document.getElementById('udpStatusText');
const feedStatus     = document.getElementById('feedStatus');
const cameraImg      = document.getElementById('cameraImg');
const cameraPlaceholder = document.getElementById('cameraPlaceholder');
const settingsBtn    = document.getElementById('settingsBtn');
const settingsPanel  = document.getElementById('settingsPanel');
const closePanelBtn  = document.getElementById('closePanelBtn');
const overlay        = document.getElementById('overlay');
const btnSaveConfig  = document.getElementById('btnSaveConfig');
const toastContainer = document.getElementById('toastContainer');

// Config inputs
const cfgIp       = document.getElementById('cfgIp');
const cfgPort     = document.getElementById('cfgPort');
const cfgCam      = document.getElementById('cfgCam');
const cfgDebounce = document.getElementById('cfgDebounce');

// ── Local state mirror ─────────────────────────────────────────
let lastCmdTime   = 0;
let debounceMs    = 2000;
let cooldownTimer = null;

// ── Socket.IO events ───────────────────────────────────────────
socket.on('connect', () => {
    connDot.classList.remove('offline');
    connLabel.textContent = 'Connected';
    console.log('[WS] Connected');
});

socket.on('disconnect', () => {
    connDot.classList.add('offline');
    connLabel.textContent = 'Disconnected';
});

socket.on('state_update', (data) => {
    applyState(data);
});

// ── Apply state from server ─────────────────────────────────────
function applyState(data) {
    // Motor state pill
    const visual = data.visual || 'closed';
    updateStatePill(visual, data.motor);

    // Curtain animation
    setCurtainState(visual);

    // Last command info
    if (data.last_cmd) {
        const cmdMap = { o: 'OPEN', c: 'CLOSE', s: 'STOP' };
        const ago    = data.last_cmd_time ? ((Date.now() / 1000 - data.last_cmd_time)).toFixed(1) : '?';
        const ok     = data.last_cmd_ok;
        lastCmdInfo.innerHTML =
            `Last: <strong>${cmdMap[data.last_cmd] || data.last_cmd}</strong> · ${ago}s ago · ` +
            `<span style="color:${ok ? 'var(--green)' : 'var(--red)'}">${ok ? '✓ Sent' : '✗ Failed'}</span>`;
    }

    // Gesture display
    const g = data.gesture || 'NEUTRAL';
    gestureVal.textContent = g;
    gestureVal.className   = 'gesture-val ' + {
        OPEN: 'g-open', CLOSED: 'g-closed', NEUTRAL: 'g-neutral'
    }[g];

    // Camera toggle (sync without triggering change event)
    const camEnabled = Boolean(data.camera_enabled);
    if (cameraToggle.checked !== camEnabled) {
        cameraToggle.checked = camEnabled;
        updateCameraUI(camEnabled);
    }

    // UDP info
    if (data.config) {
        const cfg = data.config;
        udpTarget.textContent = `${cfg.esp32_ip}:${cfg.udp_port}`;
        debounceMs            = (cfg.debounce || 2) * 1000;
        // Populate settings fields if empty
        if (!cfgIp.value)       cfgIp.value       = cfg.esp32_ip;
        if (!cfgPort.value)     cfgPort.value      = cfg.udp_port;
        if (!cfgCam.value)      cfgCam.value       = cfg.cam_index;
        if (!cfgDebounce.value) cfgDebounce.value  = cfg.debounce;
    }
    if (data.last_cmd_ok !== undefined) {
        udpStatus.className    = data.last_cmd_ok ? 'udp-ok' : 'udp-fail';
        udpStatusText.textContent = data.last_cmd_ok ? 'OK' : 'Fail';
    }

    // Cooldown bar
    if (data.last_cmd_time && data.last_cmd_time > 0) {
        lastCmdTime = data.last_cmd_time * 1000;
        startCooldownAnimation();
    }
}

// ── Curtain state classes ─────────────────────────────────────
const CURTAIN_STATES = ['s-open', 's-closed', 's-opening', 's-closing', 's-stopped'];

function setCurtainState(visual) {
    CURTAIN_STATES.forEach(c => {
        curtainLeft.classList.remove(c);
        curtainRight.classList.remove(c);
    });
    const cls = `s-${visual}`;
    curtainLeft.classList.add(cls);
    curtainRight.classList.add(cls);
}

// ── State pill ────────────────────────────────────────────────
const PILL_STATES = ['s-open', 's-closed', 's-opening', 's-closing', 's-stopped'];
const PILL_LABELS = {
    stopped: 'STOPPED', opening: 'OPENING…', closing: 'CLOSING…', open: 'OPEN', closed: 'CLOSED'
};

function updateStatePill(visual, motor) {
    PILL_STATES.forEach(c => statePill.classList.remove(c));
    statePill.classList.add(`s-${visual}`);
    statePillText.textContent = PILL_LABELS[visual] || motor;
}

// ── Cooldown progress bar ─────────────────────────────────────
function startCooldownAnimation() {
    if (cooldownTimer) clearInterval(cooldownTimer);
    cooldownTimer = setInterval(() => {
        const elapsed  = Date.now() - lastCmdTime;
        const pct      = Math.min(elapsed / debounceMs * 100, 100);
        cooldownFill.style.width = pct + '%';
        cooldownFill.className   = 'cooldown-fill' + (pct >= 100 ? '' : ' waiting');
        cooldownPct.textContent  = pct >= 100 ? 'Ready' : `${((debounceMs - elapsed) / 1000).toFixed(1)}s`;
        if (pct >= 100) clearInterval(cooldownTimer);
    }, 100);
}

// ── Motor button handlers ─────────────────────────────────────
async function sendCommand(cmd) {
    try {
        const res  = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cmd }),
        });
        const data = await res.json();
        if (!data.success) toast('UDP send failed – check ESP32 IP', 'error');
    } catch (e) {
        toast('Server error: ' + e.message, 'error');
    }
}

btnOpen.addEventListener('click',  () => sendCommand('o'));
btnStop.addEventListener('click',  () => sendCommand('s'));
btnClose.addEventListener('click', () => sendCommand('c'));

// ── Camera toggle ─────────────────────────────────────────────
function updateCameraUI(enabled) {
    if (enabled) {
        cameraPlaceholder.style.display = 'none';
        cameraImg.style.display         = 'block';
        feedStatus.textContent          = 'Live';
        feedStatus.style.color          = 'var(--green)';
        cameraSub.textContent           = 'Gesture detection active';
    } else {
        cameraImg.style.display         = 'none';
        cameraImg.src                   = '';
        cameraPlaceholder.style.display = 'flex';
        feedStatus.textContent          = 'Disabled';
        feedStatus.style.color          = 'var(--text-3)';
        cameraSub.textContent           = 'Enable hand gesture detection';
    }
}

// ── Camera frame via WebSocket ────────────────────────────
socket.on('camera_frame', (msg) => {
    if (msg.data) {
        cameraImg.src = 'data:image/jpeg;base64,' + msg.data;
    } else {
        cameraImg.style.display         = 'none';
        cameraPlaceholder.style.display = 'flex';
    }
});

// ── Camera toggle button ──────────────────────────────────
cameraToggle.addEventListener('change', async () => {
    const enable = cameraToggle.checked;
    try {
        await fetch('/api/camera', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enable }),
        });
        updateCameraUI(enable);
        toast(enable ? '🎥 Camera ON' : '📷 Camera disabled', enable ? 'success' : '');
    } catch (e) {
        cameraToggle.checked = !enable;
        toast('Failed to toggle camera', 'error');
    }
});

// ── Settings panel (Navis training-panel slide pattern) ────────
settingsBtn.addEventListener('click', () => {
    settingsPanel.classList.add('open');
    overlay.classList.add('active');
});
closePanelBtn.addEventListener('click', closeSettings);
overlay.addEventListener('click', closeSettings);
function closeSettings() {
    settingsPanel.classList.remove('open');
    overlay.classList.remove('active');
}

btnSaveConfig.addEventListener('click', async () => {
    const payload = {
        esp32_ip:  cfgIp.value.trim(),
        udp_port:  parseInt(cfgPort.value) || 4210,
        cam_index: parseInt(cfgCam.value) || 0,
        debounce:  parseFloat(cfgDebounce.value) || 2.0,
    };
    try {
        const res  = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.success) {
            udpTarget.textContent = `${payload.esp32_ip}:${payload.udp_port}`;
            debounceMs            = payload.debounce * 1000;
            toast('✅ Config saved!', 'success');
            closeSettings();
        }
    } catch (e) {
        toast('Config save failed', 'error');
    }
});

// ── Toast notifications (Navis toast pattern) ─────────────────
function toast(msg, type = '') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 3200);
}

// ── Keyboard shortcuts ────────────────────────────────────────
document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'o' || e.key === 'O') sendCommand('o');
    if (e.key === 'c' || e.key === 'C') sendCommand('c');
    if (e.key === 's' || e.key === 'S') sendCommand('s');
});
