import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-layout-tests"))

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, isDarkTheme, setTheme

from app.services.settings import settings
from app.ui.stress_view import StressView


class StressLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved_settings = copy.deepcopy(settings._data)
        self.saved_dark = isDarkTheme()
        settings._data["language"] = "en-US"
        settings._data["stress_form"] = {}
        self.view = StressView()
        self.view.resize(1360, 860)
        self.view.show()
        self.settle_layout()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        settings._data.clear()
        settings._data.update(self.saved_settings)
        setTheme(Theme.DARK if self.saved_dark else Theme.LIGHT)
        self.settle_layout()

    def settle_layout(self):
        for cycle in range(8):
            self.app.processEvents()

    def stats_geometry(self):
        card = self.view._stats_card
        geometry = [card.height()]
        for name in ("statusLabel", "progressBar", "mTotal", "mQps", "mTx",
                     "errLabel", "targetsLabel", "startBtn", "stopBtn"):
            widget = getattr(self.view, name)
            geometry.append((widget.mapTo(card, QPoint(0, 0)).y(), widget.height()))
        return geometry

    def test_protocol_switch_does_not_stretch_status_content(self):
        for theme in (Theme.LIGHT, Theme.DARK):
            setTheme(theme)
            for width in (1360, 1020, 800):
                with self.subTest(theme=theme, width=width):
                    self.view.resize(width, 860)
                    self.view.protoCombo.setCurrentText("UDP")
                    self.settle_layout()
                    baseline = self.stats_geometry()
                    for protocol in ("HTTP", "HTTPS", "TCP", "ICMP", "UDP", "HTTP"):
                        with self.subTest(protocol=protocol):
                            self.view.protoCombo.setCurrentText(protocol)
                            self.settle_layout()
                            self.assertEqual(self.stats_geometry(), baseline)
                            self.assertEqual(self.view.headersCard.isVisible(),
                                             protocol in ("HTTP", "HTTPS"))

    def test_status_card_grows_for_multiline_runtime_details(self):
        for width in (1360, 800):
            with self.subTest(width=width):
                self.view.resize(width, 860)
                self.view.errLabel.setText("Last error: none")
                self.view.targetsLabel.setText("")
                self.settle_layout()
                baseline_height = self.view._stats_card.height()
                self.view.errLabel.setText("Last error: " + "connection timed out " * 20)
                self.view.targetsLabel.setText("\n".join(
                    f"Target {index}: sent 10, successful 10, failed 0" for index in range(10)))
                self.settle_layout()
                card = self.view._stats_card
                self.assertGreater(card.height(), baseline_height)
                self.assertGreater(self.view.targetsLabel.height(),
                                   self.view.targetsLabel.fontMetrics().height())
                details_bottom = (self.view.targetsLabel.mapTo(card, QPoint(0, 0)).y()
                                  + self.view.targetsLabel.height())
                button_top = self.view.startBtn.mapTo(card, QPoint(0, 0)).y()
                self.assertGreater(button_top, details_bottom)
                self.assertGreater(card.height(), button_top + self.view.startBtn.height())

    def test_plan_tracks_configuration_without_starting_test(self):
        settings._data["authorized"] = [{"host": "one.example", "id": "one"}]
        with patch("app.ui.stress_view.engine.start") as start:
            self.view.targetEdit.setPlainText(
                "https://one.example/path\none.example\ntwo.example:443")
            self.view.protoCombo.setCurrentText("HTTPS")
            self.view.threadSpin.setValue(8)
            self.view.rateSpin.setValue(11)
            self.view.durSpin.setValue(30)
            self.settle_layout()
            self.assertEqual(self.view.planTargetsValue.text(), "2")
            self.assertEqual(self.view.planDurationValue.text(), "30 sec")
            self.assertEqual(self.view.planThreadsValue.text(), "16")
            self.assertEqual(self.view.planRateValue.text(), "22 QPS")
            self.assertEqual(self.view.planProtocolLabel.text(), "HTTPS · 443")
            self.assertEqual(self.view.planAuthorizationBar.value(), 50)
            self.assertIn("Confirmation required", self.view.planStatusLabel.text())

            settings._data["authorized"].append({"host": "two.example", "id": "two"})
            self.view.refresh_auth_list()
            self.assertEqual(self.view.planAuthorizationBar.value(), 100)
            self.assertIn("All targets authorized", self.view.planStatusLabel.text())
            settings._data["authorized"] = []
            self.view.refresh_auth_list()
            self.assertEqual(self.view.planAuthorizationBar.value(), 0)

            self.view.durUnitCombo.setCurrentIndex(1)
            self.view.durSpin.setValue(3)
            self.assertEqual(self.view.planDurationValue.text(), "3 min")
            self.assertIn("180 seconds", self.view.planDurationValue.toolTip())
            self.view.protoCombo.setCurrentText("ICMP")
            self.assertEqual(self.view.planProtocolLabel.text(), "ICMP")
            start.assert_not_called()

    def test_plan_handles_empty_and_invalid_targets(self):
        self.view.targetEdit.setPlainText("")
        self.assertEqual(self.view.planTargetsValue.text(), "0")
        self.assertEqual(self.view.planThreadsValue.text(), "0")
        self.assertEqual(self.view.planRateValue.text(), "0 QPS")
        self.assertEqual(self.view.planAuthorizationBar.value(), 0)
        self.assertIn("Add targets", self.view.planStatusLabel.text())
        self.view.targetEdit.setPlainText("https://[broken\nlocalhost:70000")
        self.assertEqual(self.view.planTargetsValue.text(), "0")
        self.assertIn("2 invalid", self.view.planStatusLabel.text())

    def test_plan_stays_below_status_card_in_all_layouts(self):
        for theme in (Theme.LIGHT, Theme.DARK):
            setTheme(theme)
            for width in (1360, 1020, 800):
                for protocol in ("HTTP", "HTTPS", "UDP"):
                    with self.subTest(theme=theme, width=width, protocol=protocol):
                        self.view.resize(width, 860)
                        self.view.protoCombo.setCurrentText(protocol)
                        self.settle_layout()
                        column = self.view._right_column
                        stats = self.view._stats_card
                        plan = self.view._plan_card
                        self.assertGreaterEqual(plan.y(), stats.y() + stats.height() + 14)
                        self.assertEqual(plan.width(), stats.width())
                        self.assertLessEqual(plan.x() + plan.width(), column.width())
                        self.assertLessEqual(plan.y() + plan.height(), column.height())
                        self.assertGreater(self.view.reportCard.mapTo(self.view.view, QPoint(0, 0)).y(),
                                           plan.mapTo(self.view.view, QPoint(0, 0)).y() + plan.height())

    def test_plan_typography_survives_theme_changes(self):
        for theme in (Theme.LIGHT, Theme.DARK, Theme.LIGHT):
            with self.subTest(theme=theme):
                setTheme(theme)
                self.settle_layout()
                for label in (self.view.planTargetsValue, self.view.planDurationValue,
                              self.view.planThreadsValue, self.view.planRateValue):
                    self.assertEqual(label.font().pixelSize(), 20)
                self.assertIn("padding:3px 8px", self.view.planProtocolLabel.styleSheet())


if __name__ == "__main__":
    unittest.main()
