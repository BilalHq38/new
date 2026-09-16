import os
import shutil
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth import (
    check_credentials, login_response, logout_response,
    get_admin_user, require_admin, ADMIN_USERNAME, ADMIN_PASSWORD
)
from database import (
    init_db, get_db, get_settings, update_settings, extra_config, EXTRA_PIGEON_NUMBER,
    seconds_to_hhmmss, hhmmss_to_seconds, get_round_dates,
    get_home_banners, add_home_banner, delete_home_banner,
    seconds_to_hhmm, normalise_clock, flight_seconds, score_durations,
    get_round_meta, save_round_meta, release_time_for_round,
)

load_dotenv()

app = FastAPI()

import re as _re

def regex_replace(value, pattern, replacement):
    return _re.sub(pattern, replacement, value)

app.mount("/static", StaticFiles(directory="static"), name="static")
# static/images/ is served under /static/images/ automatically via the mount above
templates = Jinja2Templates(directory="templates")
templates.env.filters["regex_replace"] = regex_replace

BANNER_DIR = "static/uploads/banners"
SITE_BANNER_DIR = "static/uploads/site"
PARTICIPANT_DIR = "static/uploads/participants"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_HOME_BANNERS = 5

TEMPLATES_MAP = {
    "1r7p":  {"num_rounds": 1,  "num_pigeons": 7,  "label": "1 Day – 7 Pigeons"},
    "3r7p":  {"num_rounds": 3,  "num_pigeons": 7,  "label": "3 Days – 7 Pigeons"},
    "7r7p":  {"num_rounds": 7,  "num_pigeons": 7,  "label": "7 Days – 7 Pigeons"},
    "15r7p": {"num_rounds": 15, "num_pigeons": 7,  "label": "15 Days – 7 Pigeons"},
    "1d3p":  {"num_rounds": 1,  "num_pigeons": 3,  "label": "1 Day – 3 Pigeons"},
    "3d3p":  {"num_rounds": 3,  "num_pigeons": 3,  "label": "3 Days – 3 Pigeons"},
    "7d3p":  {"num_rounds": 7,  "num_pigeons": 3,  "label": "7 Days – 3 Pigeons"},
    "15d3p": {"num_rounds": 15, "num_pigeons": 3,  "label": "15 Days – 3 Pigeons"},
    "1d15p":  {"num_rounds": 1,  "num_pigeons": 15, "label": "1 Day – 15 Pigeons"},
    "3d15p":  {"num_rounds": 3,  "num_pigeons": 15, "label": "3 Days – 15 Pigeons"},
    "7d15p":  {"num_rounds": 7,  "num_pigeons": 15, "label": "7 Days – 15 Pigeons"},
    "15d15p": {"num_rounds": 15, "num_pigeons": 15, "label": "15 Days – 15 Pigeons"},
    "custom": {"num_rounds": 1, "num_pigeons": 7, "label": "Custom – choose days and pigeons"},
}


def resolve_shape(template, custom_rounds, custom_pigeons):
    """How many days and how many regular pigeons this tournament flies.

    A template fills both in. "custom" lets the admin type whatever the
    organisers agreed instead.
    """
    if template == "custom":
        return (
            max(1, min(int(custom_rounds or 1), 60)),
            max(1, min(int(custom_pigeons or 1), 50)),
        )
    tpl = TEMPLATES_MAP[template]
    return tpl["num_rounds"], tpl["num_pigeons"]

online_users = {}  # simple in-memory tracker: session_id -> last_seen


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(BANNER_DIR, exist_ok=True)
    os.makedirs(SITE_BANNER_DIR, exist_ok=True)
    os.makedirs(PARTICIPANT_DIR, exist_ok=True)
    os.makedirs("static/images", exist_ok=True)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def save_upload(file: UploadFile, directory: str) -> str:
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid image format.")
    filename = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(directory, filename)
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return filename


def delete_file(directory: str, filename: str):
    if filename:
        path = os.path.join(directory, filename)
        if os.path.exists(path):
            os.remove(path)


def compute_tournament_status(tournament: dict) -> str:
    if tournament["is_published"]:
        return "completed"
    today = date.today().isoformat()
    if tournament["start_date"] > today:
        return "upcoming"
    return "in_progress"


def get_publish_summary(tournament_id: int, conn) -> dict:
    """Return the result totals which are frozen when a tournament is published."""
    row = conn.execute(
        """SELECT COUNT(*) AS lofts,
                  COALESCE(SUM(num_pigeons + CASE WHEN has_extra THEN 1 ELSE 0 END), 0) AS total_pigeons,
                  COALESCE(SUM(pigeons_landed), 0) AS pigeons_landed
           FROM participants
           WHERE tournament_id=?""",
        (tournament_id,)
    ).fetchone()
    total_pigeons = int(row["total_pigeons"])
    pigeons_landed = min(max(int(row["pigeons_landed"]), 0), total_pigeons)
    return {
        "lofts": int(row["lofts"]),
        "total_pigeons": total_pigeons,
        "pigeons_landed": pigeons_landed,
        "pigeons_remaining": total_pigeons - pigeons_landed,
    }


def load_pigeon_times(tournament_id: int, conn) -> dict:
    """{(participant_id, round_number): {pigeon_number: row}}"""
    rows = conn.execute(
        "SELECT * FROM pigeon_times WHERE tournament_id=?", (tournament_id,)
    ).fetchall()
    grouped: dict = {}
    for row in rows:
        key = (row["participant_id"], row["round_number"])
        grouped.setdefault(key, {})[row["pigeon_number"]] = dict(row)
    return grouped


def build_day_for_participant(participant, round_number, pigeon_entries, slots,
                              scoring_limit, extra=None):
    """Turn one participant's pigeon rows for one day into display cells + a day total.

    Every pigeon gets a cell, so a bird that never came home reads "Bad Luck"
    instead of an empty box. A bird with no entry yet reads as pending.

    The named extra bird is appended after the regular run. Its time is always
    shown; whether it is added to the day total depends on the tournament's
    "extra counts" setting, so a loft can fly an extra purely to see how it does.
    """
    extra = extra or {"enabled": False, "name": "", "counts": False}

    def read(number, label, is_extra=False):
        entry = pigeon_entries.get(number)
        arrival = (entry or {}).get("arrival_time") or ""
        base = {"number": number, "label": label, "is_extra": is_extra}
        if arrival:
            seconds = int((entry or {}).get("flight_seconds") or 0)
            return {**base, "state": "landed", "display": arrival, "seconds": seconds,
                    "arrival": arrival, "flight_display": seconds_to_hhmm(seconds)}
        if entry and entry.get("is_missed"):
            return {**base, "state": "missed", "display": "Bad Luck", "seconds": 0, "arrival": ""}
        return {**base, "state": "pending", "display": "–", "seconds": 0, "arrival": ""}

    cells = []
    durations = []
    landed = 0
    missed = 0

    for number in range(1, slots + 1):
        if number > participant["num_pigeons"]:
            cells.append({"number": number, "label": f"P{number}", "is_extra": False,
                          "state": "none", "display": "", "seconds": 0, "arrival": ""})
            continue
        cell = read(number, f"P{number}")
        cells.append(cell)
        if cell["state"] == "landed":
            durations.append(cell["seconds"])
            landed += 1
        elif cell["state"] == "missed":
            missed += 1

    extra_cell = None
    if extra["enabled"]:
        if participant.get("has_extra"):
            extra_cell = read(EXTRA_PIGEON_NUMBER, extra["name"], is_extra=True)
            if extra_cell["state"] == "landed":
                landed += 1
                if extra["counts"]:
                    durations.append(extra_cell["seconds"])
            elif extra_cell["state"] == "missed":
                missed += 1
        else:
            extra_cell = {"number": EXTRA_PIGEON_NUMBER, "label": extra["name"],
                          "is_extra": True, "state": "none", "display": "",
                          "seconds": 0, "arrival": ""}
        cells.append(extra_cell)

    return {
        "cells": cells,
        "seconds": score_durations(durations, scoring_limit),
        "landed": landed,
        "missed": missed,
        "extra_cell": extra_cell,
        "has_entries": bool(landed or missed),
    }


def build_leaderboard(tournament_id: int, conn) -> tuple[list, list]:
    """Build the public standings.

    A day's total is the sum of that day's pigeon flight times, where each flight
    is the arrival clock time minus the day's release time. The grand total is
    every day added together. Days recorded before per-pigeon entry existed fall
    back to the total the admin typed in at the time, so old results still show.
    """
    tournament = dict(conn.execute(
        "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
    ).fetchone())

    num_rounds = tournament["num_rounds"]
    round_dates = get_round_dates(
        tournament["start_date"], num_rounds, tournament["round_interval_days"]
    )
    round_meta = get_round_meta(tournament_id, conn)
    scoring_limit = int(tournament["scoring_pigeons"] or 0)
    extra = extra_config(tournament)

    participants = [
        dict(p) for p in conn.execute(
            "SELECT * FROM participants WHERE tournament_id=? ORDER BY display_order, created_at",
            (tournament_id,)
        ).fetchall()
    ]

    # Every row renders the same number of pigeon columns, so the table lines up
    # even when one loft entered an extra bird.
    slots = max(
        [int(tournament["num_pigeons"] or 0)] + [int(p["num_pigeons"] or 0) for p in participants] + [1]
    )

    times_map = {}
    for row in conn.execute(
        "SELECT * FROM round_times WHERE tournament_id=?", (tournament_id,)
    ).fetchall():
        times_map[(row["participant_id"], row["round_number"])] = dict(row)

    pigeon_map = load_pigeon_times(tournament_id, conn)

    for p in participants:
        p["rounds"] = []
        total_seconds = 0
        best_day_landed = 0
        distinct_landed = set()
        for r in range(1, num_rounds + 1):
            day = build_day_for_participant(
                p, r, pigeon_map.get((p["id"], r), {}), slots, scoring_limit, extra
            )
            legacy = times_map.get((p["id"], r))

            if day["has_entries"]:
                seconds = day["seconds"]
                landed = day["landed"]
                entered = True
            elif legacy and legacy["is_entered"]:
                # Pre-existing day total, entered before per-pigeon times.
                seconds = int(legacy["time_seconds"])
                landed = int(legacy["pigeons_landed"] or 0)
                entered = True
            else:
                seconds = 0
                landed = 0
                entered = False

            for cell in day["cells"]:
                if cell["state"] == "landed":
                    distinct_landed.add(cell["number"])
            if day["extra_cell"] and day["extra_cell"]["state"] == "landed":
                p["extra_home"] = True

            total_seconds += seconds
            best_day_landed = max(best_day_landed, landed)
            p["rounds"].append({
                "round_number": r,
                "round_date": round_dates[r - 1],
                "release_time": release_time_for_round(tournament, round_meta, r),
                "display": seconds_to_hhmm(seconds) if entered else "–",
                "seconds": seconds,
                "cell_class": "time-entered" if entered else "time-zero",
                "pigeons_landed": landed,
                "pigeons_missed": day["missed"],
                "cells": day["cells"],
                "has_entries": day["has_entries"],
            })

        p["total_seconds"] = total_seconds
        p["total_display"] = seconds_to_hhmm(total_seconds) if total_seconds else "–"
        # How many of the loft's own birds came home at least once.
        p["total_landed"] = len(distinct_landed) or best_day_landed
        # What the loft entered, the way the boards write it: "7" or "7 + 1".
        p["pigeons_label"] = (
            f"{p['num_pigeons']} + 1" if (extra["enabled"] and p["has_extra"])
            else str(p["num_pigeons"])
        )
        p["landed_label"] = f"{p['total_landed']} home"

    # Longest total flight time first; lofts with nothing recorded keep the order
    # they were added in, so an empty tournament still reads sensibly.
    participants.sort(key=lambda x: (-x["total_seconds"], x["display_order"], x["id"]))
    for i, p in enumerate(participants):
        p["sr"] = i + 1

    return participants, round_dates


def get_online_count(request: Request) -> int:
    now = datetime.now()
    session_id = request.cookies.get("session_id", str(uuid.uuid4()))
    online_users[session_id] = now
    cutoff = now - timedelta(minutes=5)
    expired = [k for k, v in online_users.items() if v < cutoff]
    for k in expired:
        del online_users[k]
    return len(online_users)


def get_all_tournaments_grouped(conn, display_mode="all"):
    rows = conn.execute(
        "SELECT * FROM tournaments WHERE is_visible=1 ORDER BY is_pinned DESC, created_at DESC"
    ).fetchall()
    tournaments = []
    for row in rows:
        t = dict(row)
        t["status"] = compute_tournament_status(t)
        t["template_label"] = TEMPLATES_MAP.get(t["template"], {}).get("label", t["template"])
        tournaments.append(t)

    if display_mode == "active":
        tournaments = [t for t in tournaments if t["status"] in ("upcoming", "in_progress")]
    elif display_mode == "completed":
        tournaments = [t for t in tournaments if t["status"] == "completed"]
    elif display_mode == "latest":
        tournaments = tournaments[:1]

    upcoming = [t for t in tournaments if t["status"] == "upcoming"]
    in_progress = [t for t in tournaments if t["status"] == "in_progress"]
    completed = [t for t in tournaments if t["status"] == "completed"]
    return upcoming, in_progress, completed, tournaments


# ─────────────────────────────────────────────
# PUBLIC ROUTES
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with get_db() as conn:
        settings = get_settings()
        upcoming, in_progress, completed, all_t = get_all_tournaments_grouped(
            conn, "all"
        )
        featured_row = conn.execute(
            "SELECT * FROM tournaments WHERE is_visible=1 AND show_on_home=1 LIMIT 1"
        ).fetchone()
        featured_tournament = None
        featured_participants = []
        featured_round_dates = []
        if featured_row:
            featured_tournament = dict(featured_row)
            featured_tournament["status"] = compute_tournament_status(featured_tournament)
            featured_participants, featured_round_dates = build_leaderboard(
                featured_tournament["id"], conn
            )
        home_banners = get_home_banners()
    online = get_online_count(request)
    response = templates.TemplateResponse("home.html", {
        "request": request,
        "settings": settings,
        "upcoming": upcoming,
        "in_progress": in_progress,
        "completed": completed,
        "all_tournaments": all_t,
        "home_banners": home_banners,
        "featured_tournament": featured_tournament,
        "featured_participants": featured_participants,
        "featured_round_dates": featured_round_dates,
        "online_count": online,
        "now": datetime.now(),
        "is_admin": bool(get_admin_user(request)),
    })
    session_id = request.cookies.get("session_id", str(uuid.uuid4()))
    response.set_cookie("session_id", session_id, httponly=True, max_age=86400)
    return response


@app.get("/tournament/{tournament_id}", response_class=HTMLResponse)
def tournament_page(request: Request, tournament_id: int):
    with get_db() as conn:
        t_row = conn.execute(
            "SELECT * FROM tournaments WHERE id=? AND is_visible=1", (tournament_id,)
        ).fetchone()
        if not t_row:
            raise HTTPException(status_code=404, detail="Tournament not found")
        tournament = dict(t_row)
        tournament["status"] = compute_tournament_status(tournament)
        tournament["template_label"] = TEMPLATES_MAP.get(
            tournament["template"], {}
        ).get("label", tournament["template"])
        participants, round_dates = build_leaderboard(tournament_id, conn)
        round_meta = get_round_meta(tournament_id, conn)
        settings = get_settings()
        upcoming, in_progress, completed, _ = get_all_tournaments_grouped(conn)

    pigeon_slots = max(
        [int(tournament["num_pigeons"] or 0)]
        + [int(p["num_pigeons"] or 0) for p in participants]
        + [1]
    )
    extra = extra_config(tournament)
    online = get_online_count(request)
    return templates.TemplateResponse("tournament.html", {
        "request": request,
        "tournament": tournament,
        "participants": participants,
        "round_dates": round_dates,
        "round_meta": round_meta,
        "pigeon_slots": pigeon_slots,
        "extra": extra,
        "settings": settings,
        "online_count": online,
        "now": datetime.now(),
        "upcoming": upcoming,
        "in_progress": in_progress,
        "completed": completed,
        "is_admin": bool(get_admin_user(request)),
    })


@app.get("/contact", response_class=HTMLResponse)
def contact(request: Request):
    with get_db() as conn:
        settings = get_settings()
        upcoming, in_progress, completed, _ = get_all_tournaments_grouped(conn)
    online = get_online_count(request)
    return templates.TemplateResponse("contact.html", {
        "request": request,
        "settings": settings,
        "online_count": online,
        "now": datetime.now(),
        "upcoming": upcoming,
        "in_progress": in_progress,
        "completed": completed,
        "is_admin": bool(get_admin_user(request)),
    })


# ─────────────────────────────────────────────
# ADMIN AUTH
# ─────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    if get_admin_user(request):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse("admin/login.html", {
        "request": request, "error": None
    })


@app.post("/admin/login")
def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):
    if check_credentials(username, password):
        return login_response(username)
    return templates.TemplateResponse("admin/login.html", {
        "request": request, "error": "Invalid username or password."
    })


@app.get("/admin/logout")
def admin_logout():
    return logout_response()


# ─────────────────────────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    require_admin(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tournaments ORDER BY created_at DESC"
        ).fetchall()
        tournaments = []
        for row in rows:
            t = dict(row)
            t["status"] = compute_tournament_status(t)
            t["template_label"] = TEMPLATES_MAP.get(t["template"], {}).get("label", t["template"])
            t["participant_count"] = conn.execute(
                "SELECT COUNT(*) FROM participants WHERE tournament_id=?", (t["id"],)
            ).fetchone()[0]
            tournaments.append(t)
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "tournaments": tournaments,
        "is_admin": True,
    })


# ─────────────────────────────────────────────
# ADMIN TOURNAMENT CRUD
# ─────────────────────────────────────────────

@app.get("/admin/tournament/new", response_class=HTMLResponse)
def new_tournament_page(request: Request):
    require_admin(request)
    return templates.TemplateResponse("admin/tournament_form.html", {
        "request": request,
        "tournament": None,
        "templates_map": TEMPLATES_MAP,
        "error": None,
        "is_admin": True,
    })


@app.post("/admin/tournament/new")
async def create_tournament(
    request: Request,
    name: str = Form(...),
    template: str = Form(...),
    start_date: str = Form(...),
    round_interval_days: int = Form(1),
    start_time: str = Form("05:00"),
    custom_rounds: int = Form(1),
    custom_pigeons: int = Form(7),
    scoring_pigeons: int = Form(0),
    extra_pigeon: str = Form("off"),
    extra_pigeon_name: str = Form("Nominated Pigeon"),
    extra_counts: str = Form("off"),
    info_text: str = Form(""),
    page_ticker_text: str = Form(""),
    is_visible: str = Form("off"),
    is_pinned: str = Form("off"),
    banner_image: UploadFile = File(None),
):
    require_admin(request)
    if template not in TEMPLATES_MAP:
        return templates.TemplateResponse("admin/tournament_form.html", {
            "request": request, "tournament": None,
            "templates_map": TEMPLATES_MAP, "error": "Invalid template.",
            "is_admin": True,
        })

    banner_filename = ""
    if banner_image and banner_image.filename:
        banner_filename = save_upload(banner_image, BANNER_DIR)

    num_rounds, num_pigeons = resolve_shape(template, custom_rounds, custom_pigeons)
    scoring_pigeons = max(0, min(int(scoring_pigeons or 0), 100))

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO tournaments
               (name, start_time, template, num_rounds, num_pigeons, scoring_pigeons,
                extra_pigeon, extra_pigeon_name, extra_counts, start_date,
                round_interval_days, info_text, page_ticker_text, banner_image, is_visible, is_pinned)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, start_time, template, num_rounds, num_pigeons, scoring_pigeons,
             1 if extra_pigeon == "on" else 0,
             (extra_pigeon_name or "").strip() or "Nominated Pigeon",
             1 if extra_counts == "on" else 0,
             start_date,
             round_interval_days, info_text, page_ticker_text, banner_filename,
             1 if is_visible == "on" else 0,
             1 if is_pinned == "on" else 0)
        )
        tournament_id = cur.lastrowid

    return RedirectResponse(url=f"/admin/tournament/{tournament_id}/participants", status_code=302)


@app.get("/admin/tournament/{tournament_id}/edit", response_class=HTMLResponse)
def edit_tournament_page(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
    return templates.TemplateResponse("admin/tournament_form.html", {
        "request": request,
        "tournament": dict(row),
        "templates_map": TEMPLATES_MAP,
        "error": None,
        "is_admin": True,
    })


@app.post("/admin/tournament/{tournament_id}/edit")
async def update_tournament(
    request: Request,
    tournament_id: int,
    name: str = Form(...),
    template: str = Form(...),
    start_date: str = Form(...),
    round_interval_days: int = Form(1),
    start_time: str = Form("05:00"),
    custom_rounds: int = Form(1),
    custom_pigeons: int = Form(7),
    scoring_pigeons: int = Form(0),
    extra_pigeon: str = Form("off"),
    extra_pigeon_name: str = Form("Nominated Pigeon"),
    extra_counts: str = Form("off"),
    info_text: str = Form(""),
    page_ticker_text: str = Form(""),
    is_visible: str = Form("off"),
    is_pinned: str = Form("off"),
    banner_image: UploadFile = File(None),
):
    require_admin(request)
    with get_db() as conn:
        existing = dict(conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone())

        banner_filename = existing["banner_image"]
        if banner_image and banner_image.filename:
            delete_file(BANNER_DIR, banner_filename)
            banner_filename = save_upload(banner_image, BANNER_DIR)

        if template not in TEMPLATES_MAP:
            template = existing["template"]
        num_rounds, num_pigeons = resolve_shape(template, custom_rounds, custom_pigeons)

        conn.execute(
            """UPDATE tournaments SET
               name=?, start_time=?, template=?, num_rounds=?, num_pigeons=?, scoring_pigeons=?,
               extra_pigeon=?, extra_pigeon_name=?, extra_counts=?,
               start_date=?, round_interval_days=?, info_text=?, page_ticker_text=?,
               banner_image=?, is_visible=?, is_pinned=?
               WHERE id=?""",
            (name, start_time, template, num_rounds, num_pigeons,
             max(0, min(int(scoring_pigeons or 0), 100)),
             1 if extra_pigeon == "on" else 0,
             (extra_pigeon_name or "").strip() or "Nominated Pigeon",
             1 if extra_counts == "on" else 0,
             start_date, round_interval_days, info_text, page_ticker_text, banner_filename,
             1 if is_visible == "on" else 0,
             1 if is_pinned == "on" else 0,
             tournament_id)
        )

    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/tournament/{tournament_id}/delete")
def delete_tournament(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT banner_image FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if row:
            delete_file(BANNER_DIR, row["banner_image"])
            participants = conn.execute(
                "SELECT image FROM participants WHERE tournament_id=?", (tournament_id,)
            ).fetchall()
            for p in participants:
                delete_file(PARTICIPANT_DIR, p["image"])
        conn.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/tournament/{tournament_id}/toggle-visible")
def toggle_visible(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        conn.execute(
            "UPDATE tournaments SET is_visible = 1 - is_visible WHERE id=?",
            (tournament_id,)
        )
        conn.execute(
            "UPDATE tournaments SET show_on_home=0 WHERE id=? AND is_visible=0",
            (tournament_id,)
        )
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/tournament/{tournament_id}/toggle-pin")
def toggle_pin(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        conn.execute(
            "UPDATE tournaments SET is_pinned = 1 - is_pinned WHERE id=?",
            (tournament_id,)
        )
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/tournament/{tournament_id}/toggle-home-display")
def toggle_home_display(request: Request, tournament_id: int):
    """Select exactly one visible tournament for the full home-page leaderboard."""
    require_admin(request)
    with get_db() as conn:
        tournament = conn.execute(
            "SELECT is_visible, show_on_home FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not tournament:
            raise HTTPException(status_code=404)
        if tournament["show_on_home"]:
            conn.execute("UPDATE tournaments SET show_on_home=0 WHERE id=?", (tournament_id,))
        elif tournament["is_visible"]:
            conn.execute("UPDATE tournaments SET show_on_home=0")
            conn.execute("UPDATE tournaments SET show_on_home=1 WHERE id=?", (tournament_id,))
    return RedirectResponse(url="/admin", status_code=302)


@app.get("/admin/tournament/{tournament_id}/publish", response_class=HTMLResponse)
def publish_tournament_page(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        tournament = dict(row)
        participants, _ = build_leaderboard(tournament_id, conn)
        summary = get_publish_summary(tournament_id, conn)
    return templates.TemplateResponse("admin/publish_confirm.html", {
        "request": request,
        "tournament": tournament,
        "participants": participants,
        "summary": summary,
        "is_admin": True,
    })


@app.post("/admin/tournament/{tournament_id}/publish")
def publish_tournament(
    request: Request,
    tournament_id: int,
    remarks: str = Form(""),
):
    require_admin(request)
    with get_db() as conn:
        summary = get_publish_summary(tournament_id, conn)
        conn.execute(
            """UPDATE tournaments
               SET is_published=1,
                   published_lofts=?,
                   published_total_pigeons=?,
                   published_pigeons_landed=?,
                   published_pigeons_remaining=?,
                   published_at=?,
                   published_remarks=?
               WHERE id=?""",
            (
                summary["lofts"],
                summary["total_pigeons"],
                summary["pigeons_landed"],
                summary["pigeons_remaining"],
                datetime.now().isoformat(timespec="seconds"),
                remarks.strip(),
                tournament_id,
            )
        )
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/tournament/{tournament_id}/unpublish")
def unpublish_tournament(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        conn.execute(
            "UPDATE tournaments SET is_published=0 WHERE id=?", (tournament_id,)
        )
    return RedirectResponse(url="/admin", status_code=302)


# ─────────────────────────────────────────────
# ADMIN PARTICIPANTS
# ─────────────────────────────────────────────

@app.get("/admin/tournament/{tournament_id}/participants", response_class=HTMLResponse)
def manage_participants(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        tournament = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not tournament:
            raise HTTPException(status_code=404)
        participants = conn.execute(
            "SELECT * FROM participants WHERE tournament_id=? ORDER BY display_order, created_at",
            (tournament_id,)
        ).fetchall()
    return templates.TemplateResponse("admin/participants.html", {
        "request": request,
        "tournament": dict(tournament),
        "participants": [dict(p) for p in participants],
        "is_admin": True,
    })


@app.post("/admin/tournament/{tournament_id}/participants/add")
async def add_participant(
    request: Request,
    tournament_id: int,
    name: str = Form(...),
    city: str = Form(""),
    num_pigeons: int = Form(7),
    has_extra: str = Form("off"),
    image: UploadFile = File(None),
):
    require_admin(request)
    num_pigeons = max(1, min(num_pigeons, 100))
    # pigeons_landed starts equal to num_pigeons; admin updates it in Enter Times
    # after each round once actual landing results are known.
    pigeons_landed = num_pigeons
    image_filename = ""
    if image and image.filename:
        image_filename = save_upload(image, PARTICIPANT_DIR)

    with get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM participants WHERE tournament_id=?", (tournament_id,)
        ).fetchone()[0]
        tournament = dict(conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone())
        num_rounds = tournament["num_rounds"]
        start_date = tournament["start_date"]
        interval = tournament["round_interval_days"]
        round_dates = get_round_dates(start_date, num_rounds, interval)

        cur = conn.execute(
            """INSERT INTO participants
               (tournament_id, name, city, image, num_pigeons, has_extra, pigeons_landed, display_order)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                tournament_id, name, city, image_filename, num_pigeons,
                1 if has_extra == "on" else 0,
                pigeons_landed, count + 1,
            )
        )
        participant_id = cur.lastrowid

        for r, rd in enumerate(round_dates, start=1):
            conn.execute(
                """INSERT INTO round_times (participant_id, tournament_id, round_number, round_date)
                   VALUES (?,?,?,?)""",
                (participant_id, tournament_id, r, rd)
            )

    return RedirectResponse(
        url=f"/admin/tournament/{tournament_id}/participants", status_code=302
    )


@app.get("/admin/participant/{participant_id}/edit", response_class=HTMLResponse)
def edit_participant_page(request: Request, participant_id: int):
    require_admin(request)
    with get_db() as conn:
        p = conn.execute(
            "SELECT * FROM participants WHERE id=?", (participant_id,)
        ).fetchone()
        if not p:
            raise HTTPException(status_code=404)
        tournament = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (p["tournament_id"],)
        ).fetchone()
    return templates.TemplateResponse("admin/edit_participant.html", {
        "request": request,
        "participant": dict(p),
        "tournament": dict(tournament) if tournament else None,
        "is_admin": True,
    })


@app.post("/admin/participant/{participant_id}/edit")
async def update_participant(
    request: Request,
    participant_id: int,
    name: str = Form(...),
    city: str = Form(""),
    num_pigeons: int = Form(7),
    has_extra: str = Form("off"),
    image: UploadFile = File(None),
):
    require_admin(request)
    num_pigeons = max(1, min(num_pigeons, 100))
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM participants WHERE id=?", (participant_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Participant not found")
        existing = dict(row)
        image_filename = existing["image"]
        # Preserve whatever pigeons_landed value was set in Enter Times
        pigeons_landed = existing["pigeons_landed"]
        if image and image.filename:
            delete_file(PARTICIPANT_DIR, image_filename)
            image_filename = save_upload(image, PARTICIPANT_DIR)
        conn.execute(
            """UPDATE participants
               SET name=?, city=?, num_pigeons=?, has_extra=?, pigeons_landed=?, image=?
               WHERE id=?""",
            (name, city, num_pigeons, 1 if has_extra == "on" else 0,
             pigeons_landed, image_filename, participant_id)
        )
        tournament_id = existing["tournament_id"]
    return RedirectResponse(
        url=f"/admin/tournament/{tournament_id}/participants", status_code=302
    )


@app.post("/admin/participant/{participant_id}/delete")
def delete_participant(request: Request, participant_id: int):
    require_admin(request)
    with get_db() as conn:
        p = conn.execute(
            "SELECT * FROM participants WHERE id=?", (participant_id,)
        ).fetchone()
        if p:
            delete_file(PARTICIPANT_DIR, p["image"])
            tournament_id = p["tournament_id"]
            conn.execute("DELETE FROM participants WHERE id=?", (participant_id,))
    return RedirectResponse(
        url=f"/admin/tournament/{tournament_id}/participants", status_code=302
    )


# ─────────────────────────────────────────────
# ADMIN ENTER TIMES  (one day at a time, one row per pigeon)
# ─────────────────────────────────────────────

def recalculate_round(conn, tournament_id: int, participant_id: int,
                     round_number: int, scoring_limit: int = 0,
                     extra_counts: bool = False) -> None:
    """Roll the day's pigeon times up into round_times so the public page,
    the home leaderboard and the publish summary all stay in step."""
    rows = conn.execute(
        """SELECT flight_seconds, arrival_time, pigeon_number FROM pigeon_times
           WHERE tournament_id=? AND participant_id=? AND round_number=?""",
        (tournament_id, participant_id, round_number)
    ).fetchall()
    home = [r for r in rows if r["arrival_time"]]
    landed = len(home)
    # The extra bird only adds to the total when the tournament says it should.
    durations = [
        int(r["flight_seconds"]) for r in home
        if r["pigeon_number"] != EXTRA_PIGEON_NUMBER or extra_counts
    ]
    total = score_durations(durations, scoring_limit)

    has_any = conn.execute(
        """SELECT COUNT(*) FROM pigeon_times
           WHERE tournament_id=? AND participant_id=? AND round_number=?
             AND (arrival_time <> '' OR is_missed=1)""",
        (tournament_id, participant_id, round_number)
    ).fetchone()[0]

    if not has_any:
        # Nothing recorded for this day — leave whatever was there before alone.
        return

    conn.execute(
        """UPDATE round_times
           SET time_seconds=?, is_entered=?, pigeons_landed=?
           WHERE participant_id=? AND round_number=? AND tournament_id=?""",
        (total, 1 if landed else 0, landed, participant_id, round_number, tournament_id)
    )


def recalculate_participant_landed(conn, tournament_id: int, participant_id: int) -> None:
    """participants.pigeons_landed feeds the published summary. Count the loft's
    own birds that came home on at least one day."""
    row = conn.execute(
        """SELECT COUNT(DISTINCT pigeon_number) FROM pigeon_times
           WHERE tournament_id=? AND participant_id=? AND arrival_time <> ''""",
        (tournament_id, participant_id)
    ).fetchone()
    distinct_landed = int(row[0] or 0)

    if distinct_landed == 0:
        # No pigeon-level data yet: keep the older per-day totals behaviour.
        legacy = conn.execute(
            """SELECT COALESCE(SUM(pigeons_landed), 0) FROM round_times
               WHERE tournament_id=? AND participant_id=?""",
            (tournament_id, participant_id)
        ).fetchone()[0]
        distinct_landed = int(legacy or 0)

    row = conn.execute(
        "SELECT num_pigeons, has_extra FROM participants WHERE id=?", (participant_id,)
    ).fetchone()
    capacity = int(row["num_pigeons"]) + (1 if row["has_extra"] else 0)
    conn.execute(
        "UPDATE participants SET pigeons_landed=? WHERE id=?",
        (min(distinct_landed, capacity), participant_id)
    )


@app.get("/admin/tournament/{tournament_id}/times", response_class=HTMLResponse)
def enter_times_page(request: Request, tournament_id: int, day: int = 1):
    require_admin(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        tournament = dict(row)
        num_rounds = int(tournament["num_rounds"])
        day = max(1, min(int(day or 1), num_rounds))

        round_dates = get_round_dates(
            tournament["start_date"], num_rounds, tournament["round_interval_days"]
        )
        round_meta = get_round_meta(tournament_id, conn)
        release_time = release_time_for_round(tournament, round_meta, day)
        scoring_limit = int(tournament["scoring_pigeons"] or 0)
        extra = extra_config(tournament)

        participants = [
            dict(p) for p in conn.execute(
                "SELECT * FROM participants WHERE tournament_id=? ORDER BY display_order, created_at",
                (tournament_id,)
            ).fetchall()
        ]
        pigeon_map = load_pigeon_times(tournament_id, conn)

        for p in participants:
            entries = pigeon_map.get((p["id"], day), {})
            day_view = build_day_for_participant(
                p, day, entries, int(p["num_pigeons"]), scoring_limit, extra
            )
            p["pigeons"] = day_view["cells"]
            p["day_seconds"] = day_view["seconds"]
            p["day_display"] = seconds_to_hhmm(day_view["seconds"])
            p["day_landed"] = day_view["landed"]
            p["day_missed"] = day_view["missed"]

        meta = round_meta.get(day, {})

    return templates.TemplateResponse("admin/enter_times.html", {
        "request": request,
        "tournament": tournament,
        "participants": participants,
        "round_dates": round_dates,
        "day": day,
        "day_date": round_dates[day - 1],
        "release_time": release_time,
        "scoring_limit": scoring_limit,
        "extra": extra,
        "first_winner": meta.get("first_winner", ""),
        "last_winner": meta.get("last_winner", ""),
        "day_start_override": meta.get("start_time", ""),
        "is_admin": True,
    })


@app.post("/admin/tournament/{tournament_id}/times/save")
async def save_times(request: Request, tournament_id: int, day: int = 1):
    """Save one day of pigeon arrival times.

    Field names:
      pt_<participant>_<day>_<pigeon>    arrival clock time, e.g. 13:53
      miss_<participant>_<day>_<pigeon>  "on" when the bird did not come home
    """
    require_admin(request)
    form = await request.form()

    with get_db() as conn:
        t_row = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not t_row:
            raise HTTPException(status_code=404)
        tournament = dict(t_row)
        num_rounds = int(tournament["num_rounds"])
        day = max(1, min(int(form.get("day") or day or 1), num_rounds))

        round_meta = get_round_meta(tournament_id, conn)
        day_start_override = normalise_clock(form.get("day_start_time", ""))
        save_round_meta(
            conn, tournament_id, day,
            day_start_override,
            (form.get("first_winner") or "").strip(),
            (form.get("last_winner") or "").strip(),
        )
        round_meta[day] = {
            "start_time": day_start_override,
            "first_winner": form.get("first_winner", ""),
            "last_winner": form.get("last_winner", ""),
        }
        release_time = release_time_for_round(tournament, round_meta, day)

        scoring_limit = int(tournament["scoring_pigeons"] or 0)
        extra = extra_config(tournament)
        day_date = get_round_dates(
            tournament["start_date"], num_rounds, tournament["round_interval_days"]
        )[day - 1]

        participants = conn.execute(
            "SELECT id, num_pigeons, has_extra FROM participants WHERE tournament_id=?",
            (tournament_id,)
        ).fetchall()

        for p in participants:
            participant_id = p["id"]
            numbers = list(range(1, int(p["num_pigeons"]) + 1))
            if extra["enabled"] and p["has_extra"]:
                numbers.append(EXTRA_PIGEON_NUMBER)   # the named extra bird
            for pigeon_number in numbers:
                arrival = normalise_clock(
                    form.get(f"pt_{participant_id}_{day}_{pigeon_number}", "")
                )
                missed = bool(form.get(f"miss_{participant_id}_{day}_{pigeon_number}"))
                if arrival:
                    # A recorded time always wins over a stale "bad luck" tick.
                    missed = False
                seconds = flight_seconds(arrival, release_time) if arrival else 0

                conn.execute(
                    """INSERT INTO pigeon_times
                       (participant_id, tournament_id, round_number, pigeon_number,
                        arrival_time, flight_seconds, is_missed)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(participant_id, round_number, pigeon_number)
                       DO UPDATE SET arrival_time=excluded.arrival_time,
                                     flight_seconds=excluded.flight_seconds,
                                     is_missed=excluded.is_missed""",
                    (participant_id, tournament_id, day, pigeon_number,
                     arrival, seconds, 1 if missed else 0)
                )

            # Birds beyond the loft's current count (the admin lowered it later).
            conn.execute(
                """DELETE FROM pigeon_times
                   WHERE tournament_id=? AND participant_id=? AND pigeon_number > ?""",
                (tournament_id, participant_id, int(p["num_pigeons"]))
            )
            if not (extra["enabled"] and p["has_extra"]):
                conn.execute(
                    """DELETE FROM pigeon_times
                       WHERE tournament_id=? AND participant_id=? AND pigeon_number=?""",
                    (tournament_id, participant_id, EXTRA_PIGEON_NUMBER)
                )

            # Make sure the day row exists before rolling totals into it.
            conn.execute(
                """INSERT OR IGNORE INTO round_times
                   (participant_id, tournament_id, round_number, round_date)
                   VALUES (?,?,?,?)""",
                (participant_id, tournament_id, day, day_date)
            )
            recalculate_round(conn, tournament_id, participant_id, day,
                              scoring_limit, extra["counts"])
            recalculate_participant_landed(conn, tournament_id, participant_id)

    return RedirectResponse(
        url=f"/admin/tournament/{tournament_id}/times?day={day}&saved=1", status_code=302
    )


# ─────────────────────────────────────────────
# ADMIN SETTINGS
# ─────────────────────────────────────────────

def render_settings_page(request: Request, settings: dict, success=None, error=None):
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "settings": settings,
        "home_banners": get_home_banners(),
        "success": success,
        "error": error,
        "is_admin": True,
    })


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(request: Request):
    require_admin(request)
    return render_settings_page(request, get_settings())


@app.post("/admin/settings")
async def save_settings(
    request: Request,
    ticker_text: str = Form(""),
    detail_ticker_text: str = Form(""),
    contact_content: str = Form(""),
    home_display_mode: str = Form("all"),
    latest_news_text: str = Form(""),
    home_banner_images: list[UploadFile] = File([]),
):
    require_admin(request)
    settings = get_settings()
    uploads = [image for image in home_banner_images if image and image.filename]
    current_banners = get_home_banners()
    available_slots = MAX_HOME_BANNERS - len(current_banners)

    if len(uploads) > available_slots:
        return render_settings_page(
            request, settings,
            error=f"You can add {available_slots} more banner(s). The carousel holds up to {MAX_HOME_BANNERS} images.",
        )

    if any(os.path.splitext(image.filename)[1].lower() not in ALLOWED_EXTENSIONS for image in uploads):
        return render_settings_page(
            request, settings,
            error="Banner images must be JPG, PNG, GIF, or WebP files.",
        )

    for image in uploads:
        add_home_banner(save_upload(image, SITE_BANNER_DIR))

    updated_banners = get_home_banners()
    legacy_banner_filename = updated_banners[0]["filename"] if updated_banners else ""

    update_settings(
        ticker_text, detail_ticker_text, contact_content, home_display_mode,
        legacy_banner_filename, latest_news_text,
    )
    return render_settings_page(request, get_settings(), success="Settings saved successfully.")


@app.post("/admin/settings/banner/{banner_id}/delete")
def remove_home_banner(request: Request, banner_id: int):
    require_admin(request)
    filename = delete_home_banner(banner_id)
    if filename:
        delete_file(SITE_BANNER_DIR, filename)

    settings = get_settings()
    remaining_banners = get_home_banners()
    update_settings(
        settings["ticker_text"], settings["detail_ticker_text"], settings["contact_content"],
        settings["home_display_mode"],
        remaining_banners[0]["filename"] if remaining_banners else "",
        settings["latest_news_text"],
    )
    return RedirectResponse(url="/admin/settings", status_code=302)


@app.post("/admin/settings/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    require_admin(request)
    import os
    settings = get_settings()
    error = None
    success = None

    if current_password != ADMIN_PASSWORD:
        error = "Current password is incorrect."
    elif new_password != confirm_password:
        error = "New passwords do not match."
    elif len(new_password) < 6:
        error = "Password must be at least 6 characters."
    else:
        env_path = ".env"
        with open(env_path, "r") as f:
            lines = f.readlines()
        with open(env_path, "w") as f:
            for line in lines:
                if line.startswith("ADMIN_PASSWORD="):
                    f.write(f"ADMIN_PASSWORD={new_password}\n")
                else:
                    f.write(line)
        success = "Password updated. Restart the server for changes to take effect."

    return render_settings_page(request, settings, success=success, error=error)
