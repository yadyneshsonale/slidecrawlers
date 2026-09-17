"""
Scan course folders in downloads and extract instructor/department info.
First pass: try to extract from filenames, PDFs, or index files.
Second pass: will be verified via research.
"""

import os
import sqlite3
import re
from pathlib import Path

DB_PATH = "course_instructors.db"
DOWNLOADS_DIR = "downloads"


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


def get_course_folders():
    """List all course folders in downloads directory."""
    if not os.path.isdir(DOWNLOADS_DIR):
        return []
    items = []
    for name in os.listdir(DOWNLOADS_DIR):
        path = os.path.join(DOWNLOADS_DIR, name)
        if os.path.isdir(path) and not name.startswith('.'):
            items.append((name, path))
    return sorted(items)


def try_extract_from_folder(course_name, folder_path):
    """Try to extract instructor/dept from folder contents."""
    instructor = None
    department = None
    notes = []

    # Check for README, syllabus, or index files
    for filename in os.listdir(folder_path):
        if filename.lower() in ['readme.txt', 'syllabus.txt', 'index.txt', 'info.txt']:
            try:
                with open(os.path.join(folder_path, filename), 'r', errors='ignore') as f:
                    content = f.read(2000)  # read first 2KB
                    # Simple pattern matching
                    instr_match = re.search(r'(?:Instructor|Professor|taught by):\s*([A-Za-z\s\.]+)', content, re.I)
                    if instr_match:
                        instructor = instr_match.group(1).strip()
                        notes.append(f"Found in {filename}")
            except Exception as e:
                notes.append(f"Error reading {filename}: {e}")

    # Try parsing course URL from folder name
    # e.g., "cs.cmu.edu_15213-f15" -> CMU, CS, 15213
    if '_' in course_name:
        parts = course_name.split('_')
        url_part = parts[0]
        course_code = '_'.join(parts[1:])

        # Extract university and department hints
        if 'stanford' in url_part or 'stanford' in course_name:
            department = "Stanford University"
        elif 'mit' in url_part or 'ocw.mit' in course_name:
            department = "MIT"
        elif 'cmu' in url_part:
            department = "Carnegie Mellon University"
        elif 'berkeley' in url_part or 'eecs.berkeley' in course_name:
            department = "UC Berkeley"
        elif 'cornell' in url_part or 'cs.cornell' in course_name:
            department = "Cornell University"
        elif 'princeton' in url_part:
            department = "Princeton University"

    return {
        "instructor": instructor,
        "department": department,
        "notes": "; ".join(notes) if notes else None,
        "confidence": "to_verify" if not instructor else "extracted",
    }


def main():
    conn = create_db()
    courses = get_course_folders()

    print(f"Found {len(courses)} course folders.\n")

    inserted = 0
    for idx, (course_name, folder_path) in enumerate(courses, 1):
        info = try_extract_from_folder(course_name, folder_path)

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO courses
                (course_name, course_url, instructor_name, department, confidence, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    course_name,
                    course_name,  # course_url placeholder
                    info["instructor"],
                    info["department"],
                    info["confidence"],
                    info["notes"],
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
                status = "✓" if info["instructor"] else "?"
                print(f"[{idx}/{len(courses)}] {status} {course_name}")
                if info["instructor"]:
                    print(f"           Instructor: {info['instructor']}")
                if info["department"]:
                    print(f"           Department: {info['department']}")
        except sqlite3.Error as e:
            print(f"[{idx}/{len(courses)}] ERROR {course_name}: {e}")

    conn.commit()

    # Summary
    total = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    extracted = conn.execute("SELECT COUNT(*) FROM courses WHERE instructor_name IS NOT NULL").fetchone()[0]
    to_verify = conn.execute("SELECT COUNT(*) FROM courses WHERE confidence = 'to_verify'").fetchone()[0]

    print(f"\n--- Summary ---")
    print(f"Total courses: {total}")
    print(f"Instructors extracted: {extracted}")
    print(f"Courses needing verification: {to_verify}")
    print(f"Database: {DB_PATH}")

    conn.close()


if __name__ == "__main__":
    main()
