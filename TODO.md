# NetPulse 1.2.2 自测与修复清单

## 已完成
- [x] 自测全部 Python 文件编译与 AST。
- [x] 运行 Agent/Hub 单元测试：6/6 通过。
- [x] 修复设置页 `ClickableCard` 深色模式 `QColor` 未导入的潜在崩溃。
- [x] 修复主题色色块在深色模式下的低对比描边。
- [x] 修复“生成服务器节点”弹窗在深色模式下退回浅色的问题。
- [x] 修复弹窗遮罩在深色模式下过亮的问题。
- [x] 修复页面切换动画在快速切页/主题切换后控件透明度卡死的问题；新增恢复 watchdog。
- [x] 缩短控件浮现总动画时间，降低切页时的长时间透明风险。
- [x] 检查 UI 双语调用：无缺失英文/中文参数。
- [x] 检查隐藏的原生 `QInputDialog` 使用：已改为 Fluent 弹窗。
- [x] 验证深色模式下生成节点、授权弹窗和主窗口渲染。
- [x] 验证本地与服务器 Agent 任务的实时数据、进度条和完成状态。
- [x] 验证协同测试节点完成后回传最终流量并在协同日志显示。
- [x] 统一版本号为 1.2.2：更新检查、安装包、README。
- [x] 重新打包 `NetPulse`、`NetPulse-Agent`、`NetPulse-Hub`、`NetPulse-AgentCtl`。
- [x] 运行 `git diff --check`，无空白错误。

## 结果
- 版本：`1.2.2`
- 单元测试：`Ran 6 tests, OK`
- 主程序：`dist/NetPulse/NetPulse.exe`
- Agent：`dist/NetPulse-Agent.exe`
- Hub：`dist/NetPulse-Hub.exe`
- CLI：`dist/NetPulse-AgentCtl.exe`

## UI 调整
- [x] 协同测试页面恢复“节点状态”统计卡，并加入总发送流量。
- [x] 服务器节点页面同步显示累计请求、成功、QPS 和总发送流量。
- [x] 服务器节点页新增操作日志，节点完成时显示总请求/成功/失败/发送流量。
- [x] 移除压力测试页面的服务器任务镜像卡；压力测试页只显示本地任务。
- [x] Agent 连接错误日志现在会附带 `hub=<url>`，方便发现 Agent 配置的 Hub 地址不对。

## TLS 连接中断修复
- [x] Hub 现在会把 TLS 握手中断、HTTP 客户端误连 HTTPS 端口、扫描器断开视为普通连接关闭。
- [x] Hub 不再因 `UNEXPECTED_EOF_WHILE_READING` / `10054` 打出异常堆栈。
- [x] 控制面日志已改为立即刷新，便于服务器容器查看。
- [x] 已重新打包新版 Hub；一体化包需重新生成以获得新 Hub。

## 最终回归自测
- [x] 全部 Python 文件编译通过。
- [x] Agent/Hub 单元测试 6/6 通过。
- [x] 浅色和深色模式下主窗口、全部页面、生成节点弹窗切换无控件卡透明。
- [x] 双语 `L()` 调用扫描无缺失参数。
- [x] `git diff --check` 无空白错误。
- [x] 补充文档说明：TCP 穿透场景下，GUI 用公网代理地址，同机 Agent 用服务器内部 `127.0.0.1:8787`。
- [x] 服务器节点状态轮询恢复时会写一条日志，不再静默失败。
- [x] 重新打包主程序和 Hub/Agent/CLI。

## 控制端自签名证书修复
- [x] GUI 连接一体化 Hub 时，若 `.ca.pem` 丢失/移动或旧包未保存证书，会自动对控制面请求进行一次自签名兼容重试。
- [x] 仅控制 Hub 请求允许该兼容逻辑；普通压测目标不受影响。
- [x] 令牌仍然必须正确，防止自动跳过证书后开放控制面。

## Docker / Railway 启动修复
- [x] 生成包的 `start-all.sh` 改为：先启动 Hub，再轮询 `/health`，健康后才启动 Agent。
- [x] Hub 增加 `--wait-ready`，完成监听和 TLS 初始化后再进入服务循环。
- [x] 生成包的 Agent 配置增加 `insecure_control_tls: true`，只允许自签名控制面证书跳过校验。
- [x] Agent 不再把 `insecure_control_tls` 应用到普通测试目标；目标流量仍由测试协议自身处理。
- [x] Hub 和 Agent 日志改为立即刷新，便于容器查看。
- [x] 验证 Hub 监听 `https://0.0.0.0:8787` 后，Agent 可使用 `https://127.0.0.1:8787` 成功上线。
- [ ] Railway 若使用官方 HTTPS 反向代理，建议 Hub 以纯 HTTP 模式启动（不带 `--certfile/--keyfile`），由 Railway 终止 TLS。

## 发布后排查
- [x] 修复打包应用首导 `PySide6.QtCore` 失败：打包时只暴露 PYZ 命名空间模块，缺少 `PySide6.__init__` 的 DLL 目录注册。已在 `main.py` 冻结分支中注册 PySide6/shiboken6 DLL 目录，预加载 `shiboken6.Shiboken` 和 `QtCore.pyd`。
- [x] 重新构建并通过本地运行验证（进程保持运行，不再出现 Unhandled exception）。
- [x] 重新生成 `NetPulse-Setup-1.2.2.exe`。
- [x] 本机直接运行 `dist/NetPulse/NetPulse.exe` 正常退出；当前构建的 Qt DLL 可在本机加载。
- [x] Release 安装包 `NetPulse-Setup-1.2.2.exe` 已上传，SHA256: `6D1FE85F54CCA4BC8A4179EFC86B957AB00E26A713110C0CDCFEEC24682D6AF0`。
- [ ] 如果用户安装后报 `QtCore DLL load failed`，先确认只保留一个 NetPulse 安装目录，并安装 Microsoft Visual C++ 2015-2022 x64 Redistributable。

## 后续建议
- [ ] 发布 Release 时上传 `installer/NetPulse-Setup-1.2.2.exe`。
- [ ] 如有签名证书，给 Windows 安装包和 Agent exe 做代码签名。
- [ ] Hub 公网部署建议继续使用 Nginx/Caddy 证书，一体化自签名包仅用于个人测试。
- [ ] 在 Linux 目标机上再跑一次 `./start-all.sh`，确认系统 Python 版本差异。
- [ ] 长期可增加 GUI 自动化冒烟测试，覆盖启动、切页、深浅主题和服务器节点生成。
