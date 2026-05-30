/**
 * ============================================================
 *  ESP32 UDP Curtain Controller – Dooya DT52EN / Dry Contact
 * ============================================================
 *  Hardware:
 *    Relay 1 (OPEN)  → GPIO 18   Active-LOW
 *    Relay 2 (CLOSE) → GPIO 19   Active-LOW
 *
 *  UDP Commands (port 4210, single-byte payload):
 *    'o' → Open  the curtain
 *    'c' → Close the curtain
 *    's' → Stop  (pulses whichever relay is currently active)
 *
 *  Motor pulse protocol:
 *    - All signals are ACTIVE-LOW (relay module)
 *    - Drive the relay LOW for PULSE_MS (500 ms) then back HIGH
 *    - NEVER hold the relay LOW; it's a momentary dry-contact trigger
 *    - Stop = re-pulse the same relay that started the movement
 *
 *  Safety interlock:
 *    Before pulsing any relay, always force the OTHER relay HIGH
 *    to ensure both relays are never simultaneously LOW.
 * ============================================================
 */

#include <WiFi.h>
#include <WiFiUdp.h>

// ─────────────────────────────────────────────
//  USER CONFIGURATION  ← edit these
// ─────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";
const uint16_t UDP_PORT   = 4210;

// ─────────────────────────────────────────────
//  Hardware pin definitions
// ─────────────────────────────────────────────
const uint8_t RELAY_OPEN  = 18;   // Relay 1 – drives motor OPEN
const uint8_t RELAY_CLOSE = 19;   // Relay 2 – drives motor CLOSE

// ─────────────────────────────────────────────
//  Timing
// ─────────────────────────────────────────────
const uint16_t PULSE_MS = 500;    // Duration of the dry-contact pulse (ms)

// ─────────────────────────────────────────────
//  Active-LOW relay helpers
// ─────────────────────────────────────────────
#define RELAY_ENGAGE(pin)   digitalWrite(pin, LOW)
#define RELAY_RELEASE(pin)  digitalWrite(pin, HIGH)

// ─────────────────────────────────────────────
//  Motor State Machine
// ─────────────────────────────────────────────
typedef enum {
  STATE_STOPPED,
  STATE_OPENING,
  STATE_CLOSING
} MotorState;

volatile MotorState motorState = STATE_STOPPED;

// ─────────────────────────────────────────────
//  Networking objects
// ─────────────────────────────────────────────
WiFiUDP udp;
char    packetBuffer[8];   // Single-byte command + null terminator safety margin

// ─────────────────────────────────────────────
//  Forward declarations
// ─────────────────────────────────────────────
void connectWiFi();
void pulseRelay(uint8_t pin);
void cmdOpen();
void cmdClose();
void cmdStop();
void printState();

// ═════════════════════════════════════════════
//  SETUP
// ═════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(100);

  Serial.println("\n========================================");
  Serial.println("  ESP32 Curtain Controller – Booting");
  Serial.println("========================================");

  // ── Relay GPIOs ────────────────────────────
  // Set HIGH FIRST (relays off) BEFORE configuring as OUTPUT
  // to avoid a false LOW glitch on startup
  digitalWrite(RELAY_OPEN,  HIGH);
  digitalWrite(RELAY_CLOSE, HIGH);
  pinMode(RELAY_OPEN,  OUTPUT);
  pinMode(RELAY_CLOSE, OUTPUT);

  Serial.printf("[GPIO] Relay OPEN  → GPIO %d  (HIGH/OFF)\n", RELAY_OPEN);
  Serial.printf("[GPIO] Relay CLOSE → GPIO %d  (HIGH/OFF)\n", RELAY_CLOSE);

  // ── WiFi ────────────────────────────────────
  connectWiFi();

  // ── UDP listener ────────────────────────────
  udp.begin(UDP_PORT);
  Serial.printf("[UDP]  Listening on port %d\n", UDP_PORT);

  Serial.println("========================================");
  Serial.println("  Ready. Waiting for commands…");
  Serial.println("========================================\n");
}

// ═════════════════════════════════════════════
//  LOOP  – Zero-latency UDP polling
// ═════════════════════════════════════════════
void loop() {
  // ── Reconnect if WiFi drops ─────────────────
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Connection lost – reconnecting…");
    connectWiFi();
    udp.begin(UDP_PORT);   // Re-bind after reconnect
  }

  // ── Poll for incoming UDP packet ────────────
  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    memset(packetBuffer, 0, sizeof(packetBuffer));
    int len = udp.read(packetBuffer, sizeof(packetBuffer) - 1);

    IPAddress remoteIP   = udp.remoteIP();
    uint16_t  remotePort = udp.remotePort();

    Serial.printf("[UDP]  Packet from %s:%d  payload='%c'  (len=%d)\n",
                  remoteIP.toString().c_str(), remotePort,
                  packetBuffer[0], len);

    // ── Dispatch command ────────────────────────
    switch (packetBuffer[0]) {
      case 'o':  cmdOpen();  break;
      case 'c':  cmdClose(); break;
      case 's':  cmdStop();  break;
      default:
        Serial.printf("[WARN] Unknown command: 0x%02X – ignored\n",
                      (uint8_t)packetBuffer[0]);
        break;
    }
  }

  // No delay() here – keep the loop spinning as fast as possible
  // so UDP packets are processed with zero latency.
}

// ═════════════════════════════════════════════
//  COMMAND HANDLERS
// ═════════════════════════════════════════════

/**
 * cmdOpen()
 * Pulses the OPEN relay to start the motor opening.
 * Ignored if the motor is already opening.
 */
void cmdOpen() {
  if (motorState == STATE_OPENING) {
    Serial.println("[CMD]  OPEN  → already OPENING, command ignored.");
    return;
  }

  Serial.println("[CMD]  OPEN  → engaging OPEN relay…");
  pulseRelay(RELAY_OPEN);
  motorState = STATE_OPENING;
  printState();
}

/**
 * cmdClose()
 * Pulses the CLOSE relay to start the motor closing.
 * Ignored if the motor is already closing.
 */
void cmdClose() {
  if (motorState == STATE_CLOSING) {
    Serial.println("[CMD]  CLOSE → already CLOSING, command ignored.");
    return;
  }

  Serial.println("[CMD]  CLOSE → engaging CLOSE relay…");
  pulseRelay(RELAY_CLOSE);
  motorState = STATE_CLOSING;
  printState();
}

/**
 * cmdStop()
 * Pulses whichever relay is currently active to stop the motor.
 * If already stopped, the command is ignored.
 *
 * This relies on the state machine memory – the ESP32 knows which
 * relay started the movement and therefore which one to pulse again.
 */
void cmdStop() {
  if (motorState == STATE_STOPPED) {
    Serial.println("[CMD]  STOP  → already STOPPED, command ignored.");
    return;
  }

  if (motorState == STATE_OPENING) {
    Serial.println("[CMD]  STOP  → pulsing OPEN relay to halt motor…");
    pulseRelay(RELAY_OPEN);
  } else if (motorState == STATE_CLOSING) {
    Serial.println("[CMD]  STOP  → pulsing CLOSE relay to halt motor…");
    pulseRelay(RELAY_CLOSE);
  }

  motorState = STATE_STOPPED;
  printState();
}

// ═════════════════════════════════════════════
//  LOW-LEVEL RELAY PULSE
// ═════════════════════════════════════════════

/**
 * pulseRelay(pin)
 *
 * Safety interlock: forces the OTHER relay HIGH before engaging
 * the requested one, ensuring both are NEVER simultaneously LOW.
 *
 * Sequence:
 *   1. Release the opposing relay (HIGH / OFF) – safety first
 *   2. Engage the target relay (LOW / ON)
 *   3. Wait PULSE_MS
 *   4. Release the target relay (HIGH / OFF)
 */
void pulseRelay(uint8_t pin) {
  // Step 1 – Interlock: guarantee the other relay is OFF
  uint8_t otherPin = (pin == RELAY_OPEN) ? RELAY_CLOSE : RELAY_OPEN;
  RELAY_RELEASE(otherPin);
  Serial.printf("[RELAY] Interlock → GPIO %d HIGH (OFF)\n", otherPin);
  delayMicroseconds(500);   // Brief settling time after interlock

  // Step 2 – Engage target relay
  Serial.printf("[RELAY] GPIO %d LOW  (ON)  – pulse start\n", pin);
  RELAY_ENGAGE(pin);

  // Step 3 – Hold for the required pulse duration
  // NOTE: Using delay() here is intentional and safe. The pulse must
  // be a fixed 500 ms blocking operation. UDP packets received during
  // this window are buffered by the ESP32's network stack and will be
  // processed immediately after the pulse completes.
  delay(PULSE_MS);

  // Step 4 – Release relay
  RELAY_RELEASE(pin);
  Serial.printf("[RELAY] GPIO %d HIGH (OFF) – pulse end (%d ms)\n", pin, PULSE_MS);
}

// ═════════════════════════════════════════════
//  WIFI CONNECTION
// ═════════════════════════════════════════════
void connectWiFi() {
  Serial.printf("[WiFi] Connecting to \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint8_t attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    if (++attempts >= 40) {
      // 20 seconds without connection – restart and retry
      Serial.println("\n[WiFi] Timeout! Restarting ESP32…");
      ESP.restart();
    }
  }

  Serial.println(" Connected!");
  Serial.printf("[WiFi] IP Address : %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("[WiFi] RSSI       : %d dBm\n", WiFi.RSSI());
}

// ═════════════════════════════════════════════
//  DEBUG HELPER
// ═════════════════════════════════════════════
void printState() {
  const char* stateNames[] = { "STOPPED", "OPENING", "CLOSING" };
  Serial.printf("[STATE] Motor is now → %s\n\n", stateNames[motorState]);
}
