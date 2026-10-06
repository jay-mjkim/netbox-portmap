"""
A small .xlsx writer.

The NetBox image carries no spreadsheet library, and a plugin that pulls one in
for a download is a heavier change to the image than the download is worth. An
.xlsx file is a zip of a few XML parts; what the port map needs of it — a few
sheets, bold headers, column widths, a frozen header, a filter — fits here.
Strings are written inline, so no shared-string table is needed.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from xml.sax.saxutils import escape

_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)
# Style 0: plain. 1: bold on a grey fill (headers). 2: bold, centred, on a darker fill (group row).
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="10"/><name val="Arial"/></font>'
    '<font><b/><sz val="10"/><name val="Arial"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEFEFEF"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFD9D9D9"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="1" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" '
    'applyAlignment="1"><alignment horizontal="center"/></xf></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
)


@dataclass
class Sheet:
    """One sheet: ``rows`` top to bottom, the first ``header_rows`` styled and frozen, the
    last of them carrying the filter. ``group_row`` is a merged, centred band above the
    headers: ``[(first column, last column, label), ...]`` (0-based, inclusive)."""

    title: str
    rows: list[list]
    widths: list[float] | None = None
    header_rows: int = 1
    group_row: list[tuple[int, int, str]] | None = None
    merges: list[str] = field(default_factory=list)


def column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value, style: int) -> str:
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{s}><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"{s}><v>{value}</v></c>'
    if isinstance(value, datetime | date):
        value = value.isoformat()
    return f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    sheet_rows: list[str] = []
    merges: list[str] = list(sheet.merges)
    width = max((len(r) for r in sheet.rows), default=1)
    r = 1
    if sheet.group_row:
        cells = []
        for first, last, label in sheet.group_row:
            cells.append(_cell(f"{column_name(first)}{r}", label, 2))
            for c in range(first + 1, last + 1):
                cells.append(_cell(f"{column_name(c)}{r}", None, 2))
            if last > first:
                merges.append(f"{column_name(first)}{r}:{column_name(last)}{r}")
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        r += 1
    header_end = r + sheet.header_rows - 1
    for i, row in enumerate(sheet.rows):
        style = 1 if i < sheet.header_rows else 0
        cells = "".join(_cell(f"{column_name(c)}{r}", v, style) for c, v in enumerate(row))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
        r += 1
    cols = ""
    if sheet.widths:
        cols = (
            "<cols>"
            + "".join(
                f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(sheet.widths)
            )
            + "</cols>"
        )
    last_ref = f"{column_name(width - 1)}{max(r - 1, header_end)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_end}" topLeftCell="A{header_end + 1}" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"{cols}<sheetData>{''.join(sheet_rows)}</sheetData>"
        + (f'<autoFilter ref="A{header_end}:{last_ref}"/>' if r - 1 > header_end else "")
        + (
            f'<mergeCells count="{len(merges)}">' + "".join(f'<mergeCell ref="{m}"/>' for m in merges) + "</mergeCells>"
            if merges
            else ""
        )
        + "</worksheet>"
    )


def _safe_title(title: str) -> str:
    for bad in "/\\?*[]:":
        title = title.replace(bad, "-" if bad in "/\\" else "")
    return escape(title[:31] or "Sheet")


def workbook_sheets(sheets: list[Sheet]) -> bytes:
    """A workbook with these sheets, in this order."""
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(sheets))
        )
        + "</Types>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(sheets))
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    wb = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(
            f'<sheet name="{_safe_title(s.title)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(sheets)
        )
        + "</sheets></workbook>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(s))
    return out.getvalue()


def workbook(
    title: str,
    rows: list[list],
    *,
    widths: list[float] | None = None,
    header_rows: int = 1,
    group_row: list[tuple[int, int, str]] | None = None,
) -> bytes:
    """One sheet named ``title``."""
    return workbook_sheets([Sheet(title, rows, widths, header_rows, group_row)])
