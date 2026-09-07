# NetPulse Server Agent

这是一个**新增的服务器节点部署方式**，不改动现有桌面 GUI。服务器运行 `server_agent.py`（或打包后的 `NetPulse-Agent.exe`），通过出站 HTTPS 轮询连接 Hub；本地操作员使用 `agentctl.py` 向指定节点发送开始/停止命令。

```text
本地操作员 / 控制台
        │ HTTPS（控制命令、状态）
        ▼
NetPulse Hub（你自己部署）
        ▲
        │ 出站 HTTPS（服务器无需开放入站端口）
        │
NetPulse-Agent.exe（你的服务器） ───► 已授权测试目标
```

## 为什么这样做

- 现有 `NetPulse.exe`、GUI、压测页和协同页完全保留。
- Agent 是独立进程，复制到服务器即可运行，不需要把桌面 GUI 搬到服务器。
- 服务器主动向 Hub 建立连接，不要求给服务器开放管理端口。
- Agent 的目标白名单、协议、速率、时长、线程数和包大小都在服务器本地配置中设上限。
- 一个 Agent 一次只接受一个任务；默认只开放 HTTP / HTTPS / TCP 远程任务。

> 仅用于你拥有或取得书面授权的测试目标。Hub/Agent 不提供任意公网目标的开放转发能力。

## 一体化生成（开源用户默认方式）

如果还没有部署 Hub，直接把“Hub 地址”和“控制令牌”留空，点击“生成服务器节点”：

1. 输入节点名称。
2. 输入这台服务器的公网 IP 或域名。
3. 保存 ZIP。

GUI 会自动生成：

- `NetPulse-Hub`
- `NetPulse-Agent`
- 随机控制令牌
- 随机节点令牌
- 随机自签名 HTTPS 证书
- Windows 一键启动脚本
- Linux 一键启动脚本
- 已配置好的 `hub.json` 和 `agent.json`

Windows 服务器解压后进入 `windows`，以管理员运行 `start-all.cmd` 并放行 TCP `8787`。Linux 服务器解压后进入 `linux`，运行 `./start-all.sh` 并放行 TCP `8787`。启动后回 GUI 点击“刷新节点”。

生成 ZIP 时旁边还会生成 `NetPulse-节点名.ca.pem`。这个文件是 GUI 信任一体化 Hub 证书用的，不要删除或改名。

## 0. 使用 GUI 生成节点包（推荐）

前提：先部署并启动 `NetPulse-Hub`，本地 GUI 能访问它的 HTTPS 地址。

1. 在本地 NetPulse 的“服务器节点”页填写 Hub 地址和控制令牌。
2. 点击“生成服务器节点”。
3. 输入节点名称并保存 ZIP。
4. 把 ZIP 复制到服务器并解压：
   - Windows Server：进入 `windows`，双击 `start-agent.cmd`。
   - Linux：进入 `linux`，先安装 `requests`，再执行 `./start-agent.sh`。
5. 回到本地 GUI 点击“刷新节点”，看到该节点在线即可。

生成的 ZIP 已经包含该节点专属 token、Hub 地址、Windows 可执行文件和 Linux 源码，不需要再手工编辑 `agent.json`。节点初次生成时目标白名单可以为空；每次从 GUI 启动任务前，控制端会同步当前已在本地授权的目标。

## 1. 生成随机令牌

在 PowerShell 中生成令牌：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

第一行用于 Hub 的 `controller_token`，第二行用于 Agent 的 `token`。不要把令牌提交到 Git 或发到聊天群。

## 2. 配置 Hub

复制 `agent/hub.example.json` 为 `hub.json`，填入同一组 Agent ID/令牌和控制端令牌：

```json
{
  "controller_token": "至少 16 个字符的随机值",
  "agents": [
    {
      "agent_id": "server-01",
      "name": "My Server",
      "token": "至少 16 个字符的随机值"
    }
  ]
}
```

开发环境启动：

```powershell
python agent/hub.py --config hub.json --host 127.0.0.1 --port 8787
```

生产环境请把 Hub 放在你自己的 HTTPS 反向代理后面（例如 Nginx/Caddy），然后让 Agent 使用 `https://...` 地址。不要把内置 HTTP 监听器直接暴露到公网。

## 3. 配置服务器 Agent

复制 `agent/agent.example.json` 为服务器上的 `agent.json`：

```json
{
  "hub_url": "https://control.example.com",
  "agent_id": "server-01",
  "token": "与 hub.json 中 server-01 相同",
  "allowed_targets": ["your-authorized-test-host.example"],
  "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
  "max_rate": 100,
  "max_duration": 300,
  "max_threads": 32,
  "max_packet_size": 65536,
  "poll_interval": 2,
  "heartbeat_interval": 10
}
```

`allowed_targets` 必须填写精确主机名/IP。任务中的目标不在这个列表里会被 Agent 拒绝。远程任务还会受到 Agent 配置的所有上限约束。

在服务器测试运行：

```powershell
python server_agent.py --config agent.json
```

看到 `online as server-01` 即说明 Agent 已上线。服务器只需要能够访问 Hub 的出站 HTTPS，不需要为 Agent 开放入站端口。

## 4. 从控制端查看和发送任务

复制 `agent/controller.example.json` 为 `controller.json`，填入 Hub 地址和 `controller_token`。

查看节点：

```powershell
python agentctl.py --config controller.json list
```

复制 `agent/job.example.json` 为 `job.json`，填入已经授权的测试主机，然后发送任务：

```powershell
python agentctl.py --config controller.json run server-01 job.json
```

停止任务：

```powershell
python agentctl.py --config controller.json stop server-01
```

## 5. 打包客户端

在开发电脑上执行：

```powershell
.\build_agent.ps1
```

产物位于 `dist\NetPulse-Agent.exe`。Agent 是跨平台纯 Python 网络进程，不依赖 Qt 或桌面环境；Windows 直接复制 exe，Linux 使用同一份 `server_agent.py`（或在 Linux 上用 PyInstaller 重新打包）和 `agent.json`。`hub.py` 和 `agentctl.py` 会生成独立 exe，详见 `Agent.spec` / `Hub.spec` / `AgentCtl.spec`。

## 6. 服务器开机启动

Windows 服务器可以用“任务计划程序”创建任务：

- 触发器：系统启动时
- 操作：`NetPulse-Agent.exe --config C:\NetPulse\agent.json`
- 勾选“无论用户是否登录都运行”
- 失败后按 1 分钟间隔重试
- 运行账户使用专用低权限账户

Linux 服务器不能运行 Windows exe，但可以直接运行 `server_agent.py`：安装 Python 3.10+ 与 `requests`（`pip install requests`），然后执行 `python3 server_agent.py --config agent.json`。也可以在 Linux 上执行 `pyinstaller Agent.spec` 生成 Linux 版二进制。
## Docker / Railway notes

The generated Linux bundle is now safe for container startup. It starts Hub first, polls `https://127.0.0.1:8787/health`, and only starts Agent after the health check succeeds.

With TCP proxies (Railway, ngrok, frp, etc.), remember there are two URLs:

```text
Public Hub URL for GUI / remote agents: https://proxy.example.com:23915
Internal Hub bind port on the server/container: 8787
```

The GUI should use the public URL. A co-located Agent should use `https://127.0.0.1:8787`; only a remote Agent should use the public proxy URL. The current one-click bundle generates the internal bind port from the address you enter, so enter the public proxy address only if the Agent will connect through the proxy. For a co-located Agent with a proxy, edit `agent/agent.json` after generation.


Build an extracted bundle:

```bash
cp deploy/docker/Dockerfile /path/to/extracted/linux/
docker build -t netpulse-node /path/to/extracted/linux/
docker run --rm -p 8787:8787 netpulse-node
```

The Hub listens on `0.0.0.0:8787`.

For Railway, prefer the official HTTPS proxy and run Hub in plain HTTP mode:

```bash
python3 hub/hub.py --config hub/hub.json --host 0.0.0.0 --port 8787
```

In that mode the local Agent can use `http://127.0.0.1:8787`, while the local GUI should use the public `https://...` URL provided by Railway. Do not put a self-signed HTTPS backend behind Railway's ordinary HTTP reverse proxy.
