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

# Updated CSS to allow long text to wrap cleanly to two lines and dynamically grow the button height
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
        "Disability Leave": "♿", "Voluntary": "💰", "Mandatory": "🚨", "Guard Personnel On-Duty": "🫡"
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

for i in range(len(am_pool)-1, -1, -1):
    clean_name = am_pool[i]["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
    if clean_name in override_dict:
        rig, pos = override_dict[clean_name].split(" | ")
        if am_rigs[rig][pos] == "": am_rigs[rig][pos] = am_pool.pop(i)["Name"]

for rig, pos in officer_seats:
    if am_pool and am_rigs[rig][pos] == "":
        chosen = None
        if rig == "CH-222":
            cands = [p for p in am_pool if p['Seniority'] <= 4]
            if cands: chosen = cands[0]
        elif rig == "E-221":
            cands = [p for p in am_pool if 6 <= p['Seniority'] <= 8]
            if cands: chosen = cands[0]
        elif rig == "R-221":
            cands = [p for p in am_pool if p['Seniority'] == 5] # Admin Captain priority
            if not cands: cands = [p for p in am_pool if 9 <= p['Seniority'] <= 10] # Lieutenant fallback
            if cands: chosen = cands[0]
        
        if not chosen:
            am_pool.sort(key=lambda x: (x["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") in partial_leaves, x["Seniority"]))
            chosen = am_pool[0]
            
        am_rigs[rig][pos] = chosen["Name"]
        am_pool.remove(chosen)

rotation_pool = []
while am_pool and len(rotation_pool) < len([p for r, p in rotating_seats if am_rigs[r][p] == ""]):
    rotation_pool.append(am_pool.pop(0))
    
for rig, pos in rotating_seats:
    if am_rigs[rig][pos] == "":
        if not rotation_pool: break
        seat_name = f"{rig} {pos}"
        if rig == "ARO":
            eligible = [p for p in rotation_pool if p["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") not in admin_names]
            if not eligible: eligible = rotation_pool
        else: eligible = rotation_pool
            
        eligible.sort(key=lambda x: (get_hours_stat(x["Name"], seat_name), x["Seniority"]))
        chosen = eligible[0]
        rotation_pool.remove(chosen)
        am_rigs[rig][pos] = chosen["Name"]

for rig, pos in crash_seats:
    if am_pool and am_rigs[rig][pos] == "": am_rigs[rig][pos] = am_pool.pop(0)["Name"]

# --- 3b. ASSIGN PM ROSTER ---
pm_rigs = get_empty_rigs()
pm_names = set(pm_crew_dict.keys())
am_names = set(am_crew_dict.keys())
assigned_pm_names = set()

for name in pm_names:
    clean_name = name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
    if clean_name in override_dict:
        rig, pos = override_dict[clean_name].split(" | ")
        if pm_rigs[rig][pos] == "":
            pm_rigs[rig][pos] = name
            assigned_pm_names.add(name)

arrivers = [pm_crew_dict[n] for n in (pm_names - am_names) if n not in assigned_pm_names]
arrivers.sort(key=lambda x: x["Seniority"])

aro_am = am_rigs["ARO"]["0700-1200 (Float)"]
if pm_rigs["ARO"]["0700-1200 (Float)"] == "" and aro_am != "":
    if aro_am in pm_names and aro_am not in assigned_pm_names:
        pm_rigs["ARO"]["0700-1200 (Float)"] = aro_am
        assigned_pm_names.add(aro_am)
    elif arrivers:
        eligible = [p for p in arrivers if p["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") not in admin_names]
        if eligible:
            p = eligible[0]
            arrivers.remove(p)
            pm_rigs["ARO"]["0700-1200 (Float)"] = p["Name"]
            assigned_pm_names.add(p["Name"])

num_crash_pm = max(0, len(pm_names) - 9)
for rig, pos in crash_seats[:num_crash_pm]:
    if pm_rigs[rig][pos] == "":
        am_name = am_rigs[rig][pos]
        if am_name:
            if am_name in pm_names and am_name not in assigned_pm_names:
                pm_rigs[rig][pos] = am_name
                assigned_pm_names.add(am_name)
            elif arrivers:
                p = arrivers.pop(0)["Name"]
                pm_rigs[rig][pos] = p
                assigned_pm_names.add(p)

structural_pm_pool = [pm_crew_dict[n] for n in pm_names if n not in assigned_pm_names]

for rig, pos in officer_seats:
    if pm_rigs[rig][pos] == "" and structural_pm_pool:
        chosen = None
        if rig == "CH-222":
            cands = [p for p in structural_pm_pool if p['Seniority'] <= 4]
            if cands: chosen = cands[0]
        elif rig == "E-221":
            cands = [p for p in structural_pm_pool if 6 <= p['Seniority'] <= 8]
            if cands: chosen = cands[0]
        elif rig == "R-221":
            cands = [p for p in structural_pm_pool if p['Seniority'] == 5]
            if not cands: cands = [p for p in structural_pm_pool if 9 <= p['Seniority'] <= 10]
            if cands: chosen = cands[0]
        
        if not chosen:
            structural_pm_pool.sort(key=lambda x: (x["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") in partial_leaves, x["Seniority"]))
            chosen = structural_pm_pool[0]
            
        pm_rigs[rig][pos] = chosen["Name"]
        structural_pm_pool.remove(chosen)

pm_rotating_seats = [("ARO", "0700-1200 (Float)"), ("E-221", "Driver/Operator"), ("E-221", "Firefighter 1"), ("E-221", "Firefighter 2"), ("R-221", "Driver/Operator"), ("R-221", "Firefighter 1")]
for rig, pos in pm_rotating_seats:
    if pm_rigs[rig][pos] == "":
        if not structural_pm_pool: break
        seat_name = f"{rig} {pos}"
        if rig == "ARO":
            eligible = [p for p in structural_pm_pool if p["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") not in admin_names]
            if not eligible: eligible = structural_pm_pool
        else: eligible = structural_pm_pool
        
        eligible.sort(key=lambda x: (get_hours_stat(x["Name"], seat_name), x["Seniority"]))
        chosen = eligible[0]
        structural_pm_pool.remove(chosen)
        pm_rigs[rig][pos] = chosen["Name"]

for rig, pos in crash_seats:
    if pm_rigs[rig][pos] == "" and structural_pm_pool: pm_rigs[rig][pos] = structural_pm_pool.pop(0)["Name"]

def apply_cross_staffing_and_watches(rigs):
    if rigs["Crash-4"]["Driver"] == "":
        if rigs["E-221"]["Station Captain"] != "": rigs["Crash-4"]["Driver"] = rigs["E-221"]["Station Captain"]
    else:
        if rigs["E-221"]["Station Captain"] != "": rigs["Crash-4"]["Crew Chief"] = rigs["E-221"]["Station Captain"]
    if rigs["Crash-1"]["Driver"] == "":
        if rigs["E-221"]["Driver/Operator"] != "": rigs["Crash-1"]["Driver"] = rigs["E-221"]["Driver/Operator"]
    else:
        if rigs["E-221"]["Driver/Operator"] != "": rigs["Crash-1"]["Crew Chief"] = rigs["E-221"]["Driver/Operator"]
        
    if rigs["Crash-3"]["Driver"] == "" and rigs["E-221"]["Firefighter 1"] != "": rigs["Crash-3"]["Driver"] = rigs["E-221"]["Firefighter 1"]
    if rigs["Crash-8"]["Driver"] == "" and rigs["E-221"]["Firefighter 2"] != "": rigs["Crash-8"]["Driver"] = rigs["E-221"]["Firefighter 2"]
        
    if rigs["Tanker"]["Crew Chief"] == "" and rigs["R-221"]["Crew Chief"] != "": 
        rigs["Tanker"]["Crew Chief"] = rigs["R-221"]["Crew Chief"]
    if rigs["Tanker"]["Driver"] == "" and rigs["R-221"]["Firefighter 1"] != "": 
        rigs["Tanker"]["Driver"] = rigs["R-221"]["Firefighter 1"]

    for wp in ["1200-1700", "1700-2200", "2200-0600", "0600-0700"]:
        if wp in db_watches:
            rigs["ARO"][wp] = db_watches[wp]
    return rigs

am_roster = apply_cross_staffing_and_watches(am_rigs)
pm_roster = apply_cross_staffing_and_watches(pm_rigs)

core_count = 0
for _, p in personnel_df.iterrows():
    if p['core_manning'] and p['name'] not in leaves_am and p['name'] not in trades_am_off: core_count += 1
total_on_duty = len(am_crew) 

all_on_duty = set([p["Name"] for p in am_crew] + [p["Name"] for p in pm_crew])
excluded_watches = {am_roster["CH-222"]["AC"], pm_roster["CH-222"]["AC"], am_roster["E-221"]["Station Captain"], pm_roster["E-221"]["Station Captain"], am_roster["ARO"]["0700-1200 (Float)"], pm_roster["ARO"]["0700-1200 (Float)"]}
eligible_aro_names = sorted([name for name in all_on_duty if name not in excluded_watches and name != ""])


# --- 4. STREAMLIT VISUALS & TABS ---
st.title("🚒 Rickenbacker Fire Department Management")

tab_names = ["📋 Daily Roster", "⏱️ Overtime Roster", "📅 Leave Calendar", "📊 Seat Statistics"]
if is_admin: tab_names.append("✍️ Data Entry")
tabs = st.tabs(tab_names)

tab_roster = tabs[0]
tab_ot = tabs[1]
tab_calendar = tabs[2]
tab_stats = tabs[3]
if is_admin: tab_data_entry = tabs[4]


# ==========================================
# TAB 1: DAILY ROSTER
# ==========================================
with tab_roster:
    col1, col2, col3 = st.columns(3)
    col1.metric("Total On-Duty (0700)", str(total_on_duty), "Minimum Met" if total_on_duty >= 9 else f"{9 - total_on_duty} Short")
    col2.metric("Core Headcount", str(core_count))
    col3.metric("Scheduled OT", f"{len(ot_df)} Slots")
    st.divider()
    rig_col1, rig_col2, rig_col3 = st.columns(3)

    color_map = {"error": "#d32f2f", "success": "#2e7d32", "info": "#0288d1", "warning": "#ed6c02"}

    def display_rig(rig_name, box_type):
        with st.container(border=True):
            st.markdown(f"<h4 style='color: {color_map[box_type]}; margin-top: 0; padding-bottom: 0;'>{rig_name}</h4>", unsafe_allow_html=True)
            
            for position in am_roster[rig_name]:
                am_name, pm_name = am_roster[rig_name][position], pm_roster[rig_name][position]
                
                if am_name == "": am_name = "VACANT"
                if pm_name == "": pm_name = "VACANT"
                
                if rig_name == "ARO" and position != "0700-1200 (Float)": 
                    disp_text = f"**{position}:** {am_name}"
                elif am_name == pm_name: 
                    disp_text = f"**{position}:** {am_name}"
                else: 
                    disp_text = f"**{position}:** {am_name}  >>  {pm_name} (1500)"

                seat_name = f"{rig_name} | {position}"
                is_locked = seat_name in seat_to_override
                locked_by = seat_to_override.get(seat_name)

                if is_locked: disp_text = f"🔒 " + disp_text

                if rig_name == "ARO" and position != "0700-1200 (Float)":
                    st.markdown(disp_text)
                    continue

                if is_admin:
                    c1, c2 = st.columns([7, 2])
                    c1.markdown(disp_text)
                    pop_icon = " 🔒 " if is_locked else " ⚙️ "
                    
                    with c2.popover(pop_icon, use_container_width=True):
                        options = ["-- Auto --"] + on_duty_dropdown_list
                        if is_locked and locked_by not in options: options.append(locked_by)
                            
                        new_val = st.selectbox("Lock to person:", options, 
                            index=options.index(locked_by) if is_locked else 0,
                            key=f"ov_{rig_name}_{position}")
                            
                        if st.button("Save", key=f"btn_{rig_name}_{position}", type="primary", use_container_width=True):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s", (target_date_str, seat_name))
                            if new_val != "-- Auto --":
                                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s", (target_date_str, new_val))
                                c.execute("INSERT INTO Manual_Overrides (Target_Date, Name, Seat) VALUES (%s, %s, %s)", (target_date_str, new_val, seat_name))
                            conn.commit()
                            conn.close()
                            st.rerun()
                else:
                    st.markdown(disp_text)
            
            if rig_name == "ARO" and is_admin:
                st.markdown("<hr style='margin: 0.5em 0;'>", unsafe_allow_html=True)
                with st.popover("🎲 Draw Watches", use_container_width=True):
                    with st.form(f"aro_draw_form_{target_date_str}"):
                        st.write("**Assign Watches:**")
                        watch_options = ["--"] + eligible_aro_names
                        def get_idx(val): return watch_options.index(val) if val in watch_options else 0
                        w1 = st.selectbox("1200-1700", watch_options, index=get_idx(db_watches.get("1200-1700")))
                        w2 = st.selectbox("1700-2200", watch_options, index=get_idx(db_watches.get("1700-2200")))
                        w3 = st.selectbox("2200-0600", watch_options, index=get_idx(db_watches.get("2200-0600")))
                        w4 = st.selectbox("0600-0700", watch_options, index=get_idx(db_watches.get("0600-0700")))
                        if st.form_submit_button("Save Watches"):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM ARO_Watch WHERE Target_Date = %s", (target_date_str,))
                            inserts = [(target_date_str, "1200-1700", w1), (target_date_str, "1700-2200", w2), (target_date_str, "2200-0600", w3), (target_date_str, "0600-0700", w4)]
                            inserts = [w for w in inserts if w[2] != "--"]
                            if inserts: c.executemany("INSERT INTO ARO_Watch (Target_Date, Watch_Period, Name) VALUES (%s, %s, %s)", inserts)
                            conn.commit()
                            conn.close()
                            st.rerun()

    with rig_col1: 
        display_rig("CH-222", "error")
        display_rig("ARO", "error")
    with rig_col2: 
        display_rig("R-221", "info")
        display_rig("E-221", "success")
        display_rig("Tanker", "info")
    with rig_col3: 
        display_rig("Crash-1", "warning")
        display_rig("Crash-3", "warning")
        display_rig("Crash-4", "warning")
        display_rig("Crash-8", "warning")
        
    st.divider()

    if is_admin:
        st.markdown("### 🛠️ Shift Finalization")
        rm_col1, rm_col2 = st.columns(2)
        
        if rm_col1.button("🔒 Lock Entire Current Roster", use_container_width=True):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s", (target_date_str,))
            inserts = []
            seen_names = set()
            for rig, seats in am_roster.items():
                for pos, am_name in seats.items():
                    seat_name = f"{rig} | {pos}"
                    if seat_name in all_seats_list:
                        if am_name and am_name != "VACANT":
                            clean_name = am_name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                            if clean_name not in seen_names:
                                inserts.append((target_date_str, clean_name, seat_name))
                                seen_names.add(clean_name)
            if inserts:
                c.executemany("INSERT INTO Manual_Overrides (Target_Date, Name, Seat) VALUES (%s, %s, %s)", inserts)
            conn.commit()
            conn.close()
            st.success("Roster successfully locked!")
            time.sleep(1)
            st.rerun()

        if rm_col2.button("💾 Commit Today's Roster to History", type="primary", use_container_width=True):
            records = []
            for rig, seats in am_roster.items():
                for pos, am_name in seats.items():
                    pm_name = pm_roster[rig][pos]
                    seat_name = f"{rig} {pos}"
                    
                    if rig == "ARO" and pos != "0700-1200 (Float)": continue
                    if rig in ["Crash-4", "Crash-1"] and pos == "Crew Chief": continue 
                    if rig == "Tanker": continue 
                    
                    if am_name and am_name == pm_name:
                        clean_name = am_name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                        if clean_name != "VACANT": records.append((target_date_str, clean_name, seat_name, 24.0))
                    else:
                        if am_name and am_name != "VACANT":
                            clean_am = am_name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                            records.append((target_date_str, clean_am, seat_name, 8.0))
                        if pm_name and pm_name != "VACANT":
                            clean_pm = pm_name.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                            records.append((target_date_str, clean_pm, seat_name, 16.0))
            
            am_float = am_roster["ARO"]["0700-1200 (Float)"]
            if am_float and am_float != "VACANT":
                clean_am = am_float.replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                records.append((target_date_str, clean_am, "Watch: 0700-1200", 1.0))
                
            for wp in ["1200-1700", "1700-2200", "2200-0600", "0600-0700"]:
                if wp in db_watches and db_watches[wp] not in ["", "--"]:
                    clean_w = db_watches[wp].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
                    records.append((target_date_str, clean_w, f"Watch: {wp}", 1.0))
                            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM Position_Log WHERE Target_Date = %s", (target_date_str,))
            c.executemany("INSERT INTO Position_Log (Target_Date, Name, Position, Hours) VALUES (%s, %s, %s, %s)", records)
            conn.commit()
            conn.close()
            st.success("Roster successfully saved to history!")
            time.sleep(1)
            st.rerun()
            
    st.divider()
    day_of_week = target_date.strftime("%A")
    st.subheader(f"🧹 Station Details ({day_of_week})")
    
    daily_details = {
        "Monday": [{"Pos_Name": "Rescue Driver", "Rig": "R-221", "Seat": "Driver/Operator", "Task": "Kitchen and dayrooms; deep cleaning of outside grill (drip tray, grates, burners) and outside cabinet"},
                   {"Pos_Name": "Rescue Crew Chief", "Rig": "R-221", "Seat": "Crew Chief", "Task": "Hallways; front entranceways (including inside/outside windows); First-aid / Lactation room"},
                   {"Pos_Name": "Alarm Room Operator (ARO)", "Rig": "ARO", "Seat": "0700-1200 (Float)", "Task": "Alarm room (cleaning, dusting, windows, and trash)"}],
        "Tuesday": [{"Pos_Name": "Rescue Backseat", "Rig": "R-221", "Seat": "Firefighter 1", "Task": "Bathrooms, locker rooms, and shower rooms (both Crew and Admin)"},
                    {"Pos_Name": "Crash 1 (Engine Driver)", "Rig": "Crash-1", "Seat": "Driver", "Task": "Hallways; all offices (Chief, A/C’s, Captain, Training Chief, training room); laundry room; weight room (including dusting and windows inside/outside)"}],
        "Wednesday": [{"Pos_Name": "Crash 3 (Backseat 1)", "Rig": "Crash-3", "Seat": "Driver", "Task": "Vehicle exteriors (wash all vehicles)"},
                      {"Pos_Name": "Crash 8 (Backseat 2)", "Rig": "Crash-8", "Seat": "Driver", "Task": "Vehicle interiors (clean compartments, tools, and inside personnel areas)"}],
        "Thursday": [{"Pos_Name": "Crash 4 (Engine Crew Chief)", "Rig": "Crash-4", "Seat": "Driver", "Task": "Apparatus stalls (clean stalls, stall door windows inside/out, and stall ramps)"},
                     {"Pos_Name": "Crash 1 (Engine Driver)", "Rig": "Crash-1", "Seat": "Driver", "Task": "Specialty storage & maintenance rooms (PPE Storage, Infectious Disease room, Agent/Hose Storage, Extinguisher Maintenance, SCBA Maintenance, and Alarm Room storage areas)"},
                     {"Pos_Name": "Crash 8 (Backseat 2)", "Rig": "Crash-8", "Seat": "Driver", "Task": "Cut grass and maintain station grounds"}],
        "Friday": [{"Pos_Name": "Rescue Crew Chief", "Rig": "R-221", "Seat": "Crew Chief", "Task": "Flight-line extinguisher check"},
                   {"Pos_Name": "Rescue Driver & Crash 1 (Engine Driver)", "Rig": "Multiple", "Seat": ["R-221 Driver/Operator", "Crash-1 Driver"], "Task": "Ballistic vests inspections & AF Form 1071 sign-off"},
                   {"Pos_Name": "All Drivers (Crash & Rescue Drivers)", "Rig": "Multiple", "Seat": ["Crash-4 Driver", "Crash-1 Driver", "Crash-3 Driver", "Crash-8 Driver", "R-221 Driver/Operator"], "Task": "Truck operational checks (ARFF and apparatus operational checkouts)"}]
    }
    
    def resolve_detail_names(rig, seat_def):
        if rig == "Multiple":
            found = []
            for s in seat_def:
                r, p = s.split(" ", 1)
                n = am_roster.get(r, {}).get(p, "")
                if not n and r.startswith("Crash"): n = am_roster.get(r, {}).get("Crew Chief" if p == "Driver" else "Driver", "")
                if n and n != "VACANT": found.append(n)
            return ", ".join(found) if found else "VACANT"
        else:
            n = am_roster.get(rig, {}).get(seat_def, "")
            if not n and rig.startswith("Crash"): n = am_roster.get(rig, {}).get("Crew Chief" if seat_def == "Driver" else "Driver", "")
            return n if n and n != "VACANT" else "VACANT"

    if day_of_week in daily_details:
        for d in daily_details[day_of_week]:
            assigned_names = resolve_detail_names(d["Rig"], d["Seat"])
            st.markdown(f"<div style='margin-bottom: 14px;'><div style='font-size: 1.25em; font-weight: bold;'>{d['Task']}</div><div style='font-size: 0.9em; opacity: 0.8;'>↳ <b>Assigned:</b> {d['Pos_Name']} ({assigned_names})</div></div>", unsafe_allow_html=True)
    else:
        st.info(f"No specific station deep-cleaning details are officially assigned for {day_of_week}s.")


# ==========================================
# TAB 2: OVERTIME ROSTER
# ==========================================
with tab_ot:
    st.subheader("⏱️ Overtime Roster Management")
    
    conn = get_db_connection()
    df_ot = pd.read_sql("SELECT * FROM Overtime_Buckets", conn)
    conn.close()
    
    df_ot.columns = [c.capitalize() for c in df_ot.columns]
    df_ot = df_ot.sort_values(by=['Contact', 'Current_hours', 'Seniority'], ascending=[True, True, True]).reset_index(drop=True)
    
    grid_height = (len(df_ot) * 35) + 40 
    
    if not is_admin:
        st.markdown("*(Read-Only View. Enter Admin PIN to process callouts.)*")
        st.dataframe(df_ot[['Seniority', 'Name', 'Shift', 'Current_hours', 'Contact']], use_container_width=True, hide_index=True, height=grid_height)
    else:
        st.markdown("### Process New Callout")
        st.markdown("Use the grid below to check the boxes for personnel who were charged or awarded. The system will automatically catch typos and prevent double-dipping.")
        
        df_ot['Charged'] = False
        df_ot['Awarded'] = False
        
        edited_df = st.data_editor(
            df_ot,
            column_config={
                "Charged": st.column_config.CheckboxColumn("Charged"),
                "Awarded": st.column_config.CheckboxColumn("Awarded"),
            },
            disabled=["Seniority", "Name", "Shift", "Current_hours", "Contact"],
            use_container_width=True,
            hide_index=True,
            height=grid_height
        )

        st.markdown("##### Callout Details")
        ot_c1, ot_c2, ot_c3, ot_c4, ot_c5 = st.columns([1.5, 1.5, 1, 1, 1])
        with ot_c1:
            callout_hours = st.number_input("Callout Hours", value=24.0, min_value=0.5, max_value=48.0, step=0.5)
        with ot_c2:
            ot_worked_date = st.date_input("Date to be Worked", value=target_date)
        with ot_c3:
            ot_shift = st.selectbox("Shift", ["A", "B", "C"])
        with ot_c4:
            ot_start = st.text_input("Start Time", "0700", key="ot_t_s")
        with ot_c5:
            ot_end = st.text_input("End Time", "0700", key="ot_t_e")
            
        ot_notes = st.text_input("Callout Notes (Required)", placeholder="e.g., Shift coverage for vacancy...")
        
        if st.button("Process & Commit Hours", type="primary"):
            if not ot_notes.strip():
                st.error("Notes cannot be blank to process a callout.")
            else:
                archive_stamp = f"{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
                compiled_notes = f"[{ot_worked_date.strftime('%Y-%m-%d')} | {ot_shift}-Shift | {ot_start}-{ot_end}] {ot_notes}"
                archive_records = []
                
                df_print = edited_df[edited_df['Contact'] == 'Yes'].copy()
                for _, r in df_print.iterrows():
                    is_awarded = r['Awarded']
                    is_charged = r['Charged']
                    
                    hours_applied = callout_hours if (is_awarded or is_charged) else 0.0
                    
                    if is_awarded: status = f"🟢 Awarded"
                    elif is_charged: status = f"🟡 Charged"
                    else: status = "⚪ Skipped / Working"
                    
                    archive_records.append((archive_stamp, compiled_notes, r['Name'], r['Current_hours'], hours_applied, status))
                
                updates = []
                for _, r in edited_df.iterrows():
                    is_awarded = r['Awarded']
                    is_charged = r['Charged']
                    
                    hours_applied = callout_hours if (is_awarded or is_charged) else 0.0
                    
                    if hours_applied > 0:
                        new_hours = r['Current_hours'] + hours_applied
                        updates.append((new_hours, r['Name']))
                    
                conn = get_db_connection()
                c = conn.cursor()
                if archive_records:
                    c.executemany("INSERT INTO OT_Archive (Archive_Stamp, Notes, Name, Start_Hours, Hours_Applied, Status) VALUES (%s, %s, %s, %s, %s, %s)", archive_records)
                if updates:
                    c.executemany("UPDATE Overtime_Buckets SET Current_Hours = %s WHERE Name = %s", updates)
                conn.commit()
                conn.close()
                st.success(f"Callout successfully processed! Snapshot saved as {archive_stamp}.")
                time.sleep(2)
                st.rerun()

    # --- ARCHIVES OUTSIDE ADMIN LOOP (PUBLIC VISIBILITY) ---
    st.divider()
    st.subheader("🗄️ Callout Archives")
    conn = get_db_connection()
    archive_df = pd.read_sql("SELECT * FROM OT_Archive ORDER BY id DESC", conn)
    conn.close()
    
    if not archive_df.empty:
        archive_df.columns = [c.lower() for c in archive_df.columns]
        
        # Calculate new hours on the fly for the archive display
        archive_df['new_hours'] = archive_df['start_hours'] + archive_df['hours_applied']
        
        archive_groups = archive_df['archive_stamp'].unique()
        for stamp in archive_groups[:10]:
            stamp_df = archive_df[archive_df['archive_stamp'] == stamp]
            stamp_notes = stamp_df['notes'].iloc[0]
            
            archive_height = (len(stamp_df) * 35) + 40
            
            with st.expander(f"Snapshot: {stamp} | Notes: {stamp_notes}"):
                c1, c2 = st.columns(2)
                
                # --- LEFT SIDE: Canvass Roster (Before) ---
                left_df = stamp_df[['name', 'start_hours', 'hours_applied', 'status']].copy()
                left_df.rename(columns={'name': 'Name', 'start_hours': 'Old Hours', 'hours_applied': 'Hours', 'status': 'Action'}, inplace=True)
                
                # Explicitly sort the left side by Old Hours to maintain perfect Canvass order
                left_df = left_df.sort_values(by='Old Hours', ascending=True).reset_index(drop=True)
                
                # Apply Pandas Styling to recreate the Excel colors
                def color_rows(row):
                    if 'Awarded' in row['Action']:
                        return ['background-color: #c8e6c9; color: black'] * len(row) # Excel Green
                    if 'Charged' in row['Action']:
                        return ['background-color: #fff9c4; color: black'] * len(row) # Excel Yellow
                    return [''] * len(row)
                
                styled_left = left_df.style.apply(color_rows, axis=1)
                
                with c1:
                    st.markdown("**Canvass Roster (Before)**")
                    st.dataframe(styled_left, use_container_width=True, hide_index=True, height=archive_height)
                
                # --- RIGHT SIDE: Updated List (After) ---
                right_df = stamp_df[['name', 'new_hours']].copy()
                right_df.rename(columns={'name': 'Name', 'new_hours': 'New Hours'}, inplace=True)
                
                # Re-sort the right side by New Hours, just like the Excel macro
                right_df = right_df.sort_values(by='New Hours', ascending=True).reset_index(drop=True)
                
                with c2:
                    st.markdown("**Updated List (After)**")
                    st.dataframe(right_df, use_container_width=True, hide_index=True, height=archive_height)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # --- PRINTABLE HTML GENERATOR ---
                styled_html = styled_left.hide(axis="index").to_html()
                
                html_content = f"""
                <html>
                <head>
                <style>
                    body {{ font-family: sans-serif; padding: 20px; }}
                    h2 {{ text-align: center; margin-bottom: 5px; font-size: 18px; }}
                    h3 {{ text-align: center; margin: 0; font-size: 14px; }}
                    .header-notes {{ text-align: center; margin-bottom: 15px; font-style: italic; color: #555; font-size: 12px; }}
                    .container {{ display: flex; justify-content: space-between; gap: 10px; width: 100%; }}
                    .col {{ width: 49%; }}
                    table {{ border-collapse: collapse; width: 100%; font-size: 11px; margin-top: 10px; }}
                    th, td {{ border: 1px solid #ddd; padding: 4px 6px; text-align: left; }}
                    th {{ background-color: #f2f2f2 !important; color: black; }}
                    @media print {{
                        @page {{ size: portrait; margin: 0.4in; }}
                        body {{ padding: 0; }}
                        button {{ display: none; }}
                    }}
                </style>
                </head>
                <body onload="window.print()">
                    <h2>Overtime Roster Snapshot</h2>
                    <div class="header-notes"><strong>Stamp:</strong> {stamp} <br> <strong>Notes:</strong> {stamp_notes}</div>
                    <div class="container">
                        <div class="col">
                            <h3>Canvass Roster (Before)</h3>
                            {styled_html}
                        </div>
                        <div class="col">
                            <h3>Updated List (After)</h3>
                            {right_df.to_html(index=False)}
                        </div>
                    </div>
                </body>
                </html>
                """
                
                st.markdown("*(Tip: When the file opens, set your printer destination to **'Save as PDF'** to create a digital copy.)*")
                st.download_button(
                    label="🖨️ Download Roster (Print / Save as PDF)",
                    data=html_content,
                    file_name=f"OT_Roster_{stamp}.html",
                    mime="text/html",
                    key=f"print_{stamp}"
                )
    else:
        st.info("No past callouts archived yet.")


# ==========================================
# TAB 3: LEAVE CALENDAR
# ==========================================
with tab_calendar:
    cal_col1, cal_col2, _ = st.columns([1, 1, 4])
    with cal_col1: selected_month = st.selectbox("Month", range(1, 13), index=target_date.month - 1, format_func=lambda x: calendar.month_name[x])
    with cal_col2: selected_year = st.selectbox("Year", [2025, 2026, 2027], index=1)
    st.divider()
    cal_matrix = calendar.monthcalendar(selected_year, selected_month)
    days_of_week = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    header_cols = st.columns(7)
    for i, day_name in enumerate(days_of_week): header_cols[i].markdown(f"<h5 style='text-align: center;'>{day_name}</h5>", unsafe_allow_html=True)
    
    for week in cal_matrix:
        week_cols = st.columns(7)
        for i, day in enumerate(week):
            with week_cols[i]:
                if day != 0:
                    current_date = datetime.date(selected_year, selected_month, day)
                    date_str = current_date.strftime("%Y-%m-%d")
                    cal_shift_mod = (current_date - anchor_date).days % 3
                    if cal_shift_mod == 0: shift_color, shift_name, day_shift = "#4CAF50", "A-Shift", "A"
                    elif cal_shift_mod == 1: shift_color, shift_name, day_shift = "#2196F3", "B-Shift", "B"
                    else: shift_color, shift_name, day_shift = "#F44336", "C-Shift", "C"

                    day_leave = all_leave_df[all_leave_df['target_date'] == date_str] if not all_leave_df.empty else pd.DataFrame()
                    day_ot = all_ot_df[all_ot_df['target_date'] == date_str] if not all_ot_df.empty else pd.DataFrame()
                    
                    day_trades = []
                    if not all_trades_df.empty:
                        for _, t in all_trades_df.iterrows():
                            if t['date_1'] == date_str: day_trades.append({'id': t['id'], 'Off_Name': t['name_1'], 'Work_Name': t['name_2'], 'Start_Time': t['start_time'], 'End_Time': t['end_time'], 'Total_Hours': t['total_hours']})
                            elif t['date_2'] == date_str: day_trades.append({'id': t['id'], 'Off_Name': t['name_2'], 'Work_Name': t['name_1'], 'Start_Time': t['start_time'], 'End_Time': t['end_time'], 'Total_Hours': t['total_hours']})
                    
                    core_names = all_personnel_df[(all_personnel_df['shift'] == day_shift) & (all_personnel_df['core_manning'] == True)]['name'].tolist()
                    
                    full_day_leaves = [l['name'] for _, l in day_leave.iterrows() if float(l['total_hours']) >= 24.0] if not day_leave.empty else []
                    full_day_trade_offs = [t['Off_Name'] for t in day_trades if float(t['Total_Hours']) >= 24.0]
                    
                    core_on_leave = len([n for n in full_day_leaves if n in core_names])
                    core_trading_off = len([n for n in full_day_trade_offs if n in core_names])
                    est_manning = len(core_names) - core_on_leave - core_trading_off + len(day_ot) + len(day_trades)
                    
                    if est_manning < 9:
                        admin_names_list = all_personnel_df[all_personnel_df['shift'] == 'ADMIN']['name'].tolist()
                        admins_on_leave = len([n for n in full_day_leaves if n in admin_names_list]) + len([n for n in full_day_trade_offs if n in admin_names_list])
                        available_admins = len(admin_names_list) - admins_on_leave
                        est_manning += min((9 - est_manning), available_admins)

                    manning_color = "green" if est_manning >= 9 else "red"
                    
                    with st.container(border=True):
                        st.markdown(f"<div style='background-color:{shift_color}; color:white; padding: 2px 4px; border-radius: 3px; font-size: 0.85em; margin-bottom: 4px; text-align: center;'><b>{day} | {shift_name}</b></div><div style='color:{manning_color}; font-size:0.9em; font-weight:bold; margin-bottom: 4px;'>Manning: {est_manning}</div>", unsafe_allow_html=True)
                        
                        if not day_leave.empty:
                            for _, l in day_leave.iterrows():
                                hrs = int(l['total_hours']) if l['total_hours'] % 1 == 0 else l['total_hours']
                                with st.popover(f"{get_icon(l['leave_type'])} {l['name']} ({hrs}h)", use_container_width=True):
                                    st.markdown(f"**{l['leave_type']}**")
                                    st.write(f"⏱️ {l['start_time']} - {l['end_time']}")
                                    if is_admin:
                                        if st.button("🗑️ Cancel Leave", key=f"del_leave_{l['id']}", type="primary", use_container_width=True):
                                            conn = get_db_connection()
                                            conn.cursor().execute("DELETE FROM Leave_Ledger WHERE id = %s", (l['id'],))
                                            conn.commit()
                                            conn.close()
                                            st.rerun()
                                            
                        if not day_leave.empty and (not day_ot.empty or day_trades):
                            st.markdown("<div style='padding: 12px 0px;'><div style='border-top: 3px dashed #888;'></div></div>", unsafe_allow_html=True)
                                        
                        if not day_ot.empty:
                            for _, o in day_ot.iterrows():
                                hrs = int(o['total_hours']) if o['total_hours'] % 1 == 0 else o['total_hours']
                                with st.popover(f"{get_icon(o['ot_type'])} {o['name']} ({hrs}h)", use_container_width=True):
                                    st.markdown(f"**{o['ot_type']}**")
                                    st.write(f"⏱️ {o['start_time']} - {o['end_time']}")
                                    if is_admin:
                                        if st.button("🗑️ Cancel OT", key=f"del_ot_{o['id']}", type="primary", use_container_width=True):
                                            conn = get_db_connection()
                                            conn.cursor().execute("DELETE FROM Overtime_Log WHERE id = %s", (o['id'],))
                                            conn.commit()
                                            conn.close()
                                            st.rerun()
                                    
                        for t in day_trades:
                            hrs = int(t['Total_Hours']) if t['Total_Hours'] % 1 == 0 else t['Total_Hours']
                            time_str = f"{t['Start_Time']} - {t['End_Time']}" if hrs < 24 else "0700 - 0700"
                            with st.popover(f"🔀 {t['Off_Name']} 🔁 {t['Work_Name']} ({hrs}h)", use_container_width=True):
                                st.markdown(f"**Shift Trade**")
                                st.write(f"⏱️ {time_str}")
                                if is_admin:
                                    if st.button("🗑️ Cancel Trade", key=f"del_trade_{t['id']}_{date_str}", type="primary", use_container_width=True):
                                        conn = get_db_connection()
                                        conn.cursor().execute("DELETE FROM Shift_Trades_V3 WHERE id = %s", (t['id'],))
                                        conn.commit()
                                        conn.close()
                                        st.rerun()
                else: st.write("")

# ==========================================
# TAB 4: SEAT STATISTICS
# ==========================================
with tab_stats:
    st.subheader("📊 Historical Seat Balances")
    st.markdown("This tracker automatically logs how many hours each person has spent in the rotating positions, as well as the number of times they have pulled each Alarm Room watch.")
    
    if not stats_df.empty:
        pivot_df = stats_df.pivot(index='name', columns='position', values='total_hours').fillna(0)
        
        # Dynamic height calculation to remove inner scrollbars
        stats_height = (len(pivot_df) * 35) + 40
        
        st.markdown("#### 🕒 Structural Seat Balance (Total Hours)")
        display_cols = [c for c in ["E-221 Driver/Operator", "E-221 Firefighter 1", "E-221 Firefighter 2", "R-221 Driver/Operator", "R-221 Firefighter 1", "ARO 0700-1200 (Float)"] if c in pivot_df.columns]
        st.dataframe(pivot_df[display_cols], use_container_width=True, height=stats_height)
        
        st.divider()
        
        st.markdown("#### 👁️ Alarm Room Watches (Total Count)")
        watch_cols = [c for c in ["Watch: 0700-1200", "Watch: 1200-1700", "Watch: 1700-2200", "Watch: 2200-0600", "Watch: 0600-0700"] if c in pivot_df.columns]
        if watch_cols:
            st.dataframe(pivot_df[watch_cols].astype(int), use_container_width=True, height=stats_height)
        else:
            st.info("No watches logged to history yet.")
    else:
        st.info("No roster history saved yet.")

# ==========================================
# TAB 5: DATA ENTRY (ADMIN ONLY)
# ==========================================
if is_admin:
    with tab_data_entry:
        st.subheader("Log New Entries")
        form_col1, form_col2, form_col3 = st.columns(3)
        
        with form_col1:
            with st.form("leave_form", clear_on_submit=True):
                st.markdown("#### 🏖️️ Enter Leave")
                l_date_range = st.date_input("Target Date(s)", value=(target_date, target_date))
                l_name = st.selectbox("Personnel", all_names)
                leave_types = sorted(["Annual Leave", "Paternity Leave", "Union Leave", "Bereavement Leave", "Medical Leave", "Military Leave", "Jury Duty", "NFPA Physical", "Personal Leave", "Disability Leave"])
                l_type = st.selectbox("Leave Type", leave_types)
                l_24h = st.checkbox("Full 24h Shift (0700-0700)", value=True, key="l_24")
                time_col1, time_col2 = st.columns(2)
                l_start = time_col1.text_input("Start Time (e.g., 0700)", "0700", key="l_s")
                l_end = time_col2.text_input("End Time (e.g., 0700)", "0700", key="l_e")
                
                if st.form_submit_button("Save Leave"):
                    if isinstance(l_date_range, tuple) or isinstance(l_date_range, list):
                        start_dt = l_date_range[0]
                        end_dt = l_date_range[1] if len(l_date_range) > 1 else l_date_range[0]
                    else:
                        start_dt = end_dt = l_date_range
                        
                    delta = end_dt - start_dt
                    p_shift = personnel_info.get(l_name, {}).get('Shift', 'A')
                    p_sched = personnel_info.get(l_name, {}).get('Schedule', 'Structural')
                    
                    dates_to_log = []
                    anchor = datetime.date(2026, 9, 16)
                    for i in range(delta.days + 1):
                        cur_dt = start_dt + datetime.timedelta(days=i)
                        if p_sched == 'Admin':
                            if cur_dt.weekday() < 5: dates_to_log.append(cur_dt.strftime("%Y-%m-%d"))
                        else:
                            s_mod = (cur_dt - anchor).days % 3
                            day_s = "A" if s_mod == 0 else ("B" if s_mod == 1 else "C")
                            if p_shift == day_s: dates_to_log.append(cur_dt.strftime("%Y-%m-%d"))

                    if not dates_to_log:
                        st.error("No assigned shift days found in that range for this person.")
                    else:
                        if l_24h: l_start, l_end, l_hours = "0700", "0700", 24.0
                        else: l_hours = calc_hours(l_start, l_end)
                            
                        conn = get_db_connection()
                        inserts = [(d, l_name, l_type, l_start, l_end, l_hours) for d in dates_to_log]
                        conn.cursor().executemany("INSERT INTO Leave_Ledger (Target_Date, Name, Leave_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)", inserts)
                        conn.commit()
                        conn.close()
                        st.success(f"Logged {len(dates_to_log)} shift(s) of leave for {l_name}.")
                        time.sleep(1)
                        st.rerun()

        with form_col2:
            with st.form("ot_form", clear_on_submit=True):
                st.markdown("#### 💰 Enter Overtime / Temp Duty")
                o_date = st.date_input("Target Date ", value=target_date)
                o_name_dd = st.selectbox("Permanent Personnel", ["-- Select --"] + all_names, key="ot_personnel") 
                o_name_wi = st.text_input("OR Write-In Name", placeholder="e.g., A1C Snuffy", key="ot_wi")
                o_type = st.selectbox("OT Type", ["Voluntary", "Mandatory", "Guard Personnel On-Duty"])
                o_24h = st.checkbox("Full 24h Shift (0700-0700)", value=False, key="o_24")
                time_col3, time_col4 = st.columns(2)
                o_start = time_col3.text_input("Start Time (e.g., 1500)", "1500", key="o_s")
                o_end = time_col4.text_input("End Time (e.g., 0700)", "0700", key="o_e")
                
                if st.form_submit_button("Save OT"):
                    final_name = o_name_wi.strip() if o_name_wi.strip() != "" else o_name_dd
                    if final_name == "-- Select --": st.error("Please select a person or write one in!")
                    else:
                        if o_24h: o_start, o_end, o_hours = "0700", "0700", 24.0
                        else: o_hours = calc_hours(o_start, o_end)
                            
                        conn = get_db_connection()
                        conn.cursor().execute("INSERT INTO Overtime_Log (Target_Date, Name, OT_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)", (o_date.strftime("%Y-%m-%d"), final_name, o_type, o_start, o_end, o_hours))
                        conn.commit()
                        conn.close()
                        st.success(f"Logged OT for {final_name}.")
                        time.sleep(1)
                        st.rerun()
                    
        with form_col3:
            with st.form("trade_form", clear_on_submit=True):
                st.markdown("#### 🔀 Enter Shift Trade")
                st.markdown("**Person 1**")
                t_name1 = st.selectbox("Name", all_names, key="t_n1")
                t_date1 = st.date_input("Original Scheduled Date", value=target_date, key="t_d1")
                st.divider()
                st.markdown("**Person 2**")
                t_name2 = st.selectbox("Name", all_names, key="t_n2")
                t_date2 = st.date_input("Original Scheduled Date", value=target_date, key="t_d2")
                st.divider()
                t_24h = st.checkbox("Full 24h Trade (0700-0700)", value=True, key="t_24")
                time_col5, time_col6 = st.columns(2)
                t_start = time_col5.text_input("Start Time", "0700", key="t_s")
                t_end = time_col6.text_input("End Time", "0700", key="t_e")
                
                if st.form_submit_button("Save Trade"):
                    if t_name1 == t_name2: st.error("Personnel cannot trade with themselves!")
                    else:
                        if t_24h: t_start, t_end, t_hours = "0700", "0700", 24.0
                        else: t_hours = calc_hours(t_start, t_end)
                            
                        conn = get_db_connection()
                        conn.cursor().execute("INSERT INTO Shift_Trades_V3 (Date_1, Name_1, Date_2, Name_2, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s, %s)", (t_date1.strftime("%Y-%m-%d"), t_name1, t_date2.strftime("%Y-%m-%d"), t_name2, t_start, t_end, t_hours))
                        conn.commit()
                        conn.close()
                        st.success(f"Logged Trade: {t_name1} and {t_name2}.")
                        time.sleep(1)
                        st.rerun()
                        
        st.divider()
        st.subheader("🔧 Manual Roster Overrides")
        ov_col1, ov_col2 = st.columns([1, 1])
        with ov_col1:
            with st.form("override_form", clear_on_submit=True):
                st.markdown(f"**Target Date:** {target_date_str}")
                o_name = st.selectbox("Personnel", on_duty_dropdown_list, key="ov_n")
                o_seat = st.selectbox("Assign to Seat", all_seats_list, key="ov_s")
                
                if st.form_submit_button("Lock Seat Override"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s", (target_date_str, o_seat))
                    c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s", (target_date_str, o_name))
                    c.execute("INSERT INTO Manual_Overrides (Target_Date, Name, Seat) VALUES (%s, %s, %s)", (target_date_str, o_name, o_seat))
                    conn.commit()
                    conn.close()
                    st.success(f"Locked {o_name} into {o_seat}.")
                    time.sleep(1)
                    st.rerun()
                    
        with ov_col2:
            st.markdown(f"**Active Overrides for Selected Date ({target_date_str}):**")
            if overrides_df.empty:
                st.info("No active overrides for this date.")
            else:
                for _, ov in overrides_df.iterrows():
                    with st.container(border=True):
                        cols = st.columns([4, 1])
                        cols[0].markdown(f"**{ov['name']}** ➡️ {ov['seat']}")
                        if cols[1].button("🗑️", key=f"del_ov_{ov['id']}", use_container_width=True):
                            conn = get_db_connection()
                            conn.cursor().execute("DELETE FROM Manual_Overrides WHERE id = %s", (ov['id'],))
                            conn.commit()
                            conn.close()
                            st.rerun()
