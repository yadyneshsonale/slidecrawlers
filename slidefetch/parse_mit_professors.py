"""
Parse mit_prof_ratings.txt and store professor metadata in a SQLite database.
"""

import sys
import re
import sqlite3
import argparse

sys.path.insert(0, '/home/b-ysonale/.local/lib/python3.11/site-packages')
from bs4 import BeautifulSoup

BASE_URL = "https://www.ratemyprofessors.com"
DB_PATH = "mit_professors.db"
HTML_PATH = "mit_prof_ratings.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse RMP search-results HTML into a professors SQLite table."
    )
    parser.add_argument(
        "--html",
        default=HTML_PATH,
        help=f"Input HTML file (default: {HTML_PATH})",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Output SQLite DB path (default: {DB_PATH})",
    )
    return parser.parse_args()


def create_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS professors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            department  TEXT,
            university  TEXT,
            quality     REAL,
            num_ratings INTEGER,
            would_take_again TEXT,
            difficulty  REAL,
            rmp_link    TEXT UNIQUE
        )
    """)
    conn.commit()


def parse_card(card) -> dict:
    href = card.get("href", "")
    rmp_link = BASE_URL + href if href else None

    # Name
    name_tag = card.find("div", class_=lambda c: c and "CardName__StyledCardName" in c)
    name = name_tag.get_text(strip=True) if name_tag else None

    # Department
    dept_tag = card.find("div", class_=lambda c: c and "CardSchool__Department" in c)
    department = dept_tag.get_text(strip=True) if dept_tag else None

    # University
    school_tag = card.find("div", class_=lambda c: c and "CardSchool__School" in c)
    university = school_tag.get_text(strip=True) if school_tag else None

    # Quality rating
    quality_tag = card.find("div", class_=lambda c: c and "CardNumRating__CardNumRatingNumber" in c)
    quality = float(quality_tag.get_text(strip=True)) if quality_tag else None

    # Number of ratings
    count_tag = card.find("div", class_=lambda c: c and "CardNumRating__CardNumRatingCount" in c)
    num_ratings = None
    if count_tag:
        m = re.search(r"(\d+)", count_tag.get_text())
        num_ratings = int(m.group(1)) if m else None

    # Feedback items (would take again / difficulty)
    would_take_again = None
    difficulty = None
    feedback_items = card.find_all("div", class_=lambda c: c and "CardFeedback__CardFeedbackItem" in c)
    for item in feedback_items:
        text = item.get_text(separator=" ", strip=True)
        num_tag = item.find("div", class_=lambda c: c and "CardFeedback__CardFeedbackNumber" in c)
        value = num_tag.get_text(strip=True) if num_tag else ""
        if "would take again" in text.lower():
            would_take_again = value  # e.g. "60%" or "N/A"
        elif "level of difficulty" in text.lower() or "difficulty" in text.lower():
            try:
                difficulty = float(value)
            except ValueError:
                difficulty = None

    return {
        "name": name,
        "department": department,
        "university": university,
        "quality": quality,
        "num_ratings": num_ratings,
        "would_take_again": would_take_again,
        "difficulty": difficulty,
        "rmp_link": rmp_link,
    }


def main():
    args = parse_args()

    content = open(args.html, encoding="utf-8").read()
    soup = BeautifulSoup(content, "html.parser")
    cards = soup.find_all("a", class_=lambda c: c and "TeacherCard__StyledTeacherCard" in c)
    print(f"Parsed {len(cards)} professor cards from HTML.")

    conn = sqlite3.connect(args.db)
    create_db(conn)

    inserted = 0
    skipped = 0
    for card in cards:
        row = parse_card(card)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO professors
                   (name, department, university, quality, num_ratings,
                    would_take_again, difficulty, rmp_link)
                   VALUES (:name, :department, :university, :quality, :num_ratings,
                           :would_take_again, :difficulty, :rmp_link)""",
                row,
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            print(f"  Error inserting {row['name']}: {e}")

    conn.commit()
    conn.close()

    print(f"Inserted {inserted} rows, skipped {skipped} duplicates.")
    print(f"Database saved to: {args.db}")


if __name__ == "__main__":
    main()
