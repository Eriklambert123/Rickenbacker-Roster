import streamlit as st
import psycopg2
import pandas as pd
import datetime
import calendar
import time

st.set_page_config(page_title="Rickenbacker Fire Department Management", layout="wide", initial_sidebar_state="expanded")

# 🛑 Streamlit Cloud Secret Database Connection
DB_URI = st.secrets["DB_URI"]

st.markdown("""
<style>
[data-testid="column"] { padding: 0 0.15rem !important; }
[data-testid="stVerticalBlock"] { gap: 0.2rem !important; }
[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 0.25rem !important; }
[data-testid="stPopover"] button { padding: 2px 4px !important; min-height: auto !important; }
[data-testid="stPopover"] button p { font-size: 0.75rem !important; white-space: normal !important; line-height: 1.2 !important; }
hr { margin: 0.5em 0 !important; }
</style>
""", unsafe_allow_html=True)

def get_icon(entry_type):
    icons = {"Annual Leave": "🏖️", "Sick Leave": "💊", "Personal": "👤", "Military": "🪖", "Admin Leave": "🏢", "Voluntary": "💰", "Mandatory": "🚨", "Guard Personnel On-Duty": "🫡"}
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

# --- 1. SIDEBAR & SHIFT AUTOCALC ---
st.sidebar.header("Dashboard Controls")
target_date = st.sidebar.date_input("Select Target Date", datetime.date(2026, 9, 23))
target_date_str = target_date.strftime("%Y-%m-%d")

anchor_date = datetime.date(2026, 9, 16) 
shift_mod = (target_date - anchor_date).days % 3

if shift_mod == 0: selected_shift, top_shift_color = "A-Shift", "#4CAF50"
elif shift_mod == 1: selected_shift, top_shift_color = "B-Shift", "#2196F3"
else: selected_shift, top_shift_color = "C-Shift", "#F44336"

st.sidebar.markdown(f"**Target Shift:** <span style='color:{top_shift_color}; font-weight:bold; font-size:1.1em;'>{selected_shift}</span>", unsafe_allow_html=True)

# --- 2. POSTGRES DATABASE CLOUD CONNECTION ---
def get_db_connection():
    return psycopg2.connect(DB_URI)

conn = get_db_connection()
c = conn.cursor()

# Create Tables
c.execute('''CREATE TABLE IF NOT EXISTS ARO_Watch (id SERIAL PRIMARY KEY, Target_Date TEXT, Watch_Period TEXT, Name TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Shift_Trades_V3 (id SERIAL PRIMARY KEY, Date_1 TEXT, Name_1 TEXT, Date_2 TEXT, Name_2 TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Position_Log (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Position TEXT, Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Manual_Overrides (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Seat TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Personnel (Name TEXT, Seniority INTEGER, Core_Manning BOOLEAN, Schedule TEXT, Shift TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS Leave_Ledger (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Leave_Type TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')
c.execute('''CREATE TABLE IF NOT EXISTS Overtime_Log (id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, OT_Type TEXT, Start_Time TEXT, End_Time TEXT, Total_Hours REAL)''')

# Auto-Seed Roster if Blank
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
conn.commit()

import warnings
warnings.filterwarnings('ignore')

# Read Data & Standardize Column Names to Lowercase to Prevent Postgres Case Errors
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
    personnel_info[r['name']] = {'Seniority': r['seniority'], 'Schedule': r['schedule'], 'Core': r['core_manning']}
    if r['schedule'] == 'Admin': admin_names.add(r['name'])

override_dict = dict(zip(overrides_df['name'], overrides_df['seat'])) if not overrides_df.empty else {}

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
personnel_df = all_personnel_df[(all_personnel_df['shift'] == db_shift) | (all_personnel_df['shift'] == 'ADMIN')]

leave_df = all_leave_df[all_leave_df['target_date'] == target_date_str] if not all_leave_df.empty else pd.DataFrame()
ot_df = all_ot_df[all_ot_df['target_date'] == target_date_str] if not all_ot_df.empty else pd.DataFrame()
all_names = sorted(all_personnel_df['name'].tolist())

ot_names_list = ot_df['name'].tolist() if not ot_df.empty else []
override_names_list = sorted(list(set(all_names + ot_names_list)))

officer_seats = [("CH-222", "AC"), ("E-221", "Station Captain"), ("R-221", "Crew Chief")]
rotating_seats = [("ARO", "0700-1200 (Float)"), ("E-221", "Driver/Operator"), ("E-221", "Firefighter 1"), ("E-221", "Firefighter 2"), ("R-221", "Driver/Operator"), ("R-221", "Firefighter 1")]
crash_seats = [("Crash-4", "Driver"), ("Crash-1", "Driver"), ("Crash-3", "Driver"), ("Crash-8", "Driver")]
all_seats_list = [f"{r} | {p}" for r, p in officer_seats + rotating_seats + crash_seats]

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

for _, person in personnel_df.iterrows():
    name = person['name']
    if name not in leaves_am and name not in trades_am_off and name not in trades_am_work and name not in ot_am_work:
        all_working_am.append((name, ""))
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

admin_am.sort(key=lambda x: x["Seniority"])
admin_pm.sort(key=lambda x: x["Seniority"])

while len(am_crew) < 9 and len(admin_am) > 0: am_crew.append(admin_am.pop(0))
while len(pm_crew) < 9 and len(admin_pm) > 0: pm_crew.append(admin_pm.pop(0))

am_crew.sort(key=lambda x: x["Seniority"])
pm_crew.sort(key=lambda x: x["Seniority"])

am_crew_dict = {p["Name"]: p for p in am_crew}
pm_crew_dict = {p["Name"]: p for p in pm_crew}

def get_empty_rigs():
    return {
        "CH-222": {"AC": ""}, 
        "R-221": {"Crew Chief": "", "Driver/Operator": "", "Firefighter 1": ""}, 
        "E-221": {"Station Captain": "", "Driver/Operator": "", "Firefighter 1": "", "Firefighter 2": ""}, 
        "ARO": {"0700-1200 (Float)": "", "1200-1700": "TBD (Draw)", "1700-2200": "TBD (Draw)", "2200-0600": "TBD (Draw)", "0600-0700": "TBD (Draw)"},
        "Crash-4": {"Crew Chief": "", "Driver": ""}, "Crash-1": {"Crew Chief": "", "Driver": ""}, "Crash-3": {"Driver": ""}, "Crash-8": {"Driver": ""}
    }

# --- 3a. ASSIGN AM ROSTER ---
am_rigs = get_empty_rigs()
am_pool = am_crew.copy()

for i in range(len(am_pool)-1, -1, -1):
    clean_name = am_pool[i]["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "")
    if clean_name in override_dict:
        rig, pos = override_dict[clean_name].split(" | ")
        if am_rigs[rig][pos] == "": am_rigs[rig][pos] = am_pool.pop(i)["Name"]

am_pool.sort(key=lambda x: (x["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") in partial_leaves, x["Seniority"]))

for rig, pos in officer_seats:
    if am_pool and am_rigs[rig][pos] == "": am_rigs[rig][pos] = am_pool.pop(0)["Name"]

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
structural_pm_pool.sort(key=lambda x: (x["Name"].replace(" (Trade)", "").replace(" (OT)", "").replace(" (Guard)", "") in partial_leaves, x["Seniority"]))

for rig, pos in officer_seats:
    if pm_rigs[rig][pos] == "" and structural_pm_pool: pm_rigs[rig][pos] = structural_pm_pool.pop(0)["Name"]

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
tab_roster, tab_calendar, tab_data_entry, tab_stats = st.tabs(["📋 Daily Roster", "📅 Leave Calendar", "✍️ Data Entry", "📊 Seat Statistics"])

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

    def display_rig(rig_name, box_type):
        output = f"### {rig_name}\n"
        for position in am_roster[rig_name]:
            am_name, pm_name = am_roster[rig_name][position], pm_roster[rig_name][position]
            
            if rig_name == "ARO" and position == "0700-1200 (Float)":
                if am_name == "": am_name = "VACANT"
                if pm_name == "": pm_name = "VACANT"
            elif am_name == "" and pm_name == "": continue
            
            if rig_name == "ARO" and position != "0700-1200 (Float)": output += f"**{position}:** {am_name}\n\n"
            elif am_name == pm_name: output += f"**{position}:** {am_name}\n\n"
            else: output += f"**{position}:** {am_name}  >>  {pm_name} (1500)\n\n"
                
        if box_type == "info": st.info(output)
        elif box_type == "success": st.success(output)
        elif box_type == "error": st.error(output)
        elif box_type == "warning": st.warning(output)
        
        if rig_name == "ARO":
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
    with rig_col3: 
        display_rig("Crash-1", "warning")
        display_rig("Crash-3", "warning")
        display_rig("Crash-4", "warning")
        display_rig("Crash-8", "warning")
        
    # ==========================================
    # STATION DETAILS INTEGRATION
    # ==========================================
    st.divider()
    day_of_week = target_date.strftime("%A")
    st.subheader(f"🧹 Station Details ({day_of_week})")
    
    daily_details = {
        "Monday": [
            {"Pos_Name": "Rescue Driver", "Rig": "R-221", "Seat": "Driver/Operator", "Task": "Kitchen and dayrooms; deep cleaning of outside grill (drip tray, grates, burners) and outside cabinet"},
            {"Pos_Name": "Rescue Crew Chief", "Rig": "R-221", "Seat": "Crew Chief", "Task": "Hallways; front entranceways (including inside/outside windows); First-aid / Lactation room"},
            {"Pos_Name": "Alarm Room Operator (ARO)", "Rig": "ARO", "Seat": "0700-1200 (Float)", "Task": "Alarm room (cleaning, dusting, windows, and trash)"}
        ],
        "Tuesday": [
            {"Pos_Name": "Rescue Backseat", "Rig": "R-221", "Seat": "Firefighter 1", "Task": "Bathrooms, locker rooms, and shower rooms (both Crew and Admin)"},
            {"Pos_Name": "Crash 1 (Engine Driver)", "Rig": "Crash-1", "Seat": "Driver", "Task": "Hallways; all offices (Chief, A/C’s, Captain, Training Chief, training room); laundry room; weight room (including dusting and windows inside/outside)"}
        ],
        "Wednesday": [
            {"Pos_Name": "Crash 3 (Backseat 1)", "Rig": "Crash-3", "Seat": "Driver", "Task": "Vehicle exteriors (wash all vehicles)"},
            {"Pos_Name": "Crash 8 (Backseat 2)", "Rig": "Crash-8", "Seat": "Driver", "Task": "Vehicle interiors (clean compartments, tools, and inside personnel areas)"}
        ],
        "Thursday": [
            {"Pos_Name": "Crash 4 (Engine Crew Chief)", "Rig": "Crash-4", "Seat": "Driver", "Task": "Apparatus stalls (clean stalls, stall door windows inside/out, and stall ramps)"},
            {"Pos_Name": "Crash 1 (Engine Driver)", "Rig": "Crash-1", "Seat": "Driver", "Task": "Specialty storage & maintenance rooms (PPE Storage, Infectious Disease room, Agent/Hose Storage, Extinguisher Maintenance, SCBA Maintenance, and Alarm Room storage areas)"},
            {"Pos_Name": "Crash 8 (Backseat 2)", "Rig": "Crash-8", "Seat": "Driver", "Task": "Cut grass and maintain station grounds"}
        ],
        "Friday": [
            {"Pos_Name": "Rescue Crew Chief", "Rig": "R-221", "Seat": "Crew Chief", "Task": "Flight-line extinguisher check"},
            {"Pos_Name": "Rescue Driver & Crash 1 (Engine Driver)", "Rig": "Multiple", "Seat": ["R-221 Driver/Operator", "Crash-1 Driver"], "Task": "Ballistic vests inspections & AF Form 1071 sign-off"},
            {"Pos_Name": "All Drivers (Crash & Rescue Drivers)", "Rig": "Multiple", "Seat": ["Crash-4 Driver", "Crash-1 Driver", "Crash-3 Driver", "Crash-8 Driver", "R-221 Driver/Operator"], "Task": "Truck operational checks (ARFF and apparatus operational checkouts)"}
        ]
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

    st.divider()
    if st.button("💾 Commit Today's Roster to History", type="primary", use_container_width=True):
        records = []
        for rig, seats in am_roster.items():
            for pos, am_name in seats.items():
                pm_name = pm_roster[rig][pos]
                seat_name = f"{rig} {pos}"
                
                if rig == "ARO" and pos != "0700-1200 (Float)": continue
                if rig in ["Crash-4", "Crash-1"] and pos == "Crew Chief": continue 
                
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
        st.success("Roster successfully saved to history! The balancing engine has been updated.")
        time.sleep(1)
        st.rerun()

# ==========================================
# TAB 2: LEAVE CALENDAR
# ==========================================
with tab_calendar:
    cal_col1, cal_col2, _ = st.columns([1, 1, 4])
    with cal_col1: selected_month = st.selectbox("Month", range(1, 13), index=target_date.month - 1, format_func=lambda x: calendar.month_name[x])
    with cal_col2: selected_year = st.selectbox("Year", [2025, 2026, 2027], index=1)
    st.divider()
    cal_matrix = calendar.monthcalendar(selected_year, selected_month)
    days_of_week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
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
                                    if st.button("🗑️ Cancel Leave", key=f"del_leave_{l['id']}", type="primary", use_container_width=True):
                                        conn = get_db_connection()
                                        conn.cursor().execute("DELETE FROM Leave_Ledger WHERE id = %s", (l['id'],))
                                        conn.commit()
                                        conn.close()
                                        st.rerun()
                                        
                        if not day_ot.empty:
                            for _, o in day_ot.iterrows():
                                hrs = int(o['total_hours']) if o['total_hours'] % 1 == 0 else o['total_hours']
                                with st.popover(f"{get_icon(o['ot_type'])} {o['name']} ({hrs}h)", use_container_width=True):
                                    st.markdown(f"**{o['ot_type']}**")
                                    st.write(f"⏱️ {o['start_time']} - {o['end_time']}")
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
                                if st.button("🗑️ Cancel Trade", key=f"del_trade_{t['id']}_{date_str}", type="primary", use_container_width=True):
                                    conn = get_db_connection()
                                    conn.cursor().execute("DELETE FROM Shift_Trades_V3 WHERE id = %s", (t['id'],))
                                    conn.commit()
                                    conn.close()
                                    st.rerun()
                else: st.write("")

# ==========================================
# TAB 3: DATA ENTRY 
# ==========================================
with tab_data_entry:
    st.subheader("Log New Entries")
    form_col1, form_col2, form_col3 = st.columns(3)
    
    with form_col1:
        with st.form("leave_form", clear_on_submit=True):
            st.markdown("#### 🏖️ Enter Leave")
            l_date = st.date_input("Target Date", value=target_date)
            l_name = st.selectbox("Personnel", all_names)
            l_type = st.selectbox("Leave Type", ["Annual Leave", "Sick Leave", "Personal", "Military", "Admin Leave"])
            l_24h = st.checkbox("Full 24h Shift (0700-0700)", value=True, key="l_24")
            time_col1, time_col2 = st.columns(2)
            l_start = time_col1.text_input("Start Time (e.g., 0700)", "0700", key="l_s")
            l_end = time_col2.text_input("End Time (e.g., 0700)", "0700", key="l_e")
            
            if st.form_submit_button("Save Leave"):
                if l_24h: l_start, l_end, l_hours = "0700", "0700", 24.0
                else: l_hours = calc_hours(l_start, l_end)
                    
                conn = get_db_connection()
                conn.cursor().execute("INSERT INTO Leave_Ledger (Target_Date, Name, Leave_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)", (l_date.strftime("%Y-%m-%d"), l_name, l_type, l_start, l_end, l_hours))
                conn.commit()
                conn.close()
                st.success(f"Logged leave for {l_name}.")
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
    st.markdown("Use this to forcefully lock a specific person into a specific seat. Overrides instantly bypass all seniority and statistics logic. The rest of the crew will automatically build around your locked seat.")
    
    ov_col1, ov_col2 = st.columns([1, 1])
    with ov_col1:
        with st.form("override_form", clear_on_submit=True):
            o_date_ov = st.date_input("Target Date", value=target_date, key="ov_d")
            o_name = st.selectbox("Personnel (Includes Write-Ins)", override_names_list, key="ov_n")
            o_seat = st.selectbox("Assign to Seat", all_seats_list, key="ov_s")
            
            if st.form_submit_button("Lock Seat Override"):
                conn = get_db_connection()
                conn.cursor().execute("INSERT INTO Manual_Overrides (Target_Date, Name, Seat) VALUES (%s, %s, %s)", (o_date_ov.strftime("%Y-%m-%d"), o_name, o_seat))
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

# ==========================================
# TAB 4: SEAT STATISTICS
# ==========================================
with tab_stats:
    st.subheader("📊 Historical Seat Balances")
    st.markdown("This tracker automatically logs how many hours each person has spent in the rotating positions, as well as the number of times they have pulled each Alarm Room watch.")
    
    if not stats_df.empty:
        pivot_df = stats_df.pivot(index='name', columns='position', values='total_hours').fillna(0)
        
        st.markdown("#### 🕒 Structural Seat Balance (Total Hours)")
        display_cols = [c for c in ["E-221 Driver/Operator", "E-221 Firefighter 1", "E-221 Firefighter 2", "R-221 Driver/Operator", "R-221 Firefighter 1", "ARO 0700-1200 (Float)"] if c in pivot_df.columns]
        st.dataframe(pivot_df[display_cols], use_container_width=True)
        
        st.divider()
        
        st.markdown("#### 👁️ Alarm Room Watches (Total Count)")
        watch_cols = [c for c in ["Watch: 0700-1200", "Watch: 1200-1700", "Watch: 1700-2200", "Watch: 2200-0600", "Watch: 0600-0700"] if c in pivot_df.columns]
        if watch_cols:
            st.dataframe(pivot_df[watch_cols].astype(int), use_container_width=True)
        else:
            st.info("No watches logged to history yet.")
    else:
        st.info("No roster history saved yet. Go to the Daily Roster tab and click the blue **'Commit Today's Roster to History'** button to start tracking stats!")
