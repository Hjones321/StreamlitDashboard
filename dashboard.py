
import streamlit as st
import time
import random
from collections import deque
import plotly.graph_objects as go


NUM_SHELVES = 2
REFRESH_SEC = 1
HISTORY_LEN = 120   # ~2 minutes


if "temps" not in st.session_state:
    st.session_state.temps = [72.0 for _ in range(NUM_SHELVES)]

if "bounds" not in st.session_state:
    st.session_state.bounds = [
        {"setpoint": 75.0, "upper": 80.0, "lower": 73.0, "isOn": True}
        for _ in range(NUM_SHELVES)
    ]

if "history" not in st.session_state:
    st.session_state.history = {i: deque(maxlen=HISTORY_LEN) for i in range(NUM_SHELVES)}

if "alarms" not in st.session_state:
    st.session_state.alarms = {i: [] for i in range(NUM_SHELVES)}

if "logs" not in st.session_state:
    st.session_state.logs = deque(maxlen=300)

if "barcodes" not in st.session_state:
    st.session_state.barcodes = deque(maxlen=20)

def log(msg):
    st.session_state.logs.append(
        f"[{time.strftime('%H:%M:%S')}] {msg}"
    )

def update_dummy_data():
    for i in range(NUM_SHELVES):
        if not st.session_state.bounds[i]["isOn"]:
            continue

        delta = random.uniform(-0.3, 0.3)
        st.session_state.temps[i] += delta
        t = st.session_state.temps[i]

        hist = st.session_state.history[i]
        hist.append(t)

        # Alarm triggers (dummy logic)
        if t > st.session_state.bounds[i]["upper"] + 3:
            add_alarm(i, "OVERTEMP", "ERROR", "Temperature above safe limit")

        if t < st.session_state.bounds[i]["lower"] - 3:
            add_alarm(i, "UNDERTEMP", "ERROR", "Temperature below safe limit")

def add_alarm(shelf, name, severity, description):
    if not any(a["name"] == name for a in st.session_state.alarms[shelf]):
        st.session_state.alarms[shelf].append({
            "name": name,
            "severity": severity,
            "ack": False,
            "desc": description,
            "ts": time.time()
        })
        log(f"Alarm triggered: Shelf {shelf} {name} ({severity})")

def acknowledge_alarm(shelf, name):
    for a in st.session_state.alarms[shelf]:
        if a["name"] == name:
            a["ack"] = True
            log(f"Alarm acknowledged: Shelf {shelf} {name}")


st.set_page_config(page_title="Heating Dashboard (Demo)", layout="wide")
st.title("Heating System Dashboard (Demo Mode)")


with st.sidebar:
    st.header("Simulation Controls")

    shelf = st.number_input("Shelf", 0, NUM_SHELVES-1, 0)
    alarm = st.selectbox("Alarm", ["OVERTEMP", "UNDERTEMP", "ELEMENT_ERROR"])
    severity = st.selectbox("Severity", ["ERROR", "CRITICAL"])

    if st.button("Simulate Alarm"):
        add_alarm(shelf, alarm, severity, "Manually simulated alarm")

    if st.button("Toggle Shelf ON/OFF"):
        st.session_state.bounds[shelf]["isOn"] = not st.session_state.bounds[shelf]["isOn"]
        state = "ON" if st.session_state.bounds[shelf]["isOn"] else "OFF"
        log(f"Shelf {shelf} turned {state}")

    if st.button("Simulate Barcode Scan"):
        code = f"SKU-{random.randint(1000,9999)}"
        st.session_state.barcodes.append(code)
        log(f"Barcode scanned: {code}")


st.subheader("Active Alarms")

cols = st.columns(2)

for col, severity in zip(cols, ["CRITICAL", "ERROR"]):
    with col:
        st.markdown(f"### {severity}")
        found = False

        for shelf, alarm_list in st.session_state.alarms.items():
            for a in alarm_list:
                if a["severity"] == severity:
                    found = True
                    with st.expander(
                        f"Shelf {shelf} – {a['name']}", expanded=True
                    ):
                        st.write(a["desc"])
                        st.write(time.strftime('%H:%M:%S', time.localtime(a["ts"])))

                        if a["ack"]:
                            st.success("Acknowledged")
                        else:
                            if st.button("Acknowledge", key=f"ack_{shelf}_{a['name']}"):
                                acknowledge_alarm(shelf, a["name"])

        if not found:
            st.success("No alarms")


st.subheader("📈 Live Temperatures")

graph_cols = st.columns(NUM_SHELVES)

for i in range(NUM_SHELVES):
    with graph_cols[i]:
        status = st.session_state.bounds[i]["isOn"]
        st.markdown(
            f"### Shelf {i} — {'🟢 ON' if status else '🔴 OFF'}"
        )

        hist = list(st.session_state.history[i])

        if hist:
            sp = st.session_state.bounds[i]["setpoint"]
            up = st.session_state.bounds[i]["upper"]
            lo = st.session_state.bounds[i]["lower"]

            fig = go.Figure()
            fig.add_trace(go.Scatter(y=hist, name="Temp"))
            fig.add_hline(y=sp, line_dash="dash", line_color="orange", annotation_text="Setpoint")
            fig.add_hline(y=up, line_dash="dot", line_color="green", annotation_text="Upper")
            fig.add_hline(y=lo, line_dash="dot", line_color="green", annotation_text="Lower")

            fig.update_layout(height=300, margin=dict(l=10,r=10,t=30,b=10))
            st.plotly_chart(fig, width='stretch')
        else:
            st.write("No data yet")



#fanlife
#elementlife

st.subheader("System Info")

bc_col, log_col = st.columns([1, 2])

with bc_col:
    st.markdown("### Barcode")
    if st.session_state.barcodes:
        st.code(st.session_state.barcodes[-1])
    else:
        st.write("None")

with log_col:
    st.markdown("### Logs")
    st.code("\n".join(st.session_state.logs), language="text")


update_dummy_data()
time.sleep(REFRESH_SEC)
st.rerun()
