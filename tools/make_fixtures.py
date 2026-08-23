"""Generates synthetic fixtures shaped like the real Zoom/Slack/Sheets exports.

Seeded, so the same fixtures regenerate byte-for-byte. No real Fellow data.
"""
import csv, json, random, datetime as dt, pathlib, shutil

random.seed(20260824)
ROOT = pathlib.Path("fixtures")
if ROOT.exists():
    shutil.rmtree(ROOT)
(ROOT / "zoom").mkdir(parents=True)
(ROOT / "slack").mkdir(parents=True)

NAMES = [
    "Amara Osei", "Ben Ortiz", "Chidi Nwosu", "Dana Reyes", "Eli Kaufman",
    "Farah Haddad", "Grace Lin", "Hugo Marchetti", "Imani Brooks", "Jonas Petrov",
    "Keiko Tanaka", "Luis Salazar", "Maya Rahman", "Noor Abadi",
]
CHANNELS = ["general", "introductions", "project-showcase", "help-desk"]

# Engagement archetypes. The point of the spread is to make the report show
# real variation -- including a Fellow who goes quiet, which is exactly the
# case the undefined `falling_behind` term would have to adjudicate.
ARCHETYPES = (
    [("steady", 0.92, 6.0)] * 5 +
    [("bursty", 0.70, 9.0)] * 4 +
    [("quiet", 0.55, 1.2)] * 3 +
    [("fading", 0.60, 3.0)] * 2
)

fellows = []
for i, name in enumerate(NAMES):
    slug = name.lower().replace(" ", ".")
    kind, attend_p, slack_rate = ARCHETYPES[i]
    fellows.append({
        "fellow_id": f"cif26-{i+1:03d}",
        "display_name": name,
        "cohort": "2026-27",
        # Two Fellows join a week late -- the enrolment window has to handle it.
        "joined_on": "2026-09-14" if i in (11, 13) else "2026-09-07",
        "status": "active",
        "zoom_email": f"{slug}@example.org",
        "slack_user_id": f"U{2600+i:04d}",
        "sheets_email": f"{slug}@example.org",
        "_kind": kind, "_attend": attend_p, "_slack": slack_rate,
    })

with open(ROOT / "roster.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=[
        "fellow_id", "display_name", "cohort", "joined_on", "status",
        "zoom_email", "slack_user_id", "sheets_email"])
    w.writeheader()
    for f in fellows:
        w.writerow({k: v for k, v in f.items() if not k.startswith("_")})

WEEK0 = dt.date(2026, 9, 7)
WEEKS = 6

def decay(f, w):
    """A 'fading' Fellow's engagement drops off after week 2."""
    if f["_kind"] == "fading":
        return max(0.15, 1.0 - 0.22 * w)
    return 1.0

# ---- Zoom: one live lesson per week, Thursday 18:00 local -----------------
for w in range(WEEKS):
    day = WEEK0 + dt.timedelta(days=7 * w + 3)
    rows = []
    for f in fellows:
        if dt.date.fromisoformat(f["joined_on"]) > day:
            continue
        if random.random() < f["_attend"] * decay(f, w):
            start = dt.datetime.combine(day, dt.time(18, random.randint(0, 6)))
            minutes = random.choice([58, 61, 55, 47, 62, 12])
            rows.append({
                "Name (Original Name)": f["display_name"],
                "User Email": f["zoom_email"],
                "Join Time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "Leave Time": (start + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"),
                "Duration (Minutes)": minutes,
                "Guest": "No",
            })
    path = ROOT / "zoom" / f"{day.isoformat()}-lesson-{w+1:02d}.csv"
    with open(path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows)

# ---- Slack: daily message files per channel -------------------------------
TEXTS = ["Sharing my draft here", "Great point earlier", "Anyone free to review?",
         "Just pushed my update", "This helped a lot, thanks", "Question about the rubric",
         "Posting my milestone", "Following up on the session"]
by_channel = {c: {} for c in CHANNELS}
for w in range(WEEKS):
    for d in range(5):
        day = WEEK0 + dt.timedelta(days=7 * w + d)
        for f in fellows:
            if dt.date.fromisoformat(f["joined_on"]) > day:
                continue
            n = sum(1 for _ in range(4) if random.random() < (f["_slack"] * decay(f, w)) / 12)
            for _ in range(n):
                ch = random.choice(CHANNELS)
                ts = dt.datetime.combine(day, dt.time(random.randint(9, 21), random.randint(0, 59)))
                msg = {"type": "message", "user": f["slack_user_id"],
                       "ts": f"{ts.timestamp():.6f}", "text": random.choice(TEXTS)}
                # Some messages attract reactions from other Fellows.
                if random.random() < 0.35:
                    reactors = random.sample(
                        [g["slack_user_id"] for g in fellows if g is not f], random.randint(1, 3))
                    msg["reactions"] = [{"name": random.choice(["tada", "raised_hands", "eyes"]),
                                         "users": reactors, "count": len(reactors)}]
                by_channel[ch].setdefault(day.isoformat(), []).append(msg)

# A join notice and a bot post, to prove the adapter filters them out.
first = WEEK0.isoformat()
by_channel["general"].setdefault(first, []).insert(0, {
    "type": "message", "subtype": "channel_join", "user": fellows[0]["slack_user_id"],
    "ts": f"{dt.datetime.combine(WEEK0, dt.time(8,0)).timestamp():.6f}", "text": "has joined"})
by_channel["general"][first].insert(1, {
    "type": "message", "subtype": "bot_message", "bot_id": "B01",
    "ts": f"{dt.datetime.combine(WEEK0, dt.time(8,1)).timestamp():.6f}", "text": "Reminder: lesson Thursday"})

for ch, days in by_channel.items():
    (ROOT / "slack" / ch).mkdir(parents=True, exist_ok=True)
    for day, msgs in days.items():
        msgs.sort(key=lambda m: m["ts"])
        (ROOT / "slack" / ch / f"{day}.json").write_text(json.dumps(msgs, indent=1))

# ---- Sheets: assignment tracker ------------------------------------------
rows = []
for w in range(WEEKS):
    due = WEEK0 + dt.timedelta(days=7 * w + 6)
    for f in fellows:
        if dt.date.fromisoformat(f["joined_on"]) > due:
            continue
        p = {"steady": 0.95, "bursty": 0.78, "quiet": 0.62, "fading": 0.70}[f["_kind"]] * decay(f, w)
        submitted = ""
        if random.random() < p:
            offset = random.choice([-2, -1, -1, 0, 0, 1])
            when = dt.datetime.combine(due + dt.timedelta(days=offset),
                                       dt.time(random.randint(10, 23), random.randint(0, 59)))
            submitted = when.strftime("%Y-%m-%d %H:%M:%S")
        rows.append({"email": f["sheets_email"], "assignment": f"Module {w+1}",
                     "due_on": due.isoformat(), "submitted_at": submitted})

with open(ROOT / "assignments.csv", "w", newline="") as fh:
    wr = csv.DictWriter(fh, fieldnames=["email", "assignment", "due_on", "submitted_at"])
    wr.writeheader(); wr.writerows(rows)

print(f"fellows={len(fellows)} zoom={WEEKS} "
      f"slack_files={sum(len(d) for d in by_channel.values())} assignments={len(rows)}")
