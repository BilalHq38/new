import sqlite3
import os
from contextlib import contextmanager
from datetime import date, timedelta

DB_PATH = "pigeon_racing.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ticker_text TEXT DEFAULT 'السلام علیکم! خوش آمدید، جی آیاں نوں . کبوتر بازی اساڑی پہچان۔',
                detail_ticker_text TEXT NOT NULL DEFAULT 'Pigeon flying Tournament',
                contact_content TEXT DEFAULT 'Contact us for more information.',
                home_display_mode TEXT DEFAULT 'all',
                home_banner_image TEXT NOT NULL DEFAULT '',
                latest_news_text TEXT NOT NULL DEFAULT ''
            );

            INSERT OR IGNORE INTO settings (id, ticker_text, contact_content, home_display_mode)
            VALUES (1, 'السلام علیکم! خوش آمدید، جی آیاں نوں . کبوتر بازی اساڑی پہچان۔', 'Contact us for more information.', 'all');

            CREATE TABLE IF NOT EXISTS home_banners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_time TEXT NOT NULL DEFAULT '05:00',
                template TEXT NOT NULL,
                num_rounds INTEGER NOT NULL,
                num_pigeons INTEGER NOT NULL DEFAULT 7,
                scoring_pigeons INTEGER NOT NULL DEFAULT 0,
                extra_pigeon INTEGER NOT NULL DEFAULT 0,
                extra_pigeon_name TEXT NOT NULL DEFAULT 'Nominated Pigeon',
                extra_counts INTEGER NOT NULL DEFAULT 0,
                start_date TEXT NOT NULL,
                round_interval_days INTEGER NOT NULL DEFAULT 1,
                info_text TEXT DEFAULT '',
                page_ticker_text TEXT NOT NULL DEFAULT '',
                banner_image TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'upcoming',
                is_published INTEGER NOT NULL DEFAULT 0,
                is_visible INTEGER NOT NULL DEFAULT 1,
                is_pinned INTEGER NOT NULL DEFAULT 0,
                show_on_home INTEGER NOT NULL DEFAULT 0,
                published_lofts INTEGER NOT NULL DEFAULT 0,
                published_total_pigeons INTEGER NOT NULL DEFAULT 0,
                published_pigeons_landed INTEGER NOT NULL DEFAULT 0,
                published_pigeons_remaining INTEGER NOT NULL DEFAULT 0,
                published_at TEXT NOT NULL DEFAULT '',
                published_remarks TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                city TEXT DEFAULT '',
                image TEXT DEFAULT '',
                num_pigeons INTEGER NOT NULL DEFAULT 7,
                has_extra INTEGER NOT NULL DEFAULT 0,
                pigeons_landed INTEGER NOT NULL DEFAULT 7,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS round_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id INTEGER NOT NULL,
                tournament_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                round_date TEXT NOT NULL,
                time_seconds INTEGER NOT NULL DEFAULT 0,
                is_entered INTEGER NOT NULL DEFAULT 0,
                pigeons_landed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(participant_id, round_number),
                FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            /* One row per pigeon, per participant, per day.
               arrival_time is the clock time the pigeon sat down ("HH:MM").
               flight_seconds is arrival_time minus that day's release time,
               stored so the leaderboard never has to recompute it. */
            CREATE TABLE IF NOT EXISTS pigeon_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id INTEGER NOT NULL,
                tournament_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                pigeon_number INTEGER NOT NULL,   /* 0 = the named extra bird */
                arrival_time TEXT NOT NULL DEFAULT '',
                flight_seconds INTEGER NOT NULL DEFAULT 0,
                is_missed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(participant_id, round_number, pigeon_number),
                FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE CASCADE,
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_pigeon_times_lookup
                ON pigeon_times(tournament_id, round_number);

            /* Per-day extras: an optional release-time override plus the two
               winner-bird announcements shown above the day's table. */
            CREATE TABLE IF NOT EXISTS round_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                start_time TEXT NOT NULL DEFAULT '',
                first_winner TEXT NOT NULL DEFAULT '',
                last_winner TEXT NOT NULL DEFAULT '',
                UNIQUE(tournament_id, round_number),
                FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
            );
        """)

        setting_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()
        }
        if "detail_ticker_text" not in setting_columns:
            conn.execute(
                "ALTER TABLE settings ADD COLUMN detail_ticker_text TEXT NOT NULL DEFAULT 'Pigeon flying Tournament'"
            )
        if "home_banner_image" not in setting_columns:
            conn.execute(
                "ALTER TABLE settings ADD COLUMN home_banner_image TEXT NOT NULL DEFAULT ''"
            )
        if "latest_news_text" not in setting_columns:
            conn.execute(
                "ALTER TABLE settings ADD COLUMN latest_news_text TEXT NOT NULL DEFAULT ''"
            )

        # Keep the previously configured single banner when upgrading to the carousel.
        old_banner = conn.execute(
            "SELECT home_banner_image FROM settings WHERE id=1"
        ).fetchone()[0]
        has_banners = conn.execute("SELECT COUNT(*) FROM home_banners").fetchone()[0]
        if old_banner and not has_banners:
            conn.execute(
                "INSERT INTO home_banners (filename, display_order) VALUES (?, 1)",
                (old_banner,)
            )

        tournament_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tournaments)").fetchall()
        }
        if "page_ticker_text" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN page_ticker_text TEXT NOT NULL DEFAULT ''"
            )
        if "show_on_home" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN show_on_home INTEGER NOT NULL DEFAULT 0"
            )
        if "extra_pigeon" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN extra_pigeon INTEGER NOT NULL DEFAULT 0"
            )
        if "extra_pigeon_name" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN extra_pigeon_name TEXT NOT NULL DEFAULT 'Nominated Pigeon'"
            )
        if "extra_counts" not in tournament_columns:
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN extra_counts INTEGER NOT NULL DEFAULT 0"
            )
        if "scoring_pigeons" not in tournament_columns:
            # 0 means "add up every pigeon that lands". Set it to 7 on a tournament
            # where lofts fly 8 birds but only the best 7 times count.
            conn.execute(
                "ALTER TABLE tournaments ADD COLUMN scoring_pigeons INTEGER NOT NULL DEFAULT 0"
            )
        selected_home_rows = conn.execute(
            "SELECT id FROM tournaments WHERE show_on_home=1 ORDER BY created_at DESC, id DESC"
        ).fetchall()
        if len(selected_home_rows) > 1:
            conn.executemany(
                "UPDATE tournaments SET show_on_home=0 WHERE id=?",
                [(row["id"],) for row in selected_home_rows[1:]],
            )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS one_home_tournament
               ON tournaments(show_on_home) WHERE show_on_home=1"""
        )
        stats_columns = {
            "published_lofts": "INTEGER NOT NULL DEFAULT 0",
            "published_total_pigeons": "INTEGER NOT NULL DEFAULT 0",
            "published_pigeons_landed": "INTEGER NOT NULL DEFAULT 0",
            "published_pigeons_remaining": "INTEGER NOT NULL DEFAULT 0",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "published_remarks": "TEXT NOT NULL DEFAULT ''",
        }
        needs_stats_backfill = "published_lofts" not in tournament_columns
        for column, definition in stats_columns.items():
            if column not in tournament_columns:
                conn.execute(f"ALTER TABLE tournaments ADD COLUMN {column} {definition}")

        round_time_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(round_times)").fetchall()
        }
        if "pigeons_landed" not in round_time_columns:
            conn.execute(
                "ALTER TABLE round_times ADD COLUMN pigeons_landed INTEGER NOT NULL DEFAULT 0"
            )

        participant_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(participants)").fetchall()
        }
        if "has_extra" not in participant_columns:
            conn.execute(
                "ALTER TABLE participants ADD COLUMN has_extra INTEGER NOT NULL DEFAULT 0"
            )
        if "pigeons_landed" not in participant_columns:
            conn.execute(
                "ALTER TABLE participants ADD COLUMN pigeons_landed INTEGER NOT NULL DEFAULT 0"
            )
            # Existing entries predate the landed-pigeon field. Treat all registered
            # pigeons as landed until the administrator records a different value.
            conn.execute("UPDATE participants SET pigeons_landed=num_pigeons")

        if needs_stats_backfill:
            # Preserve sensible summaries for tournaments published before these
            # snapshot fields existed.
            conn.execute(
                """UPDATE tournaments
                   SET published_lofts=(SELECT COUNT(*) FROM participants WHERE tournament_id=tournaments.id),
                       published_total_pigeons=COALESCE((SELECT SUM(num_pigeons) FROM participants WHERE tournament_id=tournaments.id), 0),
                       published_pigeons_landed=COALESCE((SELECT SUM(pigeons_landed) FROM participants WHERE tournament_id=tournaments.id), 0),
                       published_pigeons_remaining=MAX(0, COALESCE((SELECT SUM(num_pigeons) FROM participants WHERE tournament_id=tournaments.id), 0) - COALESCE((SELECT SUM(pigeons_landed) FROM participants WHERE tournament_id=tournaments.id), 0))
                   WHERE is_published=1"""
            )


def get_settings():
    with get_db() as conn:
        return dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())


def update_settings(
    ticker_text,
    detail_ticker_text,
    contact_content,
    home_display_mode,
    home_banner_image,
    latest_news_text,
):
    with get_db() as conn:
        conn.execute(
            """UPDATE settings
               SET ticker_text=?, detail_ticker_text=?, contact_content=?, home_display_mode=?,
                   home_banner_image=?, latest_news_text=?
               WHERE id=1""",
            (
                ticker_text, detail_ticker_text, contact_content, home_display_mode,
                home_banner_image, latest_news_text,
            )
        )


def get_home_banners():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM home_banners ORDER BY display_order, id"
        ).fetchall()
        return [dict(row) for row in rows]


def add_home_banner(filename: str):
    with get_db() as conn:
        next_order = conn.execute(
            "SELECT COALESCE(MAX(display_order), 0) + 1 FROM home_banners"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO home_banners (filename, display_order) VALUES (?, ?)",
            (filename, next_order),
        )


def delete_home_banner(banner_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT filename FROM home_banners WHERE id=?", (banner_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM home_banners WHERE id=?", (banner_id,))
        return row["filename"]


def get_round_dates(start_date: str, num_rounds: int, interval: int) -> list:
    start = date.fromisoformat(start_date)
    return [(start + timedelta(days=i * interval)).isoformat() for i in range(num_rounds)]


# ─────────────────────────────────────────────
# TIME HELPERS
# ─────────────────────────────────────────────

def seconds_to_hhmmss(total_seconds):
    if total_seconds is None:
        return "00:00:00"
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def hhmmss_to_seconds(time_str):
    if not time_str or time_str.strip() == "":
        return 0
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        try:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 3600 + m * 60 + s
        except ValueError:
            return 0
    return 0


def seconds_to_hhmm(total_seconds):
    """Flight totals are shown as hours:minutes, e.g. 78:37.

    Hours are not capped at 24 — a three day total of 78 hours prints as 78:37.
    """
    if not total_seconds:
        return "00:00"
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


def normalise_clock(value):
    """Accept 13:53, 1:53, 13:53:00 or 1353 and return "13:53". '' if unusable."""
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if ":" in raw:
        parts = raw.split(":")
    elif raw.isdigit() and len(raw) in (3, 4):
        parts = [raw[:-2], raw[-2:]]
    else:
        return ""
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except (ValueError, IndexError):
        return ""
    if not (0 <= hours <= 23) or not (0 <= minutes <= 59):
        return ""
    return f"{hours:02d}:{minutes:02d}"


def clock_to_seconds(value):
    """Seconds since midnight for a "HH:MM" clock time, or None."""
    clock = normalise_clock(value)
    if not clock:
        return None
    hours, minutes = clock.split(":")
    return int(hours) * 3600 + int(minutes) * 60


def flight_seconds(arrival_clock, release_clock):
    """How long a pigeon stayed up: arrival clock time minus the release time.

    A pigeon released at 06:00 that sits at 13:53 flew 7h53m. A pigeon that sits
    after midnight (arrival earlier on the clock than the release) is treated as
    the next day, so 06:00 -> 01:30 counts as 19h30m rather than a negative time.
    """
    arrival = clock_to_seconds(arrival_clock)
    release = clock_to_seconds(release_clock)
    if arrival is None or release is None:
        return 0
    elapsed = arrival - release
    if elapsed < 0:
        elapsed += 24 * 3600
    return elapsed


def score_durations(durations, scoring_limit=0):
    """Add up a day's flight times.

    scoring_limit 0 counts every pigeon that landed. A positive limit counts only
    the best that many flights, which is how a loft flying 8 birds for a 7 bird
    tournament is scored: the shortest flight is dropped.
    """
    ordered = sorted((d for d in durations if d > 0), reverse=True)
    if scoring_limit and scoring_limit > 0:
        ordered = ordered[:scoring_limit]
    return sum(ordered)


# ─────────────────────────────────────────────
# PER-DAY EXTRAS (release time override, winner birds)
# ─────────────────────────────────────────────

def get_round_meta(tournament_id, conn):
    rows = conn.execute(
        "SELECT * FROM round_meta WHERE tournament_id=?", (tournament_id,)
    ).fetchall()
    return {row["round_number"]: dict(row) for row in rows}


def save_round_meta(conn, tournament_id, round_number, start_time, first_winner, last_winner):
    conn.execute(
        """INSERT INTO round_meta (tournament_id, round_number, start_time, first_winner, last_winner)
           VALUES (?,?,?,?,?)
           ON CONFLICT(tournament_id, round_number)
           DO UPDATE SET start_time=excluded.start_time,
                         first_winner=excluded.first_winner,
                         last_winner=excluded.last_winner""",
        (tournament_id, round_number, start_time, first_winner, last_winner),
    )


def release_time_for_round(tournament, round_meta, round_number):
    """The day's own release time if the admin set one, otherwise the tournament's."""
    meta = round_meta.get(round_number) or {}
    return normalise_clock(meta.get("start_time")) or normalise_clock(tournament["start_time"]) or "05:00"


EXTRA_PIGEON_NUMBER = 0  # the named extra bird lives outside the 1..N run


def extra_config(tournament):
    """The tournament's extra-bird setup, with a usable name even if blank."""
    enabled = bool(tournament["extra_pigeon"])
    name = (tournament["extra_pigeon_name"] or "").strip() or "Nominated Pigeon"
    return {
        "enabled": enabled,
        "name": name,
        "counts": bool(tournament["extra_counts"]),
    }
