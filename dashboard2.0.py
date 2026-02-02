# dashboard.py
# Advanced Streamlit dashboard to showcase possibilities:
# - Top bar with Inter font + status badges
# - Sidebar: aligned setpoint rows ("− value + Apply") per shelf
# - KPI cards, tabs, Plotly charts with setpoint/upper/lower
# - Old-style alarms (CRITICAL/ERROR) with acknowledge + ack all
# - Barcode load/unload, per-SKU images persisted to disk, gallery
# - Maintenance read-only (life % and projections), CSV export
# - Logs with filter and download
# - Demo vs Live modes (auto-connect scaffold; no port UI—constants)

import os
import io
import json
import time
import random
from datetime import datetime, timedelta
from collections import deque, defaultdict

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

# Optional serial (for LIVE mode scaffold)
try:
    import serial
except ImportError:
    serial = None

# =========================
# BRAND, CONFIG, CONSTANTS
# =========================
BRAND = {
    "text": "#111827",
    "primary": "#d94d14",
    "primary_alt": "#e28e04",
    "highlight": "#ffcb71",
    "panel": "#f7f7f8",
    "muted": "#6b7280",
    "ok": "#1e7d3f",
    "critical": "#a4130e",
    "grid_light": "#f1f2f4",
}

DEFAULT_REFRESH_MS = 1000
TEMP_STEP = 0.5
MIN_TEMP, MAX_TEMP = 0.0, 120.0
LOG_LIMIT = 400
HISTORY_LEN = 300
DEFAULT_NUM_SHELVES = 2

# Serial auto-connect (no UI in this showcase)
SERIAL_PORT = "COM4"   # e.g. /dev/ttyUSB0, /dev/tty.usbmodemXXXX, COM5
SERIAL_BAUD = 115200

# Persistence for SKU images
DATA_DIR = "data"
IMG_DIR = os.path.join(DATA_DIR, "barcode_images")
IMG_MAP = os.path.join(DATA_DIR, "barcode_images.json")

# =========================
# PAGE + GLOBAL CSS + INTER
# =========================

st.set_page_config(page_title="Heating Dashboard", layout="wide")

st.markdown("""
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
               Roboto, Oxygen, Ubuntu, Cantarell, 'Helvetica Neue', Arial,
               'Apple Color Emoji','Segoe UI Emoji','Segoe UI Symbol', sans-serif !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
/* Badges */
.badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  margin-left: 6px;
}
.badge-ok      { background:#eaf7ee; color:#1e7d3f; border:1px solid #d2efda; }
.badge-warn    { background:#fff6e6; color:#9a6700; border:1px solid #fde4b1; }
.badge-critical{ background:#fde8e7; color:#a4130e; border:1px solid #f7cecc; }
.badge-dark    { background:#f3f4f6; color:#111827; border:1px solid #e5e7eb; }

/* KPI cards */
.kpi {
  background:#ffffff;
  border:1px solid #eee;
  border-radius:12px;
  padding:16px;
}
.kpi h4     { margin:0 0 4px 0; font-weight:700; color:#6b7280; }
.kpi .value { font-size:1.4rem; font-weight:800; }

/* Buttons sizing */
.small-btn > button {
  min-width:2.4rem !important;
  padding:0.25rem 0.8rem !important;
  font-size:1.05rem !important;
}
.stButton > button {
  background-color:#d94d14 !important;
  color:white !important;
  border-radius:8px !important;
  font-weight:600 !important;
}

/* Section titles */
.section-title {
  margin-top:0.25rem;
  margin-bottom:0.75rem;
  font-weight:800;
  font-size:1.05rem;
}
</style>
""", unsafe_allow_html=True)

# =========================
# UTILS
# =========================
def ts_now():
    return time.strftime("%H:%M:%S")

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)

@st.cache_data(show_spinner=False)
def load_image_map():
    ensure_dirs()
    if os.path.exists(IMG_MAP):
        with open(IMG_MAP, "r") as f:
            return json.load(f)
    return {}

def save_image_map(map_obj):
    ensure_dirs()
    with open(IMG_MAP, "w") as f:
        json.dump(map_obj, f, indent=2)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def rolling_avg(seq, n=10):
    if not seq:
        return None
    if len(seq) < n:
        return sum(seq)/len(seq)
    return sum(seq[-n:]) / n

# =========================
# STATE INIT
# =========================
def init_state():
    s = st.session_state
    if s.get("initialized"):
        return

    ensure_dirs()

    s.demo_mode = True          # showcase toggles to LIVE when auto-connect works
    s.num_shelves = DEFAULT_NUM_SHELVES

    s.shelves = [{
        "is_on": True,
        "setpoint": 75.0,
        "upper": 77.0,
        "lower": 73.0,
    } for _ in range(s.num_shelves)]

    s.hist = {i: deque(maxlen=HISTORY_LEN) for i in range(s.num_shelves)}
    s.latest_temp = [None] * s.num_shelves

    # Old alarm style
    s.alarms = {"CRITICAL": [], "ERROR": []}

    s.logs = deque(maxlen=LOG_LIMIT)

    # Barcode
    s.barcode_mode = "LOAD"
    s.barcode_counts = defaultdict(int)        # counts not persisted
    s.barcode_images = load_image_map()        # persisted
    s.last_img_name_by_sku = {sku: meta.get("name", "") for sku, meta in s.barcode_images.items()}

    # Maintenance (Pi-driven; read-only here)
    s.maintenance = {
        "fan_hours": 0.0,
        "element_hours": 0.0,
        "fan_life_hours": 2000.0,
        "element_life_hours": 3000.0,
        "last_service_fan": None,
        "last_service_element": None
    }

    # Connection
    s.serial_port = SERIAL_PORT
    s.serial_baud = SERIAL_BAUD
    s.link = None
    s.serial_connected = False
    s._attempted_connect = False

    s.last_update_ts = 0.0
    s.last_tick = time.time()

    s.initialized = True

init_state()

# =========================
# SERIAL (optional LIVE scaffold)
# =========================
class SerialLink:
    def __init__(self, port, baud=115200):
        self.port = port
        self.baud = baud
        self.ser = None

    def open(self):
        if not serial:
            return False
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            time.sleep(1.5)
            return True
        except Exception as e:
            st.session_state.logs.append(f"[{ts_now()}] Serial open error: {e}")
            return False

    def is_open(self):
        return self.ser is not None and self.ser.is_open

    def read_all(self, max_lines=200):
        msgs = []
        if not self.is_open():
            return msgs
        for _ in range(max_lines):
            try:
                line = self.ser.readline()
                if not line:
                    break
                s = line.decode(errors="ignore").strip()
                if not s:
                    continue
                try:
                    msgs.append(json.loads(s))
                except json.JSONDecodeError:
                    st.session_state.logs.append(f"[{ts_now()}] RAW: {s}")
            except Exception as e:
                st.session_state.logs.append(f"[{ts_now()}] Serial read error: {e}")
                break
        return msgs

    def send(self, obj):
        if not self.is_open():
            return False
        try:
            data = json.dumps(obj) + "\n"
            self.ser.write(data.encode())
            return True
        except Exception as e:
            st.session_state.logs.append(f"[{ts_now()}] Serial write error: {e}")
            return False

def ensure_serial_once():
    s = st.session_state
    # Allow retries when UI toggles _attempted_connect back to False
    if s.get("_attempted_connect"):
        return
    s._attempted_connect = True

    if not serial:
        s.demo_mode = True
        s.logs.append(f"[{ts_now()}] pyserial not installed; demo mode.")
        return

    s.link = SerialLink(s.serial_port, s.serial_baud)
    s.serial_connected = s.link.open()
    if s.serial_connected:
        s.demo_mode = False
        s.logs.append(f"[{ts_now()}] Connected to {s.serial_port} @ {s.serial_baud}")
        send_to_pi({"type": "GET_STATUS"})
    else:
        s.demo_mode = True
        s.logs.append(f"[{ts_now()}] Failed to open {s.serial_port} @ {s.serial_baud} — still in DEMO")

def read_from_pi():
    s = st.session_state
    if not s.link or not s.link.is_open():
        return
    msgs = s.link.read_all()
    for msg in msgs:
        process_msg(msg)

def send_to_pi(obj):
    s = st.session_state
    if s.link and s.link.is_open():
        s.link.send(obj)

def process_msg(msg: dict):
    # Expecting STATUS, LIVE_TEMP, ALARM_STATE, LOG
    t = msg.get("type")
    if t == "STATUS":
        shelves = msg.get("shelves", [])

        # Resize shelf arrays based on your incoming shelf count
        count = len(shelves)

        # Only resize if number of shelves changed
        if count != st.session_state.num_shelves:
            st.session_state.num_shelves = count
            st.session_state.shelves = [
                {"is_on": True, "setpoint": 75.0, "upper": 77.0, "lower": 73.0}
                for _ in range(count)]
            st.session_state.hist = {i: deque(maxlen=HISTORY_LEN) for i in range(count)}
            st.session_state.latest_temp = [None] * count

        for idx, sh in enumerate(shelves):

            incomingShelf = sh.get("shelf", idx)
            idx = incomingShelf - 1
            temp = sh.get("temp")
            sp_arr = sh.get("setpoint", [75.0, 77.0, 73.0])

            # Map your [setpoint, upper, lower] → dashboard fields
            sp = sp_arr[0] if len(sp_arr) > 0 else 75.0
            up = sp_arr[1] if len(sp_arr) > 1 else sp + 2
            lo = sp_arr[2] if len(sp_arr) > 2 else sp - 2

            st.session_state.latest_temp[idx] = temp
            st.session_state.shelves[idx]["setpoint"] = sp
            st.session_state.shelves[idx]["upper"] = up
            st.session_state.shelves[idx]["lower"] = lo
            st.session_state.shelves[idx]["is_on"] = sh.get("systemOn", True)

            if temp is not None:
                st.session_state.hist[idx].append(temp)

            
            st.session_state.maintenance["fan_hours"] = sh.get("fanHours", st.session_state.maintenance["fan_hours"])
            st.session_state.maintenance["element_hours"] = sh.get("elementHours", st.session_state.maintenance["element_hours"])


            # ---- Alarm mapping ----
            # Clear alarms for this shelf
            # Clear alarms for this shelf index only
            for sev in ("CRITICAL", "ERROR"):
                st.session_state.alarms[sev] = [
                    a for a in st.session_state.alarms[sev]
                    if a["shelf"] != idx]

            # Rebuild from your format
            for a in sh.get("alarms", []):
                name = a.get("name", "ALARM")
                active = a.get("active", False)
                sev = a.get("severity", "ERROR").upper()
                desc = a.get("desc", name)

                if active:
                    add_alarm(sev, idx, name, desc, ack=a.get("ack", False))

        # ---- Maintenance mapping ----
        m = msg.get("maintenance", [2000, 3000])
        if isinstance(m, list) and len(m) >= 2:
            st.session_state.maintenance["fan_life_hours"] = m[0]
            st.session_state.maintenance["element_life_hours"] = m[1]

        
        st.session_state.last_update_ts = time.time()

    elif t == "LIVE_TEMP":
        i = msg.get("shelf", 1) - 1
        val = msg.get("value")
        st.session_state.latest_temp[i] = val
        st.session_state.hist[i].append(val)
        st.session_state.last_update_ts = time.time()

    elif t == "ALARM_STATE":
        i = msg.get("shelf", 1) - 1
        for sev in ("CRITICAL", "ERROR"):
            st.session_state.alarms[sev] = [a for a in st.session_state.alarms[sev] if a["shelf"] != i]
        for a in msg.get("alarms", []):
            sev = (a.get("severity") or "ERROR").upper()
            if a.get("active"):
                add_alarm(sev, i, a["name"], a.get("desc", a["name"]), ack=a.get("ack", False))
        st.session_state.last_update_ts = time.time()

    elif t == "LOG":
        ts = msg.get("ts", time.time())
        ts_str = time.strftime("%H:%M:%S", time.localtime(ts))
        level = msg.get("level", "INFO")
        m = msg.get("msg", "")
        st.session_state.logs.append(f"[{ts_str}] {level}: {m}")

# =========================
# ALARMS (old style)
# =========================
def _find_alarm(sev, shelf, name):
    arr = st.session_state.alarms.get(sev, [])
    for a in arr:
        if a["shelf"] == shelf and a["name"] == name:
            return a
    return None

def add_alarm(sev, shelf, name, desc, ack=False):
    arr = st.session_state.alarms.setdefault(sev, [])
    if not _find_alarm(sev, shelf, name):
        arr.append({"shelf": shelf, "name": name, "desc": desc, "ts": time.time(), "ack": ack})
        st.session_state.logs.append(f"[{ts_now()}] Alarm: [{sev}] S{shelf} {name}")

def ack_alarm(sev, shelf, name):
    a = _find_alarm(sev, shelf, name)
    if a and not a["ack"]:
        a["ack"] = True
        st.session_state.logs.append(f"[{ts_now()}] Ack: [{sev}] S{shelf} {name}")
        send_to_pi({"type": "ACK_ALARM", "shelf": shelf+1, "name": name})

def clear_alarm(sev, shelf, name):
    arr = st.session_state.alarms[sev]
    if any(a["shelf"] == shelf and a["name"] == name for a in arr):
        st.session_state.alarms[sev] = [a for a in arr if not (a["shelf"] == shelf and a["name"] == name)]
        st.session_state.logs.append(f"[{ts_now()}] Cleared: [{sev}] S{shelf} {name}")

def ack_all_alarms():
    for sev in ("CRITICAL", "ERROR"):
        for a in st.session_state.alarms[sev]:
            a["ack"] = True
    st.session_state.logs.append(f"[{ts_now()}] Ack all alarms")

# =========================
# SIDEBAR: Setpoints (aligned row)
# =========================
def render_setpoint_row(i: int):
    s = st.session_state
    cols = st.columns([1.3, 0.8, 1.2, 0.8, 1.4])  # label, -, value, +, apply
    with cols[0]:
        st.caption(f"Shelf {i} — Setpoint")
    with cols[1]:
        st.markdown('<div class="small-btn">', unsafe_allow_html=True)
        if st.button("－", key=f"setpoint_minus_{i}"):
            s.shelves[i]["setpoint"] = clamp(s.shelves[i]["setpoint"] - TEMP_STEP, MIN_TEMP, MAX_TEMP)
        st.markdown("</div>", unsafe_allow_html=True)
    with cols[2]:
        st.markdown(
            f"<div style='text-align:center; font-weight:800;'>{s.shelves[i]['setpoint']:.1f}°C</div>",
            unsafe_allow_html=True
        )
    with cols[3]:
        st.markdown('<div class="small-btn">', unsafe_allow_html=True)
        if st.button("＋", key=f"setpoint_plus_{i}"):
            s.shelves[i]["setpoint"] = clamp(s.shelves[i]["setpoint"] + TEMP_STEP, MIN_TEMP, MAX_TEMP)
        st.markdown("</div>", unsafe_allow_html=True)
    with cols[4]:
        st.markdown('<div style="text-align:right;">', unsafe_allow_html=True)
        if st.button("Apply", key=f"apply_sp_{i}"):
            send_to_pi({"type": "SET", "shelf": i+1, "value": s.shelves[i]["setpoint"]})
            st.session_state.logs.append(f"[{ts_now()}] Sent setpoint S{i}={s.shelves[i]['setpoint']:.1f}")
        st.markdown("</div>", unsafe_allow_html=True)

# =========================
# TOP BAR
# =========================
def render_top_bar():
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        st.markdown(
            f'<div class="top-bar"><div class="top-title">Heating Dashboard</div>'
            f'<p class="top-sub">Pi 5 Temperature Controller• {st.session_state.num_shelves} shelves</p></div>',
            unsafe_allow_html=True
        )
    with c3:
        # Status center: Live/Demo, time, last update
        live_badge = '<span class="badge badge-ok">LIVE</span>' if not st.session_state.demo_mode else '<span class="badge badge-dark">DEMO</span>'
        t = datetime.now().strftime("%H:%M:%S")
        lu = datetime.fromtimestamp(st.session_state.last_update_ts).strftime("%H:%M:%S") if st.session_state.last_update_ts else "—"
        st.markdown(
            f'<div class="top-bar" style="text-align:center;">{live_badge}'
            f' <span class="badge badge-dark">Now {t}</span>'
            f' <span class="badge badge-dark">Last update {lu}</span></div>',
            unsafe_allow_html=True
        )
    
        

# =========================
# KPI CARDS
# =========================
def render_kpis():
    c1, c2, c3, c4 = st.columns(4)
    temps = [t for t in st.session_state.latest_temp if t is not None]
    avg_temp = f"{(sum(temps)/len(temps)):.1f}°C" if temps else "—"
    hottest = f"{max(temps):.1f}°C" if temps else "—"
    active_alarm_count = sum(len(st.session_state.alarms[sev]) for sev in ("CRITICAL","ERROR"))
    items_count = sum(st.session_state.barcode_counts.values())

    with c1:
        st.markdown('<div class="kpi"><h4>Avg Temp</h4><div class="value">{}</div></div>'.format(avg_temp), unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="kpi"><h4>Hottest</h4><div class="value">{}</div></div>'.format(hottest), unsafe_allow_html=True)
    with c3:
        badge = "badge-critical" if active_alarm_count > 0 else "badge-ok"
        st.markdown(f'<div class="kpi"><h4>Active Alarms</h4><div class="value">{active_alarm_count} <span class="badge {badge}">{active_alarm_count}</span></div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="kpi"><h4>Items (Barcode)</h4><div class="value">{}</div></div>'.format(items_count), unsafe_allow_html=True)

# =========================
# CHART
# =========================
def render_chart(i: int, context: str = "main"):
    b = st.session_state.shelves[i]
    hist = list(st.session_state.hist[i])
    if not hist:
        st.info("No data yet.")
        return

    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=hist, line=dict(color=BRAND["primary"]), name=f"Shelf {i}"))
    fig.add_hline(y=b["setpoint"], line_dash="dash", line_color=BRAND["primary_alt"], annotation_text="Setpoint")
    fig.add_hline(y=b["upper"], line_dash="dot", line_color=BRAND["ok"], annotation_text="Upper")
    fig.add_hline(y=b["lower"], line_dash="dot", line_color=BRAND["ok"], annotation_text="Lower")
    fig.update_layout(height=280, margin=dict(l=20, r=20, t=20, b=20), plot_bgcolor="white", paper_bgcolor="white")

    
    st.plotly_chart(fig, width="stretch", key=f"chart-{context}-{i}")

# =========================
# BARCODE HELPERS
# =========================
def set_barcode_image(sku, uploader=None, url=None):
    if uploader and uploader.getbuffer():
        filename = f"{sku}__{int(time.time())}__{uploader.name}"
        path = os.path.join(IMG_DIR, filename)
        with open(path, "wb") as f:
            f.write(uploader.getvalue())
        st.session_state.barcode_images[sku] = {"type": "file", "path": path, "name": uploader.name}
        st.session_state.last_img_name_by_sku[sku] = uploader.name
        save_image_map(st.session_state.barcode_images)
        st.session_state.logs.append(f"[{ts_now()}] Image set for {sku} (upload)")
    elif url:
        st.session_state.barcode_images[sku] = {"type": "url", "url": url, "name": url.split("/")[-1] or url}
        st.session_state.last_img_name_by_sku[sku] = st.session_state.barcode_images[sku]["name"]
        save_image_map(st.session_state.barcode_images)
        st.session_state.logs.append(f"[{ts_now()}] Image set for {sku} (URL)")

def display_barcode_image(sku):
    entry = st.session_state.barcode_images.get(sku)
    if not entry:
        st.info("No image for this SKU yet.")
        return
    t = entry.get("type")
    if t == "file":
        path = entry.get("path")
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                st.image(f.read(), caption=entry.get("name", os.path.basename(path)), width="stretch")
        else:
            st.warning("Saved image not found on disk.")
    elif t == "url":
        st.image(entry.get("url"), caption=entry.get("name", "Image"), width="stretch")

# =========================
# SIDEBAR
# =========================
with st.sidebar:
    st.header("Controls")
    auto_refresh = st.toggle("Auto refresh", True)
    refresh_ms = st.select_slider("Refresh (ms)", [250, 500, 1000, 2000, 5000], DEFAULT_REFRESH_MS)

    st.divider()
    st.subheader("Setpoints")
    for i in range(st.session_state.num_shelves):
        render_setpoint_row(i)

    st.divider()
    

# =========================
# TOP + KPI
# =========================
render_top_bar()
st.markdown('<div class="section-title">Key Metrics</div>', unsafe_allow_html=True)
render_kpis()
st.divider()

# =========================
# AUTO-REFRESH TICK / DATA INGEST
# =========================
if auto_refresh:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_ms, key="tick")
    ensure_serial_once()
    if st.session_state.demo_mode:
        # Simulated temperatures & alarms
        for i in range(st.session_state.num_shelves):
            b = st.session_state.shelves[i]
            cur = st.session_state.latest_temp[i] or (b["lower"] + b["upper"]) / 2
            if b["is_on"]:
                cur += random.uniform(-0.25, 0.35)
            else:
                cur -= random.uniform(0.0, 0.2)
            cur = clamp(cur, MIN_TEMP, MAX_TEMP)
            st.session_state.latest_temp[i] = cur
            st.session_state.hist[i].append(cur)

            # Demo alarm heuristic (old style)
            if cur > b["upper"] + 6:
                add_alarm("CRITICAL", i, "OVERHEAT", f"{cur:.1f}°C >> {b['upper']:.1f}°C")
                clear_alarm("ERROR", i, "OVERTEMP")
            elif cur > b["upper"] + 3:
                add_alarm("ERROR", i, "OVERTEMP", f"{cur:.1f}°C > {b['upper']:.1f}°C")
                clear_alarm("CRITICAL", i, "OVERHEAT")
            else:
                clear_alarm("ERROR", i, "OVERTEMP")
                clear_alarm("CRITICAL", i, "OVERHEAT")

        # Fake maintenance accumulation if any shelf ON
        if any(sh["is_on"] for sh in st.session_state.shelves):
            delta_h = (time.time() - st.session_state.last_tick) / 3600.0
            st.session_state.maintenance["fan_hours"] += delta_h
            st.session_state.maintenance["element_hours"] += delta_h

        st.session_state.last_tick = time.time()
        st.session_state.last_update_ts = time.time()
    else:
        read_from_pi()

# =========================
# TABS
# =========================
tab_overview, tab_shelves, tab_alarms, tab_barcodes, tab_maint, tab_logs, tab_settings = st.tabs(
    ["Overview", "Shelves", "Alarms", "Barcodes", "Maintenance", "Logs", "Settings"]
)

# --- Overview
with tab_overview:
    st.markdown('<div class="section-title">Overview</div>', unsafe_allow_html=True)
    cols = st.columns(st.session_state.num_shelves or 1)
    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**Shelf {i}**")
            render_chart(i, context="overview")
            # Inline simulate (aligned)
            sim_cols = st.columns([2, 1])
            with sim_cols[0]:
                sim_name = st.selectbox(
                    "Simulate Alarm",
                    ["UNDERTEMP_ALARM", "OVERTEMP_ALARM", "ELEMENT_ERROR"],
                    key=f"sim_over_{i}"
                )
            with sim_cols[1]:
                st.markdown('<div style="margin-top: 1.6rem; text-align:right;">', unsafe_allow_html=True)
                if st.button("Trigger", key=f"sim_over_btn_{i}"):
                    send_to_pi({"type": "SIMULATE_ALARM", "shelf": i+1, "name": sim_name})
                    st.session_state.logs.append(f"[{ts_now()}] Sim request: {sim_name} on shelf {i}")
                st.markdown('</div>', unsafe_allow_html=True)

# --- Shelves (per shelf detail)
with tab_shelves:
    st.markdown('<div class="section-title">Shelves</div>', unsafe_allow_html=True)
    for i in range(st.session_state.num_shelves):
        with st.expander(f"Shelf {i}", expanded=(st.session_state.num_shelves <= 2)):
            render_chart(i, context="shelves")
            avg = rolling_avg(list(st.session_state.hist[i]), 20)
            st.caption(f"Rolling avg (20): {avg:.1f}°C" if avg else "Rolling avg (20): —")

# --- Alarms (old style buckets)
with tab_alarms:
    colA, colB = st.columns(2)
    with colA:
        st.markdown("### CRITICAL")
        items = st.session_state.alarms["CRITICAL"]
        if not items:
            st.success("No CRITICAL alarms")
        for a in items:
            with st.expander(f"Shelf {a['shelf']} — {a['name']}"):
                st.write(a.get("desc", a["name"]))
                if not a["ack"]:
                    if st.button("Acknowledge", key=f"ack_C_{a['shelf']}_{a['name']}"):
                        ack_alarm("CRITICAL", a["shelf"], a["name"])
                else:
                    st.info("Acknowledged")
    with colB:
        st.markdown("### ERROR")
        items = st.session_state.alarms["ERROR"]
        if not items:
            st.success("No ERROR alarms")
        for a in items:
            with st.expander(f"Shelf {a['shelf']} — {a['name']}"):
                st.write(a.get("desc", a["name"]))
                if not a["ack"]:
                    if st.button("Acknowledge", key=f"ack_E_{a['shelf']}_{a['name']}"):
                        ack_alarm("ERROR", a["shelf"], a["name"])
                else:
                    st.info("Acknowledged")

# --- Barcodes
with tab_barcodes:
    st.markdown('<div class="section-title">Barcodes</div>', unsafe_allow_html=True)
    st.segmented_control("Mode", ["LOAD", "UNLOAD"], key="barcode_mode")

    with st.form("scan", clear_on_submit=True):
        sku = st.text_input("Scan SKU")
        submitted = st.form_submit_button("Submit")
        if submitted and sku.strip():
            if st.session_state.barcode_mode == "LOAD":
                st.session_state.barcode_counts[sku] += 1
            else:
                st.session_state.barcode_counts[sku] = max(0, st.session_state.barcode_counts[sku] - 1)
            st.session_state.last_img_name_by_sku.setdefault(sku, None)
            st.success(f"{st.session_state.barcode_mode} {sku} → {st.session_state.barcode_counts[sku]}")

    st.markdown("#### Counts")
    for sku, cnt in sorted(st.session_state.barcode_counts.items()):
        with st.expander(f"{sku} → {cnt}"):
            display_barcode_image(sku)
            up = st.file_uploader(f"Upload image for {sku}", type=["png","jpg","jpeg"], key=f"up_{sku}")
            url = st.text_input(f"Image URL for {sku}", key=f"url_{sku}")
            set_cols = st.columns([1,1])
            with set_cols[0]:
                if st.button("Set Upload", key=f"set_up_{sku}") and up:
                    set_barcode_image(sku, uploader=up)
            with set_cols[1]:
                if st.button("Set URL", key=f"set_url_{sku}") and url:
                    set_barcode_image(sku, url=url)

    # Gallery
    st.markdown("#### Image Gallery")
    if st.session_state.barcode_images:
        gcols = st.columns(4)
        idx = 0
        for sku, meta in st.session_state.barcode_images.items():
            with gcols[idx % 4]:
                st.markdown(f"**{sku}**")
                display_barcode_image(sku)
            idx += 1
    else:
        st.info("No images saved yet.")

# --- Maintenance
with tab_maint:
    st.markdown('<div class="section-title">Maintenance</div>', unsafe_allow_html=True)
    m = st.session_state.maintenance
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Fan hours", f"{m['fan_hours']:.1f} h")
    with c2:
        st.metric("Element hours", f"{m['element_hours']:.1f} h")
    with c3:
        remain_fan = max(0.0, m["fan_life_hours"] - m["fan_hours"])
        st.metric("Fan remaining", f"{remain_fan:.0f} h")
    with c4:
        remain_elem = max(0.0, m["element_life_hours"] - m["element_hours"])
        st.metric("Element remaining", f"{remain_elem:.0f} h")

    st.markdown("#### Export Maintenance Report")
    df = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fan_hours": m["fan_hours"],
        "element_hours": m["element_hours"],
        "fan_life_hours": m["fan_life_hours"],
        "element_life_hours": m["element_life_hours"],
    }])
    st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name="maintenance_report.csv", mime="text/csv")

    

# --- Logs
with tab_logs:
    st.markdown('<div class="section-title">Logs</div>', unsafe_allow_html=True)
    filter_txt = st.text_input("Filter contains")
    lines = [ln for ln in st.session_state.logs if (filter_txt.lower() in ln.lower())] if filter_txt else list(st.session_state.logs)
    st.code("\n".join(lines[-300:]))

    content = "\n".join(st.session_state.logs)
    st.download_button("Download Logs", data=content, file_name="logs.txt", mime="text/plain")

# --- Settings
with tab_settings:
    st.markdown('<div class="section-title">Settings</div>', unsafe_allow_html=True)
    st.write("• Theme is driven by CSS. Inter font is applied globally via Google Fonts.")
    st.write("• Live/Demo mode is automatic (no port UI in this showcase).")
    st.write("• Use this tab to host app-level toggles in your production build.")
    # --- In Settings tab (add below existing writes) ---
    with st.container(border=True):
        st.subheader("Serial Connection")
        # Suggest common ports for Windows, macOS, Linux / Pi
        default_candidates = ["COM3", "COM4", "COM5", "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyAMA0", "/dev/tty.usbmodem*", "/dev/tty.usbserial*"]
        port = st.text_input("Port", value=st.session_state.get("serial_port", "COM4"),
                            help="Examples: COM4 (Windows), /dev/ttyUSB0 or /dev/ttyACM0 (Linux/Pi)")
        baud = st.number_input("Baud", value=st.session_state.get("serial_baud", 115200), step=1200)
        cols = st.columns([1,1,1])
        with cols[0]:
            if st.button("Connect / Reconnect"):
                st.session_state.serial_port = port
                st.session_state.serial_baud = int(baud)
                # Force a reconnect attempt on next tick
                st.session_state._attempted_connect = False
                st.session_state.demo_mode = True  # will flip to LIVE if connect succeeds
        with cols[1]:
            if st.button("Disconnect"):
                if st.session_state.get("link") and st.session_state.link.is_open():
                    try:
                        st.session_state.link.ser.close()
                    except Exception:
                        pass
                st.session_state.link = None
                st.session_state.serial_connected = False
                st.session_state.demo_mode = True
        with cols[2]:
            st.markdown(f"**State:** {'LIVE' if not st.session_state.demo_mode else 'DEMO'}")