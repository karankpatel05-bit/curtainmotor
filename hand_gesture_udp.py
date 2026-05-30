"""
hand_gesture_udp.py
────────────────────────────────────────────────────────────────
Senior Computer Vision Engineer – Hand Gesture → UDP Controller
────────────────────────────────────────────────────────────────
Detects OPEN / CLOSED hand gestures via MediaPipe Hands and sends
a single-byte UDP payload to a target ESP32:
    'o'  →  hand is fully open  (all fingers extended)
    'c'  →  hand is fully closed (fist)

A 2-second debounce guard prevents flooding the UDP server while
the user holds a gesture.

Usage:
    python hand_gesture_udp.py [--ip <ESP32_IP>] [--port <PORT>] [--cam <INDEX>]

Dependencies:
    pip install opencv-python mediapipe
"""

import cv2
import mediapipe as mp
import socket
import time
import argparse
import sys

# ──────────────────────────────────────────────
# Configuration defaults (override via CLI args)
# ──────────────────────────────────────────────
DEFAULT_ESP32_IP   = "192.168.1.100"   # ← Change to your ESP32 IP
DEFAULT_UDP_PORT   = 4210
DEFAULT_CAM_INDEX  = 0
DEBOUNCE_SECONDS   = 2.0               # Cooldown between repeated UDP sends
DETECTION_CONF     = 0.75
TRACKING_CONF      = 0.75

# ──────────────────────────────────────────────
# MediaPipe landmark indices (per hand)
# ──────────────────────────────────────────────
# Tip landmarks: THUMB=4, INDEX=8, MIDDLE=12, RING=16, PINKY=20
# PIP joints   : THUMB=3, INDEX=6, MIDDLE=10, RING=14, PINKY=18
TIP_IDS = [4, 8, 12, 16, 20]
PIP_IDS = [3, 6, 10, 14, 18]   # proximal inter-phalangeal (one joint below tip)
MCP_IDS = [2, 5,  9, 13, 17]   # metacarpophalangeal (knuckle)

# ──────────────────────────────────────────────
# Gesture colours / UI constants
# ──────────────────────────────────────────────
COLOR_OPEN       = (0, 230, 100)     # Green
COLOR_CLOSED     = (0,  80, 230)     # Blue-ish red (BGR)
COLOR_NEUTRAL    = (180, 180, 180)   # Grey
COLOR_SENT_OK    = (0, 255, 200)
COLOR_SENT_FAIL  = (0,  60, 220)
COLOR_LANDMARK   = (255, 255, 255)
COLOR_CONNECTION = (100, 220, 255)

FONT       = cv2.FONT_HERSHEY_DUPLEX
FONT_SCALE = 1.1
THICKNESS  = 2


# ══════════════════════════════════════════════
# Gesture logic
# ══════════════════════════════════════════════

def is_finger_extended(landmarks, tip_id: int, pip_id: int, mcp_id: int, is_thumb: bool) -> bool:
    """
    Returns True if the finger defined by tip/pip/mcp landmark IDs is extended.

    For fingers (index → pinky): the tip y-coordinate must be above the PIP joint.
    For the thumb: we check the x-axis distance from MCP to tip vs MCP to PIP,
    since the thumb moves laterally rather than vertically.
    """
    tip = landmarks[tip_id]
    pip = landmarks[pip_id]
    mcp = landmarks[mcp_id]

    if is_thumb:
        # Thumb extended: tip is farther from MCP than PIP is along x-axis
        tip_dist = abs(tip.x - mcp.x)
        pip_dist = abs(pip.x - mcp.x)
        return tip_dist > pip_dist
    else:
        # Extended: tip is higher (smaller y) than PIP
        return tip.y < pip.y


def classify_gesture(hand_landmarks) -> str:
    """
    Classifies the hand as 'OPEN', 'CLOSED', or 'NEUTRAL'.

    OPEN   → all 5 fingers extended
    CLOSED → all 5 fingers curled (fist)
    NEUTRAL → anything in between
    """
    lm = hand_landmarks.landmark
    states = []

    for i, (tip, pip, mcp) in enumerate(zip(TIP_IDS, PIP_IDS, MCP_IDS)):
        is_thumb = (i == 0)
        states.append(is_finger_extended(lm, tip, pip, mcp, is_thumb))

    extended_count = sum(states)

    if extended_count == 5:
        return "OPEN"
    elif extended_count == 0:
        return "CLOSED"
    else:
        return "NEUTRAL"


# ══════════════════════════════════════════════
# UDP client
# ══════════════════════════════════════════════

class UDPClient:
    """Thin UDP sender with connection-less socket kept open for performance."""

    def __init__(self, ip: str, port: int):
        self.target = (ip, port)
        self._sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.1)

    def send(self, payload: bytes) -> bool:
        """Send payload; returns True on success."""
        try:
            self._sock.sendto(payload, self.target)
            return True
        except OSError as exc:
            print(f"[UDP] Send error: {exc}", file=sys.stderr)
            return False

    def close(self):
        self._sock.close()


# ══════════════════════════════════════════════
# Drawing helpers
# ══════════════════════════════════════════════

def draw_rounded_rect(img, x1, y1, x2, y2, radius, color, alpha=0.55):
    """Overlay a semi-transparent filled rounded rectangle."""
    overlay = img.copy()
    cv2.rectangle(overlay, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(overlay, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    for cx, cy in [(x1+radius, y1+radius), (x2-radius, y1+radius),
                   (x1+radius, y2-radius), (x2-radius, y2-radius)]:
        cv2.circle(overlay, (cx, cy), radius, color, -1)
    cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0, img)


def draw_hud(frame, gesture: str, last_sent: str, send_ok: bool,
             last_send_time: float, udp_target: str):
    """Draw all on-screen HUD elements."""
    h, w = frame.shape[:2]

    # ── Background panel ──────────────────────
    draw_rounded_rect(frame, 10, 10, 420, 160, 16, (30, 30, 30), alpha=0.65)

    # ── Gesture label ─────────────────────────
    color = COLOR_NEUTRAL
    if gesture == "OPEN":
        color = COLOR_OPEN
    elif gesture == "CLOSED":
        color = COLOR_CLOSED

    cv2.putText(frame, f"Gesture: {gesture}", (26, 55),
                FONT, FONT_SCALE, color, THICKNESS, cv2.LINE_AA)

    # ── Last UDP send status ──────────────────
    if last_sent:
        elapsed = time.time() - last_send_time
        status_color = COLOR_SENT_OK if send_ok else COLOR_SENT_FAIL
        status_text  = "✓ Sent" if send_ok else "✗ Failed"
        cv2.putText(frame,
                    f"UDP {status_text}: '{last_sent}'  ({elapsed:.1f}s ago)",
                    (26, 100), FONT, 0.62, status_color, 1, cv2.LINE_AA)

    # ── Cooldown bar ──────────────────────────
    if last_send_time > 0:
        elapsed   = time.time() - last_send_time
        ratio     = min(elapsed / DEBOUNCE_SECONDS, 1.0)
        bar_w     = 370
        bar_x     = 26
        bar_y     = 120
        bar_h     = 14
        # background
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                      (60, 60, 60), -1)
        # fill
        fill_color = (0, 200, 80) if ratio >= 1.0 else (0, 130, 200)
        cv2.rectangle(frame,
                      (bar_x, bar_y),
                      (bar_x + int(bar_w * ratio), bar_y + bar_h),
                      fill_color, -1)
        cv2.putText(frame, "Cooldown", (bar_x, bar_y - 4),
                    FONT, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

    # ── Target info ───────────────────────────
    cv2.putText(frame, f"Target: {udp_target}", (26, 152),
                FONT, 0.52, (140, 200, 255), 1, cv2.LINE_AA)

    # ── FPS (bottom-right) ───────────────────
    # (populated externally via draw_fps helper)


def draw_fps(frame, fps: float):
    h, w = frame.shape[:2]
    cv2.putText(frame, f"FPS: {fps:.1f}", (w - 140, h - 18),
                FONT, 0.65, (200, 200, 200), 1, cv2.LINE_AA)


def draw_landmarks(frame, hand_landmarks, mp_drawing, mp_hands):
    """Draw hand skeleton with custom colours."""
    mp_drawing.draw_landmarks(
        frame,
        hand_landmarks,
        mp_hands.HAND_CONNECTIONS,
        mp_drawing.DrawingSpec(color=COLOR_LANDMARK,   thickness=2, circle_radius=4),
        mp_drawing.DrawingSpec(color=COLOR_CONNECTION, thickness=2, circle_radius=2),
    )


# ══════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Hand Gesture → UDP Controller for ESP32"
    )
    parser.add_argument("--ip",   default=DEFAULT_ESP32_IP,  help="ESP32 IP address")
    parser.add_argument("--port", default=DEFAULT_UDP_PORT,  type=int, help="UDP port")
    parser.add_argument("--cam",  default=DEFAULT_CAM_INDEX, type=int, help="Camera index")
    return parser.parse_args()


def main():
    args = parse_args()
    udp_target_str = f"{args.ip}:{args.port}"

    print("=" * 60)
    print("  Hand Gesture UDP Controller")
    print(f"  Target : {udp_target_str}")
    print(f"  Camera : {args.cam}")
    print(f"  Debounce: {DEBOUNCE_SECONDS}s")
    print("=" * 60)
    print("  Press  Q  to quit")
    print("=" * 60)

    # ── MediaPipe setup ───────────────────────
    mp_hands   = mp.solutions.hands
    mp_drawing = mp.solutions.drawing_utils

    hands = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=DETECTION_CONF,
        min_tracking_confidence=TRACKING_CONF,
    )

    # ── UDP client ────────────────────────────
    udp = UDPClient(args.ip, args.port)

    # ── State tracking ────────────────────────
    current_gesture   = "NEUTRAL"   # Live gesture from this frame
    confirmed_gesture = "NEUTRAL"   # Last stable gesture that was sent
    last_send_time    = 0.0
    last_sent_label   = ""
    last_send_ok      = False

    # FPS tracking
    fps_prev_time = time.time()
    fps           = 0.0

    # ── Camera ────────────────────────────────
    cap = cv2.VideoCapture(args.cam)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera index {args.cam}", file=sys.stderr)
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)

    print("\n[INFO] Camera opened. Waiting for hand gesture…\n")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[WARN] Empty frame; retrying…")
                continue

            # ── Mirror + convert ──────────────
            frame = cv2.flip(frame, 1)
            rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            # ── MediaPipe inference ───────────
            results = hands.process(rgb)

            current_gesture = "NEUTRAL"

            if results.multi_hand_landmarks:
                for hand_lm in results.multi_hand_landmarks:
                    draw_landmarks(frame, hand_lm, mp_drawing, mp_hands)
                    current_gesture = classify_gesture(hand_lm)
                    break   # Only process the first detected hand

            # ── State-change + debounce logic ─
            now = time.time()
            cooldown_elapsed = (now - last_send_time) >= DEBOUNCE_SECONDS

            if (current_gesture in ("OPEN", "CLOSED")
                    and current_gesture != confirmed_gesture
                    and cooldown_elapsed):

                payload = b'o' if current_gesture == "OPEN" else b'c'
                ok = udp.send(payload)

                confirmed_gesture = current_gesture
                last_send_time    = now
                last_sent_label   = payload.decode()
                last_send_ok      = ok

                status = "OK" if ok else "FAILED"
                print(f"[UDP] Sent '{last_sent_label}' → {udp_target_str}  [{status}]  "
                      f"(gesture changed to {current_gesture})")

            # ── HUD ───────────────────────────
            draw_hud(frame, current_gesture, last_sent_label,
                     last_send_ok, last_send_time, udp_target_str)

            # ── FPS ───────────────────────────
            now_fps   = time.time()
            fps       = 1.0 / max(now_fps - fps_prev_time, 1e-6)
            fps_prev_time = now_fps
            draw_fps(frame, fps)

            cv2.imshow("Hand Gesture UDP Controller", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("\n[INFO] Quit requested.")
                break

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
    finally:
        cap.release()
        hands.close()
        udp.close()
        cv2.destroyAllWindows()
        print("[INFO] Resources released. Goodbye.")


if __name__ == "__main__":
    main()
