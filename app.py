import streamlit as st
import psycopg2
import pandas as pd
import datetime
import calendar
import time

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
        if e_dec <= s_dec: e_dec += 24.0
        return round(e_dec - s_dec, 1)
    except: return 0.0

def parse_time(start_str, end_str, hrs):
    if hrs >= 24: return True, True
    try:
        s, e = int(start_str.replace(":", "")), int(end_str.replace(":", ""))
        am, pm = False, False
        if s < 1500: am = True
        if e > 1500 or e <= 700: pm = True
        if s >= 1500 and (e <= 700 or e > 1500): pm = True; am = False
        if s < 1500 and e <= 1500: am = True; pm = False
        return am, pm
    except: return True, True

# --- 1. SIDEBAR, AUTH, & SHIFT AUTOCALC ---
st.sidebar.header("Dashboard Controls")
target_date = st.sidebar.date_input("Select Target Date", datetime.date.today())
target_date_str = target_date.strftime("%Y-%m-%d")

anchor_date = datetime.date(2026, 9, 16) 
shift_mod = (target_date - anchor_date).days % 3

if shift_mod == 0: selected_shift, top_shift_color = "A-Shift", "#4CAF50"
elif shift_mod == 1: selected_shift, top_shift_color = "B-Shift", "#2196F3"
else: selected_shift, top_shift_color = "C-Shift", "#F44336"

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

# Create Overtime Bucket & Archive Tables
c.execute('''CREATE TABLE IF NOT EXISTS Overtime_Buckets (Name TEXT PRIMARY KEY, Seniority INTEGER, Shift TEXT, Current_Hours REAL, Contact TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS OT_Archive (id SERIAL PRIMARY KEY, Archive_Stamp TEXT, Notes TEXT, Name TEXT, Start_Hours REAL, Hours_Applied REAL, Status TEXT)''')

# Auto-Seed Primary Roster if Blank
c.execute("SELECT COUNT(*) FROM Personnel")
if c.fetchone()[0] == 0:
    roster = [
        ("McJunkin", 1, True, "Structural", "A"), ("Atkins", 2, True, "Structural", "C"), ("Buzzard", 3, True, "Structural", "B"),
        ("McNamara", 4, False, "Admin", "ADMIN"), ("Garver", 5, False, "Admin", "ADMIN"), ("Slonaker", 6, True, "Structural", "C"),
        ("Riley", 7, True, "Structural", "A"), ("Lambert", 8, True, "Structural", "B"), ("Roach M", 9, True, "Structural", "B"),
        ("McKee", 10, True, "Structural", "C"), ("Klaus", 11, True, "Structural", "B"), ("Moeller", 12, True, "Structural", "A"),
        ("Downing", 13, True, "Structural", "C"), ("Finch", 14, True, "Structural", "C"), ("Moore", 15, True, "Structural", "A"),
        ("Rickord", 16, True, "Structural", "B"), ("Conrad", 17, True, "Structural", "A"), ("Ross", 18, True, "Structural", "C"),
        ("Proffitt", 19, True, "Structural", "A"), ("Guilliams", 20, True, "Structural", "C"), ("Roach P", 21, True, "Structural", "A"),
        ("Elswick", 22, True, "Structural", "B"), ("Blom", 23, True, "Structural", "A"), ("Hurst", 24, True, "Structural", "C"),
        ("Lee", 25, True, "Structural", "A"), ("Bissett II", 26, True, "Structural", "C"), ("Mefford", 27, True, "Structural", "B"),
        ("Payne", 28, True, "Structural", "B"), ("Garber", 29, True, "Structural", "C"), ("Eagle", 30, True, "Structural", "C"),
        ("Napier", 31, True, "Structural", "A"), ("Schaefer", 32, True, "Structural", "B"), ("Staggs", 33, True, "Structural", "B"),
        ("Martin", 34, True, "Structural", "B"), ("Johnson", 35, True, "Structural", "A")
    ]
    c.executemany("INSERT INTO Personnel (Name, Seniority, Core_Manning, Schedule, Shift) VALUES (%s, %s, %s, %s, %s)", roster)

# Auto-Seed Overtime Buckets if Blank
c.execute("SELECT COUNT(*) FROM Overtime_Buckets")
if c.fetchone()[0] == 0:
    ot_seed = [
        ("Aaron Elswick", 14, "B", 0.0, "No"), ("Adam Schaefer", 23, "B", 0.0, "No"), ("Zach Garber", 22, "C", 93.5, "No"),
        ("John Klaus", 2, "B", 425.0, "No"), ("Lucas Blom", 19, "A", 795.0, "No"), ("Matthew Roach", 6, "B", 866.5, "Yes"),
        ("O'Neal Payne", 20, "B", 881.0, "Yes"), ("Robert Bissett", 8, "C", 884.0, "Yes"), ("Douglas Proffitt", 18, "A", 884.0, "Yes"),
        ("Jakob Staggs", 11, "B", 884.5, "Yes"), ("John Martin", 24, "B", 886.5, "Yes"), ("Jacob Mefford", 9, "B", 887.5, "Yes"),
        ("Scott Rickord", 17, "B", 888.0, "Yes"), ("Danny Lee", 3, "A", 888.5, "Yes"), ("Dusty Downing", 4, "C", 889.2, "Yes"),
        ("Erik Guilliams", 25, "C", 889.5, "Yes"), ("Michael Ross", 13, "C", 891.5, "Yes"), ("Paul Roach", 7, "A", 892.0, "Yes"),
        ("Christopher Hurst", 5, "C", 900.0, "Yes"), ("James Moeller", 10, "A", 900.5, "Yes"), ("Matthew Moore", 15, "A", 900.5, "Yes"),
        ("Dane Eagle", 27, "C", 901.5, "Yes"), ("Gideon Johnson", 21, "A", 902.94, "Yes"), ("Jerry Napier", 16, "A", 903.0, "Yes"),
        ("Brian Conrad", 1, "A", 904.5, "Yes"), ("Chris McKee", 26, "C", 904.5, "Yes"), ("Kenneth Finch", 12, "C", 905.0, "Yes")
    ]
    c.executemany("INSERT INTO Overtime_Buckets (Name, Seniority, Shift, Current_Hours, Contact) VALUES (%s, %s, %s, %s, %s)", ot_seed)

conn.commit()

import warnings
warnings.filterwarnings('ignore')

# Read Data & Standardize Column Names to Lowercase
all_personnel_df = pd.read_sql("SELECT * FROM Personnel", conn)
all_personnel_df.columns = [c.lower() for c in all_personnel_df.columns]

all_leave_df = pd.read_sql("SELECT * FROM Leave_Ledger", conn)
if not all_leave_df.empty: all_leave_df.columns = [c.lower() for c in all_leave_df.columns]
else: all_leave_df = pd.DataFrame(columns=["id", "name", "target_date", "leave_type", "start_time", "end_time", "total_hours"])

all_ot_df = pd.read_sql("SELECT * FROM Overtime_Log", conn)
if not all_ot_df.empty: all_ot_df.columns = [c.lower() for c in all_ot_df.columns]
else: all_ot_df = pd.DataFrame(columns=["id", "name", "target_date", "ot_type", "start_time", "end_time", "total_hours"])

all_trades_df = pd.read_sql("SELECT * FROM Shift_Trades_V3", conn)
if not all_trades_df.empty: all_trades_df.columns = [c.lower() for c in all_trades_df.columns]
else: all_trades_df = pd.DataFrame(columns=["id", "date_1", "name_1", "date_2", "name_2", "start_time", "end_time", "total_hours"])

aro_watch_df = pd.read_sql("SELECT * FROM ARO_Watch WHERE Target_Date = %s", conn, params=(target_date_str,))
if not aro_watch_df.empty: aro_watch_df.columns = [c.lower() for c in aro_watch_df.columns]
else: aro_watch_df = pd.DataFrame(columns=["id", "target_date", "watch_period", "name"])

overrides_df = pd.read_sql("SELECT * FROM Manual_Overrides WHERE Target_Date = %s", conn, params=(target_date_str,))
if not overrides_df.empty: overrides_df.columns = [c.lower() for c in overrides_df.columns]
else: overrides_df = pd.DataFrame(columns=["id", "target_date", "name", "seat"])

stats_df = pd.read_sql("SELECT Name, Position, SUM(Hours) as Total_Hours FROM Position_Log GROUP BY Name, Position", conn)
if not stats_df.empty: stats_df.columns = [c.lower() for c in stats_df.columns]
else: stats_df = pd.DataFrame(columns=["name", "position", "total_hours"])
conn.close()

personnel_info = {}
admin_names = set()
for _, r in all_personnel_df.iterrows():
    personnel_info[r['name']] = {'Seniority': r['seniority'], 'Schedule': r['schedule'], 'Core': r['core_manning'], 'Shift': r['shift']}
    if r['schedule'] == 'Admin': admin_names.add(r['name'])

override_dict = dict(zip(overrides_df['name'], overrides_df['seat'])) if not overrides_df.empty else {}
seat_to_override = {v: k for k, v in override_dict.items()}

stat_lookup = {}
if not stats_df.empty:
    for _, r in stats_df.iterrows():
        clean_name = r['name']
        if clean_name not in stat_lookup: stat_lookup[clean_name] = {}
        stat_lookup[clean_name][r['position']] = r['total_hours']

def get_hours_stat(name_with_suffix, seat_name):
    clean = name_with_suffix.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
    return stat_lookup.get(clean, {}).get(seat_name, 0)

db_watches = dict(zip(aro_watch_df['watch_period'], aro_watch_df['name'])) if not aro_watch_df.empty else {}
db_shift = selected_shift[0] 

# Weekend Admin Exclusion Check
if target_date.weekday() < 5:  # Monday to Friday
    personnel_df = all_personnel_df[(all_personnel_df['shift'] == db_shift) | (all_personnel_df['shift'] == 'ADMIN')]
else:  # Saturday and Sunday
    personnel_df = all_personnel_df[(all_personnel_df['shift'] == db_shift)]

leave_df = all_leave_df[all_leave_df['target_date'] == target_date_str] if not all_leave_df.empty else pd.DataFrame()
ot_df = all_ot_df[all_ot_df['target_date'] == target_date_str] if not all_ot_df.empty else pd.DataFrame()
all_names = sorted(all_personnel_df['name'].tolist())

officer_seats = [("CH-222", "AC"), ("E-221", "Station Captain"), ("R-221", "Crew Chief")]
rotating_seats = [("ARO", "0700-1200 (Float)"), ("E-221", "Driver/Operator"), ("E-221", "Firefighter 1"), ("E-221", "Firefighter 2"), ("R-221", "Driver/Operator"), ("R-221", "Firefighter 1")]
crash_seats = [("Crash-4", "Driver"), ("Crash-1", "Driver"), ("Crash-3", "Driver"), ("Crash-8", "Driver")]

# Visual Ordering for Dropdown
all_seats_list = [
    "CH-222 | AC", "E-221 | Station Captain", "E-221 | Driver/Operator", "E-221 | Firefighter 1", "E-221 | Firefighter 2",
    "R-221 | Crew Chief", "R-221 | Driver/Operator", "R-221 | Firefighter 1", "ARO | 0700-1200 (Float)",
    "Crash-1 | Driver", "Crash-3 | Driver", "Crash-4 | Driver", "Crash-8 | Driver", "Tanker | Crew Chief", "Tanker | Driver"
]

# --- 3. ROSTER LOGIC ENGINE ---
leaves_am, leaves_pm = set(), set()
partial_leaves = []
if not leave_df.empty:
    for _, l in leave_df.iterrows():
        am, pm = parse_time(l['start_time'], l['end_time'], float(l['total_hours']))
        if am: leaves_am.add(l['name'])
        if pm: leaves_pm.add(l['name'])
        if float(l['total_hours']) < 24.0: partial_leaves.append(l['name'])

trades_am_off, trades_pm_off = set(), set()
trades_am_work, trades_pm_work = {}, {}
if not all_trades_df.empty:
    for _, t in all_trades_df.iterrows():
        am, pm = parse_time(t['start_time'], t['end_time'], float(t['total_hours']))
        if t['date_1'] == target_date_str:
            if am: trades_am_off.add(t['name_1']); trades_am_work[t['name_2']] = "Trade"
            if pm: trades_pm_off.add(t['name_1']); trades_pm_work[t['name_2']] = "Trade"
            if float(t['total_hours']) < 24.0: partial_leaves.append(t['name_1'])
        if t['date_2'] == target_date_str:
            if am: trades_am_off.add(t['name_2']); trades_am_work[t['name_1']] = "Trade"
            if pm: trades_pm_off.add(t['name_2']); trades_pm_work[t['name_1']] = "Trade"
            if float(t['total_hours']) < 24.0: partial_leaves.append(t['name_2'])

ot_am_work, ot_pm_work = {}, {}
if not ot_df.empty:
    for _, o in ot_df.iterrows():
        am, pm = parse_time(o['start_time'], o['end_time'], float(o['total_hours']))
        suffix = "Guard" if "Guard" in o['ot_type'] else "OT"
        if am: ot_am_work[o['name']] = suffix
        if pm: ot_pm_work[o['name']] = suffix

all_working_am, all_working_pm = [], []
current_weekday = target_date.weekday() # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun

for _, person in personnel_df.iterrows():
    name = person['name']
    
    # NEW ADMIN SCHEDULE ENGINE
    admin_pm_active = True
    if person['shift'] == 'ADMIN':
        if name == 'Garver' and current_weekday != 2:  # Garver 24h on Wed (2)
            admin_pm_active = False
        if name == 'McNamara' and current_weekday != 1: # McNamara 24h on Tue (1)
            admin_pm_active = False
            
    if name not in leaves_am and name not in trades_am_off and name not in trades_am_work and name not in ot_am_work:
        all_working_am.append((name, ""))
        
    if admin_pm_active:
        if name not in leaves_pm and name not in trades_pm_off and name not in trades_pm_work and name not in ot_pm_work:
            all_working_pm.append((name, ""))

for name, suffix in trades_am_work.items(): all_working_am.append((name, suffix))
for name, suffix in trades_pm_work.items(): all_working_pm.append((name, suffix))
for name, suffix in ot_am_work.items(): all_working_am.append((name, suffix))
for name, suffix in ot_pm_work.items(): all_working_pm.append((name, suffix))

am_crew, admin_am = [], []
for name, suffix in all_working_am:
    sen = personnel_info.get(name, {}).get('Seniority', 50)
    sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
    if suffix in ["Trade", "OT", "Guard"]: sched = 'Structural'
    d_name = f"{name} ({suffix})" if suffix else name
    if sched == 'Admin': admin_am.append({"Name": d_name, "Seniority": sen})
    else: am_crew.append({"Name": d_name, "Seniority": sen})

pm_crew, admin_pm = [], []
for name, suffix in all_working_pm:
    sen = personnel_info.get(name, {}).get('Seniority', 50)
    sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
    if suffix in ["Trade", "OT", "Guard"]: sched = 'Structural'
    d_name = f"{name} ({suffix})" if suffix else name
    if sched == 'Admin': admin_pm.append({"Name": d_name, "Seniority": sen})
    else: pm_crew.append({"Name": d_name, "Seniority": sen})

admin_am.sort(key=lambda x: x["Seniority"], reverse=True)
admin_pm.sort(key=lambda x: x["Seniority"], reverse=True)

while len(am_crew) < 9 and len(admin_am) > 0: am_crew.append(admin_am.pop(0))
while len(pm_crew) < 9 and len(admin_pm) > 0: pm_crew.append(admin_pm.pop(0))

am_crew.sort(key=lambda x: x["Seniority"])
pm_crew.sort(key=lambda x: x["Seniority"])

am_crew_dict = {p["Name"]: p for p in am_crew}
pm_crew_dict = {p["Name"]: p for p in pm_crew}

# ON-DUTY OVERRIDE LIST GENERATOR 
all_on_duty = set([p["Name"] for p in am_crew] + [p["Name"] for p in pm_crew])
on_duty_clean_names = set()
for name in all_on_duty:
    clean_name = name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
    if clean_name and clean_name != "VACANT":
        on_duty_clean_names.add(clean_name)
on_duty_dropdown_list = sorted(list(on_duty_clean_names))

def get_empty_rigs():
    return {
        "CH-222": {"AC": ""}, 
        "R-221": {"Crew Chief": "", "Driver/Operator": "", "Firefighter 1": ""}, 
        "E-221": {"Station Captain": "", "Driver/Operator": "", "Firefighter 1": "", "Firefighter 2": ""}, 
        "ARO": {"0700-1200 (Float)": "", "1200-1700": "TBD (Draw)", "1700-2200": "TBD (Draw)", "2200-0600": "TBD (Draw)", "0600-0700": "TBD (Draw)"},
        "Crash-4": {"Crew Chief": "", "Driver": ""}, "Crash-1": {"Crew Chief": "", "Driver": ""}, "Crash-3": {"Driver": ""}, "Crash-8": {"Driver": ""},
        "Tanker": {"Crew Chief": "", "Driver": ""}
    }

# --- 3a. ASSIGN AM ROSTER ---
am_rigs = get_empty_rigs()
am_pool = am_crew.copy()

# 1. Apply Overrides
for seat_name, locked_name in seat_to_override.items():
    rig, pos = seat_name.split(" | ")
    if locked_name == "VACANT":
        am_rigs[rig][pos] = "VACANT"
    else:
        for i, p in enumerate(am_pool):
            clean_p = p["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
            if clean_p == locked_name:
                am_rigs[rig][pos] = am_pool.pop(i)["Name"]
                break

# 2. Assign Officers
for rig, pos in officer_seats:
    if am_pool and am_rigs[rig][pos] == "":
