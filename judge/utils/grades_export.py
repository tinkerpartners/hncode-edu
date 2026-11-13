"""XLSX output for the same filtered, globally ranked rows shown on Grades."""

from io import BytesIO
from itertools import chain

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def grades_workbook(headers, rows, filename):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ranking"
    for row in chain([headers], rows):
        sheet.append(
            [
                (
                    ILLEGAL_CHARACTERS_RE.sub("", value)
                    if isinstance(value, str)
                    else value
                )
                for value in row
            ]
        )
        # User-controlled names/codes must remain text, including strings
        # starting with '='. Excel must never evaluate them as formulas.
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            elif isinstance(cell.value, (int, float)):
                cell.number_format = "0.##########"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="174478")
        sheet.column_dimensions[get_column_letter(cell.column)].width = min(
            50, max(15, len(str(cell.value)) + 2)
        )
    sheet.column_dimensions["C"].width = 30
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    # slug/id-only ASCII filename, independent of user-supplied display names.
    response["Content-Disposition"] = 'attachment; filename="%s.xlsx"' % filename
    response["Cache-Control"] = "private, no-store"
    return response
