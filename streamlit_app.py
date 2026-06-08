from __future__ import annotations

import base64
import hashlib
import hmac
import html
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# outer
ROOT = Path(__file__).parent
DB_PATH = ROOT / "budget.db"

DEFAULT_CATEGORIES = [
    ("Groceries", 450.0, "#2f9e44"),
    ("Gas", 180.0, "#1971c2"),
    ("Eating Out", 160.0, "#f08c00"),
    ("Bills", 900.0, "#7048e8"),
    ("Fun Money", 120.0, "#d6336c"),
]

DEMO_COUPONS = [
    {
        "brand": "Dunkin Donuts",
        "offer": "$3 breakfast sandwich",
        "code": "DD-SACK-3148",
        "accent": "#f06a00",
    },
    {
        "brand": "Starbucks",
        "offer": "Free tall brewed coffee",
        "code": "SB-SACK-7281",
        "accent": "#00754a",
    },
    {
        "brand": "Target",
        "offer": "$10 off a $50 trip",
        "code": "TG-SACK-5029",
        "accent": "#cc0000",
    },
    {
        "brand": "7-Eleven",
        "offer": "Free medium Slurpee",
        "code": "SE-SACK-1964",
        "accent": "#008061",
    },
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not table_exists(conn, table):
        return False
    return column in [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 200_000
    )
    return (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(password_hash).decode("ascii"),
    )


def verify_password(password: str, salt_text: str, hash_text: str) -> bool:
    salt = base64.b64decode(salt_text.encode("ascii"))
    _, candidate_hash = hash_password(password, salt)
    return hmac.compare_digest(candidate_hash, hash_text)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            target REAL NOT NULL DEFAULT 0,
            color TEXT NOT NULL DEFAULT '#2f9e44',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS daily_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            bank_balance REAL NOT NULL DEFAULT 0,
            cash_on_hand REAL NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, entry_date),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            entry_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (entry_id) REFERENCES daily_entries(id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        """
    )


def migrate_legacy_database(conn: sqlite3.Connection) -> None:
    legacy_tables = ["categories", "daily_entries", "expenses"]
    needs_migration = any(
        table_exists(conn, table) and not has_column(conn, table, "user_id")
        for table in legacy_tables
    )
    if not needs_migration:
        return

    salt, password_hash = hash_password("change-me-now")
    conn.execute(
        """
        INSERT OR IGNORE INTO users (username, salt, password_hash)
        VALUES ('imported', ?, ?)
        """,
        (salt, password_hash),
    )
    imported_user_id = conn.execute(
        "SELECT id FROM users WHERE username = 'imported'"
    ).fetchone()["id"]

    for table in legacy_tables:
        if table_exists(conn, table):
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")

    create_schema(conn)

    if table_exists(conn, "categories_legacy"):
        conn.execute(
            """
            INSERT INTO categories (id, user_id, name, target, color, created_at)
            SELECT id, ?, name, target, color, created_at FROM categories_legacy
            """,
            (imported_user_id,),
        )
    if table_exists(conn, "daily_entries_legacy"):
        conn.execute(
            """
            INSERT INTO daily_entries
                (id, user_id, entry_date, bank_balance, cash_on_hand, notes, created_at)
            SELECT id, ?, entry_date, bank_balance, cash_on_hand, notes, created_at
            FROM daily_entries_legacy
            """,
            (imported_user_id,),
        )
    if table_exists(conn, "expenses_legacy"):
        conn.execute(
            """
            INSERT INTO expenses
                (id, user_id, entry_id, category_id, description, amount, created_at)
            SELECT id, ?, entry_id, category_id, description, amount, created_at
            FROM expenses_legacy
            """,
            (imported_user_id,),
        )

    for table in legacy_tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}_legacy")


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        migrate_legacy_database(conn)
        create_schema(conn)


def seed_categories(user_id: int) -> None:
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO categories (user_id, name, target, color) VALUES (?, ?, ?, ?)",
                [(user_id, name, target, color) for name, target, color in DEFAULT_CATEGORIES],
            )


def create_user(username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."

    salt, password_hash = hash_password(password)
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                (username, salt, password_hash),
            )
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()["id"]
    except sqlite3.IntegrityError:
        return False, "That username is already taken."

    seed_categories(user_id)
    st.session_state.user_id = user_id
    st.session_state.username = username
    return True, "Account created."


def authenticate(username: str, password: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, username, salt, password_hash FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    if not row or not verify_password(password, row["salt"], row["password_hash"]):
        return False

    st.session_state.user_id = row["id"]
    st.session_state.username = row["username"]
    seed_categories(row["id"])
    return True


def update_password(user_id: int, current_password: str, new_password: str) -> tuple[bool, str]:
    if len(new_password) < 6:
        return False, "New password must be at least 6 characters."

    with connect() as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row or not verify_password(
            current_password, row["salt"], row["password_hash"]
        ):
            return False, "Current password was not recognized."

        salt, password_hash = hash_password(new_password)
        conn.execute(
            "UPDATE users SET salt = ?, password_hash = ? WHERE id = ?",
            (salt, password_hash, user_id),
        )
    return True, "Password updated."


def money(value: float) -> str:
    return f"${value:,.2f}"


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]


def as_currency(value: object, default: float = 0.0) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return default
    return max(amount, 0.0)


def get_categories(user_id: int) -> list[dict]:
    start_of_month = date.today().replace(day=1).isoformat()
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT c.id, c.name, c.target, c.color,
                       COALESCE(SUM(
                           CASE WHEN de.entry_date >= ? THEN e.amount ELSE 0 END
                       ), 0) AS spent
                FROM categories c
                LEFT JOIN expenses e
                    ON e.category_id = c.id
                    AND e.user_id = c.user_id
                LEFT JOIN daily_entries de
                    ON de.id = e.entry_id
                    AND de.user_id = c.user_id
                WHERE c.user_id = ?
                GROUP BY c.id
                ORDER BY c.created_at, c.id
                """,
                (start_of_month, user_id),
            ).fetchall()
        )

    for category in rows:
        target = category["target"] or 0
        spent = category["spent"] or 0
        category["remaining"] = max(target - spent, 0)
        category["balance"] = target - spent
        category["percent"] = min(round((spent / target) * 100), 100) if target else 0
    return rows


def get_latest_entry(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, entry_date, bank_balance, cash_on_hand, notes
            FROM daily_entries
            WHERE user_id = ?
            ORDER BY entry_date DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_entry_for_date(user_id: int, entry_date: date) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, entry_date, bank_balance, cash_on_hand, notes
            FROM daily_entries
            WHERE user_id = ? AND entry_date = ?
            """,
            (user_id, entry_date.isoformat()),
        ).fetchone()
    return dict(row) if row else None


def get_expenses_for_date(user_id: int, entry_date: date) -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT e.description, c.name AS category, e.amount
                FROM expenses e
                JOIN daily_entries de ON de.id = e.entry_id
                JOIN categories c ON c.id = e.category_id
                WHERE e.user_id = ? AND de.entry_date = ?
                ORDER BY e.id
                """,
                (user_id, entry_date.isoformat()),
            ).fetchall()
        )
    return rows


def get_recent_expenses(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT e.id, e.description, e.amount, de.entry_date,
                       c.name AS category_name, c.color AS category_color
                FROM expenses e
                JOIN daily_entries de ON de.id = e.entry_id
                JOIN categories c ON c.id = e.category_id
                WHERE e.user_id = ?
                ORDER BY de.entry_date DESC, e.id DESC
                LIMIT 80
                """,
                (user_id,),
            ).fetchall()
        )
    return rows


def save_daily_entry(
    user_id: int,
    entry_date: date,
    bank_balance: float,
    cash_on_hand: float,
    notes: str,
    expenses: pd.DataFrame,
) -> None:
    category_lookup = {category["name"]: category["id"] for category in get_categories(user_id)}
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_entries
                (user_id, entry_date, bank_balance, cash_on_hand, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, entry_date) DO UPDATE SET
                bank_balance = excluded.bank_balance,
                cash_on_hand = excluded.cash_on_hand,
                notes = excluded.notes
            """,
            (
                user_id,
                entry_date.isoformat(),
                as_currency(bank_balance),
                as_currency(cash_on_hand),
                notes.strip(),
            ),
        )
        entry_id = conn.execute(
            "SELECT id FROM daily_entries WHERE user_id = ? AND entry_date = ?",
            (user_id, entry_date.isoformat()),
        ).fetchone()["id"]
        conn.execute(
            "DELETE FROM expenses WHERE user_id = ? AND entry_id = ?", (user_id, entry_id)
        )

        for _, expense in expenses.fillna("").iterrows():
            description = str(expense.get("Description", "")).strip()
            category_name = str(expense.get("Category", "")).strip()
            amount = as_currency(expense.get("Amount", 0))
            category_id = category_lookup.get(category_name)
            if not description or amount <= 0 or not category_id:
                continue
            conn.execute(
                """
                INSERT INTO expenses
                    (user_id, entry_id, category_id, description, amount)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, entry_id, category_id, description, amount),
            )


def create_category(user_id: int, name: str, target: float, color: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO categories (user_id, name, target, color) VALUES (?, ?, ?, ?)",
            (user_id, name.strip(), as_currency(target), color),
        )


def previous_month_window(today: date) -> tuple[str, str, str]:
    current_month = today.replace(day=1)
    previous_month_end = current_month - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    return (
        previous_month_start.isoformat(),
        current_month.isoformat(),
        previous_month_start.strftime("%B %Y"),
    )


def get_coupons(user_id: int) -> tuple[str, list[dict]]:
    start, end, month_label = previous_month_window(date.today())
    with connect() as conn:
        categories = rows_to_dicts(
            conn.execute(
                """
                SELECT c.id, c.name, c.target, c.color,
                       COALESCE(SUM(e.amount), 0) AS spent
                FROM categories c
                LEFT JOIN daily_entries de
                    ON de.user_id = c.user_id
                    AND de.entry_date >= ?
                    AND de.entry_date < ?
                LEFT JOIN expenses e
                    ON e.entry_id = de.id
                    AND e.category_id = c.id
                    AND e.user_id = c.user_id
                WHERE c.user_id = ?
                GROUP BY c.id
                ORDER BY c.created_at, c.id
                """,
                (start, end, user_id),
            ).fetchall()
        )

    earned = []
    for index, category in enumerate(categories):
        if category["target"] <= 0 or category["spent"] > category["target"]:
            continue
        coupon = DEMO_COUPONS[index % len(DEMO_COUPONS)].copy()
        coupon["category"] = category["name"]
        coupon["spent"] = category["spent"]
        coupon["target"] = category["target"]
        coupon["month"] = month_label
        coupon["seed"] = f"{coupon['code']}-{category['id']}-{month_label}-{user_id}"
        earned.append(coupon)

    if not earned:
        for coupon in DEMO_COUPONS:
            demo_coupon = coupon.copy()
            demo_coupon["category"] = "Demo Goal"
            demo_coupon["spent"] = 0
            demo_coupon["target"] = 100
            demo_coupon["month"] = month_label
            demo_coupon["seed"] = f"{coupon['code']}-demo-{month_label}-{user_id}"
            earned.append(demo_coupon)

    return month_label, earned


def rerun() -> None:
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


def write_html(markup: str) -> None:
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def css() -> None:
    write_html(
        """
        <style>
        .brand-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            margin: 0 0 22px;
            padding: 18px 20px;
            border: 1px solid #ded7c7;
            border-radius: 8px;
            background: rgba(255, 253, 247, 0.96);
            box-shadow: 0 18px 45px rgba(79, 60, 24, 0.13);
        }
        .brand-header h1 {
            margin: 0;
            color: #1f2933;
            font-size: clamp(2rem, 5vw, 4.4rem);
            line-height: 0.95;
        }
        .brand-header .cash-sack {
            width: 118px;
            height: 122px;
            flex: 0 0 auto;
        }
        .stApp {
            background:
                linear-gradient(135deg, rgba(47, 158, 68, 0.12), transparent 32%),
                linear-gradient(315deg, rgba(217, 154, 24, 0.18), transparent 36%),
                #f6f2e8;
        }
        [data-testid="stSidebar"] {
            background: #fffdf7;
            border-right: 1px solid #ded7c7;
        }
        h1, h2, h3, p, label, span {
            letter-spacing: 0;
        }
        .eyebrow {
            color: #c2410c;
            font-size: 0.78rem;
            font-weight: 800;
            text-transform: uppercase;
            margin: 0 0 0.25rem;
        }
        .total-tile, .panel, .sack-card, .coupon-card {
            border: 1px solid #ded7c7;
            border-radius: 8px;
            background: rgba(255, 253, 247, 0.96);
            box-shadow: 0 18px 45px rgba(79, 60, 24, 0.13);
        }
        .total-tile {
            width: min(320px, 100%);
            padding: 18px 20px;
            margin: 0 0 18px;
        }
        .total-tile span {
            display: block;
            color: #64748b;
            font-size: 0.9rem;
        }
        .total-tile strong {
            display: block;
            margin-top: 4px;
            color: #1f2933;
            font-size: 2rem;
        }
        .sack-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(205px, 1fr));
            gap: 14px;
        }
        .sack-card {
            min-height: 238px;
            display: grid;
            grid-template-rows: auto 1fr auto;
            gap: 12px;
            padding: 14px;
            background: #fffaf0;
        }
        .sack-title {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }
        .sack-title strong {
            color: #1f2933;
            overflow-wrap: anywhere;
        }
        .sack-title span {
            color: #64748b;
            font-size: 0.85rem;
            white-space: nowrap;
        }
        .cash-sack {
            position: relative;
            align-self: center;
            justify-self: center;
            width: 132px;
            height: 136px;
        }
        .cash-sack::before {
            content: "";
            position: absolute;
            left: 31px;
            top: 4px;
            width: 70px;
            height: 42px;
            background: #f0d49a;
            clip-path: polygon(16% 0, 84% 0, 68% 100%, 32% 100%);
            border: 2px solid rgba(31, 41, 51, 0.18);
        }
        .cash-sack::after {
            content: "$";
            position: absolute;
            left: 16px;
            bottom: 0;
            width: 100px;
            height: 96px;
            display: grid;
            place-items: center;
            border: 3px solid rgba(31, 41, 51, 0.16);
            border-radius: 46% 46% 38% 38%;
            background:
                linear-gradient(to top, var(--sack-color) var(--fill), #f0d49a var(--fill));
            color: rgba(31, 41, 51, 0.74);
            font-size: 2.2rem;
            font-weight: 900;
            box-shadow: inset 0 -14px 0 rgba(31, 41, 51, 0.08);
        }
        .sack-knot {
            position: absolute;
            left: 38px;
            top: 38px;
            z-index: 2;
            width: 58px;
            height: 12px;
            border-radius: 999px;
            background: #5b3b1d;
        }
        .sack-stats {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }
        .sack-stats span {
            display: block;
            color: #64748b;
            font-size: 0.78rem;
        }
        .sack-stats strong {
            display: block;
            color: #1f2933;
            margin-top: 3px;
            font-size: 1rem;
        }
        .coupon-board {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
        }
        .coupon-card {
            min-height: 220px;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 18px;
            align-items: center;
            border: 2px dashed var(--coupon-accent);
            border-left: 10px solid var(--coupon-accent);
            padding: 18px;
        }
        .coupon-main {
            display: grid;
            gap: 14px;
        }
        .coupon-main h2 {
            color: var(--coupon-accent);
            font-size: 1.65rem;
            margin: 0;
        }
        .coupon-main strong {
            display: block;
            color: #1f2933;
            font-size: 1.1rem;
        }
        .coupon-main span {
            color: #64748b;
            font-size: 0.9rem;
            font-weight: 700;
        }
        .qr-wrap {
            display: grid;
            justify-items: center;
            gap: 10px;
        }
        .qr-wrap code {
            max-width: 116px;
            color: #64748b;
            font-size: 0.76rem;
            overflow-wrap: anywhere;
            text-align: center;
        }
        .fake-qr {
            width: 116px;
            height: 116px;
            display: grid;
            grid-template-columns: repeat(9, 1fr);
            grid-template-rows: repeat(9, 1fr);
            gap: 3px;
            border: 8px solid #ffffff;
            outline: 1px solid #d8cfbd;
            background: #ffffff;
        }
        .fake-qr span {
            background: #ffffff;
        }
        .fake-qr span.filled {
            background: #1f2933;
        }
        @media (max-width: 620px) {
            .coupon-card {
                grid-template-columns: 1fr;
            }
            .qr-wrap {
                justify-items: start;
            }
        }
        </style>
        """
    )

def render_brand_header() -> None:
    write_html(
        """
        <div class="brand-header">
            <h1>FAT SACKS OF CASH</h1>
            <div class="cash-sack" style="--sack-color:#2f9e44; --fill:22%;" aria-hidden="true">
                <span class="sack-knot"></span>
            </div>
        </div>
        """
    )

def render_total_tile(user_id: int) -> None:
    latest = get_latest_entry(user_id)
    total = 0.0
    if latest:
        total = latest["bank_balance"] + latest["cash_on_hand"]
    write_html(
        f"""
        <div class="total-tile">
            <span>Total on hand</span>
            <strong>{money(total)}</strong>
        </div>
        """
    )


def render_sacks(categories: list[dict]) -> None:
    cards = []
    for category in categories:
        cards.append(
            "<article class=\"sack-card\" "
            f"style=\"--sack-color:{html.escape(category['color'])}; "
            f"--fill:{100 - category['percent']}%;\">"
            "<div class=\"sack-title\">"
            f"<strong>{html.escape(category['name'])}</strong>"
            f"<span>{category['percent']}%</span>"
            "</div>"
            "<div class=\"cash-sack\" aria-hidden=\"true\"><span class=\"sack-knot\"></span></div>"
            "<div class=\"sack-stats\">"
            f"<div><span>Spent</span><strong>{money(category['spent'])}</strong></div>"
            f"<div><span>Target</span><strong>{money(category['target'])}</strong></div>"
            f"<div><span>Left</span><strong>{money(category['remaining'])}</strong></div>"
            f"<div><span>Balance</span><strong>{money(category['balance'])}</strong></div>"
            "</div>"
            "</article>"
        )
    write_html(f"<div class=\"sack-grid\">{''.join(cards)}</div>")


def build_fake_qr(seed: str) -> str:
    size = 9
    hash_value = 0
    for char in seed:
        hash_value = ((hash_value * 31) + ord(char)) & 0xFFFFFFFF

    cells = []
    for row in range(size):
        for col in range(size):
            finder = (
                (row < 3 and col < 3)
                or (row < 3 and col > 5)
                or (row > 5 and col < 3)
            )
            bit = ((hash_value >> ((row + col * 3) % 24)) + row * 7 + col * 11) % 3
            filled = finder or bit == 0
            cells.append(f"<span class='{'filled' if filled else ''}'></span>")
    return "".join(cells)


def render_coupons(coupons: list[dict]) -> None:
    cards = []
    for coupon in coupons:
        cards.append(
            "<article class=\"coupon-card\" "
            f"style=\"--coupon-accent:{html.escape(coupon['accent'])};\">"
            "<div class=\"coupon-main\">"
            "<div>"
            f"<p class=\"eyebrow\">{html.escape(coupon['category'])}</p>"
            f"<h2>{html.escape(coupon['brand'])}</h2>"
            "</div>"
            f"<strong>{html.escape(coupon['offer'])}</strong>"
            f"<span>{money(coupon['spent'])} of {money(coupon['target'])}</span>"
            "</div>"
            "<div class=\"qr-wrap\">"
            f"<div class=\"fake-qr\" aria-label=\"Fake QR code\">{build_fake_qr(coupon['seed'])}</div>"
            f"<code>{html.escape(coupon['code'])}</code>"
            "</div>"
            "</article>"
        )
    write_html(f"<div class=\"coupon-board\">{''.join(cards)}</div>")


def auth_screen() -> None:
    render_brand_header()
    write_html("<p class=\"eyebrow\">Private budget</p>")
    st.title("Cash Sack Budget")
    tab_sign_in, tab_create = st.tabs(["Sign in", "Create account"])

    with tab_sign_in:
        with st.form("sign-in"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", use_container_width=True)
        if submitted:
            if authenticate(username, password):
                rerun()
            st.error("Username or password was not recognized.")

    with tab_create:
        with st.form("create-account"):
            username = st.text_input("Choose username")
            password = st.text_input("Choose password", type="password")
            submitted = st.form_submit_button("Create account", use_container_width=True)
        if submitted:
            ok, message = create_user(username, password)
            if ok:
                rerun()
            st.error(message)


def daily_page(user_id: int) -> None:
    render_brand_header()
    write_html("<p class=\"eyebrow\">Daily money check-in</p>")
    st.title("Daily Entry")
    render_total_tile(user_id)

    categories = get_categories(user_id)
    category_names = [category["name"] for category in categories]
    selected_date = st.date_input("Date", value=date.today() - timedelta(days=1))
    existing_entry = get_entry_for_date(user_id, selected_date)
    existing_expenses = get_expenses_for_date(user_id, selected_date)

    if not category_names:
        st.warning("Add a spending sack before entering expenses.")
        return

    default_expenses = existing_expenses or [
        {"Description": "", "Category": category_names[0], "Amount": 0.0}
    ]
    default_expenses = pd.DataFrame(default_expenses).rename(
        columns={"description": "Description", "category": "Category", "amount": "Amount"}
    )

    with st.form(f"daily-entry-{selected_date.isoformat()}"):
        bank_balance = st.number_input(
            "Bank balance",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            value=float(existing_entry["bank_balance"]) if existing_entry else 0.0,
        )
        cash_on_hand = st.number_input(
            "Cash on hand",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            value=float(existing_entry["cash_on_hand"]) if existing_entry else 0.0,
        )
        edited_expenses = st.data_editor(
            default_expenses,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Description": st.column_config.TextColumn("Expense"),
                "Category": st.column_config.SelectboxColumn(
                    "Category", options=category_names, required=True
                ),
                "Amount": st.column_config.NumberColumn(
                    "Amount", min_value=0.0, step=0.01, format="$%.2f"
                ),
            },
            hide_index=True,
        )
        notes = st.text_area("Notes", value=existing_entry["notes"] if existing_entry else "")
        submitted = st.form_submit_button("Save daily entry", use_container_width=True)

    if submitted:
        save_daily_entry(
            user_id,
            selected_date,
            bank_balance,
            cash_on_hand,
            notes,
            edited_expenses,
        )
        st.success("Daily entry saved.")

    st.subheader("Recent expense log")
    recent = get_recent_expenses(user_id)
    if recent:
        for expense in recent:
            left, right = st.columns([3, 1])
            left.write(f"**{expense['description']}**")
            left.caption(f"{expense['category_name']} on {expense['entry_date']}")
            right.write(f"**{money(expense['amount'])}**")
    else:
        st.caption("No expenses logged yet.")


def sacks_page(user_id: int) -> None:
    render_brand_header()
    write_html("<p class=\"eyebrow\">Budget visualizer</p>")
    st.title("Sacks of Cash")
    render_total_tile(user_id)
    categories = get_categories(user_id)
    render_sacks(categories)

    st.subheader("Add a sack")
    with st.form("add-category"):
        name = st.text_input("Category name", placeholder="Coffee")
        target = st.number_input(
            "Monthly target", min_value=0.0, step=1.0, format="%.2f", value=75.0
        )
        color = st.color_picker("Color", "#2f9e44")
        submitted = st.form_submit_button("Create sack", use_container_width=True)
    if submitted:
        if not name.strip():
            st.error("Category name is required.")
        else:
            create_category(user_id, name, target, color)
            st.success("Sack created.")
            rerun()


def coupons_page(user_id: int) -> None:
    render_brand_header()
    month_label, coupons = get_coupons(user_id)
    write_html(f"<p class=\"eyebrow\">{html.escape(month_label)}</p>")
    st.title("Earned Coupons")
    render_coupons(coupons)


def main() -> None:
    st.set_page_config(page_title="Cash Sack Budget", page_icon="$", layout="wide")
    css()
    init_db()

    if "user_id" not in st.session_state:
        auth_screen()
        return

    with st.sidebar:
        st.markdown(f"**{st.session_state.username}**")
        page_name = st.radio(
            "Pages",
            ["Daily Entry", "Sacks of Cash", "Coupons"],
            label_visibility="collapsed",
        )
        with st.expander("Change password"):
            with st.form("change-password"):
                current_password = st.text_input(
                    "Current password", type="password"
                )
                new_password = st.text_input("New password", type="password")
                submitted = st.form_submit_button(
                    "Update password", use_container_width=True
                )
            if submitted:
                ok, message = update_password(
                    st.session_state.user_id, current_password, new_password
                )
                if ok:
                    st.success(message)
                else:
                    st.error(message)
        if st.button("Sign out", use_container_width=True):
            st.session_state.pop("user_id", None)
            st.session_state.pop("username", None)
            rerun()

    if page_name == "Daily Entry":
        daily_page(st.session_state.user_id)
    elif page_name == "Sacks of Cash":
        sacks_page(st.session_state.user_id)
    else:
        coupons_page(st.session_state.user_id)


if __name__ == "__main__":
    main()
