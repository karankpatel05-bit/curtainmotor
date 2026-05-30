# Curtain OS - Smart Curtain Motor Controller

This project allows you to control a curtain motor using hand gestures (via webcam) or a web-based dashboard. The system consists of a Python Flask server running on your computer and an ESP32 microcontroller controlling the motor over Wi-Fi via UDP.

This guide provides step-by-step instructions on how to set up and run this project in Visual Studio Code (VS Code) on a Windows machine.

---

## Prerequisites

Before getting started, ensure you have the following installed on your Windows machine:
1. [Visual Studio Code (VS Code)](https://code.visualstudio.com/)
2. [Python 3.8 to 3.11](https://www.python.org/downloads/) (MediaPipe requires Python < 3.12 for maximum compatibility)
3. [Arduino IDE](https://www.arduino.cc/en/software) or the **PlatformIO** extension in VS Code (for uploading code to the ESP32)
4. A webcam (built-in or USB)

---

## Step 1: Clone or Open the Project in VS Code

1. Open **Visual Studio Code**.
2. Go to `File` > `Open Folder...` and select the `curtainmotor` project directory.
3. Open a new terminal in VS Code by going to `Terminal` > `New Terminal` (or press `` Ctrl + ` ``).

---

## Step 2: Set Up the Python Environment

It is highly recommended to use a Python virtual environment to manage dependencies.

1. **Create a virtual environment**:
   In the VS Code terminal, run:
   ```bash
   python -m venv venv
   ```
2. **Activate the virtual environment**:
   ```bash
   .\venv\Scripts\activate
   ```
   *(Note: If you get a PowerShell script execution error, you may need to run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Unrestricted` as an Administrator first.)*

3. **Install the required packages**:
   With the virtual environment activated, install the dependencies listed in `requirements.txt`:
   ```bash
   pip install -r requirements.txt
   ```
   *This will install Flask, SocketIO, OpenCV, and MediaPipe.*

---

## Step 3: Configure the Application

1. Open `dashboard_server.py` in VS Code.
2. Under the `# ── Runtime config (editable via UI) ──` section (around line 35), you will find the ESP32 IP configuration:
   ```python
   config = {
       'esp32_ip':  '192.168.1.100',   # ← Change to your ESP32 IP
       'udp_port':  4210,
       'cam_index': 0,
       'debounce':  2.0,
   }
   ```
   - Change `'esp32_ip'` to the actual IP address of your ESP32 once it connects to your network.
   - If your webcam isn't turning on later, you might need to change `cam_index` to `1` or `2`.

---

## Step 4: Run the Python Server

1. Ensure your virtual environment is still activated in the terminal.
2. Run the dashboard server script:
   ```bash
   python dashboard_server.py
   ```
3. If Windows Firewall prompts you for network access, click **Allow access**.
4. Open your web browser and go to:
   ```
   http://localhost:5000
   ```
5. You should now see the Dashboard UI, and the webcam feed should automatically start, ready to detect hand gestures!

---

## Step 5: Flash the ESP32 Controller

1. Open the `curtain_controller.ino` file using **Arduino IDE** (or PlatformIO in VS Code).
2. Install any required libraries in the Arduino IDE (if applicable).
3. **Update WiFi Credentials**: Update the `ssid` and `password` variables in the `.ino` code to match your home Wi-Fi network.
4. Select your ESP32 board and the corresponding COM port in the Arduino IDE.
5. Click **Upload** to flash the code to the ESP32.
6. Open the **Serial Monitor** (baud rate usually 115200) to find the assigned IP address of the ESP32.
7. Copy this IP address and paste it into the `config` block of `dashboard_server.py` as described in Step 3.

---

## Usage Guide

- **Dashboard Control**: Use the Open, Close, and Stop buttons on the web interface to manually control the curtains.
- **Gesture Control**: 
  - Show an **OPEN HAND** (all 5 fingers extended) to the camera to Open the curtains.
  - Show a **CLOSED FIST** (0 fingers extended) to Close the curtains.
  - The system has a debounce timer to prevent spamming commands.
- **Camera Toggle**: You can turn the camera on/off using the toggle on the web dashboard to save CPU usage when gesture controls are not needed.

## Troubleshooting

- **Webcam not starting**: Change the `cam_index` in `dashboard_server.py` from `0` to `1`.
- **ModuleNotFoundError**: Ensure you have activated your virtual environment before running the server (`.\venv\Scripts\activate`) and that you successfully ran `pip install -r requirements.txt`.
- **MediaPipe not installing**: MediaPipe does not yet fully support Python 3.12 or newer. Ensure you are using Python 3.8, 3.9, 3.10, or 3.11.
- **Commands not reaching ESP32**: Ensure your PC and the ESP32 are connected to the exact same Wi-Fi network, and that the ESP32 IP is correctly set in `dashboard_server.py`.
