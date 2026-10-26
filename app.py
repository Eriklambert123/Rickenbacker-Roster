import streamlit as st
import psycopg2
import pandas as pd
import datetime
import calendar
import time
import io
import copy
import itertools
import warnings
from zoneinfo import ZoneInfo

warnings.filterwarnings('ignore')

# Force calendar to start on Sunday
calendar.setfirstweekday(calendar.SUNDAY)

st.set_page_config(
    page_title="Rickenbacker Fire Department Management",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🛑 Streamlit Cloud Secrets
DB_URI = st.secrets["DB_URI"]
ADMIN_PIN = st.secrets.get("ADMIN_PIN", "2026")

# CSS: Top padding adjusted, enlarged radio buttons
st.markdown("""
<style>
.block-container { 
    padding-top: 2rem !important; 
}
[data-testid="column"] { padding: 0 0.3rem !important; }
[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 0.5rem !important; }

/* Popover list density */
[data-testid="stPopover"] button { padding: 4px 6px !important; min-height: auto !important; height: auto !important; }
[data-testid="stPopover"] button p { 
    font-size: 0.82rem !important; 
    white-space: normal !important; 
    line-height: 1.2 !important; 
    margin: 0 !important; 
}
hr { margin: 0.8em 0 !important; }

/* Enlarge Sidebar Radio Buttons & Text */
.stRadio div[role="radiogroup"] label {
    padding-top: 6px !important;
    padding-bottom: 6px !important;
}
.stRadio div[role="radiogroup"] p {
    font-size: 1.3rem !important;
    font-weight: 500 !important;
}
.stRadio div[role="radiogroup"] div[data-baseweb="radio"] div {
    transform: scale(1.25);
}
</style>
""", unsafe_allow_html=True)

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ADMIN_DAY_OPTIONS = ["None", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def get_station_today():
    """Returns the current date in Rickenbacker Station local time (America/New_York)."""
    try:
        return datetime.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return (datetime.datetime.utcnow() - datetime.timedelta(hours=4)).date()


def get_icon(entry_type):
    icons = {
        "Annual Leave": "🏖️", "Paternity Leave": "🍼", "Union Leave": "🤝",
        "Bereavement Leave": "🕊️", "Medical Leave": "🏥", "Military Leave": "🪖",
        "Jury Duty": "⚖️", "NFPA Physical": "🩺", "Personal Leave": "👤",
        "Disability Leave": "♿", "Training": "📚", "Voluntary": "💰",
        "Mandatory": "🚨", "Guard Personnel On-Duty": "🫡"
    }
    return icons.get(entry_type, "📌")


def clean_display_name(name_str):
    if not name_str:
        return ""
    return (
        str(name_str)
        .replace(" (Trade)", "")
        .replace(" (OT)", "")
        .replace(" (Guard)", "")
        .strip()
    )


def time_to_shift_hr(t_str):
    try:
        t_str = str(t_str).replace(":", "").strip()
        if len(t_str) == 3:
            t_str = "0" + t_str
        if len(t_str) < 4:
            return 0.0
        h = int(t_str[:2])
        m = int(t_str[2:])
        val = h + (m / 60.0)
        if val < 7.0:
            val += 24.0
        return val - 7.0
    except Exception:
        return 0.0


def calc_hours(start_str, end_str):
    try:
        s, e = int(str(start_str).replace(":", "").strip()), int(str(end_str).replace(":", "").strip())
        s_dec = (s // 100) + ((s % 100) / 60.0)
        e_dec = (e // 100) + ((e % 100) / 60.0)
        if e_dec <= s_dec:
            e_dec += 24.0
        return round(e_dec - s_dec, 1)
    except Exception:
        return 0.0


def parse_time(start_str, end_str, hrs, is_absence=True):
    """
    Determines whether an entry affects the AM block (0700-1500, 8h) and/or PM block (1500-0700, 16h).
    Prevents short mid-day leaves (e.g., 1300-1700) from wiping a firefighter off both AM and PM rosters
    for the entire 24-hour shift.
    """
    try:
        hrs_val = float(hrs)
    except Exception:
        hrs_val = 24.0

    if hrs_val >= 24.0:
        return True, True

    try:
        s_hr = time_to_shift_hr(start_str)
        e_hr = time_to_shift_hr(end_str)
        if e_hr <= s_hr:
            e_hr = min(24.0, s_hr + hrs_val) if hrs_val > 0 else 24.0

        am_overlap = max(0.0, min(8.0, e_hr) - max(0.0, s_hr))
        pm_overlap = max(0.0, min(24.0, e_hr) - max(8.0, s_hr))

        if am_overlap > 0 and pm_overlap == 0:
            return True, False
        if pm_overlap > 0 and am_overlap == 0:
            return False, True

        if am_overlap > 0 and pm_overlap > 0:
            if is_absence and hrs_val < 12.0:
                if am_overlap >= 4.0 and pm_overlap >= 4.0:
                    return True, True
                elif am_overlap >= pm_overlap:
                    return True, False
                else:
                    return False, True
            return True, True

        return True, True
    except Exception:
        return True, True


def is_admin_pm_active(person_row, weekday_int):
    """
    Evaluates whether an Admin schedule member works the PM block (24-hour shift day)
    using the data-driven admin_24h_day column, with fallback support for existing records.
    """
    if str(person_row.get('shift', '')).upper() != 'ADMIN':
        return True

    admin_day = str(person_row.get('admin_24h_day', '') or '').strip()
    name = str(person_row.get('name', '')).strip()

    if not admin_day or admin_day in ['None', 'nan', 'NaN']:
        if name == 'Garver':
            admin_day = 'Wednesday'
        elif name == 'McNamara':
            admin_day = 'Tuesday'
        else:
            return False

    if admin_day in WEEKDAY_NAMES:
        return weekday_int == WEEKDAY_NAMES.index(admin_day)
    return False


# --- 1. SIDEBAR: AUTH, CONTROLS, & NAVIGATION ---
st.sidebar.markdown("### 🔒 System Access")
entered_pin = st.sidebar.text_input("Enter PIN to Edit", type="password")
is_admin = (entered_pin == str(ADMIN_PIN))

if is_admin:
    st.sidebar.success("✅ Admin Unlocked")
else:
    st.sidebar.info("👀 Read-Only Mode")

st.sidebar.divider()

st.sidebar.markdown("<div style='font-size: 1.25rem; font-weight: 600; margin-bottom: 5px;'>Select Target Date</div>", unsafe_allow_html=True)
target_date = st.sidebar.date_input("Select Target Date", get_station_today(), label_visibility="collapsed")
target_date_str = target_date.strftime("%Y-%m-%d")

anchor_date = datetime.date(2026, 9, 16)
shift_mod = (target_date - anchor_date).days % 3

if shift_mod == 0:
    selected_shift, top_shift_color = "A-Shift", "#4CAF50"
elif shift_mod == 1:
    selected_shift, top_shift_color = "B-Shift", "#2196F3"
else:
    selected_shift, top_shift_color = "C-Shift", "#F44336"

st.sidebar.divider()

st.sidebar.header("Navigation")
nav_options = [
    "📋 Daily Roster",
    "⏱️ Overtime Roster",
    "📅 Leave Calendar",
    "📆 Training & Events",
    "📊 Seat Statistics"
]
if is_admin:
    nav_options.extend(["✍️ Data Entry"])

selected_page = st.sidebar.radio("Go to:", nav_options, label_visibility="collapsed")


# --- 2. POSTGRES DATABASE CLOUD CONNECTION & CACHED INITIALIZATION ---
MASTER_CERT_DATA = [{'first_name': 'Dennis', 'last_name': 'Atkins', 'hire_date': '1998-07-06', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': False}, {'first_name': 'Robert', 'last_name': 'Bissett', 'hire_date': '2011-01-16', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Lucas', 'last_name': 'Blom', 'hire_date': '2008-08-31', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Stephen', 'last_name': 'Buzzard', 'hire_date': '2000-12-04', 'emr': False, 'emt': False, 'paramedic': True, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}, {'first_name': 'Brian', 'last_name': 'Conrad', 'hire_date': '2003-07-29', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Dusty', 'last_name': 'Downing', 'hire_date': '2000-03-12', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Dane', 'last_name': 'Eagle', 'hire_date': '2023-06-20', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Aaron', 'last_name': 'Elswick', 'hire_date': '2007-08-20', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Kenneth', 'last_name': 'Finch', 'hire_date': '2000-12-04', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': True, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}, {'first_name': 'Zachary', 'last_name': 'Garber', 'hire_date': '2021-03-29', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Lewis', 'last_name': 'Garver', 'hire_date': '1998-07-06', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Jon', 'last_name': 'Guilliams', 'hire_date': '2004-08-09', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Christopher', 'last_name': 'Hurst', 'hire_date': '2009-07-05', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Gideon', 'last_name': 'Johnson', 'hire_date': '2026-04-06', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'John', 'last_name': 'Klaus', 'hire_date': '1994-08-07', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Erik', 'last_name': 'Lambert', 'hire_date': '2007-09-21', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Danny', 'last_name': 'Lee', 'hire_date': '2012-01-15', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': False}, {'first_name': 'Derek', 'last_name': 'Lotz', 'hire_date': None, 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'John', 'last_name': 'Martin', 'hire_date': '2025-10-06', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': True, 'instructor_2': True, 'instructor_3': True, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}, {'first_name': 'Dennis', 'last_name': 'McJunkin', 'hire_date': '1998-07-06', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': True, 'instructor_2': True, 'instructor_3': True, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}, {'first_name': 'Christopher', 'last_name': 'McKee', 'hire_date': '2000-03-13', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Kevin', 'last_name': 'McNamara', 'hire_date': '2005-06-13', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Jacob', 'last_name': 'Mefford', 'hire_date': '2020-01-20', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'James', 'last_name': 'Moeller', 'hire_date': '2000-02-13', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Matthew', 'last_name': 'Moore', 'hire_date': '2000-12-04', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Jerry', 'last_name': 'Napier', 'hire_date': '2025-09-07', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': "O'Neal", 'last_name': 'Payne', 'hire_date': '2020-03-01', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Douglas', 'last_name': 'Proffitt', 'hire_date': '2004-07-11', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Scott', 'last_name': 'Rickord', 'hire_date': '2002-12-30', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': False, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Michael', 'last_name': 'Riley', 'hire_date': '2020-01-12', 'emr': False, 'emt': False, 'paramedic': True, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Matthew', 'last_name': 'Roach', 'hire_date': '1994-08-07', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': False}, {'first_name': 'Paul', 'last_name': 'Roach', 'hire_date': '2005-11-13', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': True, 'instructor_1': True, 'instructor_2': True, 'instructor_3': True, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}, {'first_name': 'Michael', 'last_name': 'Ross', 'hire_date': '2004-06-27', 'emr': True, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': True, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Adam', 'last_name': 'Schaefer', 'hire_date': '2025-06-30', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': False, 'officer_4': False}, {'first_name': 'Chad', 'last_name': 'Slonaker', 'hire_date': '2008-07-21', 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': False, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': False}, {'first_name': 'Jakob', 'last_name': 'Staggs', 'hire_date': '2025-07-25', 'emr': False, 'emt': True, 'paramedic': False, 'inspector_1': False, 'inspector_2': False, 'inspector_3': False, 'instructor_1': True, 'instructor_2': False, 'instructor_3': False, 'officer_1': False, 'officer_2': False, 'officer_3': False, 'officer_4': False}, {'first_name': 'Mark', 'last_name': 'White', 'hire_date': None, 'emr': False, 'emt': False, 'paramedic': False, 'inspector_1': True, 'inspector_2': True, 'inspector_3': False, 'instructor_1': True, 'instructor_2': True, 'instructor_3': True, 'officer_1': True, 'officer_2': True, 'officer_3': True, 'officer_4': True}]


def match_cert_record(db_name, db_shift, records):
    """
    Matches a Personnel row (Name, Shift) from the database to a record from the
    Certifications spreadsheet. Handles last-name-only, full names, initials,
    spelling variations (O'Neal vs O'Neil), and duplicate last names (Matthew vs Paul Roach).
    """
    clean = str(db_name).strip().lower()
    clean_no_punct = clean.replace(".", "").replace(",", "").replace("'", "").strip()
    shift_up = str(db_shift or "").strip().upper()

    # Special disambiguation for Roach (Matthew Roach vs Paul Roach)
    if "roach" in clean_no_punct:
        for r in records:
            if r["last_name"].lower() != "roach":
                continue
            fn = r["first_name"].lower()
            if fn == "matthew" and (
                clean_no_punct in ["m roach", "matthew roach", "matt roach", "roach m", "roach matthew", "roach matt"]
                or (clean_no_punct == "roach" and shift_up == "B")
            ):
                return r
            if fn == "paul" and (
                clean_no_punct in ["p roach", "paul roach", "roach p", "roach paul"]
                or (clean_no_punct == "roach" and shift_up != "B")
            ):
                return r
        return None

    for r in records:
        first = r["first_name"].strip().lower()
        last = r["last_name"].strip().lower()
        first_np = first.replace("'", "").replace(".", "")
        last_np = last.replace("'", "").replace(".", "")
        f_init = first_np[0] if first_np else ""

        candidates = {
            last,
            last_np,
            f"{first} {last}",
            f"{first_np} {last_np}",
            f"{f_init} {last_np}",
            f"{last_np} {first_np}",
            f"{last_np} {f_init}",
        }
        # Handle O'Neal / O'Neil Payne variations
        if last_np == "payne":
            candidates.update({"oneil payne", "oneal payne", "o payne", "payne"})

        if clean_no_punct in candidates or last_np in clean_no_punct.split():
            return r

    return None


def sync_personnel_certifications(conn, records=None):
    """
    Updates existing Personnel rows in Postgres with Hire_Date and all certification flags
    (EMR, EMT, Paramedic, Inspector 1-3, Instructor 1-3, Officer 1-4) from records.
    Returns the number of updated personnel rows.
    """
    if records is None:
        records = MASTER_CERT_DATA

    df_p = pd.read_sql("SELECT Name, Shift, Hire_Date FROM Personnel", conn)
    if df_p.empty:
        return 0

    df_p.columns = [c.lower() for c in df_p.columns]
    updated_count = 0

    with conn.cursor() as c:
        for _, row in df_p.iterrows():
            db_name = row['name']
            db_shift = row.get('shift', '')
            curr_hire = row.get('hire_date', '2020-01-01')

            matched = match_cert_record(db_name, db_shift, records)
            if not matched:
                continue

            new_hire = matched["hire_date"] if matched.get("hire_date") else (curr_hire or '2020-01-01')

            c.execute(
                """UPDATE Personnel SET
                    Hire_Date = %s,
                    EMR = %s,
                    EMT = %s,
                    Paramedic = %s,
                    Inspector = %s,
                    Inspector_2 = %s,
                    Inspector_3 = %s,
                    Instructor = %s,
                    Instructor_2 = %s,
                    Instructor_3 = %s,
                    Officer_1 = %s,
                    Officer_2 = %s,
                    Officer_3 = %s,
                    Officer_4 = %s
                WHERE Name = %s AND Shift = %s""",
                (
                    new_hire,
                    bool(matched["emr"]),
                    bool(matched["emt"]),
                    bool(matched["paramedic"]),
                    bool(matched["inspector_1"]),
                    bool(matched["inspector_2"]),
                    bool(matched["inspector_3"]),
                    bool(matched["instructor_1"]),
                    bool(matched["instructor_2"]),
                    bool(matched["instructor_3"]),
                    bool(matched["officer_1"]),
                    bool(matched["officer_2"]),
                    bool(matched["officer_3"]),
                    bool(matched["officer_4"]),
                    db_name,
                    db_shift
                )
            )
            updated_count += 1

    conn.commit()
    return updated_count

def get_db_connection():
    return psycopg2.connect(DB_URI)


# --- AUTO SHIFT-UP ENGINE ---
def recalculate_ff_seniority():
    conn_rec = get_db_connection()
    try:
        df_rec = pd.read_sql("SELECT * FROM Personnel", conn_rec)
        df_rec.columns = [c.lower() for c in df_rec.columns]
        df_rec['hire_date'] = df_rec['hire_date'].fillna('2020-01-01')
        if 'tie_breaker' not in df_rec.columns:
            df_rec['tie_breaker'] = 9999
        else:
            df_rec['tie_breaker'] = pd.to_numeric(df_rec['tie_breaker'], errors='coerce').fillna(9999).astype(int)

        ffs = df_rec[df_rec['seniority'] > 10].copy()
        ffs = ffs.sort_values(by=['hire_date', 'tie_breaker', 'seniority'], ascending=[True, True, True])

        updates = []
        current_sen = 11
        for _, row in ffs.iterrows():
            updates.append((current_sen, row['name']))
            current_sen += 1

        if updates:
            with conn_rec.cursor() as cur:
                cur.executemany("UPDATE Personnel SET Seniority = %s WHERE Name = %s", updates)
            conn_rec.commit()
    finally:
        conn_rec.close()


@st.cache_resource
def init_db(_schema_v=5):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS ARO_Watch (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Watch_Period TEXT, Name TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Shift_Trades_V3 (
                id SERIAL PRIMARY KEY, Date_1 TEXT, Name_1 TEXT, Date_2 TEXT, Name_2 TEXT,
                Start_Time TEXT, End_Time TEXT, Total_Hours REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Position_Log (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Position TEXT, Hours REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Manual_Overrides (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Seat TEXT, Period TEXT DEFAULT 'FULL'
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Personnel (
                Name TEXT, Seniority INTEGER, Core_Manning BOOLEAN, Schedule TEXT, Shift TEXT,
                Officer_1 BOOLEAN DEFAULT FALSE, Officer_2 BOOLEAN DEFAULT FALSE,
                Officer_3 BOOLEAN DEFAULT FALSE, Officer_4 BOOLEAN DEFAULT FALSE,
                Inspector BOOLEAN DEFAULT FALSE, Inspector_2 BOOLEAN DEFAULT FALSE, Inspector_3 BOOLEAN DEFAULT FALSE,
                Instructor BOOLEAN DEFAULT FALSE, Instructor_2 BOOLEAN DEFAULT FALSE, Instructor_3 BOOLEAN DEFAULT FALSE,
                EMR BOOLEAN DEFAULT FALSE, EMT BOOLEAN DEFAULT FALSE, Paramedic BOOLEAN DEFAULT FALSE,
                Hire_Date TEXT DEFAULT '2020-01-01', Tie_Breaker INTEGER DEFAULT 9999,
                Admin_24h_Day TEXT DEFAULT 'None'
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Leave_Ledger (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, Leave_Type TEXT,
                Start_Time TEXT, End_Time TEXT, Total_Hours REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Overtime_Log (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Name TEXT, OT_Type TEXT,
                Start_Time TEXT, End_Time TEXT, Total_Hours REAL
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Station_Events (
                id SERIAL PRIMARY KEY, Target_Date TEXT, Event_Type TEXT, Description TEXT,
                Start_Time TEXT, End_Time TEXT, Instructor TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Overtime_Buckets (
                Name TEXT PRIMARY KEY, Seniority INTEGER, Shift TEXT, Current_Hours REAL, Contact TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS OT_Archive (
                id SERIAL PRIMARY KEY, Archive_Stamp TEXT, Notes TEXT, Name TEXT,
                Start_Hours REAL, Hours_Applied REAL, Status TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS Apparatus (
                Rig_Name TEXT PRIMARY KEY, Status TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS System_Migrations (
                Migration_Key TEXT PRIMARY KEY, Applied_At TEXT
            )""")

            # Ensure backward-compatible schema migrations for existing Personnel tables
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Officer_1 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Officer_2 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Officer_3 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Officer_4 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Inspector BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Inspector_2 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Inspector_3 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Instructor BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Instructor_2 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Instructor_3 BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS EMR BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS EMT BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Paramedic BOOLEAN DEFAULT FALSE")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Hire_Date TEXT DEFAULT '2020-01-01'")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Tie_Breaker INTEGER DEFAULT 9999")
            c.execute("ALTER TABLE Personnel ADD COLUMN IF NOT EXISTS Admin_24h_Day TEXT DEFAULT 'None'")
            c.execute("ALTER TABLE Manual_Overrides ADD COLUMN IF NOT EXISTS Period TEXT DEFAULT 'FULL'")
            c.execute("UPDATE Manual_Overrides SET Period = 'FULL' WHERE Period IS NULL")

            # Migrate legacy hardcoded Admin 24h days if not set yet
            c.execute("UPDATE Personnel SET Admin_24h_Day = 'Wednesday' WHERE Name = 'Garver' AND (Admin_24h_Day IS NULL OR Admin_24h_Day = 'None')")
            c.execute("UPDATE Personnel SET Admin_24h_Day = 'Tuesday' WHERE Name = 'McNamara' AND (Admin_24h_Day IS NULL OR Admin_24h_Day = 'None')")

            # Seed Default Apparatus if Blank
            c.execute("SELECT COUNT(*) FROM Apparatus")
            if c.fetchone()[0] == 0:
                default_rigs = [
                    ("CH-222", "In Service"), ("R-221", "In Service"), ("E-221", "In Service"), ("Tanker", "In Service"),
                    ("Crash-1", "In Service"), ("Crash-3", "In Service"), ("Crash-4", "In Service"), ("Crash-8", "In Service"), ("ARO", "In Service")
                ]
                c.executemany("INSERT INTO Apparatus (Rig_Name, Status) VALUES (%s, %s)", default_rigs)

            # Clean up any future-dated Position_Log rows left over from legacy manual projections
            today_cleanup_str = get_station_today().strftime("%Y-%m-%d")
            c.execute("DELETE FROM Position_Log WHERE Target_Date >= %s", (today_cleanup_str,))

            # Check if master certification sheet sync has been applied
            c.execute("SELECT COUNT(*) FROM System_Migrations WHERE Migration_Key = 'cert_sheet_sync_v1'")
            already_synced = (c.fetchone()[0] > 0)

        conn.commit()

        if not already_synced:
            sync_personnel_certifications(conn, MASTER_CERT_DATA)
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO System_Migrations (Migration_Key, Applied_At) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    ("cert_sheet_sync_v1", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                )
            conn.commit()
    finally:
        conn.close()
    recalculate_ff_seniority()
    return True


init_db()

# Read Data & Standardize Column Names safely
conn = get_db_connection()
try:
    all_personnel_df = pd.read_sql("SELECT * FROM Personnel", conn)
    all_personnel_df.columns = [c.lower() for c in all_personnel_df.columns]
    if 'hire_date' not in all_personnel_df.columns:
        all_personnel_df['hire_date'] = '2020-01-01'
    else:
        all_personnel_df['hire_date'] = all_personnel_df['hire_date'].fillna('2020-01-01')

    if 'tie_breaker' not in all_personnel_df.columns:
        all_personnel_df['tie_breaker'] = 9999
    else:
        all_personnel_df['tie_breaker'] = pd.to_numeric(all_personnel_df['tie_breaker'], errors='coerce').fillna(9999).astype(int)

    if 'admin_24h_day' not in all_personnel_df.columns:
        all_personnel_df['admin_24h_day'] = 'None'
    else:
        all_personnel_df['admin_24h_day'] = all_personnel_df['admin_24h_day'].fillna('None')

    all_leave_df = pd.read_sql("SELECT * FROM Leave_Ledger", conn)
    all_leave_df.columns = [c.lower() for c in all_leave_df.columns] if not all_leave_df.empty else ["id", "target_date", "name", "leave_type", "start_time", "end_time", "total_hours"]

    all_ot_df = pd.read_sql("SELECT * FROM Overtime_Log", conn)
    all_ot_df.columns = [c.lower() for c in all_ot_df.columns] if not all_ot_df.empty else ["id", "target_date", "name", "ot_type", "start_time", "end_time", "total_hours"]

    all_trades_df = pd.read_sql("SELECT * FROM Shift_Trades_V3", conn)
    all_trades_df.columns = [c.lower() for c in all_trades_df.columns] if not all_trades_df.empty else ["id", "date_1", "name_1", "date_2", "name_2", "start_time", "end_time", "total_hours"]

    station_today = get_station_today()
    station_today_str = station_today.strftime("%Y-%m-%d")

    all_aro_watch_df = pd.read_sql("SELECT * FROM ARO_Watch", conn)
    all_aro_watch_df.columns = [c.lower() for c in all_aro_watch_df.columns] if not all_aro_watch_df.empty else ["id", "target_date", "watch_period", "name"]
    aro_watch_df = all_aro_watch_df[all_aro_watch_df['target_date'] == target_date_str].copy() if not all_aro_watch_df.empty else pd.DataFrame(columns=["id", "target_date", "watch_period", "name"])

    all_overrides_df = pd.read_sql("SELECT * FROM Manual_Overrides", conn)
    all_overrides_df.columns = [c.lower() for c in all_overrides_df.columns] if not all_overrides_df.empty else ["id", "target_date", "name", "seat", "period"]
    if 'period' not in all_overrides_df.columns:
        all_overrides_df['period'] = 'FULL'
    else:
        all_overrides_df['period'] = all_overrides_df['period'].fillna('FULL')
    overrides_df = all_overrides_df[all_overrides_df['target_date'] == target_date_str].copy() if not all_overrides_df.empty else pd.DataFrame(columns=["id", "target_date", "name", "seat", "period"])

    # Ensure Position_Log only reflects completed past shifts (< station_today_str)
    with conn.cursor() as c_clean:
        c_clean.execute("DELETE FROM Position_Log WHERE Target_Date >= %s", (station_today_str,))
    conn.commit()

    all_pos_log_df = pd.read_sql("SELECT Target_Date, Name, Position, Hours FROM Position_Log WHERE Target_Date < %s", conn, params=(station_today_str,))
    all_pos_log_df.columns = [c.lower() for c in all_pos_log_df.columns] if not all_pos_log_df.empty else ["target_date", "name", "position", "hours"]

    stats_df = pd.read_sql("SELECT Name, Position, SUM(Hours) as Total_Hours FROM Position_Log WHERE Target_Date < %s GROUP BY Name, Position", conn, params=(station_today_str,))
    stats_df.columns = [c.lower() for c in stats_df.columns] if not stats_df.empty else ["name", "position", "total_hours"]

    events_df = pd.read_sql("SELECT * FROM Station_Events", conn)
    events_df.columns = [c.lower() for c in events_df.columns] if not events_df.empty else ["id", "target_date", "event_type", "description", "start_time", "end_time", "instructor"]

    app_df = pd.read_sql("SELECT * FROM Apparatus", conn)
    app_df.columns = [c.lower() for c in app_df.columns]
finally:
    conn.close()

# Evaluate Apparatus Status
app_status_dict = dict(zip(app_df['rig_name'], app_df['status']))
oos_list = [k for k, v in app_status_dict.items() if v != "In Service"]
default_rig_names = ["CH-222", "R-221", "E-221", "Tanker", "Crash-1", "Crash-3", "Crash-4", "Crash-8", "ARO"]
temp_rigs = [r for r in app_status_dict.keys() if r not in default_rig_names]




personnel_info = {}
admin_names = set()
for _, r in all_personnel_df.iterrows():
    personnel_info[r['name']] = {
        'Seniority': r['seniority'],
        'Tie_Breaker': int(r.get('tie_breaker', 9999)),
        'Schedule': r['schedule'],
        'Core': r['core_manning'],
        'Shift': r['shift'],
        'Officer_1': bool(r.get('officer_1', False)),
        'Officer_2': bool(r.get('officer_2', False)),
        'Officer_3': bool(r.get('officer_3', False)),
        'Officer_4': bool(r.get('officer_4', False)),
        'Inspector': bool(r.get('inspector', False)),
        'Inspector_2': bool(r.get('inspector_2', False)),
        'Inspector_3': bool(r.get('inspector_3', False)),
        'Instructor': bool(r.get('instructor', False)),
        'Instructor_2': bool(r.get('instructor_2', False)),
        'Instructor_3': bool(r.get('instructor_3', False)),
        'EMR': bool(r.get('emr', False)),
        'EMT': bool(r.get('emt', False)),
        'Paramedic': bool(r.get('paramedic', False)),
        'Admin_24h_Day': r.get('admin_24h_day', 'None')
    }
    if r['schedule'] == 'Admin':
        admin_names.add(r['name'])

def build_override_maps(ov_df_local):
    am_map = {}
    pm_map = {}
    if ov_df_local is not None and not ov_df_local.empty:
        for _, ov_row in ov_df_local.iterrows():
            s = ov_row['seat']
            n = ov_row['name']
            per = str(ov_row.get('period') or 'FULL').upper()
            if per in ['FULL', 'AM']:
                am_map[s] = n
            if per in ['FULL', 'PM']:
                pm_map[s] = n
    return am_map, pm_map

seat_to_override_am, seat_to_override_pm = build_override_maps(overrides_df)

stat_lookup = {}
if not stats_df.empty:
    for _, r in stats_df.iterrows():
        clean_name = r['name']
        if clean_name not in stat_lookup:
            stat_lookup[clean_name] = {}
        stat_lookup[clean_name][r['position']] = r['total_hours']

db_watches = dict(zip(aro_watch_df['watch_period'], aro_watch_df['name'])) if not aro_watch_df.empty else {}
db_shift = selected_shift[0]

if target_date.weekday() < 5:
    personnel_df = all_personnel_df[(all_personnel_df['shift'] == db_shift) | (all_personnel_df['shift'] == 'ADMIN')]
else:
    personnel_df = all_personnel_df[(all_personnel_df['shift'] == db_shift)]

leave_df = all_leave_df[all_leave_df['target_date'] == target_date_str] if not all_leave_df.empty else pd.DataFrame()
ot_df = all_ot_df[all_ot_df['target_date'] == target_date_str] if not all_ot_df.empty else pd.DataFrame()
all_names = sorted(all_personnel_df['name'].tolist())

officer_seats = [("CH-222", "AC"), ("E-221", "Station Captain"), ("R-221", "Crew Chief")]
rotating_seats = [
    ("ARO", "0700-1200 (Float)"),
    ("E-221", "Driver/Operator"),
    ("E-221", "Firefighter 1"),
    ("E-221", "Firefighter 2"),
    ("R-221", "Driver/Operator"),
    ("R-221", "Firefighter 1")
]
crash_seats = [("Crash-4", "Driver"), ("Crash-1", "Driver"), ("Crash-3", "Driver"), ("Crash-8", "Driver")]

all_seats_list = [
    "CH-222 | AC", "E-221 | Station Captain", "E-221 | Driver/Operator", "E-221 | Firefighter 1", "E-221 | Firefighter 2",
    "R-221 | Crew Chief", "R-221 | Driver/Operator", "R-221 | Firefighter 1", "ARO | 0700-1200 (Float)",
    "Crash-1 | Crew Chief", "Crash-1 | Driver", "Crash-3 | Crew Chief", "Crash-3 | Driver",
    "Crash-4 | Crew Chief", "Crash-4 | Driver", "Crash-8 | Crew Chief", "Crash-8 | Driver",
    "Tanker | Crew Chief", "Tanker | Driver"
]
for tr in temp_rigs:
    all_seats_list.append(f"{tr} | Crew Chief")
    all_seats_list.append(f"{tr} | Driver")


def get_empty_rigs():
    rigs = {
        "CH-222": {"AC": ""},
        "R-221": {"Crew Chief": "", "Driver/Operator": "", "Firefighter 1": ""},
        "E-221": {"Station Captain": "", "Driver/Operator": "", "Firefighter 1": "", "Firefighter 2": ""},
        "ARO": {
            "0700-1200 (Float)": "",
            "1200-1700": "TBD (Draw)",
            "1700-2200": "TBD (Draw)",
            "2200-0600": "TBD (Draw)",
            "0600-0700": "TBD (Draw)"
        },
        "Crash-4": {"Crew Chief": "", "Driver": ""},
        "Crash-1": {"Crew Chief": "", "Driver": ""},
        "Crash-3": {"Crew Chief": "", "Driver": ""},
        "Crash-8": {"Crew Chief": "", "Driver": ""},
        "Tanker": {"Crew Chief": "", "Driver": ""}
    }
    for tr in temp_rigs:
        rigs[tr] = {"Crew Chief": "", "Driver": ""}
    return rigs


def apply_cross_staffing_and_watches(rigs, d_watches, oos_list_local):
    def is_empty(val):
        return val in ["", "VACANT"]

    E_Capt = rigs.get("E-221", {}).get("Station Captain", "")
    E_Driver = rigs.get("E-221", {}).get("Driver/Operator", "")
    E_FF1 = rigs.get("E-221", {}).get("Firefighter 1", "")
    E_FF2 = rigs.get("E-221", {}).get("Firefighter 2", "")
    R_CC = rigs.get("R-221", {}).get("Crew Chief", "")
    R_Driver = rigs.get("R-221", {}).get("Driver/Operator", "")
    R_FF1 = rigs.get("R-221", {}).get("Firefighter 1", "")

    # 1. Engine -> Crash Trucks Priority Cascade
    if "Crash-4" not in oos_list_local:
        if is_empty(rigs["Crash-4"]["Driver"]):
            rigs["Crash-4"]["Driver"] = E_Capt
        else:
            rigs["Crash-4"]["Crew Chief"] = E_Capt
    else:
        # C-4 OOS shifts Engine Station Captain to C-8 Crew Chief
        if "Crash-8" not in oos_list_local:
            if is_empty(rigs["Crash-8"]["Crew Chief"]):
                rigs["Crash-8"]["Crew Chief"] = E_Capt
            else:
                rigs["Crash-8"]["Driver"] = E_Capt

    if "Crash-8" not in oos_list_local:
        if is_empty(rigs["Crash-8"]["Driver"]):
            rigs["Crash-8"]["Driver"] = E_FF2
        elif is_empty(rigs["Crash-8"]["Crew Chief"]):
            rigs["Crash-8"]["Crew Chief"] = E_FF2

    if "Crash-1" not in oos_list_local:
        if is_empty(rigs["Crash-1"]["Driver"]):
            rigs["Crash-1"]["Driver"] = E_Driver
        else:
            rigs["Crash-1"]["Crew Chief"] = E_Driver

    if "Crash-3" not in oos_list_local:
        if is_empty(rigs["Crash-3"]["Driver"]):
            rigs["Crash-3"]["Driver"] = E_FF1
        else:
            rigs["Crash-3"]["Crew Chief"] = E_FF1

    # 2. Rescue -> Tanker & Crash Truck Cascade
    if "Tanker" not in oos_list_local:
        if is_empty(rigs["Tanker"]["Crew Chief"]):
            rigs["Tanker"]["Crew Chief"] = R_CC
        if is_empty(rigs["Tanker"]["Driver"]):
            rigs["Tanker"]["Driver"] = R_FF1

    if "R-221" in oos_list_local:
        rescue_crew = [p for p in [R_CC, R_Driver, R_FF1] if not is_empty(p)]
        crash_priorities = ["Crash-4", "Crash-8", "Crash-1", "Crash-3"]
        available_crashes = [t for t in crash_priorities if t not in oos_list_local]

        # Allocate Rescue personnel to the highest priority empty Crash seats
        for person in rescue_crew:
            for target_rig in available_crashes:
                if is_empty(rigs[target_rig]["Crew Chief"]):
                    rigs[target_rig]["Crew Chief"] = person
                    break
                elif is_empty(rigs[target_rig]["Driver"]):
                    rigs[target_rig]["Driver"] = person
                    break

    for wp in ["1200-1700", "1700-2200", "2200-0600", "0600-0700"]:
        if wp in d_watches:
            rigs["ARO"][wp] = d_watches[wp]

    return rigs


# --- OPTIMAL ROTATING SEAT BALANCER ---
def assign_optimal_rotating_seats(candidates_pool, open_seats_list, stats_local):
    """
    Globally assigns firefighters in `candidates_pool` to `open_seats_list` (up to 6 rotating seats)
    to minimize both individual seat hour deficits and apparatus-level (E-221 vs R-221 vs ARO) hour
    imbalance. Prevents any firefighter from getting repeatedly stacked on E-221 once their deficit
    is caught up.
    """
    if not candidates_pool or not open_seats_list:
        return {}, candidates_pool

    n_assign = min(len(candidates_pool), len(open_seats_list))
    active_cands = candidates_pool[:n_assign]
    leftover_cands = candidates_pool[n_assign:]
    seats_to_fill = open_seats_list[:n_assign]

    all_rot_names = [f"{r} {p}" for r, p in rotating_seats]
    rig_seat_counts = {"ARO": 1.0, "E-221": 3.0, "R-221": 2.0}

    # Precompute each candidate's seat hours, personal average, and apparatus averages
    cand_profiles = {}
    for p in active_cands:
        c_name = clean_display_name(p["Name"])
        p_st = stats_local.get(c_name, {})
        s_hrs = {sn: float(p_st.get(sn, 0.0)) for sn in all_rot_names}
        avg_s = sum(s_hrs.values()) / 6.0
        rig_avgs = {}
        for r_key, r_cnt in rig_seat_counts.items():
            rig_avgs[r_key] = sum(s_hrs[f"{r_iter} {p_iter}"] for r_iter, p_iter in rotating_seats if r_iter == r_key) / r_cnt
        cand_profiles[p["Name"]] = {
            "clean": c_name,
            "is_admin": c_name in admin_names,
            "seat_hrs": s_hrs,
            "avg_seat": avg_s,
            "rig_avg": rig_avgs,
            "sen": p["Seniority"]
        }

    best_perm = None
    best_score = None

    for perm in itertools.permutations(active_cands, n_assign):
        admin_in_aro = 0
        rel_seat_cost = 0.0
        raw_seat_cost = 0.0
        rig_balance_cost = 0.0
        tie_breaker = 0.0

        for idx, person in enumerate(perm):
            rig, pos = seats_to_fill[idx]
            seat_name = f"{rig} {pos}"
            prof = cand_profiles[person["Name"]]

            if rig == "ARO" and prof["is_admin"]:
                admin_in_aro += 1

            h_s = prof["seat_hrs"].get(seat_name, 0.0)
            # Deviation from firefighter's own average across the 6 rotating seats
            rel_s = h_s - prof["avg_seat"]
            # Deviation of this apparatus (E-221 vs R-221 vs ARO) from firefighter's average
            rel_r = prof["rig_avg"].get(rig, 0.0) - prof["avg_seat"]

            # Post-shift squared excess above personal baseline + raw seat hours
            rel_seat_cost += ((rel_s + 24.0) ** 2)
            raw_seat_cost += ((h_s + 24.0) ** 2)
            rig_balance_cost += ((rel_r + (24.0 / rig_seat_counts.get(rig, 1.0))) ** 2)
            tie_breaker += prof["sen"] * (idx + 1) * 0.0001

        # Combined cost prioritizes:
        # 1. Keeping Admin out of ARO Float if structural FF is available
        # 2. Equalizing seat hours (both relative to personal average and raw across shift) plus apparatus balance
        combined_metric = round(rel_seat_cost * 1.5 + raw_seat_cost * 1.0 + rig_balance_cost * 0.75, 4)
        score = (admin_in_aro, combined_metric, round(rig_balance_cost, 4), round(tie_breaker, 6))
        if best_score is None or score < best_score:
            best_score = score
            best_perm = perm

    assigned_map = {}
    for idx, person in enumerate(best_perm):
        rig, pos = seats_to_fill[idx]
        assigned_map[(rig, pos)] = person["Name"]

    return assigned_map, leftover_cands


# --- CENTRALIZED ROSTER ASSIGNMENT ENGINE ---
def build_roster_assignment(am_c, pm_c, am_ov_local, pm_ov_local, stats_local, partial_leaves_list):
    am_r = get_empty_rigs()
    pm_r = get_empty_rigs()
    am_p = am_c.copy()

    # 1. AM Overrides
    for seat_name, locked_name in am_ov_local.items():
        if seat_name not in all_seats_list:
            continue
        rig, pos = seat_name.split(" | ")
        if locked_name == "VACANT":
            am_r[rig][pos] = "VACANT"
        else:
            for i, p in enumerate(am_p):
                if clean_display_name(p["Name"]) == locked_name:
                    am_r[rig][pos] = am_p.pop(i)["Name"]
                    break

    # 2. AM Officers
    for rig, pos in officer_seats:
        if am_p and am_r[rig][pos] == "":
            chosen = None
            if rig == "CH-222":
                cands = [p for p in am_p if p['Seniority'] <= 4]
                if cands:
                    chosen = cands[0]
            else:
                cert_cands = [p for p in am_p if personnel_info.get(clean_display_name(p["Name"]), {}).get('Officer_1', False)]
                if rig == "E-221":
                    pref_cands = [p for p in cert_cands if 6 <= p['Seniority'] <= 8]
                    if pref_cands:
                        chosen = pref_cands[0]
                    elif cert_cands:
                        chosen = cert_cands[0]
                elif rig == "R-221":
                    pref_cands = [p for p in cert_cands if p['Seniority'] == 5]
                    if not pref_cands:
                        pref_cands = [p for p in cert_cands if 9 <= p['Seniority'] <= 10]
                    if pref_cands:
                        chosen = pref_cands[0]
                    elif cert_cands:
                        chosen = cert_cands[0]

            if not chosen:
                am_p.sort(key=lambda x: (clean_display_name(x["Name"]) in partial_leaves_list, x["Seniority"]))
                if not am_p:
                    break
                chosen = am_p[0]

            am_r[rig][pos] = chosen["Name"]
            am_p.remove(chosen)

    # 3. AM Rotating (Global Minimum-Imbalance Assignment)
    open_am_rot = [(r, pos) for r, pos in rotating_seats if am_r[r][pos] == ""]
    rotation_pool = []
    while am_p and len(rotation_pool) < len(open_am_rot):
        rotation_pool.append(am_p.pop(0))

    am_rot_assigned, leftover_am_rot = assign_optimal_rotating_seats(rotation_pool, open_am_rot, stats_local)
    for (rig, pos), chosen_name in am_rot_assigned.items():
        am_r[rig][pos] = chosen_name

    if leftover_am_rot:
        am_p = leftover_am_rot + am_p

    # 4. AM Crash
    for rig, pos in crash_seats:
        if am_p and am_r[rig][pos] == "":
            am_r[rig][pos] = am_p.pop(0)["Name"]

    # --- PM ASSIGNMENT ---
    pm_names = {p["Name"]: p for p in pm_c}
    assigned_pm_names = set()

    for seat_name, locked_name in pm_ov_local.items():
        if seat_name not in all_seats_list:
            continue
        rig, pos = seat_name.split(" | ")
        if locked_name == "VACANT":
            pm_r[rig][pos] = "VACANT"
        else:
            for pm_name in pm_names:
                if clean_display_name(pm_name) == locked_name:
                    pm_r[rig][pos] = pm_name
                    assigned_pm_names.add(pm_name)
                    break

    for rig, seats in am_r.items():
        for pos, am_name in seats.items():
            if am_name not in ["", "VACANT"] and pm_r[rig][pos] == "":
                if am_name in pm_names and am_name not in assigned_pm_names:
                    pm_r[rig][pos] = am_name
                    assigned_pm_names.add(am_name)

    structural_pm_pool = [pm_names[n] for n in pm_names if n not in assigned_pm_names]
    structural_pm_pool.sort(key=lambda x: x["Seniority"])

    for rig, pos in officer_seats:
        if pm_r[rig][pos] == "" and structural_pm_pool:
            chosen = None
            if rig == "CH-222":
                cands = [p for p in structural_pm_pool if p['Seniority'] <= 4]
                if cands:
                    chosen = cands[0]
            else:
                cert_cands = [p for p in structural_pm_pool if personnel_info.get(clean_display_name(p["Name"]), {}).get('Officer_1', False)]
                if rig == "E-221":
                    pref_cands = [p for p in cert_cands if 6 <= p['Seniority'] <= 8]
                    if pref_cands:
                        chosen = pref_cands[0]
                    elif cert_cands:
                        chosen = cert_cands[0]
                elif rig == "R-221":
                    pref_cands = [p for p in cert_cands if p['Seniority'] == 5]
                    if not pref_cands:
                        pref_cands = [p for p in cert_cands if 9 <= p['Seniority'] <= 10]
                    if pref_cands:
                        chosen = pref_cands[0]
                    elif cert_cands:
                        chosen = cert_cands[0]

            if not chosen:
                structural_pm_pool.sort(key=lambda x: (clean_display_name(x["Name"]) in partial_leaves_list, x["Seniority"]))
                if not structural_pm_pool:
                    break
                chosen = structural_pm_pool[0]

            pm_r[rig][pos] = chosen["Name"]
            structural_pm_pool.remove(chosen)

    open_pm_rot = [(r, pos) for r, pos in rotating_seats if pm_r[r][pos] == ""]
    pm_rot_pool = []
    while structural_pm_pool and len(pm_rot_pool) < len(open_pm_rot):
        pm_rot_pool.append(structural_pm_pool.pop(0))

    pm_rot_assigned, leftover_pm_rot = assign_optimal_rotating_seats(pm_rot_pool, open_pm_rot, stats_local)
    for (rig, pos), chosen_name in pm_rot_assigned.items():
        pm_r[rig][pos] = chosen_name

    if leftover_pm_rot:
        structural_pm_pool = leftover_pm_rot + structural_pm_pool

    for rig, pos in crash_seats:
        if pm_r[rig][pos] == "" and structural_pm_pool:
            pm_r[rig][pos] = structural_pm_pool.pop(0)["Name"]

    return am_r, pm_r


# --- PRESENCE CALCULATION ---
leaves_am = set()
leaves_pm = set()
partial_leaves = []
if not leave_df.empty:
    for _, l in leave_df.iterrows():
        am, pm = parse_time(l['start_time'], l['end_time'], float(l['total_hours']), is_absence=True)
        if am:
            leaves_am.add(l['name'])
        if pm:
            leaves_pm.add(l['name'])
        if float(l['total_hours']) < 24.0:
            partial_leaves.append(l['name'])

trades_am_off, trades_pm_off = set(), set()
trades_am_work, trades_pm_work = {}, {}

if not all_trades_df.empty:
    for _, t in all_trades_df.iterrows():
        am_off, pm_off = parse_time(t['start_time'], t['end_time'], float(t['total_hours']), is_absence=True)
        am_wrk, pm_wrk = parse_time(t['start_time'], t['end_time'], float(t['total_hours']), is_absence=False)
        if t['date_1'] == target_date_str:
            if am_off:
                trades_am_off.add(t['name_1'])
            if am_wrk:
                trades_am_work[t['name_2']] = "Trade"
            if pm_off:
                trades_pm_off.add(t['name_1'])
            if pm_wrk:
                trades_pm_work[t['name_2']] = "Trade"
            if float(t['total_hours']) < 24.0:
                partial_leaves.append(t['name_1'])
        if t['date_2'] == target_date_str:
            if am_off:
                trades_am_off.add(t['name_2'])
            if am_wrk:
                trades_am_work[t['name_1']] = "Trade"
            if pm_off:
                trades_pm_off.add(t['name_2'])
            if pm_wrk:
                trades_pm_work[t['name_1']] = "Trade"
            if float(t['total_hours']) < 24.0:
                partial_leaves.append(t['name_2'])

ot_am_work, ot_pm_work = {}, {}
if not ot_df.empty:
    for _, o in ot_df.iterrows():
        am, pm = parse_time(o['start_time'], o['end_time'], float(o['total_hours']), is_absence=False)
        suffix = "Guard" if "Guard" in str(o['ot_type']) else "OT"
        if am:
            ot_am_work[o['name']] = suffix
        if pm:
            ot_pm_work[o['name']] = suffix

all_working_am, all_working_pm = [], []
current_weekday = target_date.weekday()

for _, person in personnel_df.iterrows():
    name = person['name']
    admin_pm_active = is_admin_pm_active(person, current_weekday)

    if name not in leaves_am and name not in trades_am_off and name not in trades_am_work and name not in ot_am_work:
        all_working_am.append((name, ""))
    if admin_pm_active:
        if name not in leaves_pm and name not in trades_pm_off and name not in trades_pm_work and name not in ot_pm_work:
            all_working_pm.append((name, ""))

for name, suffix in trades_am_work.items():
    all_working_am.append((name, suffix))
for name, suffix in trades_pm_work.items():
    all_working_pm.append((name, suffix))
for name, suffix in ot_am_work.items():
    all_working_am.append((name, suffix))
for name, suffix in ot_pm_work.items():
    all_working_pm.append((name, suffix))

am_crew, admin_am = [], []
for name, suffix in all_working_am:
    sen = personnel_info.get(name, {}).get('Seniority', 50)
    sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
    if suffix in ["Trade", "OT", "Guard"]:
        sched = 'Structural'
    d_name = f"{name} ({suffix})" if suffix else name
    if sched == 'Admin':
        admin_am.append({"Name": d_name, "Seniority": sen})
    else:
        am_crew.append({"Name": d_name, "Seniority": sen})

pm_crew, admin_pm = [], []
for name, suffix in all_working_pm:
    sen = personnel_info.get(name, {}).get('Seniority', 50)
    sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
    if suffix in ["Trade", "OT", "Guard"]:
        sched = 'Structural'
    d_name = f"{name} ({suffix})" if suffix else name
    if sched == 'Admin':
        admin_pm.append({"Name": d_name, "Seniority": sen})
    else:
        pm_crew.append({"Name": d_name, "Seniority": sen})

admin_am.sort(key=lambda x: x["Seniority"], reverse=True)
admin_pm.sort(key=lambda x: x["Seniority"], reverse=True)

while len(am_crew) < 9 and len(admin_am) > 0:
    am_crew.append(admin_am.pop(0))
while len(pm_crew) < 9 and len(admin_pm) > 0:
    pm_crew.append(admin_pm.pop(0))

am_crew.sort(key=lambda x: x["Seniority"])
pm_crew.sort(key=lambda x: x["Seniority"])

all_on_duty = set([p["Name"] for p in am_crew] + [p["Name"] for p in pm_crew])
on_duty_dropdown_list = sorted(list(set([clean_display_name(n) for n in all_on_duty if n != "VACANT"])))

def get_transition_time_for_date_sim(am_full, pm_full, date_str_local, ot_df_local, trades_df_local, leave_df_local):
    am_n = clean_display_name(am_full) if am_full else ""
    pm_n = clean_display_name(pm_full) if pm_full else ""

    t_time = "1500"
    if pm_n and pm_n != "VACANT":
        if not ot_df_local.empty:
            for _, r in ot_df_local[ot_df_local['name'] == pm_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not trades_df_local.empty:
            cond = (
                ((trades_df_local['date_1'] == date_str_local) & (trades_df_local['name_2'] == pm_n)) |
                ((trades_df_local['date_2'] == date_str_local) & (trades_df_local['name_1'] == pm_n))
            )
            for _, r in trades_df_local[cond].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not leave_df_local.empty:
            for _, r in leave_df_local[leave_df_local['name'] == pm_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() == "0700" and float(r['total_hours']) < 24:
                    t_time = r['end_time']

    if am_n and am_n != "VACANT":
        if not leave_df_local.empty:
            for _, r in leave_df_local[leave_df_local['name'] == am_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not trades_df_local.empty:
            cond = (
                ((trades_df_local['date_1'] == date_str_local) & (trades_df_local['name_1'] == am_n)) |
                ((trades_df_local['date_2'] == date_str_local) & (trades_df_local['name_2'] == am_n))
            )
            for _, r in trades_df_local[cond].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not ot_df_local.empty:
            for _, r in ot_df_local[ot_df_local['name'] == am_n].iterrows():
                if str(r['end_time']).replace(":", "").strip() != "0700" and float(r['total_hours']) < 24:
                    t_time = r['end_time']

    t_time = str(t_time).replace(":", "").strip()
    if len(t_time) == 3:
        t_time = "0" + t_time
    return t_time


def simulate_single_date_roster(sim_dt, running_stats):
    """
    Computes the full AM/PM roster and Position_Log records for `sim_dt` using `running_stats`,
    and increments `running_stats` with the resulting seat hours and watches.
    """
    sim_str = sim_dt.strftime("%Y-%m-%d")
    sim_mod = (sim_dt - anchor_date).days % 3
    sim_shift = "A" if sim_mod == 0 else ("B" if sim_mod == 1 else "C")

    l_df = all_leave_df[all_leave_df['target_date'] == sim_str] if not all_leave_df.empty else pd.DataFrame()
    o_df = all_ot_df[all_ot_df['target_date'] == sim_str] if not all_ot_df.empty else pd.DataFrame()
    t_df = all_trades_df[(all_trades_df['date_1'] == sim_str) | (all_trades_df['date_2'] == sim_str)] if not all_trades_df.empty else pd.DataFrame()
    ov_df = all_overrides_df[all_overrides_df['target_date'] == sim_str] if not all_overrides_df.empty else pd.DataFrame()
    w_df = all_aro_watch_df[all_aro_watch_df['target_date'] == sim_str] if not all_aro_watch_df.empty else pd.DataFrame()

    sim_ov_am, sim_ov_pm = build_override_maps(ov_df)
    sim_w_dict = dict(zip(w_df['watch_period'], w_df['name'])) if not w_df.empty else {}

    if sim_dt.weekday() < 5:
        sim_pdf = all_personnel_df[(all_personnel_df['shift'] == sim_shift) | (all_personnel_df['shift'] == 'ADMIN')]
    else:
        sim_pdf = all_personnel_df[(all_personnel_df['shift'] == sim_shift)]

    s_l_am, s_l_pm, s_p_leaves = set(), set(), []
    if not l_df.empty:
        for _, l in l_df.iterrows():
            am, pm = parse_time(l['start_time'], l['end_time'], float(l['total_hours']), is_absence=True)
            if am:
                s_l_am.add(l['name'])
            if pm:
                s_l_pm.add(l['name'])
            if float(l['total_hours']) < 24.0:
                s_p_leaves.append(l['name'])

    s_t_am_off, s_t_pm_off, s_t_am_w, s_t_pm_w = set(), set(), {}, {}
    if not t_df.empty:
        for _, t in t_df.iterrows():
            am_off, pm_off = parse_time(t['start_time'], t['end_time'], float(t['total_hours']), is_absence=True)
            am_wrk, pm_wrk = parse_time(t['start_time'], t['end_time'], float(t['total_hours']), is_absence=False)
            if t['date_1'] == sim_str:
                if am_off:
                    s_t_am_off.add(t['name_1'])
                if am_wrk:
                    s_t_am_w[t['name_2']] = "Trade"
                if pm_off:
                    s_t_pm_off.add(t['name_1'])
                if pm_wrk:
                    s_t_pm_w[t['name_2']] = "Trade"
                if float(t['total_hours']) < 24.0:
                    s_p_leaves.append(t['name_1'])
            if t['date_2'] == sim_str:
                if am_off:
                    s_t_am_off.add(t['name_2'])
                if am_wrk:
                    s_t_am_w[t['name_1']] = "Trade"
                if pm_off:
                    s_t_pm_off.add(t['name_2'])
                if pm_wrk:
                    s_t_pm_w[t['name_1']] = "Trade"
                if float(t['total_hours']) < 24.0:
                    s_p_leaves.append(t['name_2'])

    s_o_am, s_o_pm = {}, {}
    if not o_df.empty:
        for _, o in o_df.iterrows():
            am, pm = parse_time(o['start_time'], o['end_time'], float(o['total_hours']), is_absence=False)
            suffix = "Guard" if "Guard" in str(o['ot_type']) else "OT"
            if am:
                s_o_am[o['name']] = suffix
            if pm:
                s_o_pm[o['name']] = suffix

    s_aw_am, s_aw_pm = [], []
    for _, person in sim_pdf.iterrows():
        name = person['name']
        admin_pm_active = is_admin_pm_active(person, sim_dt.weekday())
        if name not in s_l_am and name not in s_t_am_off and name not in s_t_am_w and name not in s_o_am:
            s_aw_am.append((name, ""))
        if admin_pm_active:
            if name not in s_l_pm and name not in s_t_pm_off and name not in s_t_pm_w and name not in s_o_pm:
                s_aw_pm.append((name, ""))

    for name, suffix in s_t_am_w.items():
        s_aw_am.append((name, suffix))
    for name, suffix in s_t_pm_w.items():
        s_aw_pm.append((name, suffix))
    for name, suffix in s_o_am.items():
        s_aw_am.append((name, suffix))
    for name, suffix in s_o_pm.items():
        s_aw_pm.append((name, suffix))

    s_am_c, s_admin_am = [], []
    for name, suffix in s_aw_am:
        sen = personnel_info.get(name, {}).get('Seniority', 50)
        sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
        if suffix in ["Trade", "OT", "Guard"]:
            sched = 'Structural'
        d_name = f"{name} ({suffix})" if suffix else name
        if sched == 'Admin':
            s_admin_am.append({"Name": d_name, "Seniority": sen})
        else:
            s_am_c.append({"Name": d_name, "Seniority": sen})

    s_pm_c, s_admin_pm = [], []
    for name, suffix in s_aw_pm:
        sen = personnel_info.get(name, {}).get('Seniority', 50)
        sched = personnel_info.get(name, {}).get('Schedule', 'Structural')
        if suffix in ["Trade", "OT", "Guard"]:
            sched = 'Structural'
        d_name = f"{name} ({suffix})" if suffix else name
        if sched == 'Admin':
            s_admin_pm.append({"Name": d_name, "Seniority": sen})
        else:
            s_pm_c.append({"Name": d_name, "Seniority": sen})

    s_admin_am.sort(key=lambda x: x["Seniority"], reverse=True)
    s_admin_pm.sort(key=lambda x: x["Seniority"], reverse=True)

    while len(s_am_c) < 9 and len(s_admin_am) > 0:
        s_am_c.append(s_admin_am.pop(0))
    while len(s_pm_c) < 9 and len(s_admin_pm) > 0:
        s_pm_c.append(s_admin_pm.pop(0))

    s_am_c.sort(key=lambda x: x["Seniority"])
    s_pm_c.sort(key=lambda x: x["Seniority"])

    s_am_r_raw, s_pm_r_raw = build_roster_assignment(s_am_c, s_pm_c, sim_ov_am, sim_ov_pm, running_stats, s_p_leaves)
    s_am_r = apply_cross_staffing_and_watches(s_am_r_raw, sim_w_dict, oos_list)
    s_pm_r = apply_cross_staffing_and_watches(s_pm_r_raw, sim_w_dict, oos_list)

    records = []
    for rig, seats in s_am_r.items():
        for pos, am_name in seats.items():
            pm_name = s_pm_r[rig][pos]
            seat_name = f"{rig} {pos}"
            if rig == "ARO" and pos != "0700-1200 (Float)":
                continue
            if rig in ["Crash-4", "Crash-1"] and pos == "Crew Chief":
                continue
            if rig == "Tanker":
                continue

            if am_name and am_name == pm_name:
                clean_name = clean_display_name(am_name)
                if clean_name != "VACANT":
                    records.append((sim_str, clean_name, seat_name, 24.0))
                    if clean_name not in running_stats:
                        running_stats[clean_name] = {}
                    running_stats[clean_name][seat_name] = running_stats[clean_name].get(seat_name, 0.0) + 24.0
            else:
                t_time = get_transition_time_for_date_sim(am_name, pm_name, sim_str, o_df, t_df, l_df)
                am_hrs = time_to_shift_hr(t_time)
                pm_hrs = 24.0 - am_hrs
                if am_name and am_name != "VACANT":
                    clean_am = clean_display_name(am_name)
                    records.append((sim_str, clean_am, seat_name, round(am_hrs, 1)))
                    if clean_am not in running_stats:
                        running_stats[clean_am] = {}
                    running_stats[clean_am][seat_name] = running_stats[clean_am].get(seat_name, 0.0) + round(am_hrs, 1)
                if pm_name and pm_name != "VACANT":
                    clean_pm = clean_display_name(pm_name)
                    records.append((sim_str, clean_pm, seat_name, round(pm_hrs, 1)))
                    if clean_pm not in running_stats:
                        running_stats[clean_pm] = {}
                    running_stats[clean_pm][seat_name] = running_stats[clean_pm].get(seat_name, 0.0) + round(pm_hrs, 1)

    am_float = s_am_r["ARO"]["0700-1200 (Float)"]
    if am_float and am_float != "VACANT":
        clean_am = clean_display_name(am_float)
        records.append((sim_str, clean_am, "Watch: 0700-1200", 1.0))
        if clean_am not in running_stats:
            running_stats[clean_am] = {}
        running_stats[clean_am]["Watch: 0700-1200"] = running_stats[clean_am].get("Watch: 0700-1200", 0.0) + 1.0

    for wp in ["1200-1700", "1700-2200", "2200-0600", "0600-0700"]:
        if wp in sim_w_dict and sim_w_dict[wp] not in ["", "--"]:
            clean_w = clean_display_name(sim_w_dict[wp])
            records.append((sim_str, clean_w, f"Watch: {wp}", 1.0))
            if clean_w not in running_stats:
                running_stats[clean_w] = {}
            running_stats[clean_w][f"Watch: {wp}"] = running_stats[clean_w].get(f"Watch: {wp}", 0.0) + 1.0

    return s_am_r, s_pm_r, records


# --- AUTOMATIC PAST-SHIFT COMMIT ENGINE (WHEN DATE PASSES FROM TODAY TO YESTERDAY) ---
logged_past_dates = set(all_pos_log_df['target_date'].unique().tolist()) if not all_pos_log_df.empty else set()
yesterday_dt = station_today - datetime.timedelta(days=1)

if logged_past_dates:
    try:
        max_logged_dt = datetime.datetime.strptime(max(logged_past_dates), "%Y-%m-%d").date()
    except Exception:
        max_logged_dt = yesterday_dt - datetime.timedelta(days=1)
    start_catchup_dt = min(max_logged_dt + datetime.timedelta(days=1), yesterday_dt)
else:
    start_catchup_dt = yesterday_dt

missing_past_dates = []
cur_catchup = start_catchup_dt
while cur_catchup <= yesterday_dt:
    c_str = cur_catchup.strftime("%Y-%m-%d")
    if c_str not in logged_past_dates:
        missing_past_dates.append(cur_catchup)
    cur_catchup += datetime.timedelta(days=1)

if missing_past_dates:
    auto_commit_records = []
    for m_dt in missing_past_dates:
        try:
            _, _, m_recs = simulate_single_date_roster(m_dt, stat_lookup)
            auto_commit_records.extend(m_recs)
        except Exception:
            pass
    if auto_commit_records:
        conn_auto = get_db_connection()
        try:
            with conn_auto.cursor() as c_auto:
                for m_dt in missing_past_dates:
                    c_auto.execute("DELETE FROM Position_Log WHERE Target_Date = %s", (m_dt.strftime("%Y-%m-%d"),))
                c_auto.executemany(
                    "INSERT INTO Position_Log (Target_Date, Name, Position, Hours) VALUES (%s, %s, %s, %s)",
                    auto_commit_records
                )
            conn_auto.commit()
            # Refresh stats_df so Seat Statistics tab immediately reflects auto-committed shifts
            stats_df = pd.read_sql(
                "SELECT Name, Position, SUM(Hours) as Total_Hours FROM Position_Log WHERE Target_Date < %s GROUP BY Name, Position",
                conn_auto,
                params=(station_today_str,)
            )
            stats_df.columns = [c.lower() for c in stats_df.columns] if not stats_df.empty else ["name", "position", "total_hours"]
        finally:
            conn_auto.close()


# --- DYNAMIC ROLLING FORECAST FOR TODAY & FUTURE DATES ---
effective_stat_lookup = copy.deepcopy(stat_lookup)
if target_date > station_today:
    # Step chronologically from station_today through target_date - 1 day
    # so every intermediate shift's projected hours are counted when forecasting future shifts.
    max_forecast_horizon = station_today + datetime.timedelta(days=180)
    sim_cursor = station_today
    sim_limit = min(target_date, max_forecast_horizon)
    while sim_cursor < sim_limit:
        try:
            simulate_single_date_roster(sim_cursor, effective_stat_lookup)
        except Exception:
            pass
        sim_cursor += datetime.timedelta(days=1)
elif target_date < station_today:
    # When viewing or editing a past date (< station_today), exclude that date's own logged hours
    # before computing its roster so it doesn't double-count itself, and sync Position_Log if edited.
    past_other_df = all_pos_log_df[all_pos_log_df['target_date'] < target_date_str] if not all_pos_log_df.empty else pd.DataFrame()
    effective_stat_lookup = {}
    if not past_other_df.empty:
        grp = past_other_df.groupby(['name', 'position'])['hours'].sum().reset_index()
        for _, r in grp.iterrows():
            cn = r['name']
            if cn not in effective_stat_lookup:
                effective_stat_lookup[cn] = {}
            effective_stat_lookup[cn][r['position']] = float(r['hours'])

roster_generation_error = False
try:
    am_rigs_raw, pm_rigs_raw = build_roster_assignment(am_crew, pm_crew, seat_to_override_am, seat_to_override_pm, effective_stat_lookup, partial_leaves)
    am_roster = apply_cross_staffing_and_watches(am_rigs_raw, db_watches, oos_list)
    pm_roster = apply_cross_staffing_and_watches(pm_rigs_raw, db_watches, oos_list)
except IndexError:
    roster_generation_error = True

# If viewing/editing a completed past shift (< station_today), automatically keep its Position_Log entry synced
if not roster_generation_error and target_date < station_today:
    _, _, synced_past_records = simulate_single_date_roster(target_date, copy.deepcopy(effective_stat_lookup))
    curr_logged_for_target = all_pos_log_df[all_pos_log_df['target_date'] == target_date_str] if not all_pos_log_df.empty else pd.DataFrame()
    old_tuples = sorted([(r['target_date'], r['name'], r['position'], round(float(r['hours']), 1)) for _, r in curr_logged_for_target.iterrows()])
    new_tuples = sorted([(r[0], r[1], r[2], round(float(r[3]), 1)) for r in synced_past_records])
    if old_tuples != new_tuples:
        conn_sync = get_db_connection()
        try:
            with conn_sync.cursor() as c_sync:
                c_sync.execute("DELETE FROM Position_Log WHERE Target_Date = %s", (target_date_str,))
                if synced_past_records:
                    c_sync.executemany(
                        "INSERT INTO Position_Log (Target_Date, Name, Position, Hours) VALUES (%s, %s, %s, %s)",
                        synced_past_records
                    )
            conn_sync.commit()
            stats_df = pd.read_sql(
                "SELECT Name, Position, SUM(Hours) as Total_Hours FROM Position_Log WHERE Target_Date < %s GROUP BY Name, Position",
                conn_sync,
                params=(station_today_str,)
            )
            stats_df.columns = [c.lower() for c in stats_df.columns] if not stats_df.empty else ["name", "position", "total_hours"]
        finally:
            conn_sync.close()


# --- OT Shortage Calculation Engine (Skyline Decomposition) ---
headcount = [0] * 240
for _, p in all_personnel_df.iterrows():
    name = p['name']
    sched = str(p.get('schedule', '')).strip()
    p_shift = str(p.get('shift', '')).strip()
    is_core = bool(p.get('core_manning', True))
    is_admin_member = (sched.lower() == 'admin' or p_shift.upper() == 'ADMIN')

    # Recruits on structural shifts with Core_Manning=False do not count toward the 9-person minimum,
    # whereas Admin personnel (e.g., Garver 0700-1500 or on their 24h day) step in to fill manning on weekdays.
    if not is_core and not is_admin_member:
        continue

    presence = [0] * 240
    if p_shift == db_shift:
        presence = [1] * 240
    elif is_admin_member and target_date.weekday() < 5:
        if is_admin_pm_active(p, target_date.weekday()):
            presence = [1] * 240
        else:
            for i in range(80):
                presence[i] = 1

    p_leaves = all_leave_df[(all_leave_df['target_date'] == target_date_str) & (all_leave_df['name'] == name)]
    for _, l in p_leaves.iterrows():
        s_idx = int(round(time_to_shift_hr(l['start_time']) * 10))
        e_idx = int(round(time_to_shift_hr(l['end_time']) * 10))
        if float(l['total_hours']) >= 24:
            s_idx, e_idx = 0, 240
        elif e_idx <= s_idx:
            e_idx += 240
        for i in range(max(0, s_idx), min(240, e_idx)):
            presence[i] = 0

    p_trades_off_1 = all_trades_df[(all_trades_df['date_1'] == target_date_str) & (all_trades_df['name_1'] == name)]
    for _, t in p_trades_off_1.iterrows():
        s_idx = int(round(time_to_shift_hr(t['start_time']) * 10))
        e_idx = int(round(time_to_shift_hr(t['end_time']) * 10))
        if float(t['total_hours']) >= 24:
            s_idx, e_idx = 0, 240
        elif e_idx <= s_idx:
            e_idx += 240
        for i in range(max(0, s_idx), min(240, e_idx)):
            presence[i] = 0

    p_trades_off_2 = all_trades_df[(all_trades_df['date_2'] == target_date_str) & (all_trades_df['name_2'] == name)]
    for _, t in p_trades_off_2.iterrows():
        s_idx = int(round(time_to_shift_hr(t['start_time']) * 10))
        e_idx = int(round(time_to_shift_hr(t['end_time']) * 10))
        if float(t['total_hours']) >= 24:
            s_idx, e_idx = 0, 240
        elif e_idx <= s_idx:
            e_idx += 240
        for i in range(max(0, s_idx), min(240, e_idx)):
            presence[i] = 0

    for i in range(240):
        headcount[i] += presence[i]

for _, t in all_trades_df[(all_trades_df['date_1'] == target_date_str)].iterrows():
    s_idx = int(round(time_to_shift_hr(t['start_time']) * 10))
    e_idx = int(round(time_to_shift_hr(t['end_time']) * 10))
    if float(t['total_hours']) >= 24:
        s_idx, e_idx = 0, 240
    elif e_idx <= s_idx:
        e_idx += 240
    for i in range(max(0, s_idx), min(240, e_idx)):
        headcount[i] += 1

for _, t in all_trades_df[(all_trades_df['date_2'] == target_date_str)].iterrows():
    s_idx = int(round(time_to_shift_hr(t['start_time']) * 10))
    e_idx = int(round(time_to_shift_hr(t['end_time']) * 10))
    if float(t['total_hours']) >= 24:
        s_idx, e_idx = 0, 240
    elif e_idx <= s_idx:
        e_idx += 240
    for i in range(max(0, s_idx), min(240, e_idx)):
        headcount[i] += 1

for _, o in all_ot_df[(all_ot_df['target_date'] == target_date_str)].iterrows():
    s_idx = int(round(time_to_shift_hr(o['start_time']) * 10))
    e_idx = int(round(time_to_shift_hr(o['end_time']) * 10))
    if float(o['total_hours']) >= 24:
        s_idx, e_idx = 0, 240
    elif e_idx <= s_idx:
        e_idx += 240
    for i in range(max(0, s_idx), min(240, e_idx)):
        headcount[i] += 1

shortage = [0] * 240
for i in range(240):
    shortage[i] = max(0, 9 - headcount[i])

ot_shifts = []
while max(shortage) > 0:
    start_idx = -1
    for i in range(240):
        if shortage[i] > 0:
            start_idx = i
            break

    end_idx = start_idx
    while end_idx < 240 and shortage[end_idx] > 0:
        end_idx += 1

    for i in range(start_idx, end_idx):
        shortage[i] -= 1

    hrs = (end_idx - start_idx) * 0.1
    ot_shifts.append((hrs, start_idx, end_idx))


def format_shift_time(idx):
    decimal_hr = idx * 0.1
    real_hr = decimal_hr + 7.0
    if real_hr >= 24.0:
        real_hr -= 24.0
    h = int(real_hr)
    m = int(round((real_hr - h) * 60))
    if m == 60:
        h += 1
        m = 0
    if h == 24:
        h = 0
    return f"{h:02d}{m:02d}"


shift_counts = {}
for hrs, s_idx, e_idx in ot_shifts:
    hrs_int = int(hrs) if hrs % 1 == 0 else round(hrs, 1)
    s_time = format_shift_time(s_idx)
    e_time = format_shift_time(e_idx)

    key = (hrs_int, s_time, e_time)
    shift_counts[key] = shift_counts.get(key, 0) + 1

ot_sub_list = []
total_ot_needed_count = 0
sorted_keys = sorted(shift_counts.keys(), key=lambda x: (-x[0], x[1]))

for k in sorted_keys:
    hrs_int, s_time, e_time = k
    count = shift_counts[k]
    total_ot_needed_count += count
    for _ in range(count):
        ot_sub_list.append(f"⚠️ {s_time}-{e_time} ({hrs_int}h)")

ot_needed_disp = " + ".join([f"{shift_counts[k]}x {k[0]}h" for k in sorted_keys]) if shift_counts else "0 Shifts"

scheduled_ot_list = []
if not ot_df.empty:
    for _, o in ot_df.iterrows():
        hrs = int(o['total_hours']) if o['total_hours'] % 1 == 0 else o['total_hours']
        clean_name = clean_display_name(o['name'])
        scheduled_ot_list.append(f"✅ {clean_name}: {o['start_time']}-{o['end_time']} ({hrs}h)")

scheduled_leave_list = []
if not leave_df.empty:
    for _, l in leave_df.iterrows():
        hrs = int(l['total_hours']) if l['total_hours'] % 1 == 0 else l['total_hours']
        scheduled_leave_list.append(f"{l['name']} ({hrs}h)")
scheduled_leave_str = ", ".join(scheduled_leave_list) if scheduled_leave_list else "None"
scheduled_ot_str = ", ".join([s.split(":")[0].replace("✅ ", "") for s in scheduled_ot_list]) if scheduled_ot_list else "None"

total_on_duty_am = len(am_crew)
total_on_duty_pm = len(pm_crew)

if total_on_duty_am == total_on_duty_pm:
    manning_disp = str(total_on_duty_am)
else:
    manning_disp = f"{total_on_duty_am} ➔ {total_on_duty_pm}"

if not roster_generation_error:
    excluded_watches = {
        am_roster["CH-222"]["AC"], pm_roster["CH-222"]["AC"],
        am_roster["E-221"]["Station Captain"], pm_roster["E-221"]["Station Captain"],
        am_roster["ARO"]["0700-1200 (Float)"], pm_roster["ARO"]["0700-1200 (Float)"]
    }
    eligible_aro_names = sorted([name for name in all_on_duty if name not in excluded_watches and name not in ["", "VACANT"]])
else:
    eligible_aro_names = []


def get_transition_time_for_date(am_full, pm_full, date_str_local, ot_df_local, trades_df_local, leave_df_local):
    am_n = clean_display_name(am_full) if am_full else ""
    pm_n = clean_display_name(pm_full) if pm_full else ""

    t_time = "1500"
    if pm_n and pm_n != "VACANT":
        if not ot_df_local.empty:
            for _, r in ot_df_local[ot_df_local['name'] == pm_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not trades_df_local.empty:
            cond = (
                ((trades_df_local['date_1'] == date_str_local) & (trades_df_local['name_2'] == pm_n)) |
                ((trades_df_local['date_2'] == date_str_local) & (trades_df_local['name_1'] == pm_n))
            )
            for _, r in trades_df_local[cond].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not leave_df_local.empty:
            for _, r in leave_df_local[leave_df_local['name'] == pm_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() == "0700" and float(r['total_hours']) < 24:
                    t_time = r['end_time']

    if am_n and am_n != "VACANT":
        if not leave_df_local.empty:
            for _, r in leave_df_local[leave_df_local['name'] == am_n].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not trades_df_local.empty:
            cond = (
                ((trades_df_local['date_1'] == date_str_local) & (trades_df_local['name_1'] == am_n)) |
                ((trades_df_local['date_2'] == date_str_local) & (trades_df_local['name_2'] == am_n))
            )
            for _, r in trades_df_local[cond].iterrows():
                if str(r['start_time']).replace(":", "").strip() != "0700":
                    t_time = r['start_time']
        if not ot_df_local.empty:
            for _, r in ot_df_local[ot_df_local['name'] == am_n].iterrows():
                if str(r['end_time']).replace(":", "").strip() != "0700" and float(r['total_hours']) < 24:
                    t_time = r['end_time']

    t_time = str(t_time).replace(":", "").strip()
    if len(t_time) == 3:
        t_time = "0" + t_time
    return t_time


def get_transition_time(am_full, pm_full):
    return get_transition_time_for_date(am_full, pm_full, target_date_str, ot_df, all_trades_df, leave_df)


day_of_week = target_date.strftime("%A")
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
    if roster_generation_error:
        return "VACANT"
    if rig == "Multiple":
        found = []
        for s in seat_def:
            r, p = s.split(" ", 1)
            n = am_roster.get(r, {}).get(p, "")
            if not n and r.startswith("Crash"):
                n = am_roster.get(r, {}).get("Crew Chief" if p == "Driver" else "Driver", "")
            if n and n != "VACANT":
                found.append(n)
        return ", ".join(found) if found else "VACANT"
    else:
        n = am_roster.get(rig, {}).get(seat_def, "")
        if not n and rig.startswith("Crash"):
            n = am_roster.get(rig, {}).get("Crew Chief" if seat_def == "Driver" else "Driver", "")
        return n if n and n != "VACANT" else "VACANT"


station_details_html = ""
if day_of_week in daily_details:
    for d in daily_details[day_of_week]:
        assigned_names = resolve_detail_names(d["Rig"], d["Seat"])
        station_details_html += f"<li><b>{d['Task']}</b> - Assigned: {d['Pos_Name']} ({assigned_names})</li>"
else:
    station_details_html = "<li>No specific station deep-cleaning details are officially assigned.</li>"

events_html = ""
day_events = events_df[events_df['target_date'] == target_date_str] if not events_df.empty else pd.DataFrame()
if not day_events.empty:
    for _, ev in day_events.iterrows():
        inst = f" (Instructor: {ev['instructor']})" if pd.notna(ev.get('instructor')) and ev.get('instructor') not in ["", "-- None --"] else ""
        events_html += f"<li><b>{ev['event_type']}:</b> {ev['description']} | {ev['start_time']}-{ev['end_time']}{inst}</li>"
else:
    events_html = "<li>No events logged for this date.</li>"


# --- 4. STREAMLIT VISUALS & PAGES ---
if roster_generation_error:
    st.title("🚒 Rickenbacker Fire Department Management")
    st.error("🚨 **Assignment Conflict Detected:** You have locked too many positions manually. The automated algorithm has run out of available personnel and cannot complete the roster. Please delete a manual override below to free up a seat.")
    st.subheader("🔧 Active Overrides")
    if overrides_df.empty:
        st.info("No active overrides.")
    else:
        for _, ov in overrides_df.iterrows():
            with st.container(border=True):
                cols = st.columns([4, 1])
                per_tag = ov.get('period', 'FULL')
                per_disp = "Full Shift" if per_tag == "FULL" else ("0700-1500 (AM)" if per_tag == "AM" else "1500-0700 (PM)")
                cols[0].markdown(f"**{ov['name']}** ➡️ {ov['seat']} *({per_disp})*")
                if cols[1].button("🗑 Delete to Unblock", key=f"err_del_ov_{ov['id']}", use_container_width=True, type="primary"):
                    conn = get_db_connection()
                    try:
                        conn.cursor().execute("DELETE FROM Manual_Overrides WHERE id = %s", (ov['id'],))
                        conn.commit()
                    finally:
                        conn.close()
                    st.rerun()
    st.stop()


st.title("🚒 Rickenbacker Fire Department Management")

# Dynamic Year Options for Calendars
current_yr = get_station_today().year
dynamic_years = sorted(list(set(range(min(2025, current_yr - 1), max(2028, current_yr + 4))).union({target_date.year})))

# ==========================================
# PAGE 1: DAILY ROSTER
# ==========================================
if selected_page == "📋 Daily Roster":
    st.markdown(f"<h2 style='color:{top_shift_color}; margin-top: -15px;'>{selected_shift}</h2>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total On-Duty (0700 / 1500)", manning_disp)
    with col2:
        st.metric("Overtime Needed", f"{total_ot_needed_count} Shifts")
        if total_ot_needed_count > 0:
            st.markdown(f"<div style='font-size: 0.85em; color: #d32f2f; line-height: 1.4; margin-top: -10px;'>{'<br>'.join(ot_sub_list)}</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div style='font-size: 0.85em; color: #2e7d32; margin-top: -10px;'>Fully Staffed</div>", unsafe_allow_html=True)

    with col3:
        st.metric("Scheduled OT", f"{len(ot_df)} Filled")
        if not ot_df.empty:
            st.markdown(f"<div style='font-size: 0.85em; color: #1565C0; line-height: 1.4; margin-top: -10px;'>{'<br>'.join(scheduled_ot_list)}</div>", unsafe_allow_html=True)

    st.markdown("<hr style='margin: 0.5em 0;'>", unsafe_allow_html=True)

    # --- PRINT LAYOUT GENERATOR ---
    def render_print_rig(rig_name):
        status = app_status_dict.get(rig_name, "In Service")
        is_oos = status != "In Service"
        badge = " <span style='font-size: 10px; background: #d32f2f; color: white; padding: 1px 4px; border-radius: 3px; vertical-align: middle;'>OOS</span>" if is_oos else ""

        html = f"<div class='box'><h4>{rig_name}{badge}</h4>"
        for position in am_roster[rig_name]:
            am_n = am_roster[rig_name][position] if am_roster[rig_name][position] else "VACANT"
            pm_n = pm_roster[rig_name][position] if pm_roster[rig_name][position] else "VACANT"
            if rig_name == "ARO" and position != "0700-1200 (Float)":
                html += f"<div class='seat'><b>{position}:</b> <span>{am_n}</span></div>"
            elif am_n == pm_n:
                html += f"<div class='seat'><b>{position}:</b> <span>{am_n}</span></div>"
            else:
                sw_t = get_transition_time(am_n, pm_n)
                html += f"<div class='seat'><b>{position}:</b> <span>{am_n} &raquo; {pm_n} ({sw_t})</span></div>"
        html += "</div>"
        return html

    col3_html = render_print_rig("Crash-1") + render_print_rig("Crash-3") + render_print_rig("Crash-4") + render_print_rig("Crash-8")
    for tr in temp_rigs:
        col3_html += render_print_rig(tr)

    print_html = f"""
    <html><head><style>
        @media print {{ @page {{ size: landscape; margin: 0.4in; }} body {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }} }}
        body {{ font-family: Arial, sans-serif; font-size: 13px; margin: 0; padding: 0; color: #000; }}
        h2 {{ text-align: center; margin-bottom: 5px; margin-top: 0; font-size: 22px; color: #000; }}
        h3 {{ margin-bottom: 5px; border-bottom: 2px solid #000; padding-bottom: 3px; font-size: 16px; color: #000; }}
        .summary {{ display: flex; justify-content: space-around; margin-bottom: 15px; font-weight: bold; font-size: 14px; background: #e0e0e0; padding: 8px; border-radius: 4px; border: 1px solid #000; }}
        .trucks {{ display: flex; justify-content: space-between; gap: 10px; margin-bottom: 15px; }}
        .col {{ width: 33%; }}
        .box {{ border: 1px solid #000; padding: 6px; border-radius: 4px; margin-bottom: 8px; page-break-inside: avoid; background: #fff; }}
        .box h4 {{ margin: 0 0 6px 0; color: #000; font-size: 15px; border-bottom: 1px solid #ccc; padding-bottom: 2px; }}
        .seat {{ display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 12px; }}
        .bottom-section {{ display: flex; gap: 15px; page-break-inside: avoid; }}
        .details {{ width: 50%; border: 1px solid #000; padding: 8px; border-radius: 4px; }}
        ul {{ margin-top: 5px; padding-left: 20px; margin-bottom: 0; }}
        li {{ margin-bottom: 3px; }}
    </style></head>
    <body onload="window.print()">
        <h2>Rickenbacker Fire Dept Daily Roster - {target_date_str} ({selected_shift})</h2>
        <div class="summary">
            <div>Total On-Duty: {manning_disp}</div>
            <div>OT Needed: {ot_needed_disp}</div>
            <div>Scheduled OT: {scheduled_ot_str}</div>
            <div>Scheduled Leave: {scheduled_leave_str}</div>
        </div>
        <div class="trucks">
            <div class="col">{render_print_rig("CH-222")}{render_print_rig("R-221")}{render_print_rig("Tanker")}</div>
            <div class="col">{render_print_rig("E-221")}{render_print_rig("ARO")}</div>
            <div class="col">{col3_html}</div>
        </div>
        <div class="bottom-section">
            <div class="details"><h3>🧹 Station Details</h3><ul>{station_details_html}</ul></div>
            <div class="details"><h3>📆 Events & Training</h3><ul>{events_html}</ul></div>
        </div>
    </body></html>
    """

    btn_col1, btn_col2 = st.columns([8, 2])
    with btn_col2:
        st.download_button(
            "🖨️ Print Daily Roster",
            data=print_html,
            file_name=f"RFD_Roster_{target_date_str}.html",
            mime="text/html",
            use_container_width=True
        )

    rig_col1, rig_col2, rig_col3 = st.columns(3)
    color_map = {"error": "#d32f2f", "success": "#2e7d32", "info": "#0288d1", "warning": "#ed6c02"}

    def display_rig(rig_name, box_type):
        status = app_status_dict.get(rig_name, "In Service")
        is_oos = status != "In Service"
        badge = f" <span style='font-size: 0.6em; background: #d32f2f; color: white; padding: 2px 6px; border-radius: 4px; vertical-align: middle; margin-left: 8px;'>{status}</span>" if is_oos else ""

        with st.container(border=True):
            st.markdown(f"<h4 style='color: {color_map.get(box_type, '#0288d1')}; margin-top: 0; padding-bottom: 0;'>{rig_name}{badge}</h4>", unsafe_allow_html=True)
            for position in am_roster[rig_name]:
                am_name, pm_name = am_roster[rig_name][position], pm_roster[rig_name][position]
                if am_name == "":
                    am_name = "VACANT"
                if pm_name == "":
                    pm_name = "VACANT"

                if rig_name == "ARO" and position != "0700-1200 (Float)":
                    disp_text = f"**{position}:** {am_name}"
                elif am_name == pm_name:
                    disp_text = f"**{position}:** {am_name}"
                else:
                    swap_time = get_transition_time(am_name, pm_name)
                    disp_text = f"**{position}:** {am_name}  >>  {pm_name} ({swap_time})"

                seat_name = f"{rig_name} | {position}"
                locked_am = seat_to_override_am.get(seat_name)
                locked_pm = seat_to_override_pm.get(seat_name)
                is_locked = (locked_am is not None) or (locked_pm is not None)

                if locked_am is not None and locked_pm is not None:
                    lock_prefix = "🔒 "
                elif locked_am is not None:
                    lock_prefix = "🔒(AM) "
                elif locked_pm is not None:
                    lock_prefix = "🔒(PM) "
                else:
                    lock_prefix = ""

                if is_locked:
                    disp_text = lock_prefix + disp_text

                if rig_name == "ARO" and position != "0700-1200 (Float)":
                    st.markdown(disp_text)
                    continue

                if is_admin:
                    c1, c2 = st.columns([7, 2])
                    c1.markdown(disp_text)
                    pop_icon = " 🔒 " if is_locked else " ⚙️ "
                    with c2.popover(pop_icon, use_container_width=True):
                        options = ["-- Auto --", "VACANT"] + on_duty_dropdown_list
                        for lb in [locked_am, locked_pm]:
                            if lb and lb not in options:
                                options.append(lb)

                        new_am_val = st.selectbox(
                            "0700-1500 (AM) Lock:",
                            options,
                            index=options.index(locked_am) if locked_am in options else 0,
                            key=f"ov_am_{rig_name}_{position}"
                        )
                        new_pm_val = st.selectbox(
                            "1500-0700 (PM) Lock:",
                            options,
                            index=options.index(locked_pm) if locked_pm in options else 0,
                            key=f"ov_pm_{rig_name}_{position}"
                        )
                        if st.button("Save Lock(s)", key=f"btn_{rig_name}_{position}", type="primary", use_container_width=True):
                            conn = get_db_connection()
                            try:
                                c = conn.cursor()
                                # Clear existing locks on this seat
                                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s", (target_date_str, seat_name))

                                # Helper to clear conflicting locks for a person in a given period
                                def clear_person_period_lock(cur, p_name, target_per):
                                    if p_name in ["-- Auto --", "VACANT"]:
                                        return
                                    if target_per == "FULL":
                                        cur.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s", (target_date_str, p_name))
                                    elif target_per == "AM":
                                        cur.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s AND Period = 'AM'", (target_date_str, p_name))
                                        cur.execute("UPDATE Manual_Overrides SET Period = 'PM' WHERE Target_Date = %s AND Name = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, p_name))
                                    elif target_per == "PM":
                                        cur.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s AND Period = 'PM'", (target_date_str, p_name))
                                        cur.execute("UPDATE Manual_Overrides SET Period = 'AM' WHERE Target_Date = %s AND Name = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, p_name))

                                if new_am_val != "-- Auto --" and new_am_val == new_pm_val:
                                    clear_person_period_lock(c, new_am_val, "FULL")
                                    c.execute(
                                        "INSERT INTO Manual_Overrides (Target_Date, Name, Seat, Period) VALUES (%s, %s, %s, 'FULL')",
                                        (target_date_str, new_am_val, seat_name)
                                    )
                                else:
                                    if new_am_val != "-- Auto --":
                                        clear_person_period_lock(c, new_am_val, "AM")
                                        c.execute(
                                            "INSERT INTO Manual_Overrides (Target_Date, Name, Seat, Period) VALUES (%s, %s, %s, 'AM')",
                                            (target_date_str, new_am_val, seat_name)
                                        )
                                    if new_pm_val != "-- Auto --":
                                        clear_person_period_lock(c, new_pm_val, "PM")
                                        c.execute(
                                            "INSERT INTO Manual_Overrides (Target_Date, Name, Seat, Period) VALUES (%s, %s, %s, 'PM')",
                                            (target_date_str, new_pm_val, seat_name)
                                        )
                                conn.commit()
                            finally:
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

                        def get_idx(val):
                            return watch_options.index(val) if val in watch_options else 0

                        w1 = st.selectbox("1200-1700", watch_options, index=get_idx(db_watches.get("1200-1700")))
                        w2 = st.selectbox("1700-2200", watch_options, index=get_idx(db_watches.get("1700-2200")))
                        w3 = st.selectbox("2200-0600", watch_options, index=get_idx(db_watches.get("2200-0600")))
                        w4 = st.selectbox("0600-0700", watch_options, index=get_idx(db_watches.get("0600-0700")))
                        if st.form_submit_button("Save Watches"):
                            conn = get_db_connection()
                            try:
                                c = conn.cursor()
                                c.execute("DELETE FROM ARO_Watch WHERE Target_Date = %s", (target_date_str,))
                                inserts = [
                                    (target_date_str, "1200-1700", w1),
                                    (target_date_str, "1700-2200", w2),
                                    (target_date_str, "2200-0600", w3),
                                    (target_date_str, "0600-0700", w4)
                                ]
                                inserts = [w for w in inserts if w[2] != "--"]
                                if inserts:
                                    c.executemany("INSERT INTO ARO_Watch (Target_Date, Watch_Period, Name) VALUES (%s, %s, %s)", inserts)
                                conn.commit()
                            finally:
                                conn.close()
                            st.rerun()

    with rig_col1:
        display_rig("CH-222", "error")
        display_rig("R-221", "info")
        display_rig("Tanker", "info")
    with rig_col2:
        display_rig("E-221", "success")
        display_rig("ARO", "error")
    with rig_col3:
        display_rig("Crash-1", "warning")
        display_rig("Crash-3", "warning")
        display_rig("Crash-4", "warning")
        display_rig("Crash-8", "warning")
        for tr in temp_rigs:
            display_rig(tr, "info")

    st.divider()

    if is_admin:
        st.markdown("### 🛠️ Shift Adjustments & Seat Locks")
        if target_date < station_today:
            st.caption("ℹ️ **Historical Shift:** This date has already passed and is automatically committed to Seat Statistics. Any overrides or edits made here automatically update history.")
        elif target_date == station_today:
            st.caption("ℹ️ **Active Shift:** Today's roster will automatically commit to Seat Statistics when the date rolls over to tomorrow.")
        else:
            st.caption("ℹ️ **Dynamic Rolling Forecast:** Future shifts automatically account for projected seat hours from Today and all intermediate shifts.")

        rm_col1, rm_col2 = st.columns(2)

        if rm_col1.button("🔒 Lock Entire Current Roster", use_container_width=True):
            conn = get_db_connection()
            try:
                c = conn.cursor()
                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s", (target_date_str,))
                inserts = []
                seen_am = set()
                seen_pm = set()
                for rig, seats in am_roster.items():
                    for pos, am_name in seats.items():
                        seat_name = f"{rig} | {pos}"
                        if seat_name not in all_seats_list:
                            continue
                        pm_name = pm_roster[rig][pos]
                        clean_am = clean_display_name(am_name) if am_name else ""
                        clean_pm = clean_display_name(pm_name) if pm_name else ""

                        if clean_am and clean_am == clean_pm:
                            if (clean_am not in seen_am and clean_am not in seen_pm) or clean_am == "VACANT":
                                inserts.append((target_date_str, clean_am, seat_name, "FULL"))
                                if clean_am != "VACANT":
                                    seen_am.add(clean_am)
                                    seen_pm.add(clean_am)
                        else:
                            if clean_am and (clean_am not in seen_am or clean_am == "VACANT"):
                                inserts.append((target_date_str, clean_am, seat_name, "AM"))
                                if clean_am != "VACANT":
                                    seen_am.add(clean_am)
                            if clean_pm and (clean_pm not in seen_pm or clean_pm == "VACANT"):
                                inserts.append((target_date_str, clean_pm, seat_name, "PM"))
                                if clean_pm != "VACANT":
                                    seen_pm.add(clean_pm)
                if inserts:
                    c.executemany("INSERT INTO Manual_Overrides (Target_Date, Name, Seat, Period) VALUES (%s, %s, %s, %s)", inserts)
                conn.commit()
            finally:
                conn.close()
            st.success("Roster successfully locked!")
            time.sleep(1)
            st.rerun()

        if rm_col2.button("🔓 Unlock All Seats on This Date", use_container_width=True):
            conn = get_db_connection()
            try:
                c = conn.cursor()
                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s", (target_date_str,))
                conn.commit()
            finally:
                conn.close()
            st.success("All manual seat locks cleared for this date!")
            time.sleep(1)
            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("##### 🔧 Quick Manual Overrides")
        ov_col1, ov_col2 = st.columns([1, 1])
        with ov_col1:
            with st.form("override_form", clear_on_submit=True):
                o_name = st.selectbox("Personnel", ["VACANT"] + on_duty_dropdown_list, key="ov_n")
                o_seat = st.selectbox("Assign to Seat", all_seats_list, key="ov_s")
                period_labels = {
                    "FULL": "Full Shift (0700-0700)",
                    "AM": "Partial: 0700-1500 (AM Only)",
                    "PM": "Partial: 1500-0700 (PM Remainder)"
                }
                o_period = st.selectbox(
                    "Shift Portion",
                    options=["FULL", "AM", "PM"],
                    format_func=lambda x: period_labels[x],
                    key="ov_p"
                )

                if st.form_submit_button("Lock Seat Override", type="primary"):
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        # Remove conflicting seat lock for the selected period
                        if o_period == "FULL":
                            c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s", (target_date_str, o_seat))
                        elif o_period == "AM":
                            c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s AND Period = 'AM'", (target_date_str, o_seat))
                            c.execute("UPDATE Manual_Overrides SET Period = 'PM' WHERE Target_Date = %s AND Seat = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, o_seat))
                        elif o_period == "PM":
                            c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Seat = %s AND Period = 'PM'", (target_date_str, o_seat))
                            c.execute("UPDATE Manual_Overrides SET Period = 'AM' WHERE Target_Date = %s AND Seat = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, o_seat))

                        # Remove conflicting person lock for the selected period (so a person can be locked in one seat AM and another seat PM)
                        if o_name != "VACANT":
                            if o_period == "FULL":
                                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s", (target_date_str, o_name))
                            elif o_period == "AM":
                                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s AND Period = 'AM'", (target_date_str, o_name))
                                c.execute("UPDATE Manual_Overrides SET Period = 'PM' WHERE Target_Date = %s AND Name = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, o_name))
                            elif o_period == "PM":
                                c.execute("DELETE FROM Manual_Overrides WHERE Target_Date = %s AND Name = %s AND Period = 'PM'", (target_date_str, o_name))
                                c.execute("UPDATE Manual_Overrides SET Period = 'AM' WHERE Target_Date = %s AND Name = %s AND (Period = 'FULL' OR Period IS NULL)", (target_date_str, o_name))

                        c.execute(
                            "INSERT INTO Manual_Overrides (Target_Date, Name, Seat, Period) VALUES (%s, %s, %s, %s)",
                            (target_date_str, o_name, o_seat, o_period)
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    st.success(f"Locked {o_name} into {o_seat} ({period_labels[o_period]}).")
                    time.sleep(1)
                    st.rerun()

        with ov_col2:
            if overrides_df.empty:
                st.info("No active overrides for this date.")
            else:
                for _, ov in overrides_df.iterrows():
                    with st.container(border=True):
                        cols = st.columns([4, 1])
                        per_tag = str(ov.get('period') or 'FULL').upper()
                        per_badge = "Full Shift" if per_tag == "FULL" else ("0700-1500 (AM)" if per_tag == "AM" else "1500-0700 (PM)")
                        cols[0].markdown(f"**{ov['name']}** ➡️ {ov['seat']} &nbsp; `[{per_badge}]`")
                        if cols[1].button("🗑", key=f"del_ov_{ov['id']}", use_container_width=True):
                            conn = get_db_connection()
                            try:
                                conn.cursor().execute("DELETE FROM Manual_Overrides WHERE id = %s", (ov['id'],))
                                conn.commit()
                            finally:
                                conn.close()
                            st.rerun()

    st.divider()
    day_of_week = target_date.strftime("%A")
    st.subheader(f"🧹 Station Details ({day_of_week})")
    st.markdown(f"<ul style='margin-top: 0px;'>{station_details_html}</ul>", unsafe_allow_html=True)


# ==========================================
# PAGE 2: OVERTIME ROSTER
# ==========================================
elif selected_page == "⏱️ Overtime Roster":
    st.subheader("⏱️ Overtime Roster Management")

    conn = get_db_connection()
    try:
        df_ot = pd.read_sql("SELECT * FROM Overtime_Buckets", conn)
    finally:
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

        auto_log_ot = st.checkbox("Automatically add Awarded personnel to Daily Overtime Log", value=True)
        ot_notes = st.text_input("Callout Notes (Required)", placeholder="e.g., Shift coverage for vacancy...")

        if st.button("Process & Commit Hours", type="primary"):
            both_checked = edited_df[edited_df['Charged'] & edited_df['Awarded']]['Name'].tolist()
            if not ot_notes.strip():
                st.error("Notes cannot be blank to process a callout.")
            elif both_checked:
                st.error(f"Cannot mark the same person as both Charged and Awarded: {', '.join(both_checked)}")
            else:
                archive_stamp = f"{datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
                compiled_notes = f"[{ot_worked_date.strftime('%Y-%m-%d')} | {ot_shift}-Shift | {ot_start}-{ot_end}] {ot_notes}"
                archive_records = []

                df_print = edited_df[edited_df['Contact'] == 'Yes'].copy()
                for _, r in df_print.iterrows():
                    is_awarded = bool(r['Awarded'])
                    is_charged = bool(r['Charged'])

                    hours_applied = callout_hours if (is_awarded or is_charged) else 0.0

                    if is_awarded:
                        status = "🟢 Awarded"
                    elif is_charged:
                        status = "🟡 Charged"
                    else:
                        status = "⚪ Skipped / Working"

                    archive_records.append((archive_stamp, compiled_notes, r['Name'], r['Current_hours'], hours_applied, status))

                updates = []
                ot_log_inserts = []
                worked_date_str = ot_worked_date.strftime("%Y-%m-%d")

                for _, r in edited_df.iterrows():
                    is_awarded = bool(r['Awarded'])
                    is_charged = bool(r['Charged'])

                    hours_applied = callout_hours if (is_awarded or is_charged) else 0.0

                    if hours_applied > 0:
                        new_hours = r['Current_hours'] + hours_applied
                        updates.append((new_hours, r['Name']))

                    if is_awarded and auto_log_ot:
                        ot_log_inserts.append((worked_date_str, r['Name'], "Voluntary", ot_start, ot_end, float(callout_hours)))

                conn = get_db_connection()
                try:
                    c = conn.cursor()
                    if archive_records:
                        c.executemany(
                            "INSERT INTO OT_Archive (Archive_Stamp, Notes, Name, Start_Hours, Hours_Applied, Status) VALUES (%s, %s, %s, %s, %s, %s)",
                            archive_records
                        )
                    if updates:
                        c.executemany("UPDATE Overtime_Buckets SET Current_Hours = %s WHERE Name = %s", updates)
                    if ot_log_inserts:
                        c.executemany(
                            "INSERT INTO Overtime_Log (Target_Date, Name, OT_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)",
                            ot_log_inserts
                        )
                    conn.commit()
                finally:
                    conn.close()
                st.success(f"Callout successfully processed! Snapshot saved as {archive_stamp}.")
                time.sleep(2)
                st.rerun()

    # --- ARCHIVES OUTSIDE ADMIN LOOP (PUBLIC VISIBILITY) ---
    st.divider()
    st.subheader("🗄️ Callout Archives")
    conn = get_db_connection()
    try:
        archive_df = pd.read_sql("SELECT * FROM OT_Archive ORDER BY id DESC", conn)
    finally:
        conn.close()

    if not archive_df.empty:
        archive_df.columns = [c.lower() for c in archive_df.columns]
        archive_df['new_hours'] = archive_df['start_hours'] + archive_df['hours_applied']

        archive_groups = archive_df['archive_stamp'].unique()
        for stamp in archive_groups[:10]:
            stamp_df = archive_df[archive_df['archive_stamp'] == stamp]
            stamp_notes = stamp_df['notes'].iloc[0]
            archive_height = (len(stamp_df) * 35) + 40

            with st.expander(f"Snapshot: {stamp} | Notes: {stamp_notes}"):
                c1, c2 = st.columns(2)
                left_df = stamp_df[['name', 'start_hours', 'hours_applied', 'status']].copy()
                left_df.rename(columns={'name': 'Name', 'start_hours': 'Old Hours', 'hours_applied': 'Hours', 'status': 'Action'}, inplace=True)
                left_df = left_df.sort_values(by='Old Hours', ascending=True).reset_index(drop=True)

                def color_rows(row):
                    if 'Awarded' in str(row['Action']):
                        return ['background-color: #c8e6c9; color: black'] * len(row)
                    if 'Charged' in str(row['Action']):
                        return ['background-color: #fff9c4; color: black'] * len(row)
                    return [''] * len(row)

                styled_left = left_df.style.apply(color_rows, axis=1)

                with c1:
                    st.markdown("**Canvass Roster (Before)**")
                    st.dataframe(styled_left, use_container_width=True, hide_index=True, height=archive_height)

                right_df = stamp_df[['name', 'new_hours']].copy()
                right_df.rename(columns={'name': 'Name', 'new_hours': 'New Hours'}, inplace=True)
                right_df = right_df.sort_values(by='New Hours', ascending=True).reset_index(drop=True)

                with c2:
                    st.markdown("**Updated List (After)**")
                    st.dataframe(right_df, use_container_width=True, hide_index=True, height=archive_height)

# ==========================================
# PAGE 3: LEAVE CALENDAR
# ==========================================
elif selected_page == "📅 Leave Calendar":
    cal_col1, cal_col2, _ = st.columns([1, 1, 4])
    with cal_col1:
        selected_month = st.selectbox("Month", range(1, 13), index=target_date.month - 1, format_func=lambda x: calendar.month_name[x])
    with cal_col2:
        selected_year = st.selectbox("Year", dynamic_years, index=dynamic_years.index(target_date.year))
    st.divider()
    cal_matrix = calendar.monthcalendar(selected_year, selected_month)
    days_of_week = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    header_cols = st.columns(7)
    for i, day_name in enumerate(days_of_week):
        header_cols[i].markdown(f"<h5 style='text-align: center;'>{day_name}</h5>", unsafe_allow_html=True)

    for week in cal_matrix:
        week_cols = st.columns(7)
        for i, day in enumerate(week):
            with week_cols[i]:
                if day != 0:
                    current_date = datetime.date(selected_year, selected_month, day)
                    date_str = current_date.strftime("%Y-%m-%d")
                    cal_shift_mod = (current_date - anchor_date).days % 3
                    if cal_shift_mod == 0:
                        shift_color, shift_name, day_shift = "#4CAF50", "A-Shift", "A"
                    elif cal_shift_mod == 1:
                        shift_color, shift_name, day_shift = "#2196F3", "B-Shift", "B"
                    else:
                        shift_color, shift_name, day_shift = "#F44336", "C-Shift", "C"

                    day_leave = all_leave_df[all_leave_df['target_date'] == date_str] if not all_leave_df.empty else pd.DataFrame()
                    day_ot = all_ot_df[all_ot_df['target_date'] == date_str] if not all_ot_df.empty else pd.DataFrame()

                    day_trades = []
                    if not all_trades_df.empty:
                        for _, t in all_trades_df.iterrows():
                            if t['date_1'] == date_str:
                                day_trades.append({
                                    'id': t['id'], 'Off_Name': t['name_1'], 'Work_Name': t['name_2'],
                                    'Start_Time': t['start_time'], 'End_Time': t['end_time'], 'Total_Hours': t['total_hours']
                                })
                            elif t['date_2'] == date_str:
                                day_trades.append({
                                    'id': t['id'], 'Off_Name': t['name_2'], 'Work_Name': t['name_1'],
                                    'Start_Time': t['start_time'], 'End_Time': t['end_time'], 'Total_Hours': t['total_hours']
                                })

                    core_names = all_personnel_df[(all_personnel_df['shift'] == day_shift) & (all_personnel_df['core_manning'] == True)]['name'].tolist()

                    full_day_leaves = [l['name'] for _, l in day_leave.iterrows() if float(l['total_hours']) >= 24.0] if not day_leave.empty else []
                    full_day_trade_offs = [t['Off_Name'] for t in day_trades if float(t['Total_Hours']) >= 24.0]

                    core_on_leave = len([n for n in full_day_leaves if n in core_names])
                    core_trading_off = len([n for n in full_day_trade_offs if n in core_names])
                    est_manning = len(core_names) - core_on_leave - core_trading_off + len(day_ot) + len(day_trades)

                    if est_manning < 9 and current_date.weekday() < 5:
                        admin_names_list = all_personnel_df[all_personnel_df['shift'] == 'ADMIN']['name'].tolist()
                        admins_on_leave = len([n for n in full_day_leaves if n in admin_names_list]) + len([n for n in full_day_trade_offs if n in admin_names_list])
                        available_admins = max(0, len(admin_names_list) - admins_on_leave)
                        est_manning += min((9 - est_manning), available_admins)

                    manning_color = "green" if est_manning >= 9 else "red"

                    with st.container(border=True):
                        st.markdown(
                            f"<div style='background-color:{shift_color}; color:white; padding: 6px 4px; border-radius: 4px; font-size: 1.25em; font-weight: 800; margin-bottom: 6px; text-align: center;'>{day} | {shift_name}</div>"
                            f"<div style='color:{manning_color}; font-size:1.0em; font-weight:bold; margin-bottom: 4px; text-align: center;'>Manning: {est_manning}</div>",
                            unsafe_allow_html=True
                        )

                        if not day_leave.empty:
                            for _, l in day_leave.iterrows():
                                hrs = int(l['total_hours']) if l['total_hours'] % 1 == 0 else l['total_hours']
                                with st.popover(f"{get_icon(l['leave_type'])} {l['name']} ({hrs}h)", use_container_width=True):
                                    st.markdown(f"**{l['leave_type']}**")
                                    st.write(f"⏱️ {l['start_time']} - {l['end_time']}")
                                    if is_admin:
                                        if st.button("🗑️ Cancel Leave", key=f"del_leave_{l['id']}", type="primary", use_container_width=True):
                                            conn = get_db_connection()
                                            try:
                                                conn.cursor().execute("DELETE FROM Leave_Ledger WHERE id = %s", (l['id'],))
                                                conn.commit()
                                            finally:
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
                                            try:
                                                conn.cursor().execute("DELETE FROM Overtime_Log WHERE id = %s", (o['id'],))
                                                conn.commit()
                                            finally:
                                                conn.close()
                                            st.rerun()

                        for t in day_trades:
                            hrs = int(t['Total_Hours']) if t['Total_Hours'] % 1 == 0 else t['Total_Hours']
                            time_str = f"{t['Start_Time']} - {t['End_Time']}" if hrs < 24 else "0700 - 0700"
                            with st.popover(f"🔀 {t['Off_Name']} 🔁 {t['Work_Name']} ({hrs}h)", use_container_width=True):
                                st.markdown("**Shift Trade**")
                                st.write(f"⏱️ {time_str}")
                                if is_admin:
                                    if st.button("🗑 Cancel Trade", key=f"del_trade_{t['id']}_{date_str}", type="primary", use_container_width=True):
                                        conn = get_db_connection()
                                        try:
                                            conn.cursor().execute("DELETE FROM Shift_Trades_V3 WHERE id = %s", (t['id'],))
                                            conn.commit()
                                        finally:
                                            conn.close()
                                        st.rerun()
                else:
                    st.write("")

# ==========================================
# PAGE 4: TRAINING & EVENTS CALENDAR
# ==========================================
elif selected_page == "📆 Training & Events":
    st.subheader("📆 Training & Events")

    if is_admin:
        ev_tabs = st.tabs(["📅 Calendar View", "✍️ Log New Event"])
        tab_ev_cal = ev_tabs[0]
        tab_ev_log = ev_tabs[1]
    else:
        tab_ev_cal = st.container()
        tab_ev_log = None

    with tab_ev_cal:
        cal_col1, cal_col2, _ = st.columns([1, 1, 4])
        with cal_col1:
            selected_month = st.selectbox("Month", range(1, 13), index=target_date.month - 1, format_func=lambda x: calendar.month_name[x], key="ev_month")
        with cal_col2:
            selected_year = st.selectbox("Year", dynamic_years, index=dynamic_years.index(target_date.year), key="ev_year")
        st.divider()
        cal_matrix = calendar.monthcalendar(selected_year, selected_month)
        days_of_week = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        header_cols = st.columns(7)
        for i, day_name in enumerate(days_of_week):
            header_cols[i].markdown(f"<h5 style='text-align: center;'>{day_name}</h5>", unsafe_allow_html=True)

        for week in cal_matrix:
            week_cols = st.columns(7)
            for i, day in enumerate(week):
                with week_cols[i]:
                    if day != 0:
                        current_date = datetime.date(selected_year, selected_month, day)
                        date_str = current_date.strftime("%Y-%m-%d")
                        cal_shift_mod = (current_date - anchor_date).days % 3
                        if cal_shift_mod == 0:
                            shift_color, shift_name = "#4CAF50", "A-Shift"
                        elif cal_shift_mod == 1:
                            shift_color, shift_name = "#2196F3", "B-Shift"
                        else:
                            shift_color, shift_name = "#F44336", "C-Shift"

                        day_events = events_df[events_df['target_date'] == date_str] if not events_df.empty else pd.DataFrame()

                        with st.container(border=True):
                            st.markdown(f"<div style='background-color:{shift_color}; color:white; padding: 6px 4px; border-radius: 4px; font-size: 1.25em; font-weight: 800; margin-bottom: 6px; text-align: center;'>{day} | {shift_name}</div>", unsafe_allow_html=True)

                            if not day_events.empty:
                                for _, ev in day_events.iterrows():
                                    icon = "📚" if ev['event_type'] == "Training" else "📅"
                                    with st.popover(f"{icon} {ev['description']}", use_container_width=True):
                                        st.markdown(f"**{ev['event_type']}**")
                                        st.write(f"⏱️ {ev['start_time']} - {ev['end_time']}")
                                        st.write(f"📝 {ev['description']}")

                                        instructor_val = ev.get('instructor')
                                        if pd.notna(instructor_val) and instructor_val not in ["", "-- None --"]:
                                            st.write(f"👨‍🏫 **Instructor:** {instructor_val}")

                                        if is_admin:
                                            if st.button("🗑️ Cancel Event", key=f"del_ev_cal_{ev['id']}", type="primary", use_container_width=True):
                                                conn = get_db_connection()
                                                try:
                                                    conn.cursor().execute("DELETE FROM Station_Events WHERE id = %s", (ev['id'],))
                                                    conn.commit()
                                                finally:
                                                    conn.close()
                                                st.rerun()
                    else:
                        st.write("")

    if tab_ev_log is not None:
        with tab_ev_log:
            ev_col1, ev_col2 = st.columns([1, 1])
            with ev_col1:
                with st.form("event_form", clear_on_submit=True):
                    st.markdown("#### 📝 Add New Event")
                    e_date = st.date_input("Event Date", value=target_date)
                    e_type = st.selectbox("Event Type", ["Training", "Meeting", "Inspection", "Important Event", "Other"])

                    instructor_cands = sorted(list(set(on_duty_dropdown_list).union(admin_names)))
                    e_instructor = st.selectbox("Instructor (if applicable)", ["-- None --"] + instructor_cands)

                    e_desc = st.text_input("Description (Required)", placeholder="e.g., Live Fire Training, Chief's Meeting...")
                    t_col1, t_col2 = st.columns(2)
                    e_start = t_col1.text_input("Start Time (e.g., 0900)", "0900")
                    e_end = t_col2.text_input("End Time (e.g., 1100)", "1100")

                    if st.form_submit_button("Save Event", type="primary"):
                        if not e_desc.strip():
                            st.error("Please enter a description.")
                        else:
                            conn = get_db_connection()
                            try:
                                conn.cursor().execute(
                                    "INSERT INTO Station_Events (Target_Date, Event_Type, Description, Start_Time, End_Time, Instructor) VALUES (%s, %s, %s, %s, %s, %s)",
                                    (e_date.strftime("%Y-%m-%d"), e_type, e_desc, e_start, e_end, e_instructor)
                                )
                                conn.commit()
                            finally:
                                conn.close()
                            st.success("Event added!")
                            time.sleep(1)
                            st.rerun()
            with ev_col2:
                st.markdown(f"#### **Events on {target_date_str}:**")
                day_events = events_df[events_df['target_date'] == target_date_str] if not events_df.empty else pd.DataFrame()
                if day_events.empty:
                    st.info("No events logged for this date.")
                else:
                    for _, ev in day_events.iterrows():
                        with st.container(border=True):
                            cols = st.columns([4, 1])
                            cols[0].markdown(f"**{ev['event_type']}**: {ev['description']} ({ev['start_time']}-{ev['end_time']})")

                            instructor_val = ev.get('instructor')
                            if pd.notna(instructor_val) and instructor_val not in ["", "-- None --"]:
                                cols[0].caption(f"👨‍🏫 **Instructor:** {instructor_val}")

                            if cols[1].button("🗑", key=f"del_ev_list_{ev['id']}", use_container_width=True):
                                conn = get_db_connection()
                                try:
                                    conn.cursor().execute("DELETE FROM Station_Events WHERE id = %s", (ev['id'],))
                                    conn.commit()
                                finally:
                                    conn.close()
                                st.rerun()

# ==========================================
# PAGE 5: SEAT STATISTICS
# ==========================================
elif selected_page == "📊 Seat Statistics":
    st.subheader("📊 Historical Seat Balances")
    st.markdown("This tracker automatically logs how many hours each person has spent in the rotating positions, as well as the number of times they have pulled each Alarm Room watch.")

    if not stats_df.empty:
        pivot_df = stats_df.pivot(index='name', columns='position', values='total_hours').fillna(0)

        # Dynamic height calculation to remove inner scrollbars
        stats_height = (len(pivot_df) * 35) + 40

        st.markdown("#### 🕒 Structural Seat Balance (Total Hours)")
        display_cols = [c for c in ["E-221 Driver/Operator", "E-221 Firefighter 1", "E-221 Firefighter 2", "R-221 Driver/Operator", "R-221 Firefighter 1", "ARO 0700-1200 (Float)"] if c in pivot_df.columns]
        if display_cols:
            st.dataframe(pivot_df[display_cols], use_container_width=True, height=stats_height)
        else:
            st.info("No rotating seat hours logged yet.")

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
# PAGE 6: DATA ENTRY & MANAGEMENT (ADMIN ONLY)
# ==========================================
elif selected_page == "✍️ Data Entry" and is_admin:
    st.subheader("✍️ Data Entry & Roster Management")

    de_tabs = st.tabs(["🏖️ Leave", "💰 Overtime", "🔀 Shift Trades", "🚒 Shift Roster", "⏱️ OT Canvass List", "🚒 Apparatus Status", "💾 Backups"])

    # ----------------------------------------
    # TAB 1: ENTER LEAVE
    # ----------------------------------------
    with de_tabs[0]:
        st.markdown("#### 🏖️ Enter Leave")
        c1, c2 = st.columns([1, 1])
        with c1:
            with st.form("leave_form", clear_on_submit=True):
                l_date_range = st.date_input("Target Date(s)", value=(target_date, target_date))
                l_name = st.selectbox("Personnel", all_names)
                leave_types = sorted([
                    "Annual Leave", "Paternity Leave", "Union Leave", "Bereavement Leave",
                    "Medical Leave", "Military Leave", "Jury Duty", "NFPA Physical",
                    "Personal Leave", "Disability Leave", "Training"
                ])
                l_type = st.selectbox("Leave Type", leave_types)
                l_24h = st.checkbox("Full 24h Shift (0700-0700)", value=True, key="l_24")
                time_col1, time_col2 = st.columns(2)
                l_start = time_col1.text_input("Start Time (e.g., 0700)", "0700", key="l_s")
                l_end = time_col2.text_input("End Time (e.g., 0700)", "0700", key="l_e")

                if st.form_submit_button("Save Leave", type="primary"):
                    if isinstance(l_date_range, (tuple, list)):
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
                            if cur_dt.weekday() < 5:
                                dates_to_log.append(cur_dt.strftime("%Y-%m-%d"))
                        else:
                            s_mod = (cur_dt - anchor).days % 3
                            day_s = "A" if s_mod == 0 else ("B" if s_mod == 1 else "C")
                            if p_shift == day_s:
                                dates_to_log.append(cur_dt.strftime("%Y-%m-%d"))

                    if not dates_to_log:
                        st.error("No assigned shift days found in that range for this person.")
                    else:
                        if l_24h:
                            l_start, l_end, l_hours = "0700", "0700", 24.0
                        else:
                            l_hours = calc_hours(l_start, l_end)

                        conn = get_db_connection()
                        try:
                            inserts = [(d, l_name, l_type, l_start, l_end, l_hours) for d in dates_to_log]
                            conn.cursor().executemany(
                                "INSERT INTO Leave_Ledger (Target_Date, Name, Leave_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)",
                                inserts
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        st.success(f"Logged {len(dates_to_log)} shift(s) of leave for {l_name}.")
                        time.sleep(1)
                        st.rerun()

    # ----------------------------------------
    # TAB 2: ENTER OVERTIME
    # ----------------------------------------
    with de_tabs[1]:
        st.markdown("#### 💰 Enter Overtime / Temp Duty")
        c1, c2 = st.columns([1, 1])
        with c1:
            with st.form("ot_form", clear_on_submit=True):
                o_date = st.date_input("Target Date ", value=target_date)
                o_name_dd = st.selectbox("Permanent Personnel", ["-- Select --"] + all_names, key="ot_personnel")
                o_name_wi = st.text_input("OR Write-In Name", placeholder="e.g., A1C Snuffy", key="ot_wi")
                o_type = st.selectbox("OT Type", ["Voluntary", "Mandatory", "Guard Personnel On-Duty"])
                o_24h = st.checkbox("Full 24h Shift (0700-0700)", value=False, key="o_24")
                time_col3, time_col4 = st.columns(2)
                o_start = time_col3.text_input("Start Time (e.g., 1500)", "1500", key="o_s")
                o_end = time_col4.text_input("End Time (e.g., 0700)", "0700", key="o_e")

                if st.form_submit_button("Save OT", type="primary"):
                    final_name = o_name_wi.strip() if o_name_wi.strip() != "" else o_name_dd
                    if final_name == "-- Select --":
                        st.error("Please select a person or write one in!")
                    else:
                        if o_24h:
                            o_start, o_end, o_hours = "0700", "0700", 24.0
                        else:
                            o_hours = calc_hours(o_start, o_end)

                        conn = get_db_connection()
                        try:
                            conn.cursor().execute(
                                "INSERT INTO Overtime_Log (Target_Date, Name, OT_Type, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s)",
                                (o_date.strftime("%Y-%m-%d"), final_name, o_type, o_start, o_end, o_hours)
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        st.success(f"Logged OT for {final_name}.")
                        time.sleep(1)
                        st.rerun()

    # ----------------------------------------
    # TAB 3: SHIFT TRADES
    # ----------------------------------------
    with de_tabs[2]:
        st.markdown("#### 🔀 Enter Shift Trade")
        c1, c2 = st.columns([1, 1])
        with c1:
            with st.form("trade_form", clear_on_submit=True):
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

                if st.form_submit_button("Save Trade", type="primary"):
                    if t_name1 == t_name2:
                        st.error("Personnel cannot trade with themselves!")
                    else:
                        if t_24h:
                            t_start, t_end, t_hours = "0700", "0700", 24.0
                        else:
                            t_hours = calc_hours(t_start, t_end)

                        conn = get_db_connection()
                        try:
                            conn.cursor().execute(
                                "INSERT INTO Shift_Trades_V3 (Date_1, Name_1, Date_2, Name_2, Start_Time, End_Time, Total_Hours) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                (t_date1.strftime("%Y-%m-%d"), t_name1, t_date2.strftime("%Y-%m-%d"), t_name2, t_start, t_end, t_hours)
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        st.success(f"Logged Trade: {t_name1} and {t_name2}.")
                        time.sleep(1)
                        st.rerun()

    # ----------------------------------------
    # TAB 4: PRIMARY SHIFT ROSTER
    # ----------------------------------------
    with de_tabs[3]:
        st.markdown("#### 🚒 Primary Shift Roster (`Personnel`)")
        st.caption("Seniority 1-10 are reserved for Officers. Firefighters (11+) are automatically sorted and shifted up based on Hire Date and 4-Digit Tie Breaker (lower wins).")

        display_df = all_personnel_df.copy()
        display_cols = [
            'seniority', 'name', 'shift', 'schedule', 'admin_24h_day',
            'hire_date', 'tie_breaker', 'core_manning',
            'emr', 'emt', 'paramedic',
            'officer_1', 'officer_2', 'officer_3', 'officer_4',
            'inspector', 'inspector_2', 'inspector_3',
            'instructor', 'instructor_2', 'instructor_3'
        ]
        for c_name in display_cols:
            if c_name not in display_df.columns:
                display_df[c_name] = None

        display_df = display_df[display_cols].sort_values(by='seniority').reset_index(drop=True)
        st.dataframe(display_df, use_container_width=True, hide_index=True, height=320)

        with st.expander("🔄 Sync / Import Certifications & Hire Dates from Spreadsheet"):
            sync_c1, sync_c2 = st.columns(2)
            with sync_c1:
                st.markdown("**Built-In Master Certifications Sheet (37 Personnel)**")
                st.caption("Automatically syncs Hire Dates, OHEMS (EMR/EMT/Paramedic), Inspector 1–3, Instructor 1–3, and Officer 1–4 for all matching personnel.")
                if st.button("🔄 Re-Sync from Built-In Master Sheet", type="secondary", use_container_width=True):
                    conn = get_db_connection()
                    try:
                        cnt = sync_personnel_certifications(conn, MASTER_CERT_DATA)
                    finally:
                        conn.close()
                    recalculate_ff_seniority()
                    st.success(f"✅ Synced certifications & hire dates for {cnt} personnel records and recalculated seniority!")
                    time.sleep(1.5)
                    st.rerun()
            with sync_c2:
                st.markdown("**Upload Updated Certifications Excel File (`.xlsx`)**")
                cert_upload = st.file_uploader("Upload Certifications Sheet", type=["xlsx"], key="cert_xlsx_upload", label_visibility="collapsed")
                if cert_upload is not None:
                    if st.button("📥 Import Uploaded Certifications Sheet", type="primary", use_container_width=True):
                        try:
                            up_df = pd.read_excel(cert_upload)
                            up_records = []
                            for _, r in up_df.iterrows():
                                first = str(r.get('firstName', '')).strip()
                                last = str(r.get('lastName', '')).strip()
                                if not last or last.lower() == 'nan':
                                    continue
                                hd = r.get('hireDate')
                                if isinstance(hd, (datetime.datetime, datetime.date, pd.Timestamp)):
                                    hire_str = hd.strftime("%Y-%m-%d")
                                else:
                                    hire_str = None
                                ohems = str(r.get('ohems', '')).strip() if pd.notna(r.get('ohems')) else ""
                                def _is_one(v):
                                    try:
                                        return bool(pd.notna(v) and float(v) == 1.0)
                                    except Exception:
                                        return False
                                up_records.append({
                                    "first_name": first,
                                    "last_name": last,
                                    "hire_date": hire_str,
                                    "emr": ohems.upper() == "EMR",
                                    "emt": ohems.upper() == "EMT",
                                    "paramedic": ohems.upper() == "PARAMEDIC",
                                    "inspector_1": _is_one(r.get('Inspector 1')),
                                    "inspector_2": _is_one(r.get('Inspector 2')),
                                    "inspector_3": _is_one(r.get('Inspector 3')),
                                    "instructor_1": _is_one(r.get('Instructor 1')),
                                    "instructor_2": _is_one(r.get('Instructor 2')),
                                    "instructor_3": _is_one(r.get('Instructor 3')),
                                    "officer_1": _is_one(r.get('Officer 1')),
                                    "officer_2": _is_one(r.get('Officer 2')),
                                    "officer_3": _is_one(r.get('Officer 3')),
                                    "officer_4": _is_one(r.get('Officer 4')),
                                })
                            conn = get_db_connection()
                            try:
                                cnt = sync_personnel_certifications(conn, up_records)
                            finally:
                                conn.close()
                            recalculate_ff_seniority()
                            st.success(f"✅ Imported and synced {cnt} personnel records from uploaded sheet!")
                            time.sleep(1.5)
                            st.rerun()
                        except Exception as up_err:
                            st.error(f"Error importing sheet: {up_err}")

        st.divider()
        col_add, col_edit = st.columns(2)

        with col_add:
            st.markdown("##### ➕ Add New Personnel")
            with st.form("add_personnel_form", clear_on_submit=True):
                a_name = st.text_input("Name (Last Name)")
                h_col1, h_col2 = st.columns(2)
                a_hire = h_col1.date_input(
                    "Hire Date / Seniority Date",
                    value=get_station_today(),
                    min_value=datetime.date(1990, 1, 1),
                    max_value=get_station_today() + datetime.timedelta(days=365)
                )
                a_tie = h_col2.number_input(
                    "Tie Breaker (4-Digit, Lower Wins)",
                    min_value=0,
                    max_value=9999,
                    value=9999,
                    step=1,
                    help="4-digit number used to break seniority ties when Hire Dates are identical. Lower number wins."
                )
                a_shift = st.selectbox("Shift", ["A", "B", "C", "ADMIN"])
                a_sched = st.selectbox("Schedule", ["Structural", "Admin"])
                a_admin_day = st.selectbox("Admin 24h Weekday (if Admin Schedule)", ADMIN_DAY_OPTIONS)

                st.markdown("<br>", unsafe_allow_html=True)
                a_core = st.checkbox("Counts Towards Minimum Manning (Core)", value=False, help="Uncheck this for recruits so they do not count towards the 9-person minimum.")

                st.markdown("**Certifications**")
                c1, c2, c3, c4 = st.columns(4)
                a_off1 = c1.checkbox("Officer 1")
                a_off2 = c1.checkbox("Officer 2")
                a_off3 = c1.checkbox("Officer 3")
                a_off4 = c1.checkbox("Officer 4")

                a_insp = c2.checkbox("Inspector 1")
                a_insp2 = c2.checkbox("Inspector 2")
                a_insp3 = c2.checkbox("Inspector 3")

                a_inst = c3.checkbox("Instructor 1")
                a_inst2 = c3.checkbox("Instructor 2")
                a_inst3 = c3.checkbox("Instructor 3")

                a_emr = c4.checkbox("EMR")
                a_emt = c4.checkbox("EMT")
                a_med = c4.checkbox("Paramedic")

                if st.form_submit_button("Add Personnel", type="primary"):
                    if not a_name.strip():
                        st.error("Name is required.")
                    else:
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute(
                                """INSERT INTO Personnel (
                                    Name, Seniority, Core_Manning, Schedule, Shift,
                                    Officer_1, Officer_2, Officer_3, Officer_4,
                                    Inspector, Inspector_2, Inspector_3,
                                    Instructor, Instructor_2, Instructor_3,
                                    EMR, EMT, Paramedic,
                                    Hire_Date, Tie_Breaker, Admin_24h_Day
                                ) VALUES (%s, 999, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (
                                    a_name.strip(), a_core, a_sched, a_shift,
                                    a_off1, a_off2, a_off3, a_off4,
                                    a_insp, a_insp2, a_insp3,
                                    a_inst, a_inst2, a_inst3,
                                    a_emr, a_emt, a_med,
                                    a_hire.strftime("%Y-%m-%d"), int(a_tie), a_admin_day
                                )
                            )
                            conn.commit()
                        finally:
                            conn.close()

                        recalculate_ff_seniority()
                        st.success(f"{a_name} added successfully! Seniority auto-adjusted.")
                        time.sleep(1)
                        st.rerun()

        with col_edit:
            st.markdown("##### ✏️ Edit or Remove Personnel")
            edit_target = st.selectbox("Select Personnel to Edit", ["-- Select --"] + all_names)

            if edit_target != "-- Select --":
                target_data = all_personnel_df[all_personnel_df['name'] == edit_target].iloc[0]

                try:
                    curr_hire = datetime.datetime.strptime(str(target_data.get('hire_date', '2020-01-01')), "%Y-%m-%d").date()
                except Exception:
                    curr_hire = datetime.date(2020, 1, 1)

                if curr_hire < datetime.date(1990, 1, 1):
                    min_hire_dt = curr_hire
                else:
                    min_hire_dt = datetime.date(1990, 1, 1)

                try:
                    curr_tie = int(target_data.get('tie_breaker', 9999))
                    if curr_tie < 0 or curr_tie > 9999:
                        curr_tie = 9999
                except Exception:
                    curr_tie = 9999

                curr_admin_day = str(target_data.get('admin_24h_day', 'None') or 'None')
                if curr_admin_day not in ADMIN_DAY_OPTIONS:
                    curr_admin_day = 'None'

                with st.form("edit_personnel_form"):
                    e_sen = st.number_input("Seniority Number (1-10 for Officers)", min_value=1, value=int(target_data['seniority']), help="Leave firefighters >10 alone. The system will auto-sort them.")
                    eh_col1, eh_col2 = st.columns(2)
                    e_hire = eh_col1.date_input(
                        "Hire Date",
                        value=curr_hire,
                        min_value=min_hire_dt,
                        max_value=get_station_today() + datetime.timedelta(days=365)
                    )
                    e_tie = eh_col2.number_input(
                        "Tie Breaker (4-Digit, Lower Wins)",
                        min_value=0,
                        max_value=9999,
                        value=curr_tie,
                        step=1,
                        help="4-digit number used to break seniority ties when Hire Dates are identical. Lower number wins."
                    )
                    e_shift = st.selectbox("Shift", ["A", "B", "C", "ADMIN"], index=["A", "B", "C", "ADMIN"].index(target_data['shift']))
                    e_sched = st.selectbox("Schedule", ["Structural", "Admin"], index=["Structural", "Admin"].index(target_data['schedule']))
                    e_admin_day = st.selectbox("Admin 24h Weekday (if Admin Schedule)", ADMIN_DAY_OPTIONS, index=ADMIN_DAY_OPTIONS.index(curr_admin_day))

                    st.markdown("<br>", unsafe_allow_html=True)
                    e_core = st.checkbox("Counts Towards Minimum Manning (Core)", value=bool(target_data.get('core_manning', True)))

                    st.markdown("**Certifications**")
                    c1, c2, c3, c4 = st.columns(4)
                    e_off1 = c1.checkbox("Officer 1", value=bool(target_data.get('officer_1', False)))
                    e_off2 = c1.checkbox("Officer 2", value=bool(target_data.get('officer_2', False)))
                    e_off3 = c1.checkbox("Officer 3", value=bool(target_data.get('officer_3', False)))
                    e_off4 = c1.checkbox("Officer 4", value=bool(target_data.get('officer_4', False)))

                    e_insp = c2.checkbox("Inspector 1", value=bool(target_data.get('inspector', False)))
                    e_insp2 = c2.checkbox("Inspector 2", value=bool(target_data.get('inspector_2', False)))
                    e_insp3 = c2.checkbox("Inspector 3", value=bool(target_data.get('inspector_3', False)))

                    e_inst = c3.checkbox("Instructor 1", value=bool(target_data.get('instructor', False)))
                    e_inst2 = c3.checkbox("Instructor 2", value=bool(target_data.get('instructor_2', False)))
                    e_inst3 = c3.checkbox("Instructor 3", value=bool(target_data.get('instructor_3', False)))

                    e_emr = c4.checkbox("EMR", value=bool(target_data.get('emr', False)))
                    e_emt = c4.checkbox("EMT", value=bool(target_data.get('emt', False)))
                    e_med = c4.checkbox("Paramedic", value=bool(target_data.get('paramedic', False)))

                    st.markdown("<br>", unsafe_allow_html=True)
                    btn_col1, btn_col2 = st.columns(2)
                    if btn_col1.form_submit_button("Save Changes", type="primary"):
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute(
                                """UPDATE Personnel SET
                                    Seniority=%s, Shift=%s, Schedule=%s, Core_Manning=%s,
                                    Hire_Date=%s, Tie_Breaker=%s,
                                    Officer_1=%s, Officer_2=%s, Officer_3=%s, Officer_4=%s,
                                    Inspector=%s, Inspector_2=%s, Inspector_3=%s,
                                    Instructor=%s, Instructor_2=%s, Instructor_3=%s,
                                    EMR=%s, EMT=%s, Paramedic=%s,
                                    Admin_24h_Day=%s
                                WHERE Name=%s""",
                                (
                                    e_sen, e_shift, e_sched, e_core,
                                    e_hire.strftime("%Y-%m-%d"), int(e_tie),
                                    e_off1, e_off2, e_off3, e_off4,
                                    e_insp, e_insp2, e_insp3,
                                    e_inst, e_inst2, e_inst3,
                                    e_emr, e_emt, e_med,
                                    e_admin_day, edit_target
                                )
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        recalculate_ff_seniority()
                        st.success(f"Updated {edit_target}!")
                        time.sleep(1)
                        st.rerun()

                    if btn_col2.form_submit_button("❌ Delete Personnel"):
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute("DELETE FROM Personnel WHERE Name = %s", (edit_target,))
                            c.execute("DELETE FROM Overtime_Buckets WHERE Name = %s", (edit_target,))
                            conn.commit()
                        finally:
                            conn.close()
                        recalculate_ff_seniority()
                        st.success(f"Deleted {edit_target} from roster. Firefighters shifted up.")
                        time.sleep(1.5)
                        st.rerun()

    # ----------------------------------------
    # TAB 5: OVERTIME BUCKETS
    # ----------------------------------------
    with de_tabs[4]:
        st.markdown("#### ⏱️ Overtime Canvass List (`Overtime_Buckets`)")
        st.caption("Controls the Overtime Roster tab. Update Shift or Contact status ('Yes' / 'No') when personnel transfer shifts or join the OT list.")

        conn = get_db_connection()
        try:
            raw_ot_buckets = pd.read_sql("SELECT * FROM Overtime_Buckets", conn)
        finally:
            conn.close()
        raw_ot_buckets.columns = [c.lower() for c in raw_ot_buckets.columns]

        edit_ot_df = raw_ot_buckets[['seniority', 'name', 'shift', 'current_hours', 'contact']].sort_values(by='seniority').reset_index(drop=True)
        ot_grid_height = (len(edit_ot_df) * 35) + 80

        updated_ot_df = st.data_editor(
            edit_ot_df,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            height=ot_grid_height,
            key="editor_ot_buckets",
            column_config={
                "seniority": st.column_config.NumberColumn("OT Seniority", min_value=1, max_value=200, step=1, required=True, default=28),
                "name": st.column_config.TextColumn("Full Name", required=True),
                "shift": st.column_config.SelectboxColumn("Shift", options=["A", "B", "C"], required=True, default="A"),
                "current_hours": st.column_config.NumberColumn("Current Hours", min_value=0.0, step=0.5, format="%.2f", required=True, default=0.0),
                "contact": st.column_config.SelectboxColumn("Contact", options=["Yes", "No"], required=True, default="Yes")
            }
        )

        if st.button("💾 Save Overtime List Changes", type="primary", use_container_width=True):
            clean_ot = updated_ot_df.dropna(subset=['name']).copy()
            clean_ot['name'] = clean_ot['name'].astype(str).str.strip()
            clean_ot = clean_ot[clean_ot['name'] != ""]
            clean_ot = clean_ot.drop_duplicates(subset=['name'], keep='first')
            clean_ot['seniority'] = pd.to_numeric(clean_ot['seniority'], errors='coerce').fillna(99).astype(int)
            clean_ot['shift'] = clean_ot['shift'].fillna("A")
            clean_ot['current_hours'] = pd.to_numeric(clean_ot['current_hours'], errors='coerce').fillna(0.0).astype(float)
            clean_ot['contact'] = clean_ot['contact'].fillna("Yes")

            conn = get_db_connection()
            c = conn.cursor()
            try:
                active_names = clean_ot['name'].tolist()
                if active_names:
                    c.execute("DELETE FROM Overtime_Buckets WHERE Name != ALL(%s)", (active_names,))
                else:
                    c.execute("DELETE FROM Overtime_Buckets")

                ot_records = [
                    (r['name'], int(r['seniority']), r['shift'], float(r['current_hours']), r['contact'])
                    for _, r in clean_ot.iterrows()
                ]
                if ot_records:
                    c.executemany(
                        """INSERT INTO Overtime_Buckets (Name, Seniority, Shift, Current_Hours, Contact)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (Name) DO UPDATE SET
                               Seniority = EXCLUDED.Seniority,
                               Shift = EXCLUDED.Shift,
                               Current_Hours = EXCLUDED.Current_Hours,
                               Contact = EXCLUDED.Contact""",
                        ot_records
                    )
                conn.commit()
                st.success("✅ Overtime Canvass List updated!")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                conn.rollback()
                st.error(f"Error saving Overtime List: {e}")
            finally:
                conn.close()

    # ----------------------------------------
    # TAB 6: APPARATUS STATUS
    # ----------------------------------------
    with de_tabs[5]:
        st.markdown("#### 🚒 Apparatus Status Management")

        app_c1, app_c2 = st.columns(2)
        with app_c1:
            st.markdown("##### Standard Fleet")
            for rig in default_rig_names:
                curr_stat = app_status_dict.get(rig, "In Service")
                status_opts = ["In Service", "OOS - Regular Maintenance", "OOS - Repairs", "OOS - Other"]
                curr_idx = status_opts.index(curr_stat) if curr_stat in status_opts else 0
                new_stat = st.selectbox(rig, status_opts, index=curr_idx, key=f"app_{rig}")
                if new_stat != curr_stat:
                    conn = get_db_connection()
                    try:
                        c = conn.cursor()
                        c.execute("UPDATE Apparatus SET Status = %s WHERE Rig_Name = %s", (new_stat, rig))
                        conn.commit()
                    finally:
                        conn.close()
                    st.rerun()

        with app_c2:
            st.markdown("##### Temporary / Event Rigs")
            with st.form("add_temp_rig"):
                t_rig = st.text_input("Temporary Rig Name (e.g., Air Show Engine)")
                if st.form_submit_button("Add Rig", type="primary"):
                    if t_rig.strip():
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute("INSERT INTO Apparatus (Rig_Name, Status) VALUES (%s, %s) ON CONFLICT DO NOTHING", (t_rig.strip(), "In Service"))
                            conn.commit()
                        finally:
                            conn.close()
                        st.rerun()

            if temp_rigs:
                st.markdown("**Active Temporary Rigs**")
                for tr in temp_rigs:
                    tc1, tc2 = st.columns([3, 1])
                    tc1.write(f"🚒 {tr}")
                    if tc2.button("Remove", key=f"rem_{tr}"):
                        conn = get_db_connection()
                        try:
                            c = conn.cursor()
                            c.execute("DELETE FROM Apparatus WHERE Rig_Name = %s", (tr,))
                            conn.commit()
                        finally:
                            conn.close()
                        st.rerun()

    # ----------------------------------------
    # TAB 7: DATABASE BACKUPS
    # ----------------------------------------
    with de_tabs[6]:
        st.subheader("💾 Database Backups & Disaster Recovery")
        st.markdown("Download a full snapshot of all active tables, or restore the database from a previously downloaded snapshot.")

        bak_col1, bak_col2 = st.columns(2)

        with bak_col1:
            st.markdown("#### 📤 Export Backup")
            st.caption("Creates an Excel workbook containing a complete snapshot of every database table.")

            if st.button("Generate Backup File", use_container_width=True):
                conn = get_db_connection()
                try:
                    table_queries = {
                        "Personnel": "SELECT name, seniority, core_manning, schedule, shift, emr, emt, paramedic, officer_1, officer_2, officer_3, officer_4, inspector, inspector_2, inspector_3, instructor, instructor_2, instructor_3, hire_date, tie_breaker, admin_24h_day FROM Personnel",
                        "Overtime_Buckets": "SELECT name, seniority, shift, current_hours, contact FROM Overtime_Buckets",
                        "Leave_Ledger": "SELECT id, target_date, name, leave_type, start_time, end_time, total_hours FROM Leave_Ledger",
                        "Overtime_Log": "SELECT id, target_date, name, ot_type, start_time, end_time, total_hours FROM Overtime_Log",
                        "Shift_Trades_V3": "SELECT id, date_1, name_1, date_2, name_2, start_time, end_time, total_hours FROM Shift_Trades_V3",
                        "Position_Log": "SELECT id, target_date, name, position, hours FROM Position_Log",
                        "OT_Archive": "SELECT id, archive_stamp, notes, name, start_hours, hours_applied, status FROM OT_Archive",
                        "ARO_Watch": "SELECT id, target_date, watch_period, name FROM ARO_Watch",
                        "Manual_Overrides": "SELECT id, target_date, name, seat, period FROM Manual_Overrides",
                        "Station_Events": "SELECT id, target_date, event_type, description, start_time, end_time, instructor FROM Station_Events",
                        "Apparatus": "SELECT Rig_Name, Status FROM Apparatus"
                    }

                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        for t_name, q in table_queries.items():
                            df = pd.read_sql(q, conn)
                            df.to_excel(writer, sheet_name=t_name, index=False)
                finally:
                    conn.close()

                snap_time = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                st.download_button(
                    label=f"⬇️ Download Snapshot ({snap_time}.xlsx)",
                    data=output.getvalue(),
                    file_name=f"RFD_Roster_Backup_{snap_time}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    use_container_width=True
                )

        with bak_col2:
            st.markdown("#### 📥 Restore from Backup")
            st.caption("Upload a verified snapshot file to completely reset the database to that moment in time.")

            uploaded_backup = st.file_uploader("Upload Backup Excel File", type=["xlsx"], key="restore_file")

            if uploaded_backup is not None:
                st.warning("⚠️ Restoring will overwrite all current data with the contents of this file.")
                if st.button("🚨 Overwrite & Restore Database", type="primary", use_container_width=True):
                    try:
                        excel_file = pd.ExcelFile(uploaded_backup)

                        core_sheets = [
                            "Personnel", "Overtime_Buckets", "Leave_Ledger",
                            "Overtime_Log", "Shift_Trades_V3", "Position_Log",
                            "OT_Archive", "ARO_Watch", "Manual_Overrides"
                        ]

                        missing_core = [s for s in core_sheets if s not in excel_file.sheet_names]
                        if missing_core:
                            st.error(f"Invalid backup file! Missing required sheets: {', '.join(missing_core)}")
                        else:
                            conn = get_db_connection()
                            c = conn.cursor()

                            all_possible_sheets = core_sheets + ["Station_Events", "Apparatus"]

                            for t in reversed(all_possible_sheets):
                                if t in excel_file.sheet_names:
                                    c.execute(f"DELETE FROM {t};")

                            for t in all_possible_sheets:
                                if t in excel_file.sheet_names:
                                    df = pd.read_excel(excel_file, sheet_name=t)
                                    df = df.where(pd.notnull(df), None)

                                    if not df.empty:
                                        cols = list(df.columns)
                                        col_str = ", ".join(cols)
                                        val_placeholders = ", ".join(["%s"] * len(cols))
                                        query = f"INSERT INTO {t} ({col_str}) VALUES ({val_placeholders})"

                                        records = [tuple(row) for row in df.to_numpy()]
                                        c.executemany(query, records)

                                    if "id" in [col.lower() for col in df.columns]:
                                        c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM {t};")

                            conn.commit()
                            conn.close()
                            st.success("✅ Database successfully restored to snapshot state!")
                            time.sleep(2)
                            st.rerun()
                    except Exception as e:
                        if 'conn' in locals() and conn:
                            conn.rollback()
                            conn.close()
                        st.error(f"Restore failed: {e}")
