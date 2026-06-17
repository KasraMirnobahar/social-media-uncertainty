"""
MemeBook — 30-round social-media checking task (RL + timing-uncertainty study).

Setup:
    python -m pip install flask
    python app.py
    open http://127.0.0.1:5000

Folder layout (next to this file):
    memes/   feedN.jpeg  (pool of memes; 6 are drawn per round, varying)
    data/    auto-created. THREE files per participant (pid = time-sortable id):
               {pid}.csv          event log    (one row per logged event)
               {pid}_schedule.csv design       (30 rows, written once at start)
               {pid}_rounds.csv   round summary (one tidy row per completed round)

DESIGN (redesigned for reward-learning + timing-uncertainty + habit analysis)
  * 30 upload rounds (chosen from parameter-recovery simulation).
  * Two groups, assigned RANDOMLY at session start:
      - control   (uncertain timing): "Your likes will be available shortly."
      - treatment (certain timing):   "Your likes will be available in X seconds."
    The actual delay schedule is identical across groups; only the message differs.
  * Each round has a pre-determined (delay, reward-level) pair, FULLY CROSSED so
    delay and reward magnitude are independent and neither is confounded with round
    order. The per-participant schedule is shuffled (see build_schedule).
      - delays: 8 / 20 / 40 s   (DELAY_SECONDS — edit freely)
      - reward levels: low 3-12, medium 13-26, high 27-40 likes
  * Flow per round: generic Upload button -> pick 1 of 6 memes -> posted (with the
    group-appropriate timing message) -> browse feed -> press Check (MEASURED) ->
    once delay elapses the reward is revealed -> next round auto-unlocks.

EVENT LOG columns (per row): timestamp, t_session_s, pid, username, group, round,
    event, delay_s, reward_level, reward_value, meme, checks_this_round,
    expected_reward, is_reveal, extra.
    Events: session_start, username_set, round_open, picker_open, upload,
    check, reward_revealed, round_complete, finish, task_exit.

  * TIMESTAMPS are timezone-AWARE local time (ISO-8601 with UTC offset), ms
    precision, so inter-event intervals are unambiguous.
  * t_session_s is the ABSOLUTE ACTION CLOCK: seconds since session_start (0 at
    start). e.g. upload at t_session_s=5.0, picker_open at 10.0, etc. Every row
    has it; the {pid}_rounds.csv summary also carries upload_t_session_s.
  * delay_s is always the DESIGN delay (8/20/40), independent of FAST_DEBUG.
  * EVERY "Check likes" press is its own event=="check" row, INCLUDING the press
    that reveals the reward (is_reveal==1), which is immediately followed by a
    reward_revealed row carrying the like count. So:
        total checks in a round == number of rows with event=="check"
    (equivalently the checks_this_round column on the reward_revealed row).

  DROP-OUTS are countable: everyone who starts gets a {pid}_schedule.csv, so a
    participant is a drop-out iff their event log has NO 'finish' event. An
    explicit Exit also writes a task_exit row carrying rounds_completed=K.
    {pid}_rounds.csv has exactly K rows for a drop-out, 30 for a completer.

  Supports survival/hazard + Hawkes (timestamp of every check incl. the revealing
  one) AND RL (reward, prediction error, expectation per round).
"""

import os
import csv
import uuid
import random
import datetime
from flask import (
    Flask, render_template_string, request,
    redirect, url_for, send_from_directory, session
)

app = Flask(__name__)
app.secret_key = "change-me-for-real-study"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEME_DIR = os.path.join(BASE_DIR, "memes")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(MEME_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# ===================== STUDY CONFIG (edit here) ==========================
N_ROUNDS = 30
DELAY_SECONDS = [30, 60, 90]                 # short / medium / long  (waiting time)
REWARD_BANDS = {                            # like ranges per reward level
    "low":    (3, 12),
    "medium": (13, 26),
    "high":   (27, 39),
}
REWARD_LEVELS = ["low", "medium", "high"]
GROUPS = ["control", "treatment"]
MEMES_PER_PICKER = 4
ALPHA_FOR_LIVE_EXPECTATION = 0.3            # only for logging an expectation column
FAST_DEBUG = False                          # True -> every delay = DEBUG_SECONDS
DEBUG_SECONDS = 3
DEMO_MODE = True                            # True -> show assigned group on screen
AUTOCLOSE_SECONDS = 4                        # posted screen auto-returns to feed

USERNAMES = ["polople", "Idon'tknoiwww", "goodenoughforjazz", "razzle_dazzle", "lena.morris", "tommy.hayes", "sara.klein", "noah.turner", "mia.bennett", "jake.collins", "emma.wright", "leo.mason", "ruby.carter",
             "oliver.reed", "nina.parker", "sam.holland", "ella.foster", "max.walsh", "ava.brooks", "harry.cooper", "zoe.mitchell", "ben.warren", "isla.hughes", "charlie.gray", "molly.price", "daniel.king", "lucy.bailey",
              "alex.morgan", "sophie.clark", "ethan.scott", "grace.evans", "josh.hunter", "amelia.wood", "ryan.stone", "hannah.bell", "liam.fisher", "maya.green", "jack.elliott", "chloe.hill", "owen.mills", "lily.barker",
              "adam.porter", "eva.marshall", "dylan.cross", "megan.foxley", "nathan.wells", "alice.hart", "finn.russell", "rosie.bishop", "matthew.lane", "freya.knight", "callum.shaw", "layla.spencer", "archie.west",
              "xXn3v3rth3l3ssXx", "demonic_doggo", "night_owl", "pixel_goblin", "tha_algorithm", "main_character_88", "lurker_no1", "vibe_curator", "Joanna", "nina474", "ryan.g23", "mia543", "caseyka206", "ella_smith",
              "tobyx14", "Luke_graham", "Kristy.MS", "Sarah_McKenzie", "Mike_j22", "Jordan_Ga", "emma_tk", "chris920", "nina.w3r", "user_4729", "daily_viewer18", "skyline_203", "quietscroll", "bluepixel_91", "randomleaf",
               "night_owl64", "simple.feed", "cloudyuser", "softsignal", "hiddenframe", "orbit_275", "casualtap", "moodscroll_8", "greybutton", "upload_317", "neutralwave", "feedrunner", "plainprofile", "echo_609"]
CAPTIONS = [""]


# ---------- meme pool ----------
def meme_pool():
    files = [f for f in os.listdir(MEME_DIR)
             if f.lower().endswith((".jpeg", ".jpg", ".png", ".gif", ".webp"))]
    def k(f):
        d = "".join(c for c in os.path.splitext(f)[0] if c.isdigit())
        return (0, int(d)) if d else (1, f.lower())
    return sorted(files, key=k)


# ---------- counterbalanced schedule ----------
def build_schedule(rng):
    """
    Build N_ROUNDS (delay_index, reward_level) pairs, fully crossed and
    independent, then shuffle order so neither is confounded with round number.

    3 delays x 3 reward levels = 9 cells. For N_ROUNDS=30: each cell 3x (=27),
    plus 3 filler rounds spread across reward levels at the medium delay, so the
    design stays as balanced as 30 allows. Delay and reward remain independent.
    """
    n_delays = len(DELAY_SECONDS)
    cells = [(d, r) for d in range(n_delays) for r in REWARD_LEVELS]  # 9 cells
    base_reps = N_ROUNDS // len(cells)          # 30 // 9 = 3
    remainder = N_ROUNDS - base_reps * len(cells)  # 3

    sched = cells * base_reps                   # 27 rounds, perfectly crossed
    # fill remainder with each reward level once, at the medium delay index
    mid_delay = n_delays // 2
    fillers = [(mid_delay, r) for r in REWARD_LEVELS][:remainder]
    sched += fillers

    rng.shuffle(sched)                          # decorrelate from round order
    return sched


def fresh_state():
    rng = random.Random()
    pool = meme_pool()
    group = rng.choice(GROUPS)
    sched = build_schedule(rng)

    # realise an exact reward value per round now (kept hidden until revealed)
    rewards = []
    for _, level in sched:
        lo, hi = REWARD_BANDS[level]
        rewards.append(rng.randint(lo, hi))

    # per-round 6-meme picker sets (varying), drawn from the pool
    pickers = []
    for _ in range(N_ROUNDS):
        if len(pool) >= MEMES_PER_PICKER:
            pickers.append(rng.sample(pool, MEMES_PER_PICKER))
        else:                                   # tiny pool fallback
            pickers.append([rng.choice(pool) for _ in range(MEMES_PER_PICKER)] if pool else [])

    # a stable explore feed (cosmetic)
    feed = []
    for f in pool:
        feed.append({"file": f, "user": rng.choice(USERNAMES),
                     "cap": rng.choice(CAPTIONS),
                     "likes": rng.randint(15, 39), "reposts": rng.randint(0, 12),
                     "comments": rng.randint(0, 10)})
    rng.shuffle(feed)

    # Time-sortable, collision-safe participant id: P<UTC timestamp>-<6 hex>.
    pid = ("P" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
           + "-" + uuid.uuid4().hex[:6])

    st = {
        "pid": pid,
        "username": None,           # set on the start/username page
        "group": group,
        "schedule": sched,          # list of [delay_idx, reward_level]
        "rewards": rewards,         # realised like counts per round
        "pickers": pickers,         # 6-meme set per round
        "feed": feed,
        "round": 0,                 # current round (0-based)
        "selected": [None] * N_ROUNDS,
        "upload_time": [None] * N_ROUNDS,
        "revealed": [False] * N_ROUNDS,
        "checks": [0] * N_ROUNDS,   # MEASURED checking presses per round
        # check latencies (s since this round's upload) for every press, incl.
        # the revealing one -> feeds round summary + Hawkes/survival.
        "check_times": [[] for _ in range(N_ROUNDS)],
        "expectation": 0.0,         # running TD expectation (for logging)
        "scroll": 0,
        "feed_liked": {},           # feed posts the participant liked (file -> True)
        "feed_reposted": {},        # feed posts the participant reposted (file -> True)
        "finished": False,
        "started_logged": False,
        "schedule_saved": False,
        "start_iso": None,          # t=0 anchor for the session-relative clock
    }
    return st


REQUIRED_KEYS = set(fresh_state().keys())

# Server-side state store. The full per-participant state (feed of every meme,
# pickers, schedule, ...) is far too large for Flask's 4 KB client-side session
# cookie, so we keep it here keyed by a small session id and put only that id in
# the cookie. Lives for the life of the process, which is all a localhost lab
# task needs; the durable record is the per-participant CSV in data/.
_SESSIONS = {}


def get_state():
    sid = session.get("sid")
    st = _SESSIONS.get(sid) if sid else None
    if not isinstance(st, dict) or not REQUIRED_KEYS.issubset(st.keys()):
        sid = uuid.uuid4().hex
        st = fresh_state()
        _SESSIONS[sid] = st
        session["sid"] = sid          # only the tiny id goes in the cookie
    if not st["started_logged"]:
        write_schedule_file(st)     # full design on disk before anything else
        st["start_iso"] = now_iso()  # t=0 for the session-relative action clock
        log_event(st, "session_start", extra=f"group={st['group']}")
        st["started_logged"] = True
    return st


def save_state(st):
    # st is the same object held in _SESSIONS, so mutations already persist;
    # nothing to write back to the (cookie-based) session.
    pass


def now_iso():
    """Timezone-aware local time, ISO-8601 with UTC offset, ms precision."""
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def now_dt():
    """Timezone-aware 'now' for interval arithmetic (matches now_iso/upload_time)."""
    return datetime.datetime.now().astimezone()


def t_session(st):
    """Absolute action clock: seconds elapsed since this participant's
    session_start (starts at 0). Equivalent on disk to
    (this row's timestamp - the session_start row's timestamp)."""
    s = st.get("start_iso")
    if not s:
        return ""
    return round((now_dt() - datetime.datetime.fromisoformat(s)).total_seconds(), 3)


def design_delay(st, rnd):
    """The counterbalanced design delay (8/20/40). Always logged, regardless of
    FAST_DEBUG, so the design is recoverable from the data even in test runs."""
    return DELAY_SECONDS[st["schedule"][rnd][0]]


def delay_for(st, rnd):
    """Actual wait threshold used to decide readiness (FAST_DEBUG can shrink it)."""
    if FAST_DEBUG:
        return DEBUG_SECONDS
    return DELAY_SECONDS[st["schedule"][rnd][0]]


def reward_level(st, rnd):
    return st["schedule"][rnd][1]


def log_event(st, event, rnd=None, extra="", is_reveal=""):
    path = os.path.join(DATA_DIR, f"{st['pid']}.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "t_session_s", "pid", "username", "group",
                        "round", "event", "delay_s", "reward_level", "reward_value",
                        "meme", "checks_this_round", "expected_reward", "is_reveal",
                        "extra"])
        w.writerow([
            now_iso(),
            t_session(st),
            st["pid"], st["username"] or "", st["group"],
            "" if rnd is None else rnd + 1,
            event,
            "" if rnd is None else design_delay(st, rnd),
            "" if rnd is None else reward_level(st, rnd),
            "" if rnd is None else st["rewards"][rnd],
            "" if rnd is None else (st["selected"][rnd] or ""),
            "" if rnd is None else st["checks"][rnd],
            round(st["expectation"], 4),
            is_reveal,
            extra,
        ])


def write_schedule_file(st):
    """Persist the full pre-determined design once, at session start, so the
    design (and hence drop-outs) survives even if the participant abandons
    partway: anyone with a *_schedule.csv started; anyone whose event log lacks
    a 'finish' event dropped out."""
    if st["schedule_saved"]:
        return
    path = os.path.join(DATA_DIR, f"{st['pid']}_schedule.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pid", "group", "round", "delay_idx", "delay_s",
                    "reward_level", "reward_value"])
        for i in range(N_ROUNDS):
            w.writerow([st["pid"], st["group"], i + 1,
                        st["schedule"][i][0], design_delay(st, i),
                        reward_level(st, i), st["rewards"][i]])
    st["schedule_saved"] = True


def write_round_summary(st, rnd, exp_before, exp_after):
    """One tidy row per completed round — the format the RL / reward-sensitivity
    models consume directly (no reshaping of the event log needed)."""
    path = os.path.join(DATA_DIR, f"{st['pid']}_rounds.csv")
    new = not os.path.exists(path)
    lat = st["check_times"][rnd]
    # absolute time (session clock) at which this round's post was uploaded
    up_t = ""
    if st["upload_time"][rnd] and st.get("start_iso"):
        up_t = round((datetime.datetime.fromisoformat(st["upload_time"][rnd])
                      - datetime.datetime.fromisoformat(st["start_iso"])
                      ).total_seconds(), 3)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["pid", "group", "round", "delay_s", "reward_level",
                        "reward_value", "upload_t_session_s", "total_checks",
                        "first_check_latency_s", "all_check_latencies_s",
                        "expectation_before", "expectation_after"])
        w.writerow([
            st["pid"], st["group"], rnd + 1,
            design_delay(st, rnd), reward_level(st, rnd), st["rewards"][rnd],
            up_t, st["checks"][rnd],
            f"{lat[0]:.3f}" if lat else "",
            ";".join(f"{x:.3f}" for x in lat),
            round(exp_before, 4), round(exp_after, 4),
        ])


# =========================================================================
# Retro Web 2.0 shell  (same look as before)
# =========================================================================
SHELL = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} :: MemeBook</title>
<style>
  body{margin:0;background:#d8dfea;color:#2b2b2b;
       font:13px/1.45 Tahoma,Geneva,Verdana,sans-serif;padding-bottom:64px;}
  a{color:#3b5998;}
  .topbar{background:#3b5998;border-bottom:3px solid #1a2f5c;color:#fff;padding:0 14px;
          height:44px;display:flex;align-items:center;justify-content:space-between;
          position:sticky;top:0;z-index:30;}
  .logo{font-weight:bold;font-size:21px;letter-spacing:-1px;}
  .logo b{background:#fff;color:#3b5998;padding:0 5px;border-radius:3px;}
  .topbar .right{display:flex;align-items:center;gap:10px;}
  .pid{font-size:11px;color:#aeb9d6;background:#2f4a82;padding:3px 7px;border-radius:3px;}
  .navbtn{background:#5b74b5;border:1px solid #2f4a82;border-top-color:#7e93cf;color:#fff;
          font-weight:bold;font-size:12px;text-decoration:none;padding:5px 12px;border-radius:3px;}
  .navbtn:hover{background:#6b83c4;}
  .page{max-width:600px;margin:16px auto;padding:0 12px;}
  .panel{background:#fff;border:1px solid #b3becd;border-radius:4px;margin-bottom:14px;}
  .panel .hd{background:#edf0f7;border-bottom:1px solid #c5cee0;padding:7px 12px;
             font-weight:bold;color:#3b5998;border-radius:4px 4px 0 0;}
  .panel .bd{padding:14px;}
  h2{margin:0 0 4px;font-size:18px;color:#333;}
  .muted{color:#777;font-size:12px;}
  .btn{display:inline-block;font:bold 13px Tahoma,sans-serif;cursor:pointer;
       text-decoration:none;padding:7px 18px;border-radius:4px;}
  .btn-blue{background:#5b74b5;border:1px solid #29447e;border-top:1px solid #879ac9;color:#fff;}
  .btn-blue:hover{background:#6b83c4;} .btn-blue:disabled{background:#aeb9d6;border-color:#9aa6c4;cursor:not-allowed;}
  .btn-like{background:#dd5b7a;border:1px solid #a83a55;border-top:1px solid #ec88a0;color:#fff;}
  .btn-like:hover{background:#e76e8b;}
  .btn-gray{background:#e9ebf0;border:1px solid #b3becd;color:#3b5998;}
  .bar{height:12px;background:#fff;border:1px solid #9aa6c4;border-radius:3px;overflow:hidden;margin-bottom:12px;}
  .bar > i{display:block;height:100%;background:#7bb661;}
  .post .hd2{display:flex;align-items:center;gap:8px;}
  .ava{width:34px;height:34px;border:1px solid #29447e;border-radius:3px;
       background:#3b5998 url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34"><circle cx="17" cy="12" r="7" fill="%23ffffff"/><rect x="4" y="22" width="26" height="14" rx="7" fill="%23ffffff"/></svg>') center/cover;}
  .post .uname{font-weight:bold;color:#3b5998;font-size:13px;}
  .post .cap{padding:8px 0;color:#444;}
  .imgbox{width:100%;height:340px;border:1px solid #c5cee0;border-radius:3px;
          background:#000;overflow:hidden;}
  .imgbox img{width:100%;height:100%;object-fit:contain;display:block;}
  .pstats{display:flex;gap:14px;font-size:11px;color:#888;padding:8px 0 6px;}
  .pacts{display:flex;gap:6px;border-top:1px solid #e3e8f2;padding-top:8px;}
  .pacts button{flex:1;font:bold 12px Tahoma,sans-serif;cursor:pointer;padding:6px;border-radius:3px;
                background:#eef0f7;border:1px solid #c5cee0;color:#3b5998;}
  .pacts button:hover{background:#e2e7f3;}
  .pacts button.on{background:#dd5b7a;border-color:#a83a55;color:#fff;}
  .pacts button.rp.on{background:#7bb661;border-color:#5e9249;color:#fff;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
  .pick{border:2px solid #c5cee0;border-radius:4px;overflow:hidden;cursor:pointer;padding:0;background:#f7f9fc;}
  .pickbox{width:100%;height:240px;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden;}
  .pickbox img{max-width:100%;max-height:100%;object-fit:contain;display:block;}
  .pick.sel{border-color:#f5a623;box-shadow:0 0 0 2px #f5c542;}
  .big{font-size:46px;line-height:1;}
  .yellowbox{background:#fffbe6;border:1px solid #e6d480;border-radius:4px;padding:12px;
             color:#7a6a1e;font-size:12px;margin-top:12px;}
  hr.r{border:none;border-top:2px dotted #c5cee0;margin:12px 0;}
  .footbar{position:fixed;left:0;right:0;bottom:0;background:#edf0f7;border-top:3px solid #3b5998;
           display:flex;justify-content:center;gap:10px;padding:9px;z-index:30;}
  .btn-exit{background:#c0392b;border:1px solid #7d2218;border-top:1px solid #d9594c;color:#fff;}
  .btn-exit:hover{background:#d04434;}
  .modal-bg{position:fixed;inset:0;background:rgba(20,30,60,.45);display:flex;
            align-items:center;justify-content:center;z-index:60;}
  .modal-card{background:#fff;border:1px solid #29447e;border-radius:6px;max-width:340px;
              width:88%;box-shadow:0 8px 30px rgba(0,0,0,.3);}
  .modal-title{background:#3b5998;color:#fff;font-weight:bold;padding:10px 14px;
               border-radius:6px 6px 0 0;font-size:15px;}
  .modal-msg{padding:16px 14px;color:#333;}
  .modal-acts{display:flex;justify-content:flex-end;gap:8px;padding:0 14px 14px;}
  .likes-big{font-size:72px;font-weight:bold;line-height:1.05;color:#dd5b7a;text-align:center;}
  .likes-cap{text-align:center;color:#777;font-size:13px;margin-top:2px;
             text-transform:uppercase;letter-spacing:1px;}
  .waitcard{background:#eef3ff;border:1px solid #b9c8ec;border-radius:8px;
            padding:18px 14px;margin:6px 0 10px;text-align:center;}
  .wait-label{color:#5a6b8c;font-size:13px;}
  .wait-big{font-size:76px;font-weight:bold;line-height:1;color:#3b5998;}
  .wait-unit{display:block;font-size:14px;font-weight:normal;color:#5a6b8c;
             text-transform:uppercase;letter-spacing:1px;margin-top:2px;}
  .wait-big-text{font-size:40px;font-weight:bold;color:#3b5998;line-height:1.1;margin-top:4px;}
  .toast{position:fixed;left:50%;bottom:80px;transform:translateX(-50%) translateY(20px);
         background:#2b2b2b;color:#fff;padding:9px 16px;border-radius:18px;font-size:12px;
         opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;z-index:70;}
  .toast.show{opacity:.95;transform:translateX(-50%) translateY(0);}
</style></head><body>
<div class="topbar">
  <span class="logo">meme<b>book</b></span>
  <div class="right"><span class="pid">{{ pid }}</span>
    {% if demo %}<span class="pid" style="background:#7a5; color:#fff">group: {{ group }}</span>{% endif %}
    {% if username %}<span class="pid" style="background:#5b74b5">@{{ username }}</span>{% endif %}
    <a class="navbtn" href="{{ url_for('task') }}">Upload a post</a></div>
</div>
<div class="page">{{ body|safe }}</div>
{% if username and footbar %}
<div class="footbar">
  <a class="btn btn-blue" href="{{ url_for('home') }}">&#127968; Back to feed</a>
  {% if waiting %}<button class="btn btn-like" onclick="checkLikes()">&#9825; Check likes</button>{% endif %}
  <button class="btn btn-exit" onclick="confirmExit()">&#10005; Exit task</button>
</div>
{% endif %}
<div id="modal" class="modal-bg" style="display:none">
  <div class="modal-card">
    <div id="modal-title" class="modal-title"></div>
    <div id="modal-msg" class="modal-msg"></div>
    <div class="modal-acts">
      <button id="modal-cancel" class="btn btn-gray" onclick="closeModal()">Cancel</button>
      <button id="modal-ok" class="btn btn-blue">OK</button>
    </div>
  </div>
</div>
<div id="toast" class="toast"></div>
{{ tail|safe }}
<script>
 function toast(m){var t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
   clearTimeout(window.__tt);window.__tt=setTimeout(function(){t.classList.remove('show');},1900);}
 function closeModal(){document.getElementById('modal').style.display='none';}
 function showConfirm(title,msg,okText,onOk,withCancel){
   document.getElementById('modal-title').textContent=title;
   document.getElementById('modal-msg').textContent=msg;
   var ok=document.getElementById('modal-ok');ok.textContent=okText||'OK';ok.onclick=onOk;
   document.getElementById('modal-cancel').style.display=(withCancel===false)?'none':'';
   document.getElementById('modal').style.display='flex';}
 function confirmExit(){showConfirm('End the task?',
   'Are you sure you want to end the task now? Your session will be closed.',
   'End task',function(){location.href="{{ url_for('end_task') }}";},true);}
 function showReward(likes,hasNext){
   document.getElementById('modal-title').textContent='🎉 Likes received!';
   document.getElementById('modal-msg').innerHTML=
     '<div class="likes-big">'+likes+'</div><div class="likes-cap">likes received</div>';
   var ok=document.getElementById('modal-ok');ok.textContent=hasNext?'Continue':'See results';
   ok.onclick=function(){location.href=hasNext?"{{ url_for('home') }}":"{{ url_for('task') }}";};
   document.getElementById('modal-cancel').style.display='none';
   document.getElementById('modal').style.display='flex';}
 function checkLikes(){
   fetch("{{ url_for('check') }}",{method:'POST'}).then(function(r){return r.json();})
   .then(function(d){
     if(d.state==='revealed'){ showReward(d.likes, d.has_next); }
     else if(d.state==='waiting'){ toast('No likes yet — check again in a moment.'); }
     else{ location.href="{{ url_for('home') }}"; }
   }).catch(function(){ toast('Something went wrong — please try again.'); });}
</script>
</body></html>
"""

START_BODY = """
<div class="panel"><div class="hd">Create your username</div><div class="bd">
  <p>Welcome! Before you begin, choose a <b>username</b>. This is the name your
     posts will appear under.</p>
  <p class="muted">When you upload a post, it will be shared on another platform
     where a panel of <b>40 people</b> will see it and decide whether to like it.
     Your username is how your posts are identified there.</p>
  <div style="margin-top:14px">
    <input id="uname" type="text" maxlength="20" placeholder="e.g. meme_master"
      style="width:100%;box-sizing:border-box;padding:9px;border:1px solid #9aa6c4;
             border-radius:4px;font:14px Tahoma,sans-serif" oninput="val()">
    <div id="err" class="muted" style="color:#a83a55;margin-top:6px"></div>
    <div style="text-align:center;margin-top:14px">
      <button id="go" class="btn btn-blue" disabled onclick="start()">Start the task &#8594;</button>
    </div>
  </div>
</div></div>
{% if demo %}
<div class="yellowbox"><b>Demo info (hidden from real participants):</b><br>
  Assigned group: <b>{{ group }}</b> &mdash;
  {{ 'certain timing (told exact delay)' if group=='treatment' else 'uncertain timing (told "shortly")' }}.
  Participant ID: {{ pid }}.</div>
{% endif %}
<script>
 const inp=document.getElementById('uname'),go=document.getElementById('go'),err=document.getElementById('err');
 function val(){const v=inp.value.trim();
   if(v.length<3){go.disabled=true;err.textContent=v?'At least 3 characters.':'';}
   else{go.disabled=false;err.textContent='';}}
 function start(){const v=inp.value.trim();if(v.length<3)return;
   location.href="{{ url_for('set_username') }}?u="+encodeURIComponent(v);}
 inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!go.disabled)start();});
</script>
"""

FEED_BODY = """
<div class="panel"><div class="bd" style="padding:10px 14px;color:#555">
  <b>News Feed</b> — browse posts while you wait. Press <i>Upload a post</i> up top
  to continue the task.
</div></div>
{% for p in feed %}
{% set is_liked = p.file in liked %}
{% set is_rep = p.file in reposted %}
<div class="panel post" data-file="{{ p.file }}"><div class="bd">
  <div class="hd2"><div class="ava"></div>
    <div><div class="uname">{{ p.user }}</div><div class="muted">posted a meme</div></div></div>
  <div class="cap">{{ p.cap }}</div>
  <div class="imgbox"><img src="{{ url_for('serve_meme', filename=p.file) }}" alt="meme"></div>
  <div class="pstats"><span><b class="lc">{{ p.likes + (1 if is_liked else 0) }}</b> likes</span>
    <span><b class="rc">{{ p.reposts + (1 if is_rep else 0) }}</b> reposts</span>
    <span>{{ p.comments }} comments</span></div>
  <div class="pacts">
    <button class="lk{{ ' on' if is_liked else '' }}" onclick="tLike(this)">{{ '♥ Liked' if is_liked else '♡ Like' }}</button>
    <button class="rp{{ ' on' if is_rep else '' }}" onclick="tRepost(this)">{{ '⇄ Reposted' if is_rep else '⇄ Repost' }}</button>
    <button onclick="alert('Comments are disabled in this demo')">&#128172; Comment</button>
  </div>
</div></div>
{% endfor %}
<script>
  window.scrollTo(0,{{ scroll }});
  let s=null;window.addEventListener('scroll',()=>{clearTimeout(s);s=setTimeout(()=>{
    fetch("{{ url_for('save_scroll') }}?y="+Math.round(window.scrollY),{method:"POST"});},180);});
  function sib(b,c){return b.closest('.post').querySelector(c);}
  function persist(b,kind,on){var f=b.closest('.post').dataset.file;
    fetch("{{ url_for('feed_action') }}?file="+encodeURIComponent(f)+"&kind="+kind+"&on="+(on?1:0),{method:"POST"});}
  function tLike(b){let c=sib(b,'.lc'),n=+c.textContent,on=b.classList.toggle('on');
    if(on){n++;b.textContent='\\u2665 Liked';}else{n--;b.textContent='\\u2661 Like';}c.textContent=n;persist(b,'like',on);}
  function tRepost(b){let c=sib(b,'.rc'),n=+c.textContent,on=b.classList.toggle('on');
    if(on){n++;b.textContent='\\u21c4 Reposted';}else{n--;b.textContent='\\u21c4 Repost';}c.textContent=n;persist(b,'repost',on);}
</script>
"""

# round hub: either "Upload a post" (not posted) or "Check likes" (waiting)
TASK_BODY = """
<div class="bar"><i style="width:{{ pct }}%"></i></div>
<div class="panel"><div class="hd">Round {{ rnd1 }} of {{ n }}</div><div class="bd" style="text-align:center;padding:24px">
  {% if finished %}
    <div class="big">&#127881;</div>
    <h2>All rounds complete</h2>
    <p class="muted">Thank you — you've finished all {{ n }} uploads.</p>
  {% elif selected is none %}
    <div class="big">&#128247;</div>
    <h2>Ready to upload</h2>
    <p class="muted">Share your next post. You'll pick one meme to upload.</p>
    <div style="margin-top:14px">
      <a class="btn btn-blue" href="{{ url_for('picker') }}">Upload a post &#8594;</a>
    </div>
  {% else %}
    <div class="big">&#9203;</div>
    <h2>Waiting for likes</h2>
    {% if group == 'treatment' %}
    <div class="waitcard">
      <div class="wait-label">Your likes will be available in</div>
      <div class="wait-big">{{ wait_secs }}<span class="wait-unit">seconds</span></div>
    </div>
    {% else %}
    <div class="waitcard">
      <div class="wait-label">Your likes will be available</div>
      <div class="wait-big-text">shortly</div>
    </div>
    {% endif %}
    <div style="margin-top:14px">
      <button class="btn btn-like" onclick="checkLikes()">&#9825; Check likes</button>
      <a class="btn btn-gray" href="{{ url_for('home') }}">Back to feed</a>
    </div>
  {% endif %}
</div></div>
<div class="yellowbox">
  <b>Remember:</b> use all of your upload chances. You must receive the likes for
  the current post before the next round unlocks.
</div>
"""

PICKER_BODY = """
<div class="bar"><i style="width:{{ pct }}%"></i></div>
<div class="panel"><div class="hd">Round {{ rnd1 }} — pick a meme to post</div><div class="bd">
  <p class="muted" style="margin-top:0">Choose <b>one</b> meme. Once posted, your choice
     is final and it's shared on another platform where people can like it.</p>
  <div class="grid" style="margin-top:10px">
   {% for m in memes %}
     <button class="pick" data-m="{{ m }}" onclick="choose(this)">
       <div class="pickbox"><img src="{{ url_for('serve_meme', filename=m) }}" alt="meme"></div></button>
   {% endfor %}
  </div>
  <div style="text-align:center;margin-top:14px">
    <button id="post" class="btn btn-blue" disabled onclick="postIt()">Post it &#8594;</button>
  </div>
</div></div>
<script>
 let chosen=null;
 function choose(el){document.querySelectorAll('.pick').forEach(p=>p.classList.remove('sel'));
   el.classList.add('sel');chosen=el.dataset.m;document.getElementById('post').disabled=false;}
 function postIt(){if(!chosen)return;
   showConfirm("Warning","Post this meme? You won't be able to change it once posted.",
     "Post it",
     function(){location.href="{{ url_for('post') }}?meme="+encodeURIComponent(chosen);},true);}
</script>
"""

POSTED_BODY = """
<div class="panel"><div class="bd" style="text-align:center;padding:26px">
  <div class="big">&#9989;</div>
  <h2>Upload completed!</h2>
  {% if group == 'treatment' %}
  <div class="waitcard">
    <div class="wait-label">Your likes will be available in</div>
    <div class="wait-big">{{ wait_secs }}<span class="wait-unit">seconds</span></div>
  </div>
  {% else %}
  <div class="waitcard">
    <div class="wait-label">Your likes will be available</div>
    <div class="wait-big-text">shortly</div>
  </div>
  {% endif %}
  <p class="muted">Head back to the feed and enjoy more memes while you wait. Press
     <b>Check likes</b> any time to see if they've arrived.</p>
  <p class="muted" style="margin-top:14px">Returning to the feed in <b id="cd">{{ secs }}</b>s&hellip;</p>
  <div style="margin-top:8px"><a class="btn btn-blue" href="{{ url_for('home') }}">Back to feed now</a></div>
</div></div>
"""
POSTED_TAIL = """
<script>
 let s={{ secs }};const el=document.getElementById('cd');
 const t=setInterval(()=>{s--;if(el)el.textContent=s;
   if(s<=0){clearInterval(t);location.href="{{ url_for('home') }}";}},1000);
</script>
"""

END_BODY = """
<div class="panel"><div class="bd" style="text-align:center;padding:30px">
  <div class="big">&#128075;</div>
  <h2>Task ended</h2><hr class="r">
  <p class="muted">Your session has been ended. Thank you for taking part.</p>
  <p class="muted">You can now close this window.</p>
</div></div>
"""


def wait_message(st, rnd):
    """Group-dependent timing message. Same actual delay; only certainty differs."""
    if st["group"] == "treatment":
        return f"Your likes will be available in {delay_for(st, rnd)} seconds."
    return "Your likes will be available shortly."


def render(body, title="MemeBook", tail="", footbar=True, **ctx):
    st = get_state()
    r = st["round"]
    # "waiting" = current post is uploaded but the likes haven't been revealed yet,
    # which is when the Check-likes button should be offered (e.g. on the feed).
    waiting = (not st["finished"] and r < N_ROUNDS
               and st["selected"][r] is not None and not st["revealed"][r])
    base = dict(n=N_ROUNDS, feed=st["feed"], scroll=st["scroll"],
                liked=st["feed_liked"], reposted=st["feed_reposted"],
                finished=st["finished"], pid=st["pid"],
                demo=DEMO_MODE, group=st["group"], username=st["username"])
    base.update(ctx)
    inner = render_template_string(body, **base)
    tail_html = render_template_string(tail, **base) if tail else ""
    return render_template_string(SHELL, title=title, body=inner,
                                  tail=tail_html, pid=st["pid"],
                                  demo=DEMO_MODE, group=st["group"],
                                  username=st["username"],
                                  waiting=waiting, footbar=footbar)


def needs_username():
    return get_state()["username"] is None


# =========================================================================
# Routes
# =========================================================================
@app.route("/")
def home():
    if needs_username():
        return render(START_BODY, title="Welcome")
    return render(FEED_BODY, title="Feed")


@app.route("/set_username")
def set_username():
    st = get_state()
    u = (request.args.get("u", "") or "").strip()[:20]
    if len(u) >= 3 and st["username"] is None:
        st["username"] = u
        save_state(st)
        log_event(st, "username_set", extra=f"username={u}")
    return redirect(url_for("home"))


@app.route("/save_scroll", methods=["POST"])
def save_scroll():
    st = get_state()
    st["scroll"] = request.args.get("y", 0, type=int)
    save_state(st)
    return ("", 204)


@app.route("/feed_action", methods=["POST"])
def feed_action():
    """Persist a like/repost toggle on a feed post so it survives navigation."""
    st = get_state()
    f = request.args.get("file", "")
    kind = request.args.get("kind", "")
    on = request.args.get("on", "0") == "1"
    store = {"like": st["feed_liked"], "repost": st["feed_reposted"]}.get(kind)
    if store is not None and f:
        if on:
            store[f] = True
        else:
            store.pop(f, None)
        save_state(st)
    return ("", 204)


@app.route("/task")
def task():
    st = get_state()
    if st["username"] is None:
        return redirect(url_for("home"))
    r = st["round"]
    pct = int(100 * r / N_ROUNDS)
    if not st["finished"]:
        log_event(st, "round_open", r)
    return render(TASK_BODY, title="Task",
                  rnd1=min(r + 1, N_ROUNDS), pct=pct,
                  selected=None if st["finished"] else st["selected"][r],
                  wait_msg="" if st["finished"] else wait_message(st, r),
                  wait_secs="" if st["finished"] else delay_for(st, r))


@app.route("/picker")
def picker():
    st = get_state()
    r = st["round"]
    if st["finished"] or st["selected"][r] is not None:
        return redirect(url_for("task"))
    log_event(st, "picker_open", r)
    pct = int(100 * r / N_ROUNDS)
    return render(PICKER_BODY, title=f"Round {r+1}",
                  rnd1=r + 1, pct=pct, memes=st["pickers"][r])


@app.route("/post")
def post():
    st = get_state()
    r = st["round"]
    meme = request.args.get("meme", "")
    if st["finished"] or st["selected"][r] is not None:
        return redirect(url_for("task"))
    st["selected"][r] = meme
    st["upload_time"][r] = now_iso()
    save_state(st)
    log_event(st, "upload", r)
    return render(POSTED_BODY, title="Posted", tail=POSTED_TAIL,
                  wait_msg=wait_message(st, r), wait_secs=delay_for(st, r),
                  secs=AUTOCLOSE_SECONDS)


@app.route("/check", methods=["POST"])
def check():
    """Each press = one Check-likes = the MEASURED behaviour. Returns JSON so the
    UI can show an inline tooltip while still waiting (no extra page/navigation)
    or a reward pop-up once the likes are revealed."""
    st = get_state()
    r = st["round"]
    if st["finished"] or st["selected"][r] is None:
        return {"state": "invalid"}

    st["checks"][r] += 1   # <-- MEASUREMENT (counts EVERY press)
    started = datetime.datetime.fromisoformat(st["upload_time"][r])
    elapsed = (now_dt() - started).total_seconds()
    st["check_times"][r].append(round(elapsed, 3))
    is_reveal = elapsed >= delay_for(st, r) and not st["revealed"][r]

    # Every press is its own check row, including the revealing one (is_reveal=1).
    # So total checks in a round == count of event=="check" rows for that round.
    log_event(st, "check", r, is_reveal=1 if is_reveal else 0,
              extra="reveal" if is_reveal else "no_likes_yet")

    if is_reveal:
        st["revealed"][r] = True
        reward = st["rewards"][r]
        # TD expectation update (for logged expectation column / later RL fit)
        exp_before = st["expectation"]
        a = ALPHA_FOR_LIVE_EXPECTATION
        exp_after = exp_before + a * (reward - exp_before)
        st["expectation"] = exp_after
        log_event(st, "reward_revealed", r, is_reveal=1, extra=f"likes={reward}")
        write_round_summary(st, r, exp_before, exp_after)
        st["round"] = r + 1
        has_next = st["round"] < N_ROUNDS
        if not has_next:
            st["finished"] = True
            log_event(st, "finish")
        else:
            log_event(st, "round_complete", r)
        save_state(st)
        return {"state": "revealed", "likes": reward, "has_next": has_next}

    return {"state": "waiting"}


@app.route("/meme/<path:filename>")
def serve_meme(filename):
    return send_from_directory(MEME_DIR, filename)


@app.route("/end")
def end_task():
    """User-triggered termination (from the Exit button, after confirmation)."""
    st = get_state()
    if st["username"] is None:
        return redirect(url_for("home"))
    if not st["finished"]:
        # st["round"] (0-based) == number of rounds already completed, so explicit
        # drop-outs are countable straight from the task_exit row.
        log_event(st, "task_exit", st["round"],
                  extra=f"user_terminated;rounds_completed={st['round']}")
        st["finished"] = True
    return render(END_BODY, title="Task ended", footbar=False)


@app.route("/reset")
def reset():
    sid = session.pop("sid", None)
    _SESSIONS.pop(sid, None)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
