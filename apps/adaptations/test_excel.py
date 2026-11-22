"""The adapted class in Excel keeps every text as text: never a formula (made-up content only)."""
import io

import openpyxl
from django.test import SimpleTestCase

from .lesson_plan import text_cell


class ExcelTextTests(SimpleTestCase):
    def saved_again(self, values):
        book = openpyxl.Workbook()
        for row, value in enumerate(values, start=1):
            text_cell(book.active, row, 1, value)
        buffer = io.BytesIO()
        book.save(buffer)
        return openpyxl.load_workbook(io.BytesIO(buffer.getvalue())).active

    def test_a_text_that_starts_with_equals_stays_text(self):
        sheet = self.saved_again(['= 4 × 5 = 20 caramelos', '=HYPERLINK("http://ejemplo.test","clic")'])
        for row in (1, 2):
            self.assertEqual(sheet.cell(row, 1).data_type, 's')
        self.assertEqual(sheet['A1'].value, '= 4 × 5 = 20 caramelos')

    def test_other_texts_and_numbers_are_unchanged(self):
        sheet = self.saved_again(['Lee el problema', 45])
        self.assertEqual((sheet['A1'].value, sheet['A2'].value), ('Lee el problema', 45))
