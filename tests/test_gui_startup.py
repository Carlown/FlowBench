import os
import tempfile
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-gui-tests"))

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main


class _WindowProbe:
    def __init__(self):
        self.show_requests = 0

    def _show_from_tray(self):
        self.show_requests += 1


class GuiStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_qlockfile_excludes_a_second_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "flowbench.lock")
            first = QLockFile(path)
            second = QLockFile(path)
            self.assertTrue(first.tryLock(0))
            self.assertFalse(second.tryLock(0))
            first.unlock()
            self.assertTrue(second.tryLock(0))
            second.unlock()

    def test_launch_requests_wait_until_the_window_is_ready(self):
        name = f"FlowBench-startup-test-{uuid.uuid4().hex}"
        old_name = main.SINGLE_INSTANCE_KEY
        main.SINGLE_INSTANCE_KEY = name
        server = None
        clients = []
        try:
            QLocalServer.removeServer(name)
            server = main.create_single_instance_server()
            self.assertIsNotNone(server)
            self.assertTrue(server.isListening())

            first = QLocalSocket()
            clients.append(first)
            first.connectToServer(name)
            self.assertTrue(first.waitForConnected(500))
            first.write(b"show")
            first.flush()
            self.app.processEvents()

            probe = _WindowProbe()
            server.set_main_window(probe)
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 0)

            server.set_ready()
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 1)

            second = QLocalSocket()
            clients.append(second)
            second.connectToServer(name)
            self.assertTrue(second.waitForConnected(500))
            second.write(b"show")
            second.flush()
            self.app.processEvents()
            QTest.qWait(30)
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 2)
        finally:
            for client in clients:
                client.abort()
            if server is not None:
                server.close()
                server.deleteLater()
            QLocalServer.removeServer(name)
            main.SINGLE_INSTANCE_KEY = old_name


if __name__ == "__main__":
    unittest.main()
