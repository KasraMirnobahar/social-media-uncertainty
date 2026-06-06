"""
MemeBook — retro Web 2.0 meme task (experimental study app).

Setup:
    python -m pip install flask
    python app.py
    open http://127.0.0.1:5000

Folder layout (next to this file):
    app.py
    memes/   feed1.jpeg ... feed30.jpeg  (30 memes)
             uploads use 6 each: 1-6, 7-12, 13-18, 19-24, 25-30
             the explore feed shows all 30 as scrollable posts
    data/    auto-created; one CSV per participant

MEASUREMENT
    Each upload row on the "Your uploads" page has its own Like button.
    Pressing it checks whether that upload's likes have arrived yet.
    Every press is logged and counted in checks[i]  <-- the study variable.
    Per-upload like-wait (minutes): 2, 3, 1, 2, 3.  Strict order, no going back.

FEED
    Cosmetic like/repost on feed posts (NOT measured). Scroll position is
    remembered, so returning from an upload lands you where you left off.
"""

import os
import csv
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

# ---- study config -------------------------------------------------------
N_UPLOADS = 5
MEMES_PER_UPLOAD = 6
TOTAL_MEMES = 30
WAIT_MINUTES = [2, 3, 1, 2, 3]
FAST_DEBUG = True           # True -> all timers = DEBUG_SECONDS
DEBUG_SECONDS = 6
AUTOCLOSE_SECONDS = 5        # "Upload Completed" screen auto-returns to feed

USERNAMES = ["polople", "bikinimybottom", "goodenoughforjazz", "razzle_dazzle",
             "xXn3v3rth3l3ssXx", "demonic_doggo", "night_owl", "pixel_goblin",
             "tha_algorithm", "main_character_88", "lurker_no1", "vibe_curator"]
CAPTIONS = ["where is your god now", "what has it got in its nasty pocketses",
            "acidic vs basic solution lol", "i bet that suitcase is jam packed",
            "demonic little asshat but u love him", "razzle dazzle",
            "real ones know", "no thoughts head empty", "its giving",
            "rate this 1-10", "tag someone", "fr fr"]


def wait_seconds(i):
    return DEBUG_SECONDS if FAST_DEBUG else WAIT_MINUTES[i] * 60


def meme_set(i):
    start = i * MEMES_PER_UPLOAD + 1
    return [f"feed{n}.jpeg" for n in range(start, start + MEMES_PER_UPLOAD)]


def fresh_state():
    rng = random.Random()
    feed = []
    for n in range(1, TOTAL_MEMES + 1):
        feed.append({
            "file": f"feed{n}.jpeg",
            "user": rng.choice(USERNAMES),
            "cap": rng.choice(CAPTIONS),
            "likes": rng.randint(15, 39),
            "reposts": rng.randint(0, 12),
            "comments": rng.randint(0, 10),
        })
    rng.shuffle(feed)
    return {
        "pid": "P" + str(random.randint(10000, 99999)),
        "feed": feed,
        "current": 0,
        "selected": [None] * N_UPLOADS,
        "upload_time": [None] * N_UPLOADS,
        "likes": [None] * N_UPLOADS,
        "revealed": [False] * N_UPLOADS,
        "checks": [0] * N_UPLOADS,
        "scroll": 0,                # remembered feed scroll position (px)
        "finished": False,
    }


REQUIRED_KEYS = set(fresh_state().keys())


def get_state():
    st = session.get("task")
    if not isinstance(st, dict) or not REQUIRED_KEYS.issubset(st.keys()):
        st = fresh_state()
        session["task"] = st
    return st


def save_state(st):
    session["task"] = st
    session.modified = True


def log_event(st, event, upload_idx=None, extra=""):
    path = os.path.join(DATA_DIR, f"{st['pid']}.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "pid", "event", "upload",
                        "meme", "checks_so_far", "extra"])
        w.writerow([
            datetime.datetime.now().isoformat(timespec="seconds"),
            st["pid"], event,
            "" if upload_idx is None else upload_idx + 1,
            "" if upload_idx is None else (st["selected"][upload_idx] or ""),
            "" if upload_idx is None else st["checks"][upload_idx],
            extra,
        ])


# =========================================================================
# Retro Web 2.0 shell
# =========================================================================
SHELL = """
<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} :: MemeBook</title>
<style>
  body{margin:0;background:#d8dfea;color:#2b2b2b;
       font:13px/1.45 Tahoma,Geneva,Verdana,sans-serif;padding-bottom:64px;}
  a{color:#3b5998;}
  .topbar{background:#3b5998;border-bottom:3px solid #1a2f5c;color:#fff;
          padding:0 14px;height:44px;display:flex;align-items:center;
          justify-content:space-between;position:sticky;top:0;z-index:30;}
  .logo{font-weight:bold;font-size:21px;letter-spacing:-1px;}
  .logo b{background:#fff;color:#3b5998;padding:0 5px;border-radius:3px;}
  .topbar .right{display:flex;align-items:center;gap:10px;}
  .pid{font-size:11px;color:#aeb9d6;background:#2f4a82;padding:3px 7px;border-radius:3px;}
  .navbtn{background:#5b74b5;border:1px solid #2f4a82;border-top-color:#7e93cf;
          color:#fff;font-weight:bold;font-size:12px;text-decoration:none;
          padding:5px 12px;border-radius:3px;}
  .navbtn:hover{background:#6b83c4;}
  .page{max-width:600px;margin:16px auto;padding:0 12px;}
  /* chunky panel */
  .panel{background:#fff;border:1px solid #b3becd;border-radius:4px;
         box-shadow:0 1px 0 #fff inset;margin-bottom:14px;}
  .panel .hd{background:#edf0f7;border-bottom:1px solid #c5cee0;
             padding:7px 12px;font-weight:bold;color:#3b5998;border-radius:4px 4px 0 0;}
  .panel .bd{padding:14px;}
  h2{margin:0 0 4px;font-size:18px;color:#333;}
  .muted{color:#777;font-size:12px;}
  /* beveled buttons */
  .btn{display:inline-block;font:bold 13px Tahoma,sans-serif;cursor:pointer;
       text-decoration:none;padding:6px 16px;border-radius:4px;}
  .btn-blue{background:#5b74b5;border:1px solid #29447e;border-top:1px solid #879ac9;
            color:#fff;}
  .btn-blue:hover{background:#6b83c4;}
  .btn-blue:disabled{background:#aeb9d6;border-color:#9aa6c4;cursor:not-allowed;}
  .btn-like{background:#dd5b7a;border:1px solid #a83a55;border-top:1px solid #ec88a0;
            color:#fff;}
  .btn-like:hover{background:#e76e8b;}
  .btn-gray{background:#e9ebf0;border:1px solid #b3becd;color:#3b5998;}
  /* progress dots */
  .steps{display:flex;gap:6px;margin-bottom:12px;}
  .steps .s{flex:1;height:10px;border:1px solid #9aa6c4;border-radius:2px;background:#fff;}
  .steps .s.done{background:#7bb661;border-color:#5e9249;}
  .steps .s.active{background:#f5c542;border-color:#c89b1f;}
  /* upload rows */
  .urow{display:flex;align-items:center;justify-content:space-between;gap:10px;
        border:1px solid #c5cee0;border-radius:4px;padding:10px 12px;margin-bottom:9px;
        background:#f7f9fc;}
  .urow.locked{opacity:.6;background:#eef0f5;border-style:dashed;}
  .urow.done{background:#eef7ea;border-color:#bcd9ad;}
  .urow .lbl{font-weight:bold;color:#333;}
  .urow .sub{font-size:11px;color:#888;}
  /* feed post */
  .post .hd{display:flex;align-items:center;gap:8px;}
  .ava{width:34px;height:34px;border:1px solid #29447e;border-radius:3px;
       background:#3b5998 url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34"><circle cx="17" cy="12" r="7" fill="%23ffffff"/><rect x="4" y="22" width="26" height="14" rx="7" fill="%23ffffff"/></svg>') center/cover;}
  .post .uname{font-weight:bold;color:#3b5998;font-size:13px;}
  .post .cap{padding:0 0 8px;color:#444;}
  .post .imgbox{width:100%;height:340px;border:1px solid #c5cee0;border-radius:3px;
                background:#000;overflow:hidden;}
  .post .imgbox img{width:100%;height:100%;object-fit:contain;display:block;}
  .pstats{display:flex;gap:14px;font-size:11px;color:#888;padding:8px 0 6px;}
  .pacts{display:flex;gap:6px;border-top:1px solid #e3e8f2;padding-top:8px;}
  .pacts button{flex:1;font:bold 12px Tahoma,sans-serif;cursor:pointer;padding:6px;
                border-radius:3px;background:#eef0f7;border:1px solid #c5cee0;color:#3b5998;}
  .pacts button:hover{background:#e2e7f3;}
  .pacts button.on{background:#dd5b7a;border-color:#a83a55;color:#fff;}
  .pacts button.rp.on{background:#7bb661;border-color:#5e9249;color:#fff;}
  /* picker */
  .grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;}
  .pick{border:2px solid #c5cee0;border-radius:4px;overflow:hidden;cursor:pointer;
        padding:0;background:#f7f9fc;}
  .pick img{width:100%;height:96px;object-fit:cover;display:block;}
  .pick.sel{border-color:#f5a623;box-shadow:0 0 0 2px #f5c542;}
  .big{font-size:46px;line-height:1;}
  .yellowbox{background:#fffbe6;border:1px solid #e6d480;border-radius:4px;
             padding:12px;color:#7a6a1e;font-size:12px;margin-top:12px;}
  hr.r{border:none;border-top:2px dotted #c5cee0;margin:12px 0;}
  /* fixed bottom bar */
  .footbar{position:fixed;left:0;right:0;bottom:0;background:#edf0f7;
           border-top:3px solid #3b5998;display:flex;justify-content:center;
           gap:10px;padding:9px;z-index:30;}
</style></head><body>
<div class="topbar">
  <span class="logo">meme<b>book</b></span>
  <div class="right">
    <span class="pid">{{ pid }}</span>
    <a class="navbtn" href="{{ url_for('uploads') }}">Upload a post</a>
  </div>
</div>
<div class="page">{{ body|safe }}</div>
<div class="footbar">
  <a class="btn btn-blue" href="{{ url_for('home') }}">&#127968; Back to feed</a>
  <a class="btn btn-gray" href="{{ url_for('uploads') }}">My uploads</a>
</div>
{{ tail|safe }}
</body></html>
"""

FEED_BODY = """
<div class="panel"><div class="bd" style="padding:10px 14px;color:#555">
  <b>News Feed</b> — browse posts. Press <i>Upload a post</i> up top to share your own.
</div></div>
{% for p in feed %}
<div class="panel post"><div class="bd">
  <div class="hd"><div class="ava"></div>
    <div><div class="uname">{{ p.user }}</div>
         <div class="muted">posted a meme</div></div></div>
  <div class="cap" style="padding-top:8px">{{ p.cap }}</div>
  <div class="imgbox"><img src="{{ url_for('serve_meme', filename=p.file) }}" alt="meme"></div>
  <div class="pstats"><span><b class="lc">{{ p.likes }}</b> likes</span>
    <span><b class="rc">{{ p.reposts }}</b> reposts</span>
    <span>{{ p.comments }} comments</span></div>
  <div class="pacts">
    <button class="lk" onclick="tLike(this)">&#9825; Like</button>
    <button class="rp" onclick="tRepost(this)">&#8644; Repost</button>
    <button onclick="alert('Comments are disabled in this demo')">&#128172; Comment</button>
  </div>
</div></div>
{% endfor %}
<script>
  // restore scroll position
  window.scrollTo(0, {{ scroll }});
  // remember scroll position (throttled) so uploads return you here
  let st=null;
  window.addEventListener('scroll',()=>{clearTimeout(st);st=setTimeout(()=>{
    fetch("{{ url_for('save_scroll') }}?y="+Math.round(window.scrollY),{method:"POST"});
  },180);});
  function sib(b,c){return b.closest('.post').querySelector(c);}
  function tLike(b){let c=sib(b,'.lc'),n=+c.textContent;
    if(b.classList.toggle('on')){n++;b.textContent='\\u2665 Liked';}else{n--;b.textContent='\\u2661 Like';}c.textContent=n;}
  function tRepost(b){let c=sib(b,'.rc'),n=+c.textContent;
    if(b.classList.toggle('on')){n++;b.textContent='\\u2644 Reposted';}else{n--;b.textContent='\\u2644 Repost';}c.textContent=n;}
</script>
"""

UPLOADS_BODY = """
<div class="steps">
 {% for k in range(n) %}
  <div class="s {{ 'done' if k<current else ('active' if k==current else '') }}"></div>
 {% endfor %}
</div>
<div class="panel"><div class="hd">Your uploads</div><div class="bd">
  <p class="muted" style="margin-top:0">Complete each upload in order. Press a row's
     <b>Check likes</b> button to see if your likes have arrived — you must receive
     the likes for an upload before the next one unlocks.</p>

  {% for k in range(n) %}
    {% if k < current %}
      <div class="urow done">
        <div><span class="lbl">{{ k+1 }}. Upload</span>
             <span class="sub">&#10003; received {{ likes[k] }} likes</span></div>
        <span class="muted">done</span>
      </div>
    {% elif k == current %}
      {% if selected[k] is none %}
        <div class="urow">
          <div><span class="lbl">{{ k+1 }}. Upload</span>
               <span class="sub">not posted yet</span></div>
          <a class="btn btn-blue" href="{{ url_for('upload', i=k) }}">Choose a meme &#8594;</a>
        </div>
      {% else %}
        <div class="urow">
          <div><span class="lbl">{{ k+1 }}. Upload</span>
               <span class="sub">posted &mdash; waiting for likes</span></div>
          <a class="btn btn-like" href="{{ url_for('result', i=k) }}">&#9825; Check likes</a>
        </div>
      {% endif %}
    {% else %}
      <div class="urow locked">
        <div><span class="lbl">{{ k+1 }}. Upload</span>
             <span class="sub">complete the previous upload first</span></div>
        <span>&#128274; locked</span>
      </div>
    {% endif %}
  {% endfor %}

  {% if finished %}
    <div class="urow done" style="justify-content:center">
      &#127881; All 5 uploads complete. Thank you!</div>
  {% endif %}

  <div class="yellowbox">
    <b>Remember:</b><br>
    (1) Make sure you have received the likes from the last post you uploaded.<br>
    (2) Don't forget to use all of your chances to upload a post, otherwise you
        won't be eligible for the prize draw.
  </div>
</div></div>
"""

SELECT_BODY = """
<div class="panel"><div class="hd">Upload {{ idx+1 }} of {{ n }} &mdash; pick a meme</div>
<div class="bd">
  <p class="muted" style="margin-top:0">Choose <b>one</b> meme to post. Once you post it,
     your choice is final &mdash; it's shared on another platform where people can like it.</p>
  <div class="grid" style="margin-top:10px">
   {% for m in memes %}
     <button class="pick" data-m="{{ m }}" onclick="choose(this)">
       <img src="{{ url_for('serve_meme', filename=m) }}" alt="meme"></button>
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
   if(!confirm("Post this meme? You won't be able to change it."))return;
   location.href="{{ url_for('post') }}?i={{ idx }}&meme="+encodeURIComponent(chosen);}
</script>
"""

POSTED_BODY = """
<div class="panel"><div class="bd" style="text-align:center;padding:26px">
  <div class="big">&#9989;</div>
  <h2>Upload completed!</h2>
  <p class="muted">Your post is now shared on another platform. You'll receive your
     likes after <b>{{ minutes }} minute(s)</b>.</p>
  <p class="muted">Head back to the feed and enjoy more memes while you wait. Come back
     to <b>My uploads</b> and press <b>Check likes</b> any time to see if they've arrived.</p>
  <p class="muted" style="margin-top:14px">Returning to the feed in
     <b id="cd">{{ secs }}</b>s&hellip;</p>
  <div style="margin-top:8px">
    <a class="btn btn-blue" href="{{ url_for('home') }}">Back to feed now</a>
  </div>
</div></div>
"""
POSTED_TAIL = """
<script>
 let s={{ secs }};const el=document.getElementById('cd');
 const t=setInterval(()=>{s--;if(el)el.textContent=s;
   if(s<=0){clearInterval(t);location.href="{{ url_for('home') }}";}},1000);
</script>
"""

RESULT_WAIT_BODY = """
<div class="panel"><div class="bd" style="text-align:center;padding:26px">
  <div class="big">&#128533;</div>
  <h2>No likes yet</h2><hr class="r">
  <p class="muted">Your content hasn't received any likes yet, but don't worry.</p>
  <p class="muted">Good content takes time to discover.
     <b style="color:#d9831f">Check back soon</b> &mdash; your first like might be
     just around the corner.</p>
  <div style="margin-top:14px;display:flex;gap:8px;justify-content:center">
    <a class="btn btn-like" href="{{ url_for('result', i=idx) }}">&#9825; Check again</a>
    <a class="btn btn-gray" href="{{ url_for('home') }}">Back to feed</a>
  </div>
</div></div>
"""

RESULT_DONE_BODY = """
<div class="panel"><div class="bd" style="text-align:center;padding:26px">
  <div class="big">&#127881;</div>
  <h2>Congratulations!</h2><hr class="r">
  <p style="font-size:16px">You have received
     <b style="color:#dd5b7a">{{ likes }} likes</b>!</p>
  <div style="margin-top:14px;display:flex;gap:8px;justify-content:center">
    <a class="btn btn-blue" href="{{ url_for('uploads') }}">
       {{ 'Next upload &#8594;'|safe if has_next else 'Finish &#8594;'|safe }}</a>
    <a class="btn btn-gray" href="{{ url_for('home') }}">Back to feed</a>
  </div>
</div></div>
"""


def render(body, title="MemeBook", tail="", **ctx):
    st = get_state()
    base = dict(n=N_UPLOADS, current=st["current"], likes=st["likes"],
                finished=st["finished"], feed=st["feed"],
                selected=st["selected"], scroll=st["scroll"])
    base.update(ctx)                      # route-supplied values win, no collision
    inner = render_template_string(body, **base)
    tail_html = render_template_string(tail, **base) if tail else ""
    return render_template_string(SHELL, title=title, body=inner,
                                  tail=tail_html, pid=st["pid"])


# =========================================================================
# Routes
# =========================================================================
@app.route("/")
def home():
    get_state()
    return render(FEED_BODY, title="Feed")


@app.route("/save_scroll", methods=["POST"])
def save_scroll():
    st = get_state()
    st["scroll"] = request.args.get("y", 0, type=int)
    save_state(st)
    return ("", 204)


@app.route("/uploads")
def uploads():
    get_state()
    return render(UPLOADS_BODY, title="My uploads")


@app.route("/upload")
def upload():
    st = get_state()
    i = request.args.get("i", 0, type=int)
    # can't open the picker if it's not the current upload, already posted, or done
    if i != st["current"] or st["finished"] or st["selected"][i] is not None:
        return redirect(url_for("uploads"))
    return render(SELECT_BODY, title=f"Upload {i+1}", idx=i, memes=meme_set(i))


@app.route("/post")
def post():
    st = get_state()
    i = request.args.get("i", type=int)
    meme = request.args.get("meme", "")
    # lock: refuse if not current, already posted, or finished
    if i != st["current"] or st["finished"] or st["selected"][i] is not None:
        return redirect(url_for("uploads"))
    st["selected"][i] = meme
    st["upload_time"][i] = datetime.datetime.now().isoformat()
    save_state(st)
    log_event(st, "upload", i, extra=f"wait={WAIT_MINUTES[i]}min")
    return render(POSTED_BODY, title="Posted", tail=POSTED_TAIL,
                  idx=i, minutes=WAIT_MINUTES[i], secs=AUTOCLOSE_SECONDS)


@app.route("/result")
def result():
    """Each visit = one Check-likes press = the measured behaviour."""
    st = get_state()
    i = request.args.get("i", type=int)
    if i is None or i != st["current"] or st["selected"][i] is None or st["finished"]:
        return redirect(url_for("uploads"))

    st["checks"][i] += 1   # <-- MEASUREMENT

    started = datetime.datetime.fromisoformat(st["upload_time"][i])
    elapsed = (datetime.datetime.now() - started).total_seconds()
    ready = elapsed >= wait_seconds(i)

    if ready and not st["revealed"][i]:
        st["revealed"][i] = True
        st["likes"][i] = random.randint(8, 95)
        st["current"] = i + 1
        if st["current"] >= N_UPLOADS:
            st["finished"] = True
        save_state(st)
        log_event(st, "likes_revealed", i, extra=f"likes={st['likes'][i]}")
    else:
        save_state(st)
        log_event(st, "like_press", i)

    if st["revealed"][i]:
        return render(RESULT_DONE_BODY, title="Likes received!", idx=i,
                      likes=st["likes"][i], has_next=(i + 1 < N_UPLOADS))
    return render(RESULT_WAIT_BODY, title="No likes yet", idx=i)


@app.route("/meme/<path:filename>")
def serve_meme(filename):
    return send_from_directory(MEME_DIR, filename)


@app.route("/reset")
def reset():
    session.pop("task", None)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)
