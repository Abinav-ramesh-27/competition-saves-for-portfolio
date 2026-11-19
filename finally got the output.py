# ============================================================
# RIVERWATCH AI - COMPLETE MICROPYTHON PROGRAM
# Raspberry Pi Pico W + TCS3200 + HC-SR04 + Analog Water Sensor
# + Float Switch. SERVO REMOVED.
#
# HC-SR04 monitors distance to the water surface and its trend.
# A decreasing distance means the water is rising.
# An increasing distance means the water is falling.
#
# IMPORTANT:
# 1. Set your Wi-Fi name and password below.
# 2. HC-SR04 ECHO is 5V: use a voltage divider before Pico GP8.
# 3. Mount HC-SR04 firmly above the river model, facing down.
# 4. Calibrate WATER_DRY_RAW and WATER_WET_RAW using your sensor.
# 5. TCS3200 calibration requires clear water at startup.
# 6. Optical colour changes do NOT prove chemical or biological safety.
# ============================================================

import network
import socket
import time
import json
from machine import Pin, ADC, time_pulse_us

# ============================================================
# 1. WIFI SETTINGS
# ============================================================


WIFI_NAME = "SV Robotics Lab"
WIFI_PASSWORD ="A#dulk@l@m@2o22"



# ============================================================
# 2. PIN CONFIGURATION
# ============================================================

# TCS3200
S0 = Pin(2, Pin.OUT)
S1 = Pin(3, Pin.OUT)
S2 = Pin(4, Pin.OUT)
S3 = Pin(5, Pin.OUT)
OUT = Pin(6, Pin.IN)

S0.value(0)
S1.value(1)  # 2% frequency scaling on common modules

# HC-SR04
TRIG = Pin(7, Pin.OUT)
ECHO = Pin(8, Pin.IN)  # ECHO must be reduced to 3.3V
TRIG.value(0)

# Analog water-level sensor
water_sensor = ADC(26)

# Float switch
float_switch = Pin(15, Pin.IN, Pin.PULL_UP)

# ============================================================
# 3. ANALOG WATER-LEVEL CALIBRATION
# ============================================================

# Replace these with actual ADC readings from your sensor.
WATER_DRY_RAW = 0
WATER_WET_RAW = 50000
FLOOD_THRESHOLD = 80

# ============================================================
# 4. TCS3200 SETTINGS
# ============================================================

SAMPLE_TIME_MS = 100
FILTER_SETTLE_MS = 30
COLOUR_SAMPLES = 5
MIN_VALID_PULSES = 5
COLOUR_DOMINANCE_MARGIN = 8.0
NEUTRAL_SPREAD_LIMIT = 12.0

NEUTRAL_REFERENCE_COUNTS = {
    "RED": None,
    "GREEN": None,
    "BLUE": None
}

# ============================================================
# 5. ULTRASONIC WATER-LEVEL CALIBRATION AND FILTERING
# ============================================================

# The sensor is fixed 7 cm above the EMPTY container floor.
# Empty container: distance = 7 cm, water height = 0 cm.
# As water rises, distance decreases and water height increases.
ULTRASONIC_EMPTY_DISTANCE_CM = 7.0
ULTRASONIC_MAX_LEVEL_CM = 7.0

# Median filtering removes occasional false echoes. The additional
# smoothing and deadband prevent the display flickering from tiny changes.
ULTRASONIC_SAMPLES = 5
ULTRASONIC_SAMPLE_GAP_MS = 70
ULTRASONIC_SMOOTHING = 0.35
ULTRASONIC_DISPLAY_DEADBAND_CM = 0.10

# Trend uses water height (not raw distance). A rise of at least 0.15 cm
# across the observation window is treated as RISING.
TREND_WINDOW_MS = 12000
TREND_MINIMUM_TIME_MS = 4000
TREND_DEADBAND_CM = 0.15

ultrasonic_samples = []
filtered_distance = -1.0
water_height_cm = 0.0
trend_samples = []
water_trend = "CALIBRATING"
water_rate = 0.0

# ============================================================
# 6. SYSTEM VARIABLES
# ============================================================

distance = -1
colour = "NONE"

rgb_red = 0
rgb_green = 0
rgb_blue = 0
rgb_normalized_red = 0
rgb_normalized_green = 0
rgb_normalized_blue = 0

contamination = "CALIBRATING"
water_level = 0
continuity_status = "OFF"
float_status = "SAFE"
flood_status = "SAFE"
message = "System starting"

# ============================================================
# 7. ULTRASONIC DISTANCE
# ============================================================

def get_distance():
    """Read HC-SR04 and return a stable median-filtered distance in cm."""
    global ultrasonic_samples, filtered_distance

    readings = []

    # Take several pings and discard invalid echoes.
    for sample_number in range(ULTRASONIC_SAMPLES):
        TRIG.value(0)
        time.sleep_us(3)
        TRIG.value(1)
        time.sleep_us(10)
        TRIG.value(0)

        try:
            duration = time_pulse_us(ECHO, 1, 30000)
            if duration > 0:
                value = (duration * 0.0343) / 2
                if 2.0 <= value <= 30.0:
                    readings.append(value)
        except Exception:
            pass

        if sample_number < ULTRASONIC_SAMPLES - 1:
            time.sleep_ms(ULTRASONIC_SAMPLE_GAP_MS)

    # Require at least 3 valid echoes. Otherwise retain the last stable value.
    if len(readings) < 3:
        if filtered_distance >= 0:
            return round(filtered_distance, 2)
        return -1

    readings.sort()
    middle = len(readings) // 2
    median_distance = readings[middle]

    if filtered_distance < 0:
        filtered_distance = median_distance
    else:
        # Exponential smoothing reduces surface-ripple fluctuations.
        change = median_distance - filtered_distance
        filtered_distance += ULTRASONIC_SMOOTHING * change

    # Convert sensor-to-water distance into water height above the floor.
    # At 7 cm (empty floor), the displayed water height is exactly 0 cm.
    level = ULTRASONIC_EMPTY_DISTANCE_CM - filtered_distance
    level = max(0.0, min(ULTRASONIC_MAX_LEVEL_CM, level))

    global water_height_cm
    if abs(level - water_height_cm) >= ULTRASONIC_DISPLAY_DEADBAND_CM:
        water_height_cm = round(level, 1)
    elif water_height_cm == 0.0 and level < ULTRASONIC_DISPLAY_DEADBAND_CM:
        water_height_cm = 0.0

    return round(filtered_distance, 2)


def update_water_trend(current_distance):
    global trend_samples, water_trend, water_rate

    now = time.ticks_ms()

    if current_distance < 0:
        if filtered_distance < 0:
            water_trend = "NO READING"
        return

    # Store calculated water height. Increasing height means RISING.
    trend_samples.append((now, water_height_cm))

    while len(trend_samples) > 1:
        age = time.ticks_diff(now, trend_samples[0][0])
        if age > TREND_WINDOW_MS:
            trend_samples.pop(0)
        else:
            break

    if len(trend_samples) < 2:
        water_trend = "CALIBRATING"
        water_rate = 0.0
        return

    first_time, first_level = trend_samples[0]
    last_time, last_level = trend_samples[-1]
    elapsed_ms = time.ticks_diff(last_time, first_time)

    if elapsed_ms < TREND_MINIMUM_TIME_MS:
        water_trend = "CALIBRATING"
        water_rate = 0.0
        return

    level_change = last_level - first_level
    elapsed_minutes = elapsed_ms / 60000.0
    water_rate = round(level_change / elapsed_minutes, 2)

    if level_change >= TREND_DEADBAND_CM:
        water_trend = "RISING"
    elif level_change <= -TREND_DEADBAND_CM:
        water_trend = "FALLING"
    else:
        water_trend = "STABLE"


# ============================================================
# 8. TCS3200 COLOUR SENSOR
# ============================================================

pulse_count = 0


def count_pulse(pin):
    global pulse_count
    pulse_count += 1


def read_colour_frequency(s2, s3):
    global pulse_count

    OUT.irq(handler=None)
    S2.value(s2)
    S3.value(s3)
    time.sleep_ms(FILTER_SETTLE_MS)

    pulse_count = 0
    OUT.irq(trigger=Pin.IRQ_FALLING, handler=count_pulse)
    time.sleep_ms(SAMPLE_TIME_MS)
    OUT.irq(handler=None)

    return pulse_count


def median(values):
    ordered = sorted(values)
    count = len(ordered)

    if count == 0:
        return 0

    middle = count // 2

    if count % 2:
        return ordered[middle]

    return (ordered[middle - 1] + ordered[middle]) / 2


def read_rgb_averaged():
    readings = {"RED": [], "GREEN": [], "BLUE": []}

    for _ in range(COLOUR_SAMPLES):
        readings["RED"].append(read_colour_frequency(0, 0))
        readings["BLUE"].append(read_colour_frequency(0, 1))
        readings["GREEN"].append(read_colour_frequency(1, 1))

    return {
        "RED": median(readings["RED"]),
        "GREEN": median(readings["GREEN"]),
        "BLUE": median(readings["BLUE"])
    }


def calibrate_colour_sensor():
    print("")
    print("TCS3200 CLEAR-WATER CALIBRATION")
    print("Place clear water in front of the sensor.")
    print("Keep distance and lighting unchanged.")

    for remaining in range(5, 0, -1):
        print("Starting in", remaining)
        time.sleep(1)

    calibration_readings = {
        "RED": [], "GREEN": [], "BLUE": []
    }

    for sample_number in range(3):
        sample = read_rgb_averaged()

        for channel in ("RED", "GREEN", "BLUE"):
            calibration_readings[channel].append(sample[channel])

        print(
            "Calibration sample", sample_number + 1,
            "R =", sample["RED"],
            "G =", sample["GREEN"],
            "B =", sample["BLUE"]
        )

    for channel in ("RED", "GREEN", "BLUE"):
        NEUTRAL_REFERENCE_COUNTS[channel] = median(
            calibration_readings[channel]
        )

    if any(
        NEUTRAL_REFERENCE_COUNTS[channel] < MIN_VALID_PULSES
        for channel in ("RED", "GREEN", "BLUE")
    ):
        print("TCS3200 CALIBRATION FAILED. Check wiring and lighting.")
        return False

    print("TCS3200 CALIBRATION COMPLETE:")
    print(NEUTRAL_REFERENCE_COUNTS)
    return True


def colour_sensor_is_calibrated():
    for channel in ("RED", "GREEN", "BLUE"):
        reference = NEUTRAL_REFERENCE_COUNTS[channel]
        if reference is None or reference <= 0:
            return False
    return True


def normalize_rgb(raw_values):
    normalized = {}

    for channel in ("RED", "GREEN", "BLUE"):
        reference = NEUTRAL_REFERENCE_COUNTS[channel]

        if reference is None or reference <= 0:
            return None

        normalized[channel] = (
            raw_values[channel] / reference
        ) * 100.0

    return normalized


def classify_colour(raw_values):
    if not colour_sensor_is_calibrated():
        return "NONE", "SENSOR CHECK", None

    if max(raw_values.values()) < MIN_VALID_PULSES:
        return "NONE", "SENSOR CHECK", None

    normalized = normalize_rgb(raw_values)

    if normalized is None:
        return "NONE", "SENSOR CHECK", None

    ordered = sorted(normalized.values(), reverse=True)
    strongest = ordered[0]
    second_strongest = ordered[1]
    weakest = ordered[2]

    if (strongest - weakest) < NEUTRAL_SPREAD_LIMIT:
        return "NONE", "SAFE", normalized

    if (strongest - second_strongest) < COLOUR_DOMINANCE_MARGIN:
        return "NONE", "SAFE", normalized

    detected_colour = max(normalized, key=normalized.get)

    # Project indicator only: optical colour anomaly, not a water
    # safety test or proof of chemical/biological contamination.
    return detected_colour, "CONTAMINATED", normalized


def detect_colour():
    global rgb_red, rgb_green, rgb_blue
    global rgb_normalized_red, rgb_normalized_green
    global rgb_normalized_blue

    raw_values = read_rgb_averaged()
    detected, status, normalized = classify_colour(raw_values)

    rgb_red = int(raw_values["RED"])
    rgb_green = int(raw_values["GREEN"])
    rgb_blue = int(raw_values["BLUE"])

    if normalized is not None:
        rgb_normalized_red = round(normalized["RED"], 1)
        rgb_normalized_green = round(normalized["GREEN"], 1)
        rgb_normalized_blue = round(normalized["BLUE"], 1)
    else:
        rgb_normalized_red = 0
        rgb_normalized_green = 0
        rgb_normalized_blue = 0

    return detected, status


# ============================================================
# 9. ANALOG WATER-LEVEL SENSOR
# ============================================================

def get_water_level():
    raw = water_sensor.read_u16()
    difference = WATER_WET_RAW - WATER_DRY_RAW

    if difference == 0:
        return 0

    percentage = (
        (raw - WATER_DRY_RAW) / difference
    ) * 100

    percentage = max(0, min(100, percentage))
    return round(percentage, 1)


# ============================================================
# 10. FLOAT SWITCH
# ============================================================

# This retains the original logic: value 1 = FLOOD, value 0 = SAFE.
# Verify the logic against the actual switch and wiring.
def get_float_status():
    if float_switch.value() == 1:
        return "FLOOD"
    return "SAFE"


# ============================================================
# 11. WIFI CONNECTION
# ============================================================

wlan = network.WLAN(network.STA_IF)
wlan.active(True)

print("Connecting to Wi-Fi...")
wlan.connect(WIFI_NAME, WIFI_PASSWORD)

timeout = 20

while not wlan.isconnected() and timeout > 0:
    time.sleep(1)
    timeout -= 1

if not wlan.isconnected():
    raise RuntimeError("Wi-Fi connection failed. Check credentials.")

ip = wlan.ifconfig()[0]
print("Wi-Fi connected!")
print("Open this address on a device connected to the same Wi-Fi:")
print("http://" + ip + "/")

# Calibrate TCS3200 before starting monitoring.
colour_sensor_ready = calibrate_colour_sensor()

# ============================================================
# 12. DASHBOARD HTML
# ============================================================

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>RIVERWATCH AI // QUANTUM NEXUS</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;700&family=JetBrains+Mono:wght@400;700&display=swap');

:root {
    --bg-deep:#050b14;
    --card-bg:rgba(9,18,33,.82);
    --neon-cyan:#00f0ff;
    --neon-purple:#b000ff;
    --neon-green:#00ff66;
    --neon-red:#ff0055;
    --border-glow:rgba(0,240,255,.25);
    --text-main:#f1f5f9;
    --text-muted:#8494aa;
}

body.theme-ganges {
    --neon-cyan:#00ffaa;
    --neon-purple:#0088ff;
    --border-glow:rgba(0,255,170,.3);
}
body.theme-yamuna {
    --neon-cyan:#ffaa00;
    --neon-purple:#ff0055;
    --border-glow:rgba(255,170,0,.3);
}
body.theme-thames {
    --neon-cyan:#00d2ff;
    --neon-purple:#3a7bd5;
    --border-glow:rgba(0,210,255,.3);
}

* {box-sizing:border-box;margin:0;padding:0}
body {
    background-color:var(--bg-deep);
    background-image:
      radial-gradient(circle at 20% 20%,rgba(176,0,255,.08),transparent 40%),
      radial-gradient(circle at 80% 80%,rgba(0,240,255,.08),transparent 40%),
      linear-gradient(rgba(0,240,255,.02) 1px,transparent 1px),
      linear-gradient(90deg,rgba(0,240,255,.02) 1px,transparent 1px);
    background-size:100% 100%,100% 100%,40px 40px,40px 40px;
    font-family:'JetBrains Mono',monospace;
    color:var(--text-main);
    min-height:100vh;
    padding:30px 20px;
}
header{text-align:center;margin-bottom:25px}
h1 {
    font-family:'Rajdhani',sans-serif;
    font-size:3rem;
    color:var(--neon-cyan);
    text-shadow:0 0 20px var(--neon-cyan);
    letter-spacing:4px;
}
.subtitle {
    font-size:.85rem;color:var(--text-muted);
    letter-spacing:2px;text-transform:uppercase;
}
.station-deck {
    display:flex;justify-content:center;gap:15px;
    flex-wrap:wrap;max-width:1100px;margin:0 auto 25px;
}
.pod-btn {
    background:var(--card-bg);border:1px solid var(--border-glow);
    color:var(--text-main);padding:14px 24px;border-radius:12px;
    cursor:pointer;font-family:'Rajdhani',sans-serif;
    font-size:1.1rem;font-weight:700;letter-spacing:1px;
}
.pod-btn:hover,.pod-btn.active {
    border-color:var(--neon-cyan);color:var(--neon-cyan);
    box-shadow:0 0 20px var(--border-glow);
}
.global-status {
    max-width:1040px;margin:0 auto 25px;
    background:var(--card-bg);border:1px solid var(--border-glow);
    border-left:4px solid var(--neon-green);padding:16px 24px;
    display:flex;justify-content:space-between;align-items:center;
    gap:12px;border-radius:0 12px 12px 0;
}
.global-status.danger {
    border-left-color:var(--neon-red);
    background:rgba(255,0,85,.1);
    box-shadow:0 0 25px rgba(255,0,85,.2);
}
.quantum-grid {
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
    gap:20px;max-width:1040px;margin:0 auto;
}
.quantum-card {
    background:var(--card-bg);border:1px solid var(--border-glow);
    border-radius:16px;padding:24px;position:relative;
    overflow:hidden;box-shadow:0 10px 30px rgba(0,0,0,.4);
}
.quantum-card:before {
    content:'';position:absolute;top:0;left:0;width:100%;height:3px;
    background:linear-gradient(90deg,var(--neon-cyan),var(--neon-purple));
}
.card-title {
    font-size:.75rem;color:var(--text-muted);
    text-transform:uppercase;letter-spacing:2px;margin-bottom:12px;
}
.card-value {
    font-family:'Rajdhani',sans-serif;font-size:2rem;
    font-weight:700;color:var(--neon-cyan);
    text-shadow:0 0 10px rgba(0,240,255,.3);
    word-break:break-word;
}
.small-value{font-size:1.2rem}
.quantum-console {
    max-width:1040px;margin:25px auto 0;
    background:var(--card-bg);border:1px solid var(--border-glow);
    padding:18px 24px;border-radius:16px;
}
.console-header {
    font-size:.75rem;color:var(--neon-cyan);
    letter-spacing:2px;margin-bottom:8px;text-transform:uppercase;
}
.console-body{font-size:.9rem;line-height:1.6}
.graph-card{max-width:1040px;margin:20px auto 0}
#trendGraph {
    width:100%;height:220px;display:block;
    background:rgba(0,0,0,.18);border-radius:10px;
}
.footer {
    text-align:center;color:var(--text-muted);
    margin-top:30px;font-size:.75rem;letter-spacing:3px;
}
#linkBadge {
    font-family:'Rajdhani',sans-serif;font-weight:700;
    background:rgba(0,255,102,.1);border:1px solid var(--neon-green);
    color:var(--neon-green);padding:6px 16px;border-radius:20px;
    white-space:nowrap;
}
#quantumOverlay {
    position:fixed;inset:0;background:rgba(5,11,20,.95);
    z-index:9999;display:flex;justify-content:center;
    align-items:center;opacity:0;pointer-events:none;
    transition:opacity .3s;
}
#quantumOverlay.active{opacity:1;pointer-events:auto}
.quantum-core {
    border:2px solid var(--neon-cyan);
    box-shadow:0 0 40px var(--neon-cyan);
    padding:35px;border-radius:16px;text-align:center;
    max-width:450px;width:90%;
}
.quantum-spinner {
    width:60px;height:60px;border:3px solid rgba(255,255,255,.1);
    border-top:3px solid var(--neon-cyan);
    border-right:3px solid var(--neon-purple);
    border-radius:50%;animation:qSpin .7s linear infinite;
    margin:0 auto 20px;
}
@keyframes qSpin {to{transform:rotate(360deg)}}
@media(max-width:600px) {
    body{padding:20px 12px}
    h1{font-size:2.2rem}
    .global-status{align-items:flex-start;flex-direction:column}
}
</style>
</head>

<body>
<div id="quantumOverlay">
  <div class="quantum-core">
    <div class="quantum-spinner"></div>
    <div id="qText">CONNECTING TO RIVER NODE...</div>
  </div>
</div>

<header>
  <h1>RIVERWATCH AI</h1>
  <div class="subtitle">Intelligent River Monitoring & Flood Detection</div>
</header>

<div class="station-deck">
  <button class="pod-btn active" id="btn-cauvery"
    onclick="switchQuantumStation('cauvery','Cauvery Basin // Node Alpha')">🌊 Cauvery Basin</button>
  <button class="pod-btn" id="btn-ganges"
    onclick="switchQuantumStation('ganges','Ganges Delta // Node Beta')">🌿 Ganges Main</button>
  <button class="pod-btn" id="btn-yamuna"
    onclick="switchQuantumStation('yamuna','Yamuna Grid // Node Gamma')">⚡ Yamuna Node</button>
  <button class="pod-btn" id="btn-thames"
    onclick="switchQuantumStation('thames','Thames Estuary // Node Delta')">⚓ Thames Estuary</button>
</div>

<div id="globalBanner" class="global-status">
  <div>
    <div class="card-title">BASIN SECURITY STATUS</div>
    <div id="bannerText">SYSTEM NOMINAL // MONITORING ACTIVE</div>
  </div>
  <div id="linkBadge">LINK: ACTIVE</div>
</div>

<div class="quantum-grid">
  <!-- PRIORITY 1: TCS3200 COLOUR SENSOR OUTPUTS -->
  <div class="quantum-card">
    <div class="card-title">Spectral Colour Signature</div>
    <div class="card-value" id="colour">SCANNING</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">Optical Colour Alert</div>
    <div class="card-value" id="contamination">CALIBRATING</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">Live TCS3200 RGB Readings</div>
    <div class="card-value small-value" id="rgbreadings">R: 0 | G: 0 | B: 0</div>
    <div class="card-title" style="margin-top:12px">Normalized RGB</div>
    <div class="card-value small-value" id="rgbnormalized">R: 0 | G: 0 | B: 0</div>
  </div>

  <!-- PRIORITY 2: FLOAT SWITCH -->
  <div class="quantum-card">
    <div class="card-title">Float Switch Continuity</div>
    <div class="card-value" id="continuity">OFF</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">Float Sensor Status</div>
    <div class="card-value" id="floatstatus">SAFE</div>
  </div>

  <!-- PRIORITY 3: ANALOG WATER-LEVEL SENSOR -->
  <div class="quantum-card">
    <div class="card-title">Analog Water-Level Sensor</div>
    <div class="card-value" id="waterlevel">0.0 %</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">Combined Flood Status</div>
    <div class="card-value" id="floodstatus">SAFE</div>
  </div>

  <!-- PRIORITY 4: ULTRASONIC WATER-LEVEL MONITORING -->
  <div class="quantum-card">
    <div class="card-title">Ultrasonic Water Level Above Container Floor</div>
    <div class="card-value" id="distance">0.0 cm</div>
    <div class="card-title" id="sensorDistance" style="margin-top:10px">Sensor distance: -- cm</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">River Water Trend</div>
    <div class="card-value" id="trend">CALIBRATING</div>
  </div>

  <div class="quantum-card">
    <div class="card-title">Rate of Water-Level Change</div>
    <div class="card-value" id="rate">0.00 cm/min</div>
  </div>
</div>

<div class="quantum-card graph-card">
  <div class="card-title">Live Ultrasonic Water-Level Trend</div>
  <canvas id="trendGraph" width="1000" height="220"></canvas>
  <div class="card-title" style="margin-top:10px">
    Water height increases as water is poured into the container.
  </div>
</div>

<div class="quantum-console">
  <div class="console-header">&gt;&gt; River Telemetry Channel // Active Node</div>
  <div class="console-body" id="message">Awaiting hardware telemetry...</div>
</div>

<div class="footer">RIVERWATCH AI • RIVER MONITORING SYSTEM</div>

<script>
let currentStation = "Cauvery Basin // Node Alpha";
let graphValues = [];
const graphLimit = 60;

function switchQuantumStation(themeKey, stationTitle) {
  const overlay = document.getElementById('quantumOverlay');
  document.getElementById('qText').innerText =
    "CONNECTING TO " + stationTitle.toUpperCase() + "...";
  overlay.classList.add('active');

  setTimeout(() => {
    currentStation = stationTitle;
    document.querySelectorAll('.pod-btn').forEach(
      btn => btn.classList.remove('active')
    );
    document.getElementById('btn-' + themeKey).classList.add('active');
    document.body.className = '';

    if (themeKey !== 'cauvery') {
      document.body.classList.add('theme-' + themeKey);
    }
    overlay.classList.remove('active');
  }, 650);
}

function drawGraph() {
  const canvas = document.getElementById('trendGraph');
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(120,150,180,0.22)';
  ctx.lineWidth = 1;

  for (let i = 1; i <= 4; i++) {
    const y = (h / 5) * i;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }

  const valid = graphValues.filter(v => v !== null);
  if (valid.length < 2) {
    ctx.fillStyle = '#8494aa';
    ctx.font = '20px monospace';
    ctx.fillText('Collecting ultrasonic readings...', 20, 35);
    return;
  }

  let min = Math.min(...valid);
  let max = Math.max(...valid);
  if (max - min < 1) {
    max += 0.5;
    min -= 0.5;
  }

  ctx.strokeStyle = '#00f0ff';
  ctx.lineWidth = 3;
  ctx.beginPath();

  let started = false;
  graphValues.forEach((value, index) => {
    if (value === null) return;
    const x = (index / Math.max(graphLimit - 1, 1)) * w;
    const y = h - 15 - ((value - min) / (max - min)) * (h - 30);

    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();

  ctx.fillStyle = '#8494aa';
  ctx.font = '13px monospace';
  ctx.fillText('Water level: ' + valid[valid.length - 1] + ' cm', 12, 18);
}

async function updateDashboard() {
  try {
    const res = await fetch('/data', {cache:'no-store'});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    document.getElementById('distance').innerText =
      data.water_height_cm + ' cm';
    document.getElementById('sensorDistance').innerText =
      data.distance < 0 ? 'Sensor distance: NO READING' :
      'Sensor distance: ' + data.distance + ' cm';

    const trendElement = document.getElementById('trend');
    trendElement.innerText = data.water_trend;

    if (data.water_trend === 'RISING') {
      trendElement.style.color = '#ff0055';
    } else if (data.water_trend === 'FALLING') {
      trendElement.style.color = '#00d2ff';
    } else if (data.water_trend === 'STABLE') {
      trendElement.style.color = '#00ff66';
    } else {
      trendElement.style.color = '#ffaa00';
    }

    document.getElementById('rate').innerText =
      data.water_rate + ' cm/min';

    document.getElementById('colour').innerText = data.colour;
    document.getElementById('contamination').innerText = data.contamination;

    document.getElementById('rgbreadings').innerText =
      'R: ' + data.rgb_red + ' | G: ' + data.rgb_green +
      ' | B: ' + data.rgb_blue;

    document.getElementById('rgbnormalized').innerText =
      'R: ' + data.rgb_normalized_red + ' | G: ' +
      data.rgb_normalized_green + ' | B: ' +
      data.rgb_normalized_blue;

    document.getElementById('waterlevel').innerText =
      data.water_level + ' %';
    document.getElementById('continuity').innerText = data.continuity;
    document.getElementById('floatstatus').innerText = data.float_status;
    document.getElementById('floodstatus').innerText = data.flood_status;
    document.getElementById('message').innerText =
      '[' + currentStation + '] ' + data.message;

    const contaminationElement = document.getElementById('contamination');
    if (data.contamination === 'CONTAMINATED') {
      contaminationElement.style.color = '#ff0055';
    } else if (data.contamination === 'SENSOR CHECK') {
      contaminationElement.style.color = '#ffaa00';
    } else {
      contaminationElement.style.color = '#00ff66';
    }

    graphValues.push(data.distance < 0 ? null : data.water_height_cm);
    if (graphValues.length > graphLimit) graphValues.shift();
    drawGraph();

    const banner = document.getElementById('globalBanner');
    const bannerText = document.getElementById('bannerText');
    const linkBadge = document.getElementById('linkBadge');

    if (data.flood_status === 'FLOOD' ||
        data.contamination === 'CONTAMINATED') {
      banner.className = 'global-status danger';

      if (data.flood_status === 'FLOOD') {
        bannerText.innerText =
          'FLOOD WARNING AT ' + currentStation.toUpperCase();
      } else {
        bannerText.innerText =
          'OPTICAL COLOUR ANOMALY AT ' + currentStation.toUpperCase();
      }

      bannerText.style.color = '#ff0055';
      linkBadge.style.borderColor = '#ff0055';
      linkBadge.style.color = '#ff0055';
      linkBadge.innerText = 'ALERT ACTIVE';
    } else {
      banner.className = 'global-status';
      bannerText.innerText =
        'SYSTEM MONITORING // ' + currentStation.toUpperCase();
      bannerText.style.color = '#00ff66';
      linkBadge.style.borderColor = '#00ff66';
      linkBadge.style.color = '#00ff66';
      linkBadge.innerText = 'LINK: ACTIVE';
    }
  } catch (e) {
    const linkBadge = document.getElementById('linkBadge');
    linkBadge.style.borderColor = '#ff0055';
    linkBadge.style.color = '#ff0055';
    linkBadge.innerText = 'LINK LOST';
  }
}

drawGraph();
setInterval(updateDashboard, 2000);
updateDashboard();
</script>
</body>
</html>
"""

# ============================================================
# 13. WEB SERVER
# ============================================================

address = socket.getaddrinfo("0.0.0.0", 80)[0][-1]

server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(address)
server.listen(2)
server.settimeout(0.2)

print("Dashboard is ready!")
print("Open http://" + ip + "/ on a device using the same Wi-Fi.")


def send_all(sock, payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    view = memoryview(payload)

    while len(view) > 0:
        sent = sock.send(view)

        if sent is None or sent <= 0:
            raise OSError("Socket connection closed")

        view = view[sent:]


def handle_client(client):
    try:
        client.settimeout(3)
        request = client.recv(1024)

        if not request:
            return

        request_line = request.decode(
            "utf-8", "ignore"
        ).split("\r\n", 1)[0]

        parts = request_line.split()
        path = parts[1] if len(parts) >= 2 else "/"

        if path == "/data" or path.startswith("/data?"):
            data = {
                "distance": distance,
                "water_height_cm": water_height_cm,
                "water_trend": water_trend,
                "water_rate": water_rate,
                "colour": colour,
                "contamination": contamination,
                "rgb_red": rgb_red,
                "rgb_green": rgb_green,
                "rgb_blue": rgb_blue,
                "rgb_normalized_red": rgb_normalized_red,
                "rgb_normalized_green": rgb_normalized_green,
                "rgb_normalized_blue": rgb_normalized_blue,
                "water_level": water_level,
                "continuity": continuity_status,
                "float_status": float_status,
                "flood_status": flood_status,
                "message": message
            }

            body = json.dumps(data).encode("utf-8")
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                "Content-Length: " + str(len(body)) + "\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")

            send_all(client, header + body)

        elif path == "/" or path.startswith("/?") or path == "/index.html":
            body = HTML.encode("utf-8")
            header = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Content-Length: " + str(len(body)) + "\r\n"
                "Cache-Control: no-cache\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")

            send_all(client, header + body)

        else:
            body = b"Not Found"
            header = (
                "HTTP/1.1 404 Not Found\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                "Content-Length: " + str(len(body)) + "\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            send_all(client, header + body)

    except Exception as e:
        print("Client error:", e)

    finally:
        try:
            client.close()
        except Exception:
            pass


# ============================================================
# 14. MAIN MONITORING LOOP
# ============================================================

print("RIVERWATCH AI STARTED!")

while True:

    # HC-SR04: distance and river water trend
    distance = get_distance()
    update_water_trend(distance)

    # TCS3200: colour detection
    if colour_sensor_ready:
        colour, contamination = detect_colour()
    else:
        colour = "NONE"
        contamination = "SENSOR CHECK"
        rgb_red = 0
        rgb_green = 0
        rgb_blue = 0
        rgb_normalized_red = 0
        rgb_normalized_green = 0
        rgb_normalized_blue = 0

    # Analog water-level sensor (retained)
    water_level = get_water_level()

    # Float switch (retained)
    switch_value = float_switch.value()

    if switch_value == 1:
        continuity_status = "ON"
        float_status = "FLOOD"
    else:
        continuity_status = "OFF"
        float_status = "SAFE"

    # Combined flood detection (retained)
    if (
        float_status == "FLOOD"
        or water_level >= FLOOD_THRESHOLD
    ):
        flood_status = "FLOOD"
    else:
        flood_status = "SAFE"

    # System message
    if flood_status == "FLOOD":
        message = "FLOOD WARNING! Existing flood sensor threshold reached!"

    elif contamination == "CONTAMINATED":
        message = "OPTICAL COLOUR ANOMALY DETECTED - CHECK WATER SAMPLE!"

    elif contamination == "SENSOR CHECK":
        message = "TCS3200 needs calibration or wiring check."

    elif water_trend == "RISING":
        message = "WARNING: River model water level is rising."

    elif water_trend == "FALLING":
        message = "River model water level is falling."

    elif water_trend == "STABLE":
        message = "River model water level is stable."

    elif water_trend == "NO READING":
        message = "Ultrasonic sensor reading unavailable. Check sensor."

    else:
        message = "Collecting ultrasonic readings for trend calculation."

    # Serve one dashboard request when available
    try:
        client, addr = server.accept()
        handle_client(client)
    except OSError:
        pass

    time.sleep_ms(100)
