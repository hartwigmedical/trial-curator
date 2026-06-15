from __future__ import annotations

import re
import zipfile
from collections.abc import Sequence
from typing import Any
from xml.etree import ElementTree

XLSX_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def read_xlsx_sheets(path: str, sheets: Sequence[str]) -> dict[str, list[dict[str, str]]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        sheet_paths = xlsx_sheet_paths(archive)
        out: dict[str, list[dict[str, str]]] = {}
        for sheet in sheets:
            if sheet not in sheet_paths:
                raise ValueError(f"Workbook is missing sheet: {sheet}")
            out[sheet] = read_xlsx_sheet_rows(
                archive,
                sheet_paths[sheet],
                shared_strings,
            )
        return out


def read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml)
    strings: list[str] = []
    for item in root.findall(f"{XLSX_MAIN_NS}si"):
        parts = [text.text or "" for text in item.findall(f".//{XLSX_MAIN_NS}t")]
        strings.append("".join(parts))
    return strings


def xlsx_sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets_by_id = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(f"{PACKAGE_REL_NS}Relationship")
    }
    paths: dict[str, str] = {}
    for sheet in workbook.findall(f".//{XLSX_MAIN_NS}sheet"):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib[f"{XLSX_REL_NS}id"]
        target = targets_by_id[rel_id].lstrip("/")
        paths[name] = target if target.startswith("xl/") else f"xl/{target}"
    return paths


def read_xlsx_sheet_rows(
    archive: zipfile.ZipFile,
    sheet_path: str,
    shared_strings: list[str],
) -> list[dict[str, str]]:
    root = ElementTree.fromstring(archive.read(sheet_path))
    raw_rows: list[dict[int, str]] = []
    for row in root.findall(f".//{XLSX_MAIN_NS}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{XLSX_MAIN_NS}c"):
            ref = cell.attrib.get("r", "")
            col_idx = column_index_from_cell_ref(ref)
            values[col_idx] = read_xlsx_cell_value(cell, shared_strings)
        if values:
            raw_rows.append(values)

    if not raw_rows:
        return []
    headers = {
        col_idx: value.strip()
        for col_idx, value in raw_rows[0].items()
        if value.strip()
    }
    rows: list[dict[str, str]] = []
    for raw_row in raw_rows[1:]:
        row = {
            header: clean_cell(raw_row.get(col_idx, ""))
            for col_idx, header in headers.items()
        }
        if any(row.values()):
            rows.append(row)
    return rows


def read_xlsx_cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(f".//{XLSX_MAIN_NS}t"))

    value = cell.find(f"{XLSX_MAIN_NS}v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return normalize_excel_scalar(value.text)


def column_index_from_cell_ref(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    if not letters:
        return 0
    col_idx = 0
    for char in letters.group(0):
        col_idx = col_idx * 26 + ord(char) - ord("A") + 1
    return col_idx - 1


def clean_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip()) if value is not None else ""


def normalize_excel_scalar(value: Any) -> str:
    text = clean_cell(value)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text

