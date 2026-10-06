"""
A small .xlsx writer.

The NetBox image carries no spreadsheet library, and a plugin that pulls one in
for a single download is a heavier change to the image than the download is
worth. An .xlsx file is a zip of a few XML parts; what the port map needs of it
— one sheet, bold headers, column widths, a frozen header, a filter — fits here.
Strings are written inline, so no shared-string table is needed.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>'
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


def workbook(
    title: str,
    rows: list[list],
    *,
    widths: list[float] | None = None,
    header_rows: int = 1,
    group_row: list[tuple[int, int, str]] | None = None,
) -> bytes:
    """
    One sheet named ``title``. ``rows`` are written top to bottom; the first
    ``header_rows`` rows are styled as headers, frozen, and the last of them
    carries the filter. ``group_row`` puts a merged, centred band above the
    headers: ``[(first column, last column, label), ...]`` (0-based, inclusive).
    """
    sheet_rows: list[str] = []
    merges: list[str] = []
    width = max((len(r) for r in rows), default=1)
    r = 1
    if group_row:
        cells = []
        for first, last, label in group_row:
            cells.append(_cell(f"{column_name(first)}{r}", label, 2))
            for c in range(first + 1, last + 1):
                cells.append(_cell(f"{column_name(c)}{r}", None, 2))
            if last > first:
                merges.append(f"{column_name(first)}{r}:{column_name(last)}{r}")
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
        r += 1
    header_end = r + header_rows - 1
    for i, row in enumerate(rows):
        style = 1 if i < header_rows else 0
        cells = "".join(_cell(f"{column_name(c)}{r}", v, style) for c, v in enumerate(row))
        sheet_rows.append(f'<row r="{r}">{cells}</row>')
        r += 1
    cols = ""
    if widths:
        cols = (
            "<cols>"
            + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
            + "</cols>"
        )
    last_ref = f"{column_name(width - 1)}{max(r - 1, header_end)}"
    sheet = (
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
    safe_title = escape(title[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", ""))
    wb = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_title}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()
