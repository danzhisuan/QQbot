# 本地 Windows 上运行：建立到阿里云服务器的 SSH 隧道。
#
# 为什么需要它：
#   NapCat 跑在本地，NoneBot 跑在服务器。与其在服务器上开公网端口
#   （要改阿里云安全组、还要暴露鉴权端点），不如用 SSH 隧道把服务器的
#   回环端口映射到本地——服务器防火墙和安全组都不用动，公网看不到任何端口。
#
# 用法（Windows PowerShell 5.1 即可，不需要 pwsh）：
#   powershell -File deploy\tunnel.ps1
#   或直接双击 deploy\tunnel.bat（更省事，不依赖 PowerShell 版本）
# 然后保持这个窗口开着，NapCat 连接：
#   ws://127.0.0.1:18080/onebot/v11/ws
#
# 若 NapCat 跑在本地 Docker 里，把 NapCat 侧的地址改成：
#   ws://host.docker.internal:18080/onebot/v11/ws

param(
    [string]$SshHost = "myserver",
    [int]$Port = 18080
)

$ErrorActionPreference = "Stop"

Write-Host "隧道: 本地 127.0.0.1:$Port  ->  $SshHost 的 127.0.0.1:$Port"
Write-Host "NapCat 连接地址: ws://127.0.0.1:$Port/onebot/v11/ws"
Write-Host "按 Ctrl+C 断开。"
Write-Host ""

# -N            不执行远程命令，只做端口转发
# ServerAlive*  断线时自动终止，配合下面的自动重连循环
ssh -N `
    -L "${Port}:127.0.0.1:${Port}" `
    -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=3 `
    -o ExitOnForwardFailure=yes `
    $SshHost
