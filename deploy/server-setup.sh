#!/usr/bin/env bash
# 在服务器上一次性装好 NoneBot 的运行环境与 systemd 服务。
#
# 用法（在服务器上，需要 sudo）：
#     sudo bash ~/bot/deploy/server-setup.sh
#
# 幂等：可以重复执行，用于升级依赖或重装服务。
# 日常启停请用 systemctl，不要再用这个脚本。

set -euo pipefail

BOT_USER="${BOT_USER:-botuser}"
BOT_HOME="/home/${BOT_USER}/bot"
VENV="${BOT_HOME}/.venv"
UNIT_NAME="qq-bot.service"
PIP_INDEX="${PIP_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"

if [[ $EUID -ne 0 ]]; then
  echo "请用 sudo 运行：sudo bash $0" >&2
  exit 1
fi

echo "==> [1/5] 检查 python3-venv"
if ! dpkg -s python3-venv >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
else
  echo "    已安装，跳过"
fi

echo "==> [2/5] 创建虚拟环境 ${VENV}"
if [[ ! -x "${VENV}/bin/python" ]]; then
  sudo -u "${BOT_USER}" python3 -m venv "${VENV}"
else
  echo "    已存在，跳过"
fi

echo "==> [3/5] 安装依赖（${PIP_INDEX}）"
sudo -u "${BOT_USER}" "${VENV}/bin/pip" install --quiet --upgrade pip -i "${PIP_INDEX}"
sudo -u "${BOT_USER}" "${VENV}/bin/pip" install --quiet -i "${PIP_INDEX}" -r "${BOT_HOME}/requirements.txt"

echo "==> [4/5] 安装 systemd 服务 ${UNIT_NAME}"
install -m 644 "${BOT_HOME}/deploy/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
systemctl daemon-reload
systemctl enable "${UNIT_NAME}" >/dev/null

echo "==> [5/5] 启动服务"
systemctl restart "${UNIT_NAME}"
sleep 3

if systemctl is-active --quiet "${UNIT_NAME}"; then
  echo "    ✅ ${UNIT_NAME} 正在运行"
else
  echo "    ❌ ${UNIT_NAME} 启动失败，日志如下：" >&2
  journalctl -u "${UNIT_NAME}" -n 30 --no-pager >&2
  exit 1
fi

# 清理旧的 --target 依赖目录（如果之前用过）
if [[ -d "${BOT_HOME}/.pylibs" ]]; then
  echo "==> 清理遗留的 .pylibs 目录"
  rm -rf "${BOT_HOME}/.pylibs"
fi

cat <<EOF

完成。

  查看状态: systemctl status ${UNIT_NAME}
  查看日志: journalctl -u ${UNIT_NAME} -f
  重启服务: sudo systemctl restart ${UNIT_NAME}
  停止服务: sudo systemctl stop ${UNIT_NAME}

提醒：本服务监听 127.0.0.1:18080，不对外暴露。
      本地 NapCat 需通过 SSH 隧道连接，见 README「方案 B」。
EOF
