"""Проверки шрифта классической темы в Windows Qt."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtGui import QFont, QFontDatabase, QRawFont
from PyQt6.QtWidgets import QApplication
from config import THEMES


APP = QApplication.instance() or QApplication([])


@unittest.skipUnless(sys.platform == 'win32' and APP.platformName() == 'windows',
                     'Системные шрифты требуют QT_QPA_PLATFORM=windows')
class Windows95FontTests(unittest.TestCase):
    def test_ui_font_is_scalable_and_supports_cyrillic(self):
        family = THEMES['Windows 95']['font_family']
        self.assertIn(family, QFontDatabase.families())
        self.assertTrue(QFontDatabase.isSmoothlyScalable(family))
        for size in (9, 12, 16):
            with self.subTest(size=size):
                font = QRawFont.fromFont(QFont(family, size))
                self.assertTrue(font.isValid())
                self.assertEqual(font.familyName(), family)
                for char in 'АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя':
                    self.assertTrue(font.supportsCharacter(ord(char)), char)


if __name__ == '__main__':
    unittest.main()
