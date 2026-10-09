#!/usr/bin/env bash
# 服务器准备：为在容器里运行 NapCat 腾出内存
#
#   sudo bash deploy/server-prep.sh
#
# 做三件事：
#   1. 关闭对云主机完全无用的服务（约省 73MB 常驻内存）
#   2. 添加 4GB Swap（内存打满时的安全网，避免 OOM 杀掉 MySQL）
#   3. 安装 Docker
#
# 幂等：可重复执行。

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "请用 root 运行：sudo bash $0" >&2
  exit 1
fi

echo "==> [1/4] 关闭云主机上无用的服务"
# multipathd   无 SAN 存储     ~27MB
# tuned        性能调优守护     ~27MB
# ModemManager 无 4G 模块      ~9MB
# udisks2      桌面磁盘管理     ~10MB
for svc in multipathd tuned ModemManager udisks2; do
  if systemctl is-enabled "$svc" >/dev/null 2>&1; then
    if systemctl disable --now "$svc" >/dev/null 2>&1; then
      echo "    ✅ 已关闭 $svc"
    else
      echo "    ⚠️  $svc 关闭失败，跳过"
    fi
  else
    echo "    －  $svc 未启用，跳过"
  fi
done

echo
echo "==> [2/4] 添加 4GB Swap"
if swapon --show 2>/dev/null | grep -q .; then
  echo "    已有 Swap，跳过："
  swapon --show
else
  if [[ ! -f /swapfile ]]; then
    fallocate -l 4G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    ✅ 已启用 4GB Swap"
fi
# 让内核更倾向使用 Swap 而不是直接 OOM 杀进程
sysctl -qw vm.swappiness=20
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=20' >> /etc/sysctl.conf

echo
echo "==> [3/4] 安装 Docker"
if command -v docker >/dev/null 2>&1; then
  echo "    Docker 已安装：$(docker --version)"
else
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
  systemctl enable --now docker >/dev/null 2>&1
  echo "    ✅ 已安装：$(docker --version)"
fi
docker compose version 2>/dev/null || echo "    ⚠️  docker compose 插件不可用"
usermod -aG docker botuser 2>/dev/null && echo "    ✅ botuser 已加入 docker 组"

echo
echo "==> [4/4] 当前资源"
free -h
echo
swapon --show
echo
echo "完成。"
