"""The drawings of numbers the worksheet can carry (Mathematics): only safe values, and every kind renders."""
import io

from django.test import SimpleTestCase
from PIL import Image

from . import visuals

SPECS = [
    {'type': 'FRACTION_CIRCLE', 'parts': 4, 'shaded': 3, 'crossed': 1},
    {'type': 'FRACTION_BAR', 'parts': 8, 'shaded': 5},
    {'type': 'NUMBER_LINE', 'start': 0, 'end': 10, 'marks': [3, 7]},
    {'type': 'COUNT', 'count': 12, 'crossed': 4},
    {'type': 'MULTIPLICATION_GRID', 'a': 23, 'b': 14, 'show_products': True},
    {'type': 'MULTIPLICATION_GRID', 'a': 36, 'b': 12, 'show_products': False},
    {'type': 'SEQUENCE', 'items': ['Lee el problema', 'Dibuja los grupos', 'Cuenta', 'Escribe la respuesta']},
]


class VisualTests(SimpleTestCase):
    def test_every_kind_of_drawing_renders_a_png(self):
        for spec in SPECS:
            with self.subTest(kind=spec['type']):
                clean = visuals.clean_visual(spec)
                self.assertIsNotNone(clean)
                image = Image.open(io.BytesIO(visuals.render_png(clean)))
                self.assertEqual((image.format, image.size), ('PNG', (visuals.WIDTH, visuals.HEIGHT)))
                self.assertTrue(visuals.data_uri(clean).startswith('data:image/png;base64,'))

    def test_the_drawing_keeps_the_worksheets_own_numbers(self):
        self.assertEqual(visuals.clean_visual({'type': 'FRACTION_BAR', 'parts': '12', 'shaded': 12}),
                         {'type': 'FRACTION_BAR', 'parts': 12, 'shaded': 12, 'crossed': 0})
        self.assertEqual(visuals.clean_visual({'type': 'MULTIPLICATION_GRID', 'a': 99, 'b': 10})['a'], 99)
        self.assertEqual(visuals.clean_visual({'type': 'NUMBER_LINE', 'start': -5, 'end': 15, 'marks': [-5, 15]})['marks'],
                         [-5, 15])

    def test_a_number_that_cannot_be_drawn_leaves_the_step_without_drawing(self):
        # Before, these were moved into the range: 5 × 14 was drawn as 10 × 14, 40 parts as 12, 0 objects as 1
        for spec in ({'type': 'MULTIPLICATION_GRID', 'a': 5, 'b': 14}, {'type': 'MULTIPLICATION_GRID', 'a': 123, 'b': 14},
                     {'type': 'FRACTION_CIRCLE', 'parts': 40, 'shaded': 3}, {'type': 'FRACTION_BAR', 'parts': 4, 'shaded': 6},
                     {'type': 'FRACTION_BAR', 'parts': 4, 'shaded': 2, 'crossed': 3}, {'type': 'COUNT', 'count': 0},
                     {'type': 'COUNT', 'count': 500}, {'type': 'COUNT', 'count': 5, 'crossed': 9},
                     {'type': 'COUNT', 'count': 2.5}, {'type': 'NUMBER_LINE', 'start': 5, 'end': 90},
                     {'type': 'NUMBER_LINE', 'start': 0, 'end': 10, 'marks': [3, 200]},
                     {'type': 'NUMBER_LINE', 'start': 0, 'end': 10, 'marks': [1, 2, 3, 4, 5, 6]}):
            with self.subTest(spec=spec):
                self.assertIsNone(visuals.clean_visual(spec))

    def test_nothing_to_draw_gives_none(self):
        for spec in (None, 'texto', {'type': 'NONE'}, {'type': 'OTRO'}, {'type': 'FRACTION_BAR'},
                     {'type': 'MULTIPLICATION_GRID', 'a': 'diez', 'b': 14}, {'type': 'SEQUENCE', 'items': ['solo uno']}):
            with self.subTest(spec=spec):
                self.assertIsNone(visuals.clean_visual(spec))
