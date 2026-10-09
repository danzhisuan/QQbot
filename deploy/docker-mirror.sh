#!/usr/bin/env bash
# 为 Docker 配置国内镜像加速（服务器在阿里云，直连 Docker Hub 会超时）
#
#   su -c "bash deploy/docker-mirror.sh" root
#
# 同时设置容器日志上限，避免把 40G 磁盘写满。

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "需要 root 权限" >&2
  exit 1
fi

mkdir -p /etc/docker

cat > /etc/docker/daemon.json <<'JSON'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.1panel.live"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
JSON

echo "==> 重启 Docker 使配置生效"
systemctl restart docker
sleep 3

echo "==> docker 状态: $(systemctl is-active docker)"
echo "==> 当前镜像源:"
docker info 2>/dev/null | sed -n '/Registry Mirrors/,/^$/p'
