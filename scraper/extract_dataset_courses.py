"""
Scan university/course folders in dataset/ and extract instructor/department info.
Replicates the schema and logic from slidefetch/extract_course_instructors.py.
"""

import os
import sqlite3
import re
import hashlib
from html import unescape

DB_PATH = "/home/b-ysonale/scraper/dataset_courses.db"
DATASET_DIR = "/home/b-ysonale/scraper/dataset"
INDEX_DB_PATH = "/home/b-ysonale/scraper/index.sqlite"
CACHE_DIR = "/home/b-ysonale/scraper/work/cache"

UNIVERSITY_MAP = {
    "mit": "MIT",
    "stanford": "Stanford University",
    "cmu": "Carnegie Mellon University",
    "harvard": "Harvard University",
    "caltech": "Caltech",
    "cambridge": "University of Cambridge",
    "university_of_cambridge": "University of Cambridge",
    "oxford": "University of Oxford",
    "princeton": "Princeton University",
    "uc_berkeley": "UC Berkeley",
    "eth_zurich": "ETH Zurich",
    "nus": "National University of Singapore",
    "national_university_of_singapore": "National University of Singapore",
    "iit_bombay": "IIT Bombay",
    "iit_delhi": "IIT Delhi",
    "iit_madras": "IIT Madras",
    "iit_kanpur": "IIT Kanpur",
    "iit_kharagpur": "IIT Kharagpur",
    "iit_guwahati": "IIT Guwahati",
    "iit_hyderabad": "IIT Hyderabad",
    "iisc": "IISc Bangalore",
    "iiit_hyderabad": "IIIT Hyderabad",
    "brown_university": "Brown University",
    "duke_university": "Duke University",
    "northwestern_university": "Northwestern University",
    "northeastern_university": "Northeastern University",
    "rice_university": "Rice University",
    "arizona_state_university": "Arizona State University",
    "texas_a_m_university": "Texas A&M University",
    "ut_austin": "UT Austin",
    "uiuc": "UIUC",
    "uw": "University of Washington",
    "university_of_toronto": "University of Toronto",
    "university_of_waterloo": "University of Waterloo",
    "university_of_maryland": "University of Maryland",
    "university_of_pennsylvania": "University of Pennsylvania",
    "university_of_florida": "University of Florida",
    "university_of_utah": "University of Utah",
    "university_of_edinburgh": "University of Edinburgh",
    "university_of_north_carolina_at_chapel_hill": "UNC Chapel Hill",
    "university_of_central_florida": "University of Central Florida",
    "university_of_wisconsin-madison": "University of Wisconsin-Madison",
}


def create_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            course_name     TEXT UNIQUE NOT NULL,
            course_url      TEXT,
            instructor_name TEXT,
            department      TEXT,
            confidence      TEXT,  -- 'extracted', 'to_verify', 'verified'
            notes           TEXT,
            last_updated    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def normalize_text(text):
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_plausible_instructor_name(name):
    if not name:
        return False
    cleaned = name.strip(" .,:;|-\t\n\r")
    low = cleaned.lower()

    if any(tok in low for tok in ["http", "www.", "@", "lecture", "syllabus", "schedule", "assignment", "overview", "resources/"]):
        return False
    if any(ch in cleaned for ch in ["<", ">", "=", "#", "_", "/", "\\", "\""]):
        return False

    words = [w for w in re.split(r"\s+", cleaned) if w]
    if len(words) < 2 or len(words) > 6:
        return False

    alpha_words = 0
    for w in words:
        if re.fullmatch(r"[A-Za-z][A-Za-z\.'-]{0,29}", w):
            alpha_words += 1
    if alpha_words < 2:
        return False

    if sum(ch.isdigit() for ch in cleaned) > 0:
        return False
    return True


def extract_instructor_from_text(content):
    patterns = [
        r"(?:course\s+)?(?:instructor|instructors)\s*[:\-]\s*(?:<[^>]+>\s*)*<a[^>]*>([^<]{2,120})</a>",
        r"<h[1-6][^>]*>\s*(?:instructor|professor)s?\s*</h[1-6]>\s*(?:<[^>]+>\s*){0,4}<a[^>]*>([^<]{2,120})</a>",
        r"(?:course\s+)?(?:instructor|instructors)\s*[:\-]\s*([^\n<|]{2,120})",
        r"(?:professor|lecturer|faculty)\s*[:\-]\s*([^\n<|]{2,120})",
        r"taught\s+by\s*[:\-]?\s*([^\n<|]{2,120})",
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        name = normalize_text(match.group(1))
        if not name:
            continue
        if not is_plausible_instructor_name(name):
            continue
        return name
    return None


def cached_html_from_page_url(page_url):
    if not page_url:
        return None
    key = hashlib.sha256(page_url.encode("utf-8")).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{key}.html")
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "r", errors="ignore") as f:
            return f.read(100000)
    except Exception:
        return None


def get_page_url_from_index(uni_name, course_folder):
    if not os.path.isfile(INDEX_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(INDEX_DB_PATH)
        row = conn.execute(
            "SELECT page_url FROM courses WHERE university=? AND course=? LIMIT 1",
            (uni_name, course_folder),
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except sqlite3.Error:
        return None


def get_course_entries():
    """Walk two levels deep: dataset/<university>/<course>/"""
    entries = []
    if not os.path.isdir(DATASET_DIR):
        return entries
    for uni_name in sorted(os.listdir(DATASET_DIR)):
        uni_path = os.path.join(DATASET_DIR, uni_name)
        if not os.path.isdir(uni_path) or uni_name.startswith('.'):
            continue
        for course_name in sorted(os.listdir(uni_path)):
            course_path = os.path.join(uni_path, course_name)
            if not os.path.isdir(course_path) or course_name.startswith('.'):
                continue
            entries.append((uni_name, course_name, course_path))
    return entries


def resolve_department(uni_name):
    if uni_name in UNIVERSITY_MAP:
        return UNIVERSITY_MAP[uni_name]
    return uni_name.replace('_', ' ').replace('-', ' ').title()


def try_extract_instructor(uni_name, course_folder, course_path):
    """Extract instructor from local metadata files, then cached HTML via page_url."""
    instructor = None
    notes = []

    try:
        filenames = os.listdir(course_path)
    except OSError:
        return instructor, None

    for filename in filenames:
        if filename.lower() in [
            'readme.txt', 'readme.md', 'syllabus.txt', 'syllabus.md',
            'index.txt', 'index.md', 'info.txt', 'info.md',
            'course.html', 'course.htm',
        ]:
            filepath = os.path.join(course_path, filename)
            try:
                with open(filepath, 'r', errors='ignore') as f:
                    content = f.read(20000)
                candidate = extract_instructor_from_text(content)
                if candidate:
                    instructor = candidate
                    notes.append(f"Found in {filename}")
                    break
            except Exception as e:
                notes.append(f"Error reading {filename}: {e}")

    if not instructor:
        page_url = get_page_url_from_index(uni_name, course_folder)
        if page_url:
            html = cached_html_from_page_url(page_url)
            if html:
                candidate = extract_instructor_from_text(html)
                if candidate:
                    instructor = candidate
                    notes.append("Found in cached course page HTML")
            if not instructor:
                candidate = extract_instructor_from_text(page_url)
                if candidate:
                    instructor = candidate
                    notes.append("Found in page URL text")

    return instructor, ("; ".join(notes) if notes else None)


def cleanup_bad_instructors(conn):
    """Clear previously stored instructor names that are clearly invalid."""
    rows = conn.execute(
        "SELECT course_name, instructor_name FROM courses WHERE instructor_name IS NOT NULL AND trim(instructor_name) != ''"
    ).fetchall()
    cleaned = 0
    for course_name, instructor_name in rows:
        if is_plausible_instructor_name(instructor_name):
            continue
        conn.execute(
            """
            UPDATE courses
            SET instructor_name=NULL,
                confidence='to_verify',
                notes=COALESCE(notes, '') || CASE WHEN notes IS NULL OR notes='' THEN '' ELSE '; ' END || 'Cleared implausible instructor value',
                last_updated=datetime('now')
            WHERE course_name=?
            """,
            (course_name,),
        )
        cleaned += 1
    conn.commit()
    return cleaned


def main():
    conn = create_db()
    entries = get_course_entries()
    cleaned = cleanup_bad_instructors(conn)

    print(f"Found {len(entries)} course folders.\n")
    if cleaned:
        print(f"[INFO] Cleared {cleaned} implausible instructor values before extraction.\n")

    inserted = 0
    extracted_count = 0

    for idx, (uni_name, course_folder, course_path) in enumerate(entries, 1):
        course_name = f"{uni_name}/{course_folder}"
        department = resolve_department(uni_name)
        instructor, notes = try_extract_instructor(uni_name, course_folder, course_path)
        confidence = "extracted" if instructor else "to_verify"

        try:
            conn.execute(
                """
                INSERT INTO courses
                (course_name, course_url, instructor_name, department, confidence, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_name) DO UPDATE SET
                    course_url=excluded.course_url,
                    department=excluded.department,
                    instructor_name=COALESCE(excluded.instructor_name, courses.instructor_name),
                    confidence=CASE
                        WHEN excluded.instructor_name IS NOT NULL
                        THEN 'extracted'
                        ELSE courses.confidence
                    END,
                    notes=COALESCE(excluded.notes, courses.notes),
                    last_updated=datetime('now')
                """,
                (
                    course_name,
                    course_name,
                    instructor,
                    department,
                    confidence,
                    notes,
                ),
            )

            inserted += 1
            if instructor:
                extracted_count += 1
            status = "✓" if instructor else "?"
            print(f"[{idx}/{len(entries)}] {status} {course_name}")
            if instructor:
                print(f"           Instructor: {instructor}")
            if department:
                print(f"           Department: {department}")
        except sqlite3.Error as e:
            print(f"[{idx}/{len(entries)}] ERROR {course_name}: {e}")

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    extracted = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE instructor_name IS NOT NULL"
    ).fetchone()[0]
    to_verify = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE confidence = 'to_verify'"
    ).fetchone()[0]

    print(f"\n--- Summary ---")
    print(f"Total courses: {total}")
    print(f"Instructors extracted: {extracted}")
    print(f"Courses needing verification: {to_verify}")
    print(f"Database: {DB_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
