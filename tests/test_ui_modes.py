import copy
import os
import re
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-ui-modes-tests"))

from PySide6.QtCore import QEvent, QLocale, Qt
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox, QLabel,
                               QLineEdit, QPlainTextEdit, QTextEdit, QWidget)
from qfluentwidgets import FluentTranslator, Theme, isDarkTheme, qconfig, setTheme, setThemeColor

from app.services.settings import settings
from app.ui.agent_view import GenerateNodeDialog
from app.ui.busy_overlay import BusyOverlay
from app.ui.disclaimer import AuthDialog, DisclaimerDialog
from app.ui.i18n import L, current_lang
from app.ui.main_window import MainWindow
from app.ui.market_view import PublishDialog


HAN_RE = re.compile(r"[\u3400-\u9fff]")


def ui_texts(root):
    texts = []
    for widget in [root] + root.findChildren(QWidget):
        if isinstance(widget, (QLabel, QAbstractButton)):
            texts.append(widget.text())
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            texts.append(widget.placeholderText())
        if isinstance(widget, QComboBox):
            texts.extend(widget.itemText(index) for index in range(widget.count()))
        texts.extend((widget.toolTip(), widget.windowTitle()))
    for action in root.findChildren(QAction):
        texts.extend((action.text(), action.toolTip()))
    return [text for text in texts if text]


class UiModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved_settings = copy.deepcopy(settings._data)
        self.saved_dark = isDarkTheme()
        self.saved_color = qconfig.themeColor.value
        self.market_patch = patch("app.services.market.MarketClient.fetch_index")
        self.market_patch.start()

    def tearDown(self):
        self.market_patch.stop()
        settings._data.clear()
        settings._data.update(self.saved_settings)
        setTheme(Theme.DARK if self.saved_dark else Theme.LIGHT)
        setThemeColor(self.saved_color)
        self.settle()

    def settle(self):
        for cycle in range(8):
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    @contextmanager
    def window_for(self, language, theme):
        settings._data.update({
            "language": language,
            "theme": "dark" if theme == Theme.DARK else "light",
            "theme_color": "#5B5FC7",
            "animations_enabled": False,
            "minimize_to_tray": False,
            "stress_form": {},
            "authorized": [],
            "github_token": "",
            "github_login": "",
            "agent_hub_url": "",
            "agent_hub_token": "",
        })
        setTheme(theme)
        setThemeColor("#5B5FC7")
        translator = FluentTranslator(QLocale("zh_CN" if language == "zh-CN" else "en_US"), self.app)
        self.app.installTranslator(translator)
        window = MainWindow()
        window.tray_icon.hide()
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.resize(1360, 860)
        window.show()
        window.market.marketPage._on_index([], False)
        self.settle()
        try:
            yield window
        finally:
            window.tray_icon.hide()
            window.close()
            window.deleteLater()
            self.settle()
            self.app.removeTranslator(translator)

    def assert_language(self, root, language):
        texts = ui_texts(root)
        if language == "en-US":
            leaked = [text for text in texts if HAN_RE.search(text)]
            self.assertEqual(leaked, [], f"Chinese text in English UI: {leaked[:10]}")
        else:
            self.assertTrue(any(HAN_RE.search(text) for text in texts))

    def test_all_pages_in_both_languages_and_themes(self):
        titles = (
            ("dashboard", "FlowBench", "FlowBench"),
            ("stress", "压力测试", "Stress Test"),
            ("collab", "协同测试", "Collaborative Testing"),
            ("agentView", "服务器节点", "Server Agents"),
            ("monitor", "监控面板", "Monitor"),
            ("market", "插件", "Plugins"),
            ("settingsView", "设置", "Settings"),
        )
        for language in ("zh-CN", "en-US"):
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.window_for(language, theme) as window:
                    self.assertEqual(isDarkTheme(), theme == Theme.DARK)
                    self.assertEqual(qconfig.themeColor.value.name().upper(), "#5B5FC7")
                    for name, chinese, english in titles:
                        with self.subTest(language=language, theme=theme, page=name):
                            page = getattr(window, name)
                            window.switchTo(page)
                            self.settle()
                            self.assert_language(page, language)
                            self.assertIn(chinese if language == "zh-CN" else english, ui_texts(page))
                            self.assertFalse(page.grab().isNull())
                    self.assert_language(window, language)
                    for label in (window.stress.planTargetsValue, window.stress.planDurationValue,
                                  window.stress.planThreadsValue, window.stress.planRateValue):
                        color = label.palette().color(QPalette.WindowText)
                        if theme == Theme.DARK:
                            self.assertGreater(color.lightness(), 180)
                        else:
                            self.assertLess(color.lightness(), 100)

    def test_dialogs_and_busy_overlay_follow_language_and_theme(self):
        for language in ("zh-CN", "en-US"):
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.window_for(language, theme) as window:
                    factories = (
                        lambda: DisclaimerDialog(window),
                        lambda: AuthDialog("127.0.0.1", window),
                        lambda: GenerateNodeDialog(window),
                        lambda: PublishDialog(window),
                    )
                    for factory in factories:
                        dialog = factory()
                        with self.subTest(language=language, theme=theme, dialog=type(dialog).__name__):
                            self.assert_language(dialog, language)
                            if isinstance(dialog, (DisclaimerDialog, AuthDialog)):
                                self.assertFalse(dialog.yesButton.isEnabled())
                            self.assertFalse(dialog.widget.grab().isNull())
                        dialog.close()
                        dialog.deleteLater()
                    overlay = BusyOverlay(window)
                    overlay.show(L("正在检查...", "Checking..."), L("不会发送测试流量", "No test traffic is sent"))
                    self.settle()
                    self.assert_language(overlay, language)
                    expected_color = "rgb(245, 245, 245)" if theme == Theme.DARK else "rgb(30, 30, 30)"
                    self.assertIn(expected_color, overlay._label.styleSheet())
                    overlay.hide()
                    overlay.deleteLater()

    def test_publish_dialog_localizes_bilingual_plugin_names(self):
        for language in ("zh-CN", "en-US"):
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.window_for(language, theme) as window:
                    for loaded in (False, True):
                        for name in (("演示插件", "Demo plugin"), ["演示插件", "Demo plugin"]):
                            with self.subTest(language=language, theme=theme, loaded=loaded, name=name):
                                metadata = {"name": name, "version": "1.0", "category": "tool"}
                                plugin = SimpleNamespace(**metadata) if loaded else None
                                record = SimpleNamespace(
                                    pid="demo", path="", plugin=plugin, display_name=name,
                                    display_version="1.0", state="loaded" if loaded else "disabled")
                                with (patch("app.ui.market_view.plugin_manager.records", return_value=[record]),
                                      patch("app.ui.market_view.plugin_manager.record", return_value=record),
                                      patch("app.ui.market_view._scan_plugin_meta", return_value=metadata)):
                                    dialog = PublishDialog(window)
                                    try:
                                        self.settle()
                                        self.assert_language(dialog, language)
                                        expected = "演示插件" if language == "zh-CN" else "Demo plugin"
                                        self.assertIn(expected, dialog.combo.itemText(0))
                                        self.assertEqual(dialog._generate()["name"], list(name))
                                    finally:
                                        dialog.close()
                                        dialog.deleteLater()
                                        self.settle()

    def test_auto_language_resolves_chinese_and_english_locales(self):
        settings._data["language"] = "auto"
        for locale, expected in (("zh_CN", "zh-CN"), ("zh_TW", "zh-CN"), ("en_US", "en-US")):
            with self.subTest(locale=locale), patch("app.ui.i18n.QLocale.system", return_value=QLocale(locale)):
                self.assertEqual(current_lang(), expected)
