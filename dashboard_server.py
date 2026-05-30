"""
dashboard_server.py  –  Curtain OS Dashboard Server
═══════════════════════════════════════════════════
Architecture (mirrors navis-LLM app.py pattern):
  Browser ◄─ HTTP/WS ─► Flask+SocketIO ─── UDP ──► ESP32 :4210
                               │
                            Webcam (MediaPipe background thread)

Endpoints:
  GET  /             → Dashboard HTML
  GET  /video_feed   → MJPEG camera stream
  POST /api/command  → {cmd: 'o'|'c'|'s'}
  POST /api/camera   → {enable: true|false}
  POST /api/config   → {esp32_ip, udp_port, cam_index, debounce}
  GET  /api/status   → Current state JSON
  GET  /health       → Health check
  WS   /socket.io    → Real-time state push
"""

from flask import Flask, render_template, Response, request, jsonify
from flask_socketio import SocketIO, emit
import cv2
import mediapipe as mp
import socket
import threading
import time
import os

# ── App init (mirrors navis app.py) ───────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'curtain-os-robo-manthan'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# ── Runtime config (editable via UI) ──────────────────────────
config = {
    'esp32_ip':  '192.168.1.100',   # ← Change to your ESP32 IP
    'udp_port':  4210,
    'cam_index': 0,
    'debounce':  2.0,               # seconds between repeated gesture sends
}

# ── Shared motor state (protected by lock) ────────────────────
_lock = threading.Lock()
state = {
    'motor':             'STOPPED',   # STOPPED | OPENING | CLOSING
    'visual':            'closed',    # open | closed | opening | closing
    'gesture':           'NEUTRAL',   # OPEN | CLOSED | NEUTRAL
    'confirmed_gesture': 'NEUTRAL',
    'camera_enabled':    True,        # Auto-start camera on boot
    'last_cmd':          '',
    'last_cmd_ok':       False,
    'last_cmd_time':     0.0,
    'last_gesture_time': 0.0,
}

# ── UDP sender ─────────────────────────────────────────────────
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_sock.settimeout(0.2)

def send_udp(cmd: str) -> bool:
    try:
        _udp_sock.sendto(cmd.encode(), (config['esp32_ip'], config['udp_port']))
        return True
    except OSError as e:
        print(f'[UDP] Error: {e}')
        return False

def dispatch_command(cmd: str, source: str = 'manual') -> bool:
    ok = send_udp(cmd)
    with _lock:
        state['last_cmd']      = cmd
        state['last_cmd_ok']   = ok
        state['last_cmd_time'] = time.time()
        if cmd == 'o':
            state['motor']  = 'OPENING'
            state['visual'] = 'opening'
        elif cmd == 'c':
            state['motor']  = 'CLOSING'
            state['visual'] = 'closing'
        elif cmd == 's':
            prev = state['motor']
            state['motor']  = 'STOPPED'
            state['visual'] = 'open' if prev == 'OPENING' else 'closed'
        # Build snapshot inline — do NOT call _snapshot() here as it
        # would try to re-acquire _lock → deadlock when called from camera thread
        snap = {**state, 'config': dict(config)}
    socketio.emit('state_update', snap)
    status = '✅' if ok else '❌'
    print(f'[CMD] {status} cmd={cmd!r}  source={source}  target={config["esp32_ip"]}:{config["udp_port"]}')
    return ok

def _snapshot():
    with _lock:
        return {**state, 'config': dict(config)}

# ── MediaPipe gesture classifier ──────────────────────────────
TIP_IDS = [4,  8, 12, 16, 20]
PIP_IDS = [3,  6, 10, 14, 18]
MCP_IDS = [2,  5,  9, 13, 17]

def _finger_extended(lm, tip, pip, mcp, is_thumb):
    if is_thumb:
        return abs(lm[tip].x - lm[mcp].x) > abs(lm[pip].x - lm[mcp].x)
    return lm[tip].y < lm[pip].y

def classify_gesture(hand_lm) -> str:
    lm = hand_lm.landmark
    n = sum(_finger_extended(lm, TIP_IDS[i], PIP_IDS[i], MCP_IDS[i], i == 0)
            for i in range(5))
    if n == 5: return 'OPEN'
    if n == 0: return 'CLOSED'
    return 'NEUTRAL'

# ── Camera / gesture background thread ────────────────────────
import base64
_cam_stop   = threading.Event()
_cam_thread = None

def _camera_worker():
    mp_hands   = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils
    hands = mp_hands.Hands(
        static_image_mode=False, max_num_hands=1,
        min_detection_confidence=0.75, min_tracking_confidence=0.75,
    )
    cap = cv2.VideoCapture(config['cam_index'])
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # Always grab the latest frame
    print('[CAM] Thread started (640x480 – WS frame push)')

    frame_count  = 0
    last_results = None
    last_push    = 0.0            # Throttle: push frame every ~80 ms (≈12 fps)
    PUSH_INTERVAL = 0.08

    while not _cam_stop.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.02)
            continue

        frame = cv2.flip(frame, 1)
        frame_count += 1

        # MediaPipe every 2nd frame
        if frame_count % 2 == 0:
            rgb          = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            last_results = hands.process(rgb)

        results         = last_results
        current_gesture = 'NEUTRAL'
        hand_present    = bool(results and results.multi_hand_landmarks)

        if results and results.multi_hand_landmarks:
            for hlm in results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(
                    frame, hlm, mp_hands.HAND_CONNECTIONS,
                    mp_drawing.DrawingSpec(color=(255, 255, 255), thickness=2, circle_radius=3),
                    mp_drawing.DrawingSpec(color=(0, 212, 255),   thickness=1),
                )
                current_gesture = classify_gesture(hlm)
                break

        # HUD
        color_map = {'OPEN': (52, 211, 153), 'CLOSED': (247, 147, 30), 'NEUTRAL': (180, 180, 180)}
        cv2.putText(frame, f'Gesture: {current_gesture}',
                    (14, 40), cv2.FONT_HERSHEY_DUPLEX, 0.9,
                    color_map.get(current_gesture, (180, 180, 180)), 2, cv2.LINE_AA)

        # Gesture → UDP logic
        with _lock:
            cam_on    = state['camera_enabled']
            confirmed = state['confirmed_gesture']
            last_t    = state['last_gesture_time']

        if cam_on:
            now = time.time()
            if hand_present:
                if (current_gesture in ('OPEN', 'CLOSED')
                        and current_gesture != confirmed
                        and (now - last_t) >= config['debounce']):
                    cmd = 'o' if current_gesture == 'OPEN' else 'c'
                    with _lock:
                        state['confirmed_gesture'] = current_gesture
                        state['last_gesture_time'] = now
                    dispatch_command(cmd, source='gesture')
            else:
                if confirmed != 'NEUTRAL' and (now - last_t) >= config['debounce']:
                    with _lock:
                        state['confirmed_gesture'] = 'NEUTRAL'
                        state['last_gesture_time'] = now
                    dispatch_command('s', source='no-hand-failsafe')

        with _lock:
            state['gesture'] = current_gesture

        # ── Push frame via WebSocket (throttled) ──────────────
        now = time.time()
        if now - last_push >= PUSH_INTERVAL:
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
            b64    = base64.b64encode(buf).decode('utf-8')
            socketio.emit('camera_frame', {'data': b64})
            last_push = now

    cap.release()
    hands.close()
    print('[CAM] Thread stopped')

def start_camera():
    global _cam_thread
    _cam_stop.clear()
    _cam_thread = threading.Thread(target=_camera_worker, daemon=True)
    _cam_thread.start()

def stop_camera():
    _cam_stop.set()
    if _cam_thread:
        _cam_thread.join(timeout=3)
    socketio.emit('camera_frame', {'data': None})  # Signal browser: feed stopped

# ── State heartbeat broadcast ──────────────────────────────────
def _broadcaster():
    while True:
        socketio.emit('state_update', _snapshot())
        time.sleep(1)

# ── Routes (mirrors navis route style) ────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

# /video_feed removed – frames now pushed over WebSocket (see camera_frame event)

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.json or {}
    cmd  = data.get('cmd', '')
    if cmd not in ('o', 'c', 's'):
        return jsonify({'error': 'Invalid command. Use o / c / s'}), 400
    ok = dispatch_command(cmd, source='manual-ui')
    return jsonify({'success': ok, 'state': _snapshot()})

@app.route('/api/camera', methods=['POST'])
def api_camera():
    data   = request.json or {}
    enable = bool(data.get('enable', False))
    with _lock:
        state['camera_enabled'] = enable
        if not enable:
            state['confirmed_gesture'] = 'NEUTRAL'
            state['gesture']           = 'NEUTRAL'
    if enable:
        start_camera()
    else:
        stop_camera()
    socketio.emit('state_update', _snapshot())
    return jsonify({'success': True, 'camera_enabled': enable})

@app.route('/api/config', methods=['POST'])
def api_config():
    data = request.json or {}
    if 'esp32_ip'  in data: config['esp32_ip']  = data['esp32_ip']
    if 'udp_port'  in data: config['udp_port']  = int(data['udp_port'])
    if 'cam_index' in data: config['cam_index'] = int(data['cam_index'])
    if 'debounce'  in data: config['debounce']  = float(data['debounce'])
    return jsonify({'success': True, 'config': config})

@app.route('/api/status')
def api_status():
    return jsonify(_snapshot())

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'esp32': config['esp32_ip']})

# ── SocketIO events ────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    emit('state_update', _snapshot())
    print('[WS] Client connected')

@socketio.on('command')
def on_ws_command(data):
    cmd = data.get('cmd', '')
    if cmd in ('o', 'c', 's'):
        dispatch_command(cmd, source='ws-manual')

# ── Entry point (mirrors navis main block) ─────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    threading.Thread(target=_broadcaster, daemon=True).start()

    # Auto-start camera so gesture detection is live immediately
    print('[CAM] Auto-starting camera on index', config['cam_index'])
    start_camera()

    print('\n🪟  Curtain OS – Dashboard Server  |  Robo Manthan')
    print(f'   ESP32  : {config["esp32_ip"]}:{config["udp_port"]}')
    print(f'   Camera : index {config["cam_index"]} (auto-started)')
    print(f'   🌐 Open: http://localhost:{port}\n')

    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
