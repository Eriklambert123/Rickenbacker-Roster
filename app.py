import streamlit as st
import psycopg2
import pandas as pd
import datetime
import calendar
import time
import io
import warnings

warnings.filterwarnings('ignore')

# Force calendar to start on Sunday
calendar.setfirstweekday(calendar.SUNDAY)

st.set_page_config(page_title="Rickenbacker Fire Department Management", layout="wide", initial_sidebar_state="expanded")

# 🛑 Streamlit Cloud Secrets
DB_URI = st.secrets["DB_URI"]
ADMIN_PIN = st.secrets.get("ADMIN_PIN", "2026")

st.markdown("""
<style>
[data-testid="column"] { padding: 0 0.3rem !important; }
[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 0.5rem !important; }
[data-testid="stPopover"] button { padding: 4px 6px !important; min-height: auto !important; height: auto !important; }
[data-testid="stPopover"] button p { 
    font-size: 0.82rem !important; 
    white-space: normal !important; 
    line-height: 1.2 !important; 
    margin: 0 !important; 
}
hr { margin: 0.8em 0 !important; }
</style>
""", unsafe_allow_html=True)

def get_icon(entry_type):
    icons = {
        "Annual Leave": "🏖️", "Paternity Leave": "🍼", "Union Leave": "🤝", 
        "Bereavement Leave": "🕊️", "Medical Leave": "🏥", "Military Leave": "🪖", 
        "Jury Duty": "⚖️", "NFPA Physical": "🩺", "Personal Leave": "👤", 
        "Disability Leave": "♿", "Training": "📚", "Voluntary": "💰", "Mandatory": "🚨", "Guard Personnel On-Duty": "🫡"
    }
    return icons.get(entry_type, "📌")

def calc_hours(start_str, end_str):
    try:
        s, e = int(start_str.replace(":", "")), int(end_str.replace(":", ""))
        s_dec = (s // 100) + ((s % 100) / 60.0)
        e_dec = (e // 100) + ((e % 100) / 60.0)
        if e_dec <= s_dec: 
            e_dec += 24.0
        return round(e_dec - s_dec, 1)
    except: 
        return 0.0

def parse_time(start_str, end_str, hrs):
    if hrs >= 24: 
        return True, True
    try:
        s, e = int(start_str.replace(":", "")), int(end_str.replace(":", ""))
        am, pm = False, False
        if s < 1500: 
            am = True
        if e > 1500 or e <= 700: 
            pm = True
        if s >= 1500 and (e <= 700 or e > 1500): 
            pm = True
            am = False
        if s < 1500 and e <= 1500: 
            am = True
            pm = False
        return am, pm
    except: 
        return True, True

# --- 1. SIDEBAR, AUTH, & SHIFT AUTOCALC ---
st.sidebar.header("Dashboard Controls")
target_date = st.sidebar.date_input("Select Target Date", datetime.date.today())
target_date_str = target_date.strftime("%Y-%m-%d")

anchor_date = datetime.date(2026, 9, 16) 
shift_mod = (target_date - anchor_date).days % 3

if shift_mod == 0: 
    selected_shift, top_shift_color = "A-Shift", "#4CAF50"
elif shift_mod == 1: 
    selected_shift, top_shift_color = "B-Shift", "#2196F3"
else: 
    selected_shift, top_shift_color = "C-Shift", "#F44336"

st.sidebar.markdown(f"**Target Shift:** <span style='color:{top_shift_color}; font-weight:bold; font-size:1.1em;'>{selected_shift}</span>", unsafe_allow_html=True)

st.sidebar.divider()
st.sidebar.markdown("### 🔒 System Access")
entered_pin = st.sidebar.text_input("Enter PIN to Edit", type="password")
is_admin = (entered_pin == str(ADMIN_PIN))

if is_admin:
    st.sidebar.success("✅ Admin Unlocked")
else:
    st.sidebar.info("👀 Read-Only Mode")

# --- 2. POSTGRES DATABASE CLOUD CONNECTION ---
def get_db_connection():
    return psycopg2.connect(DB_URI)

conn = get_db_connection()
c = conn.cursor()

# Create General Tables
c.execute('''CREATE TABLE IF NOT EXISTS ARO_Watch (id SERIAL PRIMARY KEY, Target_Date TEXT, Watch_Period TEXT, Name TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Shift_Trades_V3 (id SERIAL PRIMARY KEY, Date_1 TEXT, Name_1 TEXT, Date_2 TEXT, Name_2 TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Position_Log (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Position TEXT, Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Manual_Overrides (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Seat TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Personnel (Name TEXT, Seniority INTEGER, Core_Manning BOOLEAN, Schedule TEXT, Shift TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Leave_Ledger (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Leave_Type TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Overtime_Log (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, OT_Type TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')
