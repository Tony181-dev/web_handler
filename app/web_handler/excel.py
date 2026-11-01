"""Read rows from a workbook and write results to a copy. The source is never changed."""
from dataclasses import dataclass, field
from datetime import date, datetime, time
import hashlib
import os
from pathlib import Path
import shutil

from . import paths
from .i18n import t

SUPPORTED = (".xlsx", ".xlsm")


class ExcelError(Exception):
    pass


@dataclass
class Row:
    number: int
    values: dict = field(default_factory=dict)
    skip_reason: str = ""


def cell_to_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime) and value.time() == time():
        return value.date().isoformat()
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value).strip()


def cell_value(cell):
    # Links inside the workbook have no target; the visible text is used for them.
    hyperlink = cell.hyperlink
    if hyperlink is not None and hyperlink.target and not hyperlink.target.startswith("#"):
        return hyperlink.target.strip()
    return cell_to_text(cell.value)


def _open(path, **options):
    from openpyxl import load_workbook

    path = Path(path)
    if not path.is_file():
        raise ExcelError(t("File not found: {path}", path=path))
    if path.suffix.lower() == ".xls":
        raise ExcelError(t("Old .xls files cannot be read. Open it in Excel and save it as .xlsx."))
    if path.suffix.lower() not in SUPPORTED:
        raise ExcelError(t("Only {types} files are supported.", types=", ".join(SUPPORTED)))
    try:
        return load_workbook(path, **options)
    except PermissionError as error:
        raise ExcelError(t("Could not open the file: {error}", error=error)) from error
    except Exception as error:
        raise ExcelError(t("The Excel file is damaged or cannot be read: {error}", error=error)) from error


def _sheet(workbook, name):
    if name not in workbook.sheetnames:
        raise ExcelError(t("There is no sheet '{name}'.", name=name))
    return workbook[name]


def sheet_info(path):
    from openpyxl.utils import get_column_letter

    workbook = _open(path, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            columns = []
            for index in range(1, sheet.max_column + 1):
                letter = get_column_letter(index)
                columns.append({
                    "column": letter,
                    "header": cell_to_text(sheet[f"{letter}1"].value),
                    "sample": cell_value(sheet[f"{letter}2"]) if sheet.max_row >= 2 else "",
                })
            sheets.append({"name": sheet.title, "max_row": sheet.max_row, "columns": columns})
        return {"sheets": sheets}
    finally:
        workbook.close()


def read_rows(path, sheet_name, start_row=2, end_row=None, required=(), stop_column=None):
    """Rows to work on. Without an end row they run until the first empty cell of stop_column."""
    from openpyxl.utils import get_column_letter

    workbook = _open(path, data_only=True)
    try:
        sheet = _sheet(workbook, sheet_name)
        letters = [get_column_letter(index) for index in range(1, sheet.max_column + 1)]
        rows = []
        for number in range(start_row, sheet.max_row + 1):
            values = {letter: cell_value(sheet[f"{letter}{number}"]) for letter in letters}
            rows.append(Row(number, values))
    finally:
        workbook.close()

    if end_row is not None:
        rows = [row for row in rows if row.number <= end_row]
    else:
        # Rows that only carry formatting must not count as work to do.
        checked = [stop_column] if stop_column else (list(required) or letters)
        for position, row in enumerate(rows):
            if not any(row.values.get(column) for column in checked):
                rows = rows[:position]
                break
    for row in rows:
        missing = [column for column in required if not row.values.get(column)]
        if missing:
            row.skip_reason = t("Missing a value in column {columns}", columns=", ".join(missing))
    return rows


def nonempty_columns(path, sheet_name, columns, start_row=2):
    rows = read_rows(path, sheet_name, start_row, None, ())
    return {column for column in columns if any(row.values.get(column) for row in rows)}


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ResultWriter:
    """Collects statuses and read values, and saves them to a separate workbook."""

    SAVE_EVERY = 20

    def __init__(self, source, sheet_name, status_column, run_id):
        self.source = Path(source)
        self.status_column = status_column
        self.workbook = _open(source, keep_vba=self.source.suffix.lower() == ".xlsm")
        self.sheet = _sheet(self.workbook, sheet_name)
        self.target = self.source.with_name(f"{self.source.stem}_result_{run_id}{self.source.suffix}")
        self.pending = 0

    def set_status(self, row, status):
        if self.status_column:
            self.set_value(row, self.status_column, status)

    def set_value(self, row, column, value):
        self.sheet[f"{column}{row}"] = value
        self.pending += 1

    def row_finished(self):
        if self.pending >= self.SAVE_EVERY:
            self.save()

    def save(self):
        try:
            self._save_to(self.target)
        except OSError:
            fallback = paths.runs_dir() / self.target.name
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self._save_to(fallback)
            self.target = fallback
        self.pending = 0
        return self.target

    def _save_to(self, target):
        # A half-written file must never replace the last good one.
        temporary = target.with_name("~" + target.name)
        self.workbook.save(temporary)
        os.replace(temporary, target)

    def close(self):
        self.workbook.close()


def copy_without_changes(source, target):
    shutil.copy2(source, target)
