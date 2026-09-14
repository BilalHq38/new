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
    init_db, get_db, get_settings, update_settings,
    seconds_to_hhmmss, hhmmss_to_seconds, get_round_dates,
    get_home_banners, add_home_banner, delete_home_banner,
)

load_dotenv()

app = FastAPI()

import re as _re

def regex_replace(value, pattern, replacement):
    return _re.sub(pattern, replacement, value)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.filters["regex_replace"] = regex_replace

BANNER_DIR = "static/uploads/banners"
SITE_BANNER_DIR = "static/uploads/site"
PARTICIPANT_DIR = "static/uploads/participants"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_HOME_BANNERS = 5

TEMPLATES_MAP = {
    "1r7p":  {"num_rounds": 1,  "num_pigeons": 7,  "label": "1 Round – 7 Pigeons"},
    "3r7p":  {"num_rounds": 3,  "num_pigeons": 7,  "label": "3 Rounds – 7 Pigeons"},
    "7r7p":  {"num_rounds": 7,  "num_pigeons": 7,  "label": "7 Rounds – 7 Pigeons"},
    "15r7p": {"num_rounds": 15, "num_pigeons": 7,  "label": "15 Rounds – 7 Pigeons"},
    "1d3p":  {"num_rounds": 1,  "num_pigeons": 3,  "label": "1 Day – 3 Pigeons"},
    "3d3p":  {"num_rounds": 3,  "num_pigeons": 3,  "label": "3 Days – 3 Pigeons"},
    "7d3p":  {"num_rounds": 7,  "num_pigeons": 3,  "label": "7 Days – 3 Pigeons"},
    "15d3p": {"num_rounds": 15, "num_pigeons": 3,  "label": "15 Days – 3 Pigeons"},
    "1d15p":  {"num_rounds": 1,  "num_pigeons": 15, "label": "1 Day – 15 Pigeons"},
    "3d15p":  {"num_rounds": 3,  "num_pigeons": 15, "label": "3 Days – 15 Pigeons"},
    "7d15p":  {"num_rounds": 7,  "num_pigeons": 15, "label": "7 Days – 15 Pigeons"},
    "15d15p": {"num_rounds": 15, "num_pigeons": 15, "label": "15 Days – 15 Pigeons"},
}

online_users = {}  # simple in-memory tracker: session_id -> last_seen


@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(BANNER_DIR, exist_ok=True)
    os.makedirs(SITE_BANNER_DIR, exist_ok=True)
    os.makedirs(PARTICIPANT_DIR, exist_ok=True)


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
                  COALESCE(SUM(num_pigeons), 0) AS total_pigeons,
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



def build_leaderboard(tournament_id: int, conn) -> tuple[list, list]:
    tournament = dict(conn.execute(
        "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
    ).fetchone())

    num_rounds = tournament["num_rounds"]
    round_dates = get_round_dates(
        tournament["start_date"], num_rounds, tournament["round_interval_days"]
    )

    participants = [
        dict(p) for p in conn.execute(
            "SELECT * FROM participants WHERE tournament_id=? ORDER BY display_order, created_at",
            (tournament_id,)
        ).fetchall()
    ]

    times_rows = conn.execute(
        "SELECT * FROM round_times WHERE tournament_id=?", (tournament_id,)
    ).fetchall()

    times_map = {}
    for row in times_rows:
        times_map[(row["participant_id"], row["round_number"])] = dict(row)

    today = date.today().isoformat()

    for p in participants:
        p["rounds"] = []
        total_seconds = 0
        for r in range(1, num_rounds + 1):
            round_date = round_dates[r - 1]
            entry = times_map.get((p["id"], r))
            if entry and entry["is_entered"]:
                secs = entry["time_seconds"]
                display = seconds_to_hhmmss(secs)
                cell_class = "time-entered"
            else:
                secs = 0
                display = "00:00:00"
                cell_class = "time-zero"
            total_seconds += secs
            p["rounds"].append({
                "round_number": r,
                "round_date": round_date,
                "display": display,
                "seconds": secs,
                "cell_class": cell_class,
            })
        p["total_seconds"] = total_seconds
        p["total_display"] = seconds_to_hhmmss(total_seconds)

    if tournament["is_published"]:
        participants.sort(key=lambda x: x["total_seconds"], reverse=True)
        for i, p in enumerate(participants):
            p["sr"] = i + 1
    else:
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
        settings = get_settings()
        upcoming, in_progress, completed, _ = get_all_tournaments_grouped(conn)

    online = get_online_count(request)
    return templates.TemplateResponse("tournament.html", {
        "request": request,
        "tournament": tournament,
        "participants": participants,
        "round_dates": round_dates,
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

    tpl = TEMPLATES_MAP[template]
    num_rounds = tpl["num_rounds"]
    num_pigeons = tpl["num_pigeons"]

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO tournaments
               (name, start_time, template, num_rounds, num_pigeons, start_date,
                round_interval_days, info_text, page_ticker_text, banner_image, is_visible, is_pinned)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, start_time, template, num_rounds, num_pigeons, start_date,
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

        tpl = TEMPLATES_MAP.get(template, TEMPLATES_MAP[existing["template"]])
        num_rounds = tpl["num_rounds"]
        num_pigeons = tpl["num_pigeons"]

        conn.execute(
            """UPDATE tournaments SET
               name=?, start_time=?, template=?, num_rounds=?, num_pigeons=?,
               start_date=?, round_interval_days=?, info_text=?, page_ticker_text=?,
               banner_image=?, is_visible=?, is_pinned=?
               WHERE id=?""",
            (name, start_time, template, num_rounds, num_pigeons,
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


@app.post("/admin/tournament/{tournament_id}/publish")
def publish_tournament(request: Request, tournament_id: int):
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
                   published_at=?
               WHERE id=?""",
            (
                summary["lofts"],
                summary["total_pigeons"],
                summary["pigeons_landed"],
                summary["pigeons_remaining"],
                datetime.now().isoformat(timespec="seconds"),
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
    pigeons_landed: Optional[int] = Form(None),
    image: UploadFile = File(None),
):
    require_admin(request)
    num_pigeons = max(1, min(num_pigeons, 100))
    pigeons_landed = num_pigeons if pigeons_landed is None else max(
        0, min(pigeons_landed, num_pigeons)
    )
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
               (tournament_id, name, city, image, num_pigeons, pigeons_landed, display_order)
               VALUES (?,?,?,?,?,?,?)""",
            (
                tournament_id, name, city, image_filename, num_pigeons,
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
    return templates.TemplateResponse("admin/edit_participant.html", {
        "request": request,
        "participant": dict(p),
        "is_admin": True,
    })


@app.post("/admin/participant/{participant_id}/edit")
async def update_participant(
    request: Request,
    participant_id: int,
    name: str = Form(...),
    city: str = Form(""),
    num_pigeons: int = Form(7),
    pigeons_landed: int = Form(0),
    image: UploadFile = File(None),
):
    require_admin(request)
    num_pigeons = max(1, min(num_pigeons, 100))
    pigeons_landed = max(0, min(pigeons_landed, num_pigeons))
    with get_db() as conn:
        existing = dict(conn.execute(
            "SELECT * FROM participants WHERE id=?", (participant_id,)
        ).fetchone())
        image_filename = existing["image"]
        if image and image.filename:
            delete_file(PARTICIPANT_DIR, image_filename)
            image_filename = save_upload(image, PARTICIPANT_DIR)
        conn.execute(
            """UPDATE participants
               SET name=?, city=?, num_pigeons=?, pigeons_landed=?, image=?
               WHERE id=?""",
            (name, city, num_pigeons, pigeons_landed, image_filename, participant_id)
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
# ADMIN ENTER TIMES
# ─────────────────────────────────────────────

@app.get("/admin/tournament/{tournament_id}/times", response_class=HTMLResponse)
def enter_times_page(request: Request, tournament_id: int):
    require_admin(request)
    with get_db() as conn:
        tournament = conn.execute(
            "SELECT * FROM tournaments WHERE id=?", (tournament_id,)
        ).fetchone()
        if not tournament:
            raise HTTPException(status_code=404)
        tournament = dict(tournament)
        participants, round_dates = build_leaderboard(tournament_id, conn)
        times_rows = conn.execute(
            "SELECT * FROM round_times WHERE tournament_id=?", (tournament_id,)
        ).fetchall()
        times_map = {}
        for row in times_rows:
            times_map[(row["participant_id"], row["round_number"])] = dict(row)

    return templates.TemplateResponse("admin/enter_times.html", {
        "request": request,
        "tournament": tournament,
        "participants": participants,
        "round_dates": round_dates,
        "times_map": times_map,
        "seconds_to_hhmmss": seconds_to_hhmmss,
        "is_admin": True,
    })


@app.post("/admin/tournament/{tournament_id}/times/save")
async def save_times(request: Request, tournament_id: int):
    require_admin(request)
    form = await request.form()
    with get_db() as conn:
        for key, value in form.items():
            if key.startswith("time_"):
                parts = key.split("_")
                if len(parts) == 3:
                    participant_id = int(parts[1])
                    round_number = int(parts[2])
                    secs = hhmmss_to_seconds(value)
                    is_entered = 1 if value.strip() and value.strip() != "00:00:00" else 0
                    conn.execute(
                        """UPDATE round_times SET time_seconds=?, is_entered=?
                           WHERE participant_id=? AND round_number=? AND tournament_id=?""",
                        (secs, is_entered, participant_id, round_number, tournament_id)
                    )
    return RedirectResponse(
        url=f"/admin/tournament/{tournament_id}/times", status_code=302
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
