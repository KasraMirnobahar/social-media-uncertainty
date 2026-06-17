# Social Media Uncertainty

A local Python (Flask) task for a behavioural research project on **social-media
checking behaviour** — how the *timing uncertainty* of social rewards (likes) and
the *magnitude* of those rewards shape how often people check for feedback.

Participants play a retro meme-board ("MemeBook"): over 30 rounds they upload a
meme, then repeatedly press **Check likes** while waiting for the likes to arrive
after a pre-set delay. Each check press is the measured behaviour.

## Design
The task is originally adapted from <a href = "https://www.science.org/doi/full/10.1126/sciadv.adp8775"> this study </a>, and then added some feature and used in my own study. However, the current version has some significant improvement to compare the previous versions. If you are interested to know more about previoius version and why I have changed it, please feel free to contact me.

- **30 upload rounds** (round count chosen from a parameter-recovery simulation).
- **Two groups**, assigned randomly at session start (same actual delays; only the
  message differs):
  - `control` — *uncertain* timing: "Your likes will be available shortly."
  - `treatment` — *certain* timing: "Your likes will be available in X seconds."
- Each round has a pre-determined **(delay, reward-level)** pair, **fully crossed**
  so delay and reward magnitude are independent and neither is confounded with
  round order:
  - delays: **8 / 20 / 40 s**
  - reward levels: **low** 3–12, **medium** 13–26, **high** 27–40 likes

This supports three analyses: timing-uncertainty effects (control vs treatment),
reinforcement-learning modelling of how received likes shape later checking, and
habit/self-excitation (Hawkes / survival) modelling using the timing of every check.

## Run it

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Drop meme images into `memes/` as `feedN.jpeg` (a pool is included).

## Data

Data is written **locally** to `data/`, one set of CSVs per participant.

Per participant (`pid` is a time-sortable, collision-safe id):

| File | Contents |
|------|----------|
| `{pid}.csv` | Event log — one row per action, with a timezone-aware `timestamp` and `t_session_s` (an absolute clock in seconds from session start). Every Check-likes press is its own `check` row, including the revealing press (`is_reveal=1`). |
| `{pid}_schedule.csv` | The full 30-round counterbalanced design, written once at session start (so the design is recoverable even for drop-outs). |
| `{pid}_rounds.csv` | Tidy one-row-per-round summary (delay, reward, total checks, check latencies, TD expectation before/after) for the RL models. |

## Analysis helpers

- `check_data.py [pid]` — verifies a participant's files are internally consistent
  (check counts, reward values, round coverage, counterbalancing). Defaults to the
  most recent participant.
- `dropout_report.py` — scans `data/` and tabulates completers vs. drop-outs
  (explicit Exit vs. silent), with per-group drop-out rates.

  ## Consider in your study!

- You may want to add a brief mood-check to the task if you are interested in understanding how mood plays a role in reward-seeking or checking behaviour. For example, you could measure how participants feel before receiving rewards, after receiving rewards, or during the waiting period. The exact placement of the mood-check depends on the specific question you want to answer.

- You may also consider using short videos or reels instead of memes, as social media platforms are now increasingly video-based. This could provide richer and more ecologically valid data, allowing you to examine behaviours such as watch time, rewatching, skipping, or engagement patterns. However, videos may also introduce additional confounds.
 
# Citation
Feel free to use this project into your study. Please make sure, you properly cite this study and the original one properly in your project:
- <a href = "https://www.science.org/doi/full/10.1126/sciadv.adp8775"> Ana da Silva Pinho et al. ,Youths’ sensitivity to social media feedback: A computational account.Sci. Adv.10,eadp8775(2024).DOI:10.1126/sciadv.adp8775 </a>

- The current one is under the process of publication, please contact me for the citation!

## Repository structure

```text
social-media-uncertainty/
├── app.py              # the Flask task
├── check_data.py       # per-participant consistency checker
├── dropout_report.py   # dataset-level completion / drop-out report
├── requirements.txt
├── .gitignore          # excludes data/, __pycache__/, *.zip
├── README.md
└── memes/              # meme image pool (feedN.jpeg)
```
