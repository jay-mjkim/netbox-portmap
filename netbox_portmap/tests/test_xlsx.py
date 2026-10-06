import re
import zipfile
from io import BytesIO
from unittest import TestCase

from netbox_portmap import xlsx


class WriterTest(TestCase):
    def test_column_names(self):
        self.assertEqual([xlsx.column_name(i) for i in (0, 25, 26, 27, 701, 702)], ["A", "Z", "AA", "AB", "ZZ", "AAA"])

    def test_a_workbook_opens_as_a_zip_of_the_expected_parts(self):
        body = xlsx.workbook(
            "Port Map", [["id", "name"], [7, "a<b"], [8, None]], widths=[8, 20], group_row=[(0, 1, "G")]
        )
        z = zipfile.ZipFile(BytesIO(body))
        self.assertEqual(
            sorted(z.namelist()),
            sorted(
                [
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "xl/workbook.xml",
                    "xl/_rels/workbook.xml.rels",
                    "xl/styles.xml",
                    "xl/worksheets/sheet1.xml",
                ]
            ),
        )
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('<mergeCell ref="A1:B1"/>', sheet)
        self.assertIn('<c r="A3"><v>7</v></c>', sheet)
        self.assertIn("a&lt;b", sheet)
        self.assertIn('<autoFilter ref="A2:B4"/>', sheet)
        self.assertIn('ySplit="2"', sheet)
        self.assertIsNone(re.search(r'<c r="B4"', sheet))  # an empty cell is not written
        self.assertIn('name="Port Map"', z.read("xl/workbook.xml").decode())

    def test_sheet_titles_keep_to_excel_rules(self):
        body = xlsx.workbook("a/very/long/title/that/excel/would/refuse/x", [["h"]])
        name = re.search(r'name="([^"]+)"', zipfile.ZipFile(BytesIO(body)).read("xl/workbook.xml").decode()).group(1)
        self.assertLessEqual(len(name), 31)
        self.assertNotIn("/", name)
