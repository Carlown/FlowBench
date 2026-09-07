"""Local GUI page for controlling the additive server-agent feature.

The Stress page remains the single source of truth for a job configuration.
This page intentionally contains only connection/node controls and start/stop
buttons; it does not duplicate target, protocol, or rate inputs.
"""
from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import sys
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, FluentStyleSheet,
                            InfoBar,
                            MessageBoxBase,
                            InfoBarPosition, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton, ScrollArea,
                            SimpleCardWidget, StrongBodyLabel, SubtitleLabel,
                            TextEdit, isDarkTheme, qconfig, Theme)

from app.services.agent_control import ControllerClient
from app.services.auth import add_authorized, build_http_url, is_authorized, normalize_host
from app.services.settings import settings
from app.ui.disclaimer import AuthDialog
from app.ui.i18n import L
from app.ui.stress_view import HIGH_RATE, MiniStat, fmt_bytes


class GenerateNodeDialog(MessageBoxBase):
    """Fluent-style one-shot dialog for creating a server-node package."""

    def __init__(self, parent=None, has_hub=False, hub_url=""):
        super().__init__(parent)
        self.setWindowTitle(L("生成服务器节点", "Generate Server Node"))
        self.widget.setMinimumWidth(560)
        # MessageBoxBase applies styles during import-time construction; force
        # the current theme so dark mode never falls back to a light dialog.
        FluentStyleSheet.DIALOG.apply(self, Theme.DARK if isDarkTheme() else Theme.LIGHT)
        self.setMaskColor(QColor(0, 0, 0, 120) if isDarkTheme() else QColor(0, 0, 0, 76))
        self.yesButton.setText(L("生成", "Generate"))
        self.cancelButton.setText(L("取消", "Cancel"))

        self.viewLayout.addWidget(StrongBodyLabel(
            L("生成服务器节点", "Generate Server Node"), self.widget))

        description = BodyLabel(L(
            "输入节点信息后点击生成。安装包会自动保存到下面的位置。",
            "Enter the node information and generate it. The bundle will be saved to the location below."),
            self.widget)
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)

        name_row = QHBoxLayout()
        name_row.addWidget(BodyLabel(L("节点名称", "Node name"), self.widget))
        self.nameEdit = LineEdit(self.widget)
        self.nameEdit.setPlaceholderText(L("例如 server-hk-01", "For example, server-hk-01"))
        self.nameEdit.setText(socket.gethostname() or "server-node")
        self.nameEdit.textChanged.connect(self._update_output)
        name_row.addWidget(self.nameEdit, 1)
        self.viewLayout.addLayout(name_row)

        if has_hub:
            hint = CaptionLabel(L(
                f"将向现有 Hub 追加节点：{hub_url}",
                f"A new node will be added to the existing Hub: {hub_url}"), self.widget)
            hint.setWordWrap(True)
            self.viewLayout.addWidget(hint)
            self.addressEdit = None
        else:
            address_row = QHBoxLayout()
            address_row.addWidget(BodyLabel(L("服务器地址", "Server address"), self.widget))
            self.addressEdit = LineEdit(self.widget)
            self.addressEdit.setPlaceholderText(L("公网 IP 或域名，可带端口", "Public IP or domain; port optional"))
            address_row.addWidget(self.addressEdit, 1)
            self.viewLayout.addLayout(address_row)
            address_hint = CaptionLabel(L(
                "未配置 Hub 时会自动生成 HTTPS 控制端和节点，服务器需放行 TCP 8787。",
                "Without a configured Hub, an HTTPS control Hub and node are generated automatically; open TCP 8787 on the server."),
                self.widget)
            address_hint.setWordWrap(True)
            self.viewLayout.addWidget(address_hint)

        output_row = QHBoxLayout()
        output_row.addWidget(BodyLabel(L("保存位置", "Save location"), self.widget))
        self.outputEdit = LineEdit(self.widget)
        self.outputEdit.textEdited.connect(lambda: setattr(self, "_custom_output", True))
        self._output_dir = self._default_output_dir()
        self._custom_output = False
        self._update_output()
        output_row.addWidget(self.outputEdit, 1)
        browse_button = PushButton(L("浏览…", "Browse…"), self.widget)
        browse_button.clicked.connect(self._pick_output)
        output_row.addWidget(browse_button)
        self.viewLayout.addLayout(output_row)

    @staticmethod
    def _default_output_dir():
        desktop = Path.home() / "Desktop"
        return desktop if desktop.is_dir() else Path.home()

    @staticmethod
    def _safe_name(value):
        clean = re.sub(r'[\\/:*?"<>|\s]+', "-", value.strip()).strip("-.")
        return clean[:48] or "server-node"

    def _update_output(self):
        if not self._custom_output:
            path = self._output_dir / f"NetPulse-{self._safe_name(self.nameEdit.text())}.zip"
            self.outputEdit.setText(str(path))

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            L("选择保存位置", "Choose Save Location"),
            self.outputEdit.text().strip(),
            L("ZIP 安装包 (*.zip)", "ZIP Bundle (*.zip)"),
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._custom_output = True
        self.outputEdit.setText(path)

    def node_name(self):
        return self.nameEdit.text().strip()

    def server_address(self):
        return self.addressEdit.text().strip() if self.addressEdit is not None else ""

    def output_path(self):
        return self.outputEdit.text().strip()


class AgentView(ScrollArea):
    _result = Signal(str, object, object)
    remote_event = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentView")
        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        self._agents = []
        self._ca_file = str(settings.agent_hub_ca or "")
        self._result.connect(self._on_result)
        self._poll_in_flight = False
        self._last_poll_error = ""
        self._last_remote_key = ""
        self._completion_logged = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_remote_status)
        self._poll_timer.start()

        root = QVBoxLayout(self.view)
        root.setContentsMargins(36, 24, 36, 24)
        root.setSpacing(16)
        root.addWidget(SubtitleLabel(L("服务器节点", "Server Agents"), self.view))
        root.addWidget(CaptionLabel(
            L("服务器节点使用压力测试页当前配置；本页不重复填写目标和协议。未配置 Hub 时，生成节点会自动生成一体化控制端。",
              "Server nodes use the current Stress Test page configuration; no duplicate target or protocol form here. Without a Hub, Generate creates an all-in-one control Hub automatically."),
            self.view))

        control = SimpleCardWidget(self.view)
        form = QVBoxLayout(control)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(10)
        form.addWidget(StrongBodyLabel(L("节点控制", "Agent Control"), control))

        connection = QHBoxLayout()
        connection.addWidget(BodyLabel(L("Hub 地址", "Hub URL"), control))
        self.hubEdit = LineEdit(control)
        self.hubEdit.setPlaceholderText("https://control.example.com")
        connection.addWidget(self.hubEdit, 1)
        connection.addWidget(BodyLabel(L("控制令牌", "Controller token"), control))
        self.tokenEdit = PasswordLineEdit(control)
        connection.addWidget(self.tokenEdit, 1)
        self.refreshBtn = PushButton(L("刷新节点", "Refresh agents"), control)
        self.refreshBtn.clicked.connect(self.refresh_agents)
        connection.addWidget(self.refreshBtn)
        self.generateBtn = PushButton(L("生成服务器节点", "Generate server node"), control)
        self.generateBtn.setToolTip(L("已配置 Hub 时追加节点；未配置时生成 Hub + 节点一体化 ZIP。",
                                      "With a configured Hub, append a node; otherwise generate an all-in-one Hub + node ZIP."))
        self.generateBtn.clicked.connect(self.generate_node)
        connection.addWidget(self.generateBtn)
        form.addLayout(connection)

        selection = QHBoxLayout()
        selection.addWidget(BodyLabel(L("服务器节点", "Server agent"), control))
        self.agentCombo = ComboBox(control)
        self.agentCombo.setPlaceholderText(L("先刷新在线节点", "Refresh to load agents"))
        selection.addWidget(self.agentCombo, 1)
        self.nodeStatus = CaptionLabel(L("未连接", "Not connected"), control)
        selection.addWidget(self.nodeStatus)
        form.addLayout(selection)

        # Node status card, migrated from the Collaborative page.
        status_card = SimpleCardWidget(self.view)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 14)
        status_layout.setSpacing(8)
        status_layout.addWidget(StrongBodyLabel(L("节点状态", "Node Status"), status_card))
        self.mTotal = MiniStat("#0078D4", status_card)
        self.mSuccess = MiniStat("#107C10", status_card)
        self.mQps = MiniStat("#8764B8", status_card)
        self.mTx = MiniStat("#00B7C3", status_card)
        status_grid = QGridLayout()
        status_grid.addWidget(self._mini(L("累计请求", "Total Requests"), self.mTotal, status_card), 0, 0)
        status_grid.addWidget(self._mini(L("累计成功", "Total Success"), self.mSuccess, status_card), 0, 1)
        status_grid.addWidget(self._mini(L("实时 QPS", "Live QPS"), self.mQps, status_card), 1, 0)
        status_grid.addWidget(self._mini(L("总发送流量", "Total Sent Traffic"), self.mTx, status_card), 1, 1)
        status_layout.addLayout(status_grid)
        self.nodeListLabel = CaptionLabel(L("（暂无节点连接）", "(no nodes connected)"), status_card)
        self.nodeListLabel.setWordWrap(True)
        status_layout.addWidget(self.nodeListLabel)
        root.addWidget(status_card)

        buttons = QHBoxLayout()
        self.runBtn = PrimaryPushButton(L("在服务器启动", "Start on server"), control)
        self.runBtn.clicked.connect(self.run_job)
        self.stopBtn = PushButton(L("停止服务器任务", "Stop server job"), control)
        self.stopBtn.clicked.connect(self.stop_job)
        buttons.addWidget(self.runBtn)
        buttons.addWidget(self.stopBtn)
        buttons.addStretch(1)
        form.addLayout(buttons)
        root.addWidget(control)

        # QFluentWidgets TextEdit follows the application theme.  QPlainTextEdit
        # used here previously kept a black native background in dark mode.
        log_card = SimpleCardWidget(self.view)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 16, 20, 14)
        log_layout.addWidget(StrongBodyLabel(L("节点状态和操作结果", "Agent status and operation results"), log_card))
        self.logEdit = TextEdit(log_card)
        self.logEdit.setReadOnly(True)
        self.logEdit.setMinimumHeight(150)
        self.logEdit.setMaximumHeight(240)
        self.logEdit.setPlaceholderText(L("节点状态和操作结果会显示在这里。",
                                         "Agent status and operation results appear here."))
        log_layout.addWidget(self.logEdit)
        qconfig.themeChanged.connect(self._refresh_log_theme)
        self._refresh_log_theme()
        self.hubEdit.setText(str(settings.agent_hub_url or ""))
        self.tokenEdit.setText(str(settings.agent_hub_token or ""))
        root.addWidget(log_card)
        root.addStretch(1)

    def _refresh_log_theme(self, *_):
        """Keep the status editor readable in both light and dark themes."""
        if isDarkTheme():
            background, foreground, border = "#202020", "#F3F3F3", "#454545"
        else:
            background, foreground, border = "#FFFFFF", "#1F1F1F", "#D6D6D6"
        self.logEdit.setStyleSheet(
            "QTextEdit {"
            f"background-color: {background}; color: {foreground}; "
            f"border: 1px solid {border}; border-radius: 6px; padding: 6px;"
            "}"
        )

    def _mini(self, title, value_label, parent):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(CaptionLabel(title, w))
        v.addWidget(value_label)
        return w

    def _client(self):
        url = self.hubEdit.text().strip()
        token = self.tokenEdit.text().strip()
        if not url or not token:
            raise ValueError(L("请填写 Hub 地址和控制令牌", "Enter the Hub URL and controller token"))
        return ControllerClient(url, token, ca_file=self._ca_file)

    def _async(self, action, fn):
        def run():
            try:
                self._result.emit(action, fn(), None)
            except Exception as exc:
                self._result.emit(action, None, exc)
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _remote_status_key(event):
        if not isinstance(event, dict):
            return ""
        return "|".join((
            str(event.get("state", "")),
            str(event.get("job_id", "")),
            str(event.get("total", "")),
            str(event.get("tx", "")),
        ))

    def _poll_remote_status(self):
        """Keep the selected job visible on the Stress Test page."""
        if self._poll_in_flight or not self._selected_agent():
            return
        try:
            client = self._client()
        except Exception:
            return

        self._poll_in_flight = True

        def query():
            try:
                return client.list_agents()
            finally:
                self._poll_in_flight = False

        self._async("poll_status", query)

    def refresh_agents(self):
        try:
            client = self._client()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refreshBtn.setEnabled(False)
        self._async("list", client.list_agents)

    def generate_node(self):
        """Show one Fluent dialog and generate either an appended or all-in-one node."""
        url = self.hubEdit.text().strip()
        token = self.tokenEdit.text().strip()
        has_hub = bool(url and token)
        dialog = GenerateNodeDialog(self.window(), has_hub=has_hub, hub_url=url)
        if not dialog.exec():
            return

        name = dialog.node_name()
        if not name:
            self._show_error(L("请填写节点名称", "Enter a node name"))
            return

        output = dialog.output_path()
        if not output:
            self._show_error(L("请填写保存位置", "Enter a save location"))
            return
        if not output.lower().endswith(".zip"):
            output += ".zip"
        try:
            output_path = Path(output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_error(str(exc))
            return

        self.generateBtn.setEnabled(False)
        if has_hub:
            try:
                client = ControllerClient(url, token, ca_file=self._ca_file)
            except Exception as exc:
                self.generateBtn.setEnabled(True)
                self._show_error(str(exc))
                return
            self._async("generate", lambda: self._provision_and_package(client, name, str(output_path)))
            return

        public_address = dialog.server_address()
        try:
            host, port = self._normalize_public_hub(public_address)
        except Exception as exc:
            self.generateBtn.setEnabled(True)
            self._show_error(str(exc))
            return
        self._async("generate_offline", lambda: self._generate_all_in_one_package(
            name, host, port, str(output_path)))

    @staticmethod
    def _normalize_public_hub(value):
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(L("请填写服务器公网 IP 或域名", "Enter the server public IP or domain"))
        if "://" not in raw:
            raw = f"https://{raw}"
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError(L("服务器地址必须是 IP 或域名", "Server address must be an IP or domain"))
        host = parsed.hostname.rstrip(".")
        port = parsed.port or 8787
        if not 1 <= port <= 65535:
            raise ValueError(L("端口必须在 1–65535", "Port must be 1-65535"))
        return host, port

    @staticmethod
    def _bracket_host(host):
        return f"[{host}]" if ":" in host else host

    @staticmethod
    def _generate_certificate(directory, host):
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_path = Path(directory) / "cert.pem"
        key_path = Path(directory) / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        return cert_path, key_path

    def _generate_all_in_one_package(self, name, public_host, public_port, output):
        """Create a Hub plus node package without requiring an already-running Hub."""
        output = Path(output).resolve()
        agent_exe, linux_root = self._node_payload_paths()
        hub_exe = self._hub_exe_path()
        hub_source = linux_root / "agent" / "hub.py"
        transport_source = linux_root / "agent" / "transport.py"
        missing = [path for path in (agent_exe, hub_exe, hub_source, transport_source,
                                     linux_root / "server_agent.py") if not path.is_file()]
        if missing:
            raise FileNotFoundError(L("安装包缺少服务端文件，请重新构建主程序。",
                                      "The installed app is missing server payload files; rebuild the main app."))

        agent_id = "server-" + secrets.token_hex(6)
        agent_token = secrets.token_urlsafe(32)
        controller_token = secrets.token_urlsafe(32)
        public_url = f"https://{self._bracket_host(public_host)}:{public_port}"
        local_hub_url = f"https://127.0.0.1:{public_port}"
        agent_config = {
            "hub_url": local_hub_url,
            "agent_id": agent_id,
            "token": agent_token,
            "ca_file": "ca.pem",
            "allowed_targets": sorted({str(item.get("host")) for item in settings.authorized if item.get("host")}),
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 100,
            "max_duration": 300,
            "max_threads": 32,
            "max_packet_size": 65536,
            "poll_interval": 2,
            "heartbeat_interval": 10,
            "job_heartbeat_interval": 2,
        }
        agent_config["insecure_control_tls"] = True
        hub_config = {
            "controller_token": controller_token,
            "agents": [{"agent_id": agent_id, "name": name, "token": agent_token}],
        }
        config_json = json.dumps(agent_config, ensure_ascii=False, indent=2)
        hub_json = json.dumps(hub_config, ensure_ascii=False, indent=2)

        with tempfile.TemporaryDirectory(prefix="netpulse-cert-") as temp:
            cert_path, key_path = self._generate_certificate(temp, public_host)
            cert_pem = cert_path.read_bytes()
            key_pem = key_path.read_bytes()

            windows_hub = """@echo off
cd /d "%~dp0"
NetPulse-Hub.exe --config hub.json --host 0.0.0.0 --port __NETPULSE_PORT__ --certfile cert.pem --keyfile key.pem
""".replace("__NETPULSE_PORT__", str(public_port)).replace("\n", "\r\n")
            windows_agent = """@echo off
cd /d "%~dp0"
NetPulse-Agent.exe --config agent.json
""".replace("\n", "\r\n")
            windows_all = r"""@echo off
cd /d "%~dp0"
start "NetPulse Hub" cmd /c hub\start-hub.cmd
timeout /t 2 /nobreak >nul
cd agent
NetPulse-Agent.exe --config agent.json
""".replace("\n", "\r\n")
            linux_hub = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec python3 hub.py --config hub.json --host 0.0.0.0 --port __NETPULSE_PORT__ --certfile cert.pem --keyfile key.pem
""".replace("__NETPULSE_PORT__", str(public_port))
            linux_agent = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec python3 server_agent.py --config agent.json
"""
            linux_all = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ "${SKIP_PIP:-0}" != "1" ]; then
    python3 -m pip install --user -r agent/requirements.txt
fi
export PYTHONUNBUFFERED=1
python3 hub/hub.py --config hub/hub.json --host 0.0.0.0 --port __NETPULSE_PORT__ --certfile hub/cert.pem --keyfile hub/key.pem --wait-ready 1 &
hub_pid=$!
trap 'kill "$hub_pid" 2>/dev/null || true' EXIT INT TERM
for i in $(seq 1 30); do
    if curl -ksSf "https://127.0.0.1:__NETPULSE_PORT__/health" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$hub_pid" 2>/dev/null; then
        echo "NetPulse Hub exited during startup" >&2
        exit 1
    fi
    sleep 0.5
done
if ! curl -ksSf "https://127.0.0.1:__NETPULSE_PORT__/health" >/dev/null 2>&1; then
    echo "NetPulse Hub did not become ready" >&2
    kill "$hub_pid" 2>/dev/null || true
    exit 1
fi
echo "NetPulse Hub ready"
exec python3 agent/server_agent.py --config agent/agent.json
""".replace("__NETPULSE_PORT__", str(public_port))

            def executable_info(content):
                info = zipfile.ZipInfo(content[0])
                info.create_system = 3
                info.external_attr = 0o755 << 16
                return info, content[1]

            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
                # Windows all-in-one deployment
                bundle.write(hub_exe, "windows/hub/NetPulse-Hub.exe")
                bundle.writestr("windows/hub/hub.json", hub_json)
                bundle.writestr("windows/hub/cert.pem", cert_pem)
                bundle.writestr("windows/hub/key.pem", key_pem)
                bundle.writestr("windows/hub/start-hub.cmd", windows_hub)
                bundle.write(agent_exe, "windows/agent/NetPulse-Agent.exe")
                bundle.writestr("windows/agent/agent.json", config_json)
                bundle.writestr("windows/agent/ca.pem", cert_pem)
                bundle.writestr("windows/agent/start-agent.cmd", windows_agent)
                bundle.writestr("windows/start-all.cmd", windows_all)

                # Linux all-in-one deployment
                bundle.write(hub_source, "linux/hub/hub.py")
                bundle.write(transport_source, "linux/hub/transport.py")
                bundle.writestr("linux/hub/hub.json", hub_json)
                bundle.writestr("linux/hub/cert.pem", cert_pem)
                bundle.writestr("linux/hub/key.pem", key_pem)
                bundle.write(linux_root / "server_agent.py", "linux/agent/server_agent.py")
                bundle.write(linux_root / "agent" / "worker.py", "linux/agent/agent/worker.py")
                bundle.write(transport_source, "linux/agent/agent/transport.py")
                bundle.write(linux_root / "agent" / "__init__.py", "linux/agent/agent/__init__.py")
                bundle.writestr("linux/agent/agent.json", config_json)
                bundle.writestr("linux/agent/ca.pem", cert_pem)
                bundle.writestr("linux/agent/requirements.txt", "requests>=2.31" + chr(10))
                for script in (("linux/hub/start-hub.sh", linux_hub),
                               ("linux/agent/start-agent.sh", linux_agent),
                               ("linux/start-all.sh", linux_all)):
                    bundle.writestr(*executable_info(script))

                instructions = L(
                    f"NetPulse 一体化服务器节点\n\n"
                    f"控制地址：{public_url}\n\n"
                    "Windows：解压后进入 windows，右键以管理员运行 start-all.cmd，并允许防火墙端口 8787。\n"
                    "Linux 脚本会等待 Hub 健康检查通过后再启动 Agent。\n"
                    "Linux：解压后进入 linux，执行 chmod +x start-all.sh && ./start-all.sh，并放行 TCP 8787。\n"
                    "启动完成后，在本地 GUI 服务器节点页点击刷新节点。\n"
                    "本地会同时生成 .ca.pem 证书文件；不要删除它，GUI 需要它连接控制端。\n"
                    "这个包使用随机自签名证书，仅用于你自己的服务器控制面。\n"
                    "只对你拥有或取得书面授权的目标执行测试。",
                    f"NetPulse all-in-one server node\n\n"
                    f"Control URL: {public_url}\n\n"
                    "Windows: extract, open windows, run start-all.cmd as administrator, and allow TCP 8787.\n"
                    "Linux: extract, open linux, run chmod +x start-all.sh && ./start-all.sh, and open TCP 8787. The script waits for the Hub health check before starting the Agent.\n"
                    "After it starts, click Refresh agents in the local GUI.\n"
                    "A .ca.pem certificate is generated beside the ZIP; keep it because the GUI needs it to connect.\n"
                    "This package uses a random self-signed certificate for your own control plane only.\n"
                    "Use only targets you own or are authorized to test.")
                bundle.writestr("README.txt", instructions)

        ca_path = output.with_suffix(".ca.pem")
        ca_path.write_bytes(cert_pem)
        return {
            "path": str(output),
            "agent_id": agent_id,
            "name": name,
            "hub_url": public_url,
            "controller_token": controller_token,
            "ca_file": str(ca_path),
        }

    def _provision_and_package(self, client, name, output):
        record = client.provision_agent(name)
        exe, linux_root = self._node_payload_paths()
        source_files = [
            linux_root / "server_agent.py",
            linux_root / "agent" / "worker.py",
            linux_root / "agent" / "transport.py",
            linux_root / "agent" / "__init__.py",
        ]
        missing = ([exe] if not exe.is_file() else []) + [path for path in source_files if not path.is_file()]
        if missing:
            raise FileNotFoundError(L("安装包缺少 Agent 文件，请重新构建主程序。",
                                      "The installed app is missing agent payload files; rebuild the main app."))
        allowed_targets = sorted({str(item.get("host")) for item in settings.authorized if item.get("host")})
        agent_config = {
            "hub_url": self.hubEdit.text().strip().rstrip("/"),
            "agent_id": record["agent_id"],
            "token": record["token"],
            "allowed_targets": allowed_targets,
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 100,
            "max_duration": 300,
            "max_threads": 32,
            "max_packet_size": 65536,
            "poll_interval": 2,
            "heartbeat_interval": 10,
            "job_heartbeat_interval": 2,
        }
        instructions = L(
            "NetPulse 服务器节点\n\n"
            "1. Windows：进入 windows 文件夹，把 agent.json 与 NetPulse-Agent.exe 放在同一目录，双击 start-agent.cmd。\n"
            "2. Linux：进入 linux 文件夹，安装 requests 后运行 ./start-agent.sh。\n"
            "3. 节点上线后，在本地压力测试页填写配置，再回到服务器节点页点击启动。\n"
            "4. 只对你拥有或取得书面授权的目标执行测试。",
            "NetPulse server node\n\n"
            "1. Windows: open the windows folder, keep agent.json beside NetPulse-Agent.exe, then run start-agent.cmd.\n"
            "2. Linux: open the linux folder, install requests, then run ./start-agent.sh.\n"
            "3. After the node is online, configure the Stress Test page locally and click Start on server.\n"
            "4. Use only targets you own or are authorized to test.")
        start_cmd = '@echo off\ncd /d "%~dp0"\nNetPulse-Agent.exe --config agent.json\n'
        start_sh = '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\nexec python3 server_agent.py --config agent.json\n'
        config_json = json.dumps(agent_config, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            if exe.is_file():
                bundle.write(exe, "windows/NetPulse-Agent.exe")
                bundle.writestr("windows/agent.json", config_json)
                bundle.writestr("windows/start-agent.cmd", start_cmd)
            linux_payloads = [
                (source_files[0], "linux/server_agent.py"),
                (source_files[1], "linux/agent/worker.py"),
                (source_files[2], "linux/agent/transport.py"),
                (source_files[3], "linux/agent/__init__.py"),
            ]
            for path, archive_name in linux_payloads:
                if path.is_file():
                    bundle.write(path, archive_name)
            bundle.writestr("linux/agent.json", config_json)
            bundle.writestr("linux/requirements.txt", "requests>=2.31\n")
            # Preserve the executable bit for Linux ZIP archives.
            info = zipfile.ZipInfo("linux/start-agent.sh")
            info.create_system = 3
            info.external_attr = 0o755 << 16
            bundle.writestr(info, start_sh, compress_type=zipfile.ZIP_DEFLATED)
            bundle.writestr("README.txt", instructions)
        return {"path": output, "agent_id": record["agent_id"], "name": name}

    @classmethod
    def _hub_exe_path(cls):
        resource = cls._resource_root()
        project = Path(__file__).resolve().parents[2]
        candidates = [
            resource / "agent_template" / "hub" / "NetPulse-Hub.exe",
            project / "dist" / "NetPulse-Hub.exe",
            project / "NetPulse-Hub.exe",
        ]
        return next((path for path in candidates if path.is_file()), candidates[0])

    @staticmethod
    def _resource_root():
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _node_payload_paths(cls):
        """Locate the Windows binary and Linux source bundled with the app."""
        resource = cls._resource_root()
        project = Path(__file__).resolve().parents[2]
        exe_candidates = [
            resource / "agent_template" / "windows" / "NetPulse-Agent.exe",
            project / "dist" / "NetPulse-Agent.exe",
            project / "NetPulse-Agent.exe",
        ]
        linux_candidates = [
            resource / "agent_template" / "linux",
            project,
        ]
        exe = next((path for path in exe_candidates if path.is_file()), exe_candidates[0])
        linux_root = next((path for path in linux_candidates if (path / "server_agent.py").is_file()), linux_candidates[0])
        return exe, linux_root

    def _selected_agent(self):
        idx = self.agentCombo.currentIndex()
        return self._agents[idx]["agent_id"] if 0 <= idx < len(self._agents) else ""

    def _stress_config(self):
        """Build the same single-target config used by the existing Collab page."""
        stress = self.window().stress
        targets = stress._parse_targets()
        if targets is None:
            raise ValueError(L("请先修正压力测试页的目标地址", "Fix the target address on the Stress Test page first"))
        if not targets:
            raise ValueError(L("请先在压力测试页填写目标", "Set the target on the Stress Test page first"))

        # A server agent runs one job at a time. Keep the existing Stress page
        # as the source of truth and use its first target, like Collab Start.
        target_raw, host = targets[0]
        if not is_authorized(host):
            dlg = AuthDialog(host, self.window())
            if not dlg.exec():
                raise ValueError(L(f"目标 {host} 未授权，服务器任务已阻止",
                                   f"Target {host} is not authorized; server job blocked"))
            if not add_authorized(host, dlg.note()):
                raise ValueError(L(f"无法保存目标 {host} 的授权记录：{settings.last_error}",
                                   f"Could not save authorization for {host}: {settings.last_error}"))
            stress.refresh_auth_list()

        protocol = stress.protoCombo.currentText().upper()
        rate = stress.rateSpin.value()
        if rate > HIGH_RATE:
            from qfluentwidgets import MessageBox
            confirm = MessageBox(
                L("高请求速率二次确认", "High Rate Confirmation"),
                L(f"服务器节点将使用压力测试页配置向 {host} 发送最高 {rate} QPS。\n"
                  "请再次确认目标已获授权且能够承受该速率。",
                  f"The server agent will use the Stress page configuration against {host}, up to {rate} QPS.\n"
                  "Confirm that the target is authorized and can handle this rate."),
                self.window())
            if not confirm.exec():
                raise ValueError(L("高速率未确认，服务器任务已取消",
                                   "High rate not confirmed; server job cancelled"))

        headers_raw = stress.headersEdit.toPlainText().strip()
        try:
            headers = json.loads(headers_raw) if headers_raw else {}
        except json.JSONDecodeError as exc:
            raise ValueError(L("请求头须为合法 JSON", "Headers must be valid JSON")) from exc
        if not isinstance(headers, dict):
            raise ValueError(L("请求头必须是 JSON 对象", "Headers must be a JSON object"))

        port = stress.portSpin.value()
        config = {
            "target": host,
            "port": port,
            "protocol": protocol,
            "url": "",
            "threads": stress.threadSpin.value(),
            "duration": stress.get_duration_seconds(),
            "rate": rate,
            "packet_size": settings.default_packet_size,
            "timeout": settings.default_timeout_ms,
            "headers": headers,
        }
        if protocol in ("HTTP", "HTTPS"):
            config["url"] = build_http_url(target_raw, host, port, protocol)
        return config

    def run_job(self):
        agent_id = self._selected_agent()
        if not agent_id:
            self._show_error(L("请先刷新并选择节点", "Refresh and select an agent first"))
            return
        try:
            client = self._client()
            job = self._stress_config()
        except Exception as exc:
            self._show_error(str(exc))
            return
        command = {"type": "start", "job_id": uuid.uuid4().hex, "config": job}
        # Keep the node allow-list aligned with the authorized target currently
        # selected on the Stress page, then queue the start command in order.
        def dispatch():
            client.send(agent_id, {"type": "sync_targets", "targets": [job["target"]]})
            return client.send(agent_id, command)
        self._async("run", dispatch)

    def stop_job(self):
        agent_id = self._selected_agent()
        if not agent_id:
            self._show_error(L("请先选择节点", "Select an agent first"))
            return
        try:
            client = self._client()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._async("stop", lambda: client.send(agent_id, {"type": "stop"}))

    def _on_result(self, action, value, error):
        self.refreshBtn.setEnabled(True)
        self.generateBtn.setEnabled(True)
        if error:
            if action == "poll_status":
                if str(error) != self._last_poll_error:
                    self._last_poll_error = str(error)
                    self.logEdit.append(f"[poll] {error}")
                return
            self._show_error(str(error))
            return
        if action == "list":
            self._agents = list(value or [])
            self.agentCombo.clear()
            for agent in self._agents:
                state = L("在线", "online") if agent.get("online") else L("离线", "offline")
                self.agentCombo.addItem(f"{agent.get('name', agent['agent_id'])} [{state}] — {agent['agent_id']}")
            self.nodeStatus.setText(L(f"已加载 {len(self._agents)} 个节点",
                                      f"Loaded {len(self._agents)} agent(s)"))
            self.logEdit.append(L(f"已刷新节点列表，共 {len(self._agents)} 个节点。",
                                  f"Agent list refreshed: {len(self._agents)} agent(s)."))
            for agent in self._agents:
                event = agent.get("last_event") or {}
                if event:
                    state = event.get("state", "unknown")
                    self.logEdit.append(f"{agent.get('agent_id')}: {state}")
            self._update_agent_status_widgets()
            self._emit_selected_remote_event(force=True)
        elif action == "poll_status":
            self._agents = list(value or [])
            self._update_agent_status_widgets()
            self._emit_selected_remote_event()
            if self._last_poll_error:
                self.logEdit.append(L("服务器节点状态恢复刷新。", "Server-agent status polling recovered."))
            self._last_poll_error = ""
        else:
            if action in {"generate", "generate_offline"}:
                self.logEdit.append(L(f"节点安装包已生成：{value['path']}",
                                      f"Node bundle generated: {value['path']}"))
                if action == "generate_offline":
                    self.hubEdit.setText(value["hub_url"])
                    self.tokenEdit.setText(value["controller_token"])
                    self._ca_file = value["ca_file"]
                    settings.set("agent_hub_url", value["hub_url"])
                    settings.set("agent_hub_token", value["controller_token"])
                    settings.set("agent_hub_ca", value["ca_file"])
                    self.logEdit.append(L(
                        f"一体化包已生成。复制到服务器运行 start-all 后，本地控制地址：{value['hub_url']}",
                        f"All-in-one bundle generated. Run start-all on the server; local control URL: {value['hub_url']}"))
                InfoBar.success(L("节点已生成", "Node generated"),
                                L("解压后把对应目录复制到服务器即可。", "Extract it and copy the matching folder to the server."),
                                parent=self, position=InfoBarPosition.TOP)
            else:
                InfoBar.success(L("已发送", "Sent"), L("命令已发送到 Hub", "Command queued on the Hub"),
                                parent=self, position=InfoBarPosition.TOP)

    def _update_agent_status_widgets(self):
        total_req = total_ok = total_tx = 0
        total_qps = 0.0
        parts = []
        for agent in self._agents:
            stats = agent.get("last_event") or {}
            name = agent.get("name") or agent.get("agent_id", "-")
            if stats:
                total_req += stats.get("total", 0)
                total_ok += stats.get("success", 0)
                total_qps += stats.get("qps", 0.0)
                tx = stats.get("tx", stats.get("bytes_tx", 0))
                total_tx += tx
                state = stats.get("state", "running")
                state_text = {
                    "idle": L("空闲", "idle"), "ready": L("就绪", "ready"),
                    "starting": L("启动中", "starting"), "running": L("运行中", "running"),
                    "stopping": L("停止中", "stopping"), "completed": L("完成", "completed"),
                    "error": L("错误", "error"),
                }.get(state, state)
                parts.append(
                    f"{name} [{state_text}]: ✓{stats.get('success',0)} "
                    f"✗{stats.get('fail',0)} ({stats.get('qps',0):.0f} QPS) {fmt_bytes(tx)}")
                if state == "completed":
                    job_key = str(stats.get("job_id") or stats.get("ts") or "final")
                    if self._completion_logged.get(name) != job_key:
                        self._completion_logged[name] = job_key
                        self.logEdit.append(L(
                            f"服务器节点 {name} 压测完成：总请求 {stats.get('total',0)}  "
                            f"成功 {stats.get('success',0)}  失败 {stats.get('fail',0)}  "
                            f"发送 {fmt_bytes(tx)}",
                            f"Server node {name} finished: total {stats.get('total',0)}  "
                            f"success {stats.get('success',0)}  failed {stats.get('fail',0)}  "
                            f"sent {fmt_bytes(tx)}"))
            else:
                parts.append(f"{name}: " + L("等待数据", "waiting for data"))
        self.mTotal.setText(str(total_req))
        self.mSuccess.setText(str(total_ok))
        self.mQps.setText(f"{total_qps:.1f}")
        self.mTx.setText(fmt_bytes(total_tx))
        self.nodeListLabel.setText("  |  ".join(parts) if parts else L("（暂无节点连接）", "(no nodes connected)"))

    def _emit_selected_remote_event(self, force=False):
        idx = self.agentCombo.currentIndex()
        if not 0 <= idx < len(self._agents):
            return
        event = dict(self._agents[idx].get("last_event") or {})
        if not event:
            return
        key = self._remote_status_key(event)
        if force or key != self._last_remote_key:
            self._last_remote_key = key
            self.remote_event.emit(event)

    def _show_error(self, text):
        self.logEdit.append(f"[error] {text}")
        InfoBar.error(L("服务器节点操作失败", "Server-agent operation failed"), text,
                      parent=self, position=InfoBarPosition.TOP)

