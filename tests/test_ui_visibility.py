"""Проверки независимой видимости аналитических элементов интерфейса."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PyQt6.QtWidgets import QApplication
from config import DEFAULT_UI_FEATURES, load_settings, save_settings
from gui.log_viewer import LogViewerWidget
from gui.settings import SettingsDialog
from gui.window import MainWindow

APP = QApplication.instance() or QApplication([])


class VisibilityTests(unittest.TestCase):
    def test_batches_tab_is_independent_and_can_be_restored(self):
        viewer = LogViewerWidget('unused.log', 'Default', 10, lazy=True)
        try:
            index = viewer.bottom_tabs.indexOf(viewer.batches_container)
            viewer.bottom_tabs.setCurrentIndex(index)
            viewer.apply_ui_features({'batches_tab': False})
            self.assertFalse(viewer.bottom_tabs.isTabVisible(index))
            self.assertNotEqual(viewer.bottom_tabs.currentIndex(), index)
            self.assertFalse(viewer.btn_batches.isHidden())
            viewer.apply_ui_features({'batches_tab': True, 'batches_filter': False})
            self.assertTrue(viewer.bottom_tabs.isTabVisible(index))
            self.assertTrue(viewer.btn_batches.isHidden())
        finally:
            viewer.stop_background_work()
            viewer.deleteLater()

    def test_settings_roundtrip_and_startup_visibility(self):
        dialog = SettingsDialog('Default', 10, DEFAULT_UI_FEATURES)
        for key in ('batches_tab', 'group_stats'):
            dialog.feature_checkboxes[key].setChecked(False)
        features = dialog.get_settings()[2]
        dialog.deleteLater()
        with tempfile.TemporaryDirectory() as directory:
            with patch('config.get_settings_path', return_value=str(Path(directory) / 'settings.json')):
                save_settings({'ui_features': features})
                settings = load_settings()
        self.assertFalse(settings['ui_features']['batches_tab'])
        self.assertFalse(settings['ui_features']['group_stats'])
        with patch('gui.window.load_settings', return_value=settings), \
                patch.object(MainWindow, '_sync_file_associations'), \
                patch.object(MainWindow, 'save_current_settings'):
            window = MainWindow()
            try:
                self.assertTrue(window.btn_group_stats.isHidden())
                window.ui_features['group_stats'] = True
                window._apply_ui_features_everywhere()
                self.assertFalse(window.btn_group_stats.isHidden())
            finally:
                window.close()
                window.deleteLater()


if __name__ == '__main__':
    unittest.main()
