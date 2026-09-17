"""Dependency-free .xlsx reader (stdlib only).

Reads the first worksheet of an Excel workbook into a list of dict rows keyed by
the header row. Handles both shared-string and inline-string cells, which is all
`dataset_ccr_new.xlsx` uses. We avoid openpyxl/pandas so the data step has zero
third-party dependencies.
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(cell_ref: str) -> int:
    """'B7' -> 1 (0-based column index)."""
    letters = re.match(r"([A-Z]+)", cell_ref).group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall(f"{_NS}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    return out


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    # sheet1.xml is the conventional first sheet; fall back to any worksheet.
    if "xl/worksheets/sheet1.xml" in zf.namelist():
        return "xl/worksheets/sheet1.xml"
    for name in zf.namelist():
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            return name
    raise ValueError("no worksheet found in workbook")


def _cell_value(cell: ET.Element, shared: list[str]) -> str | None:
    t = cell.get("t")
    v = cell.find(f"{_NS}v")
    inline = cell.find(f"{_NS}is")
    if t == "s" and v is not None:
        return shared[int(v.text)]
    if t == "inlineStr" and inline is not None:
        return "".join(x.text or "" for x in inline.iter(f"{_NS}t"))
    if v is not None:
        return v.text
    return None


def read_rows(path: str) -> list[dict[str, str | None]]:
    """Return worksheet rows as dicts keyed by the header row's cell text."""
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        sheet = ET.fromstring(zf.read(_first_sheet_path(zf)))

    xml_rows = sheet.findall(f".//{_NS}row")
    if not xml_rows:
        return []

    def row_map(row: ET.Element) -> dict[int, str | None]:
        cells: dict[int, str | None] = {}
        for cell in row.findall(f"{_NS}c"):
            cells[_col_index(cell.get("r"))] = _cell_value(cell, shared)
        return cells

    header_cells = row_map(xml_rows[0])
    n_cols = (max(header_cells) + 1) if header_cells else 0
    headers = [(header_cells.get(i) or f"col{i}").strip() for i in range(n_cols)]

    rows: list[dict[str, str | None]] = []
    for xml_row in xml_rows[1:]:
        cells = row_map(xml_row)
        rows.append({headers[i]: cells.get(i) for i in range(n_cols)})
    return rows
