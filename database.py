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
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                city TEXT DEFAULT '',
                image TEXT DEFAULT '',
                num_pigeons INTEGER NOT NULL DEFAULT 7,
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
