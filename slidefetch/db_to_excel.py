#!/usr/bin/env python3
"""Convert a SQLite database into an .xlsx workbook (one sheet per table).

Uses only the Python standard library, so it works without pandas/openpyxl.
An .xlsx file is just a zip archive of XML parts, which we build by hand.

Usage:
    python3 db_to_excel.py <database.db> [output.xlsx]
"""
import os
import re
import sqlite3
import sys
import zipfile

# Characters that are illegal in XML 1.0, even when escaped.
_ILLEGAL_XML = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


def xml_escape(value: str) -> str:
    value = _ILLEGAL_XML.sub("", value)
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def col_letter(idx: int) -> str:
    """0-based column index -> Excel column letters (A, B, ... Z, AA, ...)."""
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def safe_sheet_name(name: str, used: set) -> str:
    # Excel sheet names: max 31 chars, cannot contain : \ / ? * [ ]
    name = re.sub(r"[:\\/?*\[\]]", "_", name)[:31] or "Sheet"
    base, n = name, 1
    while name.lower() in used:
        suffix = f"_{n}"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def cell_xml(ref: str, value) -> str:
    if value is None or value == "":
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = xml_escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def sheet_xml(columns, rows) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    header_cells = "".join(
        cell_xml(f"{col_letter(c)}1", name) for c, name in enumerate(columns)
    )
    parts.append(f'<row r="1">{header_cells}</row>')
    for r, row in enumerate(rows, start=2):
        cells = "".join(
            cell_xml(f"{col_letter(c)}{r}", val) for c, val in enumerate(row)
        )
        parts.append(f'<row r="{r}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def convert(db_path: str, xlsx_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = con.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    tables = [r[0] for r in cur.fetchall()]
    if not tables:
        raise SystemExit(f"No tables found in {db_path}")

    sheets = []  # (sheet_name, columns, rows)
    used_names = set()
    for table in tables:
        cur.execute(f'PRAGMA table_info("{table}")')
        columns = [c[1] for c in cur.fetchall()]
        cur.execute(f'SELECT * FROM "{table}"')
        rows = cur.fetchall()
        sheets.append((safe_sheet_name(table, used_names), columns, rows))
    con.close()

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    for i in range(len(sheets)):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    wb_sheets = "".join(
        f'<sheet name="{xml_escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, (name, _, _) in enumerate(sheets)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets}</sheets></workbook>"
    )

    wb_rels_items = "".join(
        f'<Relationship Id="rId{i + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(len(sheets))
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{wb_rels_items}</Relationships>"
    )

    with zipfile.ZipFile(xlsx_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for i, (_, columns, rows) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml(columns, rows))

    summary = ", ".join(f"{name} ({len(rows)} rows)" for name, _, rows in sheets)
    print(f"Wrote {xlsx_path}: {summary}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    db = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(db)[0] + ".xlsx"
    convert(db, out)
