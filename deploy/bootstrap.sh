#!/usr/bin/env bash
# AgentLit 一键部署（Ubuntu 22.04/24.04，root 运行）
#
# 用法：
#   sudo bash deploy/bootstrap.sh                      # 自动用公网 IP 生成 sslip.io 域名
#   sudo bash deploy/bootstrap.sh demo.example.com     # 用自有域名（需先解析到本机）
#   sudo AUTH_USER=judge AUTH_PASS='评审口令' bash deploy/bootstrap.sh demo.example.com
#
# 幂等：重复执行会重新拉代码、重装依赖并重启服务，数据目录保留。
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/cui1001/ai-literature-platform.git}"
APP_DIR="${APP_DIR:-/opt/agentlit}"
DATA_DIR="${DATA_DIR:-/var/lib/agentlit}"
SERVICE_USER="${SERVICE_USER:-agentlit}"
DOMAIN="${1:-}"
AUTH_USER="${AUTH_USER:-}"
AUTH_PASS="${AUTH_PASS:-}"

log() { printf '\033[1;32m[bootstrap]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[bootstrap]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "请用 root 运行：sudo bash deploy/bootstrap.sh [域名]"
command -v apt-get >/dev/null || die "仅支持 Ubuntu/Debian"

log "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip curl ca-certificates caddy >/dev/null

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  log "创建服务用户 ${SERVICE_USER}"
  useradd -r -s /bin/bash -d "${APP_DIR}" "${SERVICE_USER}"
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  log "克隆仓库到 ${APP_DIR}"
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone --quiet "${REPO_URL}" "${APP_DIR}"
else
  log "更新仓库"
  sudo -u "${SERVICE_USER}" git -C "${APP_DIR}" pull --quiet --ff-only || true
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"

log "安装 Python 依赖"
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  sudo -u "${SERVICE_USER}" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "${SERVICE_USER}" "${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/backend/requirements.txt"

mkdir -p "${DATA_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"

if [[ -z "${DOMAIN}" ]]; then
  IP="$(curl -fsS --max-time 5 https://api.ipify.org || true)"
  [[ -n "${IP}" ]] || die "取不到公网 IP，请手动传入域名：sudo bash deploy/bootstrap.sh demo.example.com"
  DOMAIN="${IP//./-}.sslip.io"
fi

log "写入 systemd 服务（数据目录 ${DATA_DIR}）"
cat > /etc/systemd/system/agentlit.service <<EOF
[Unit]
Description=AgentLit 科研助手 Agent 平台
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${APP_DIR}
Environment=DATA_DIR=${DATA_DIR}
Environment=LLM_MODE=offline
Environment=EMBED_MODE=hashing
Environment=ARXIV_MODE=offline
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/scripts/run_server.py --demo --host 127.0.0.1 --port 8000 --data-dir ${DATA_DIR}
Restart=always
RestartSec=3
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable agentlit >/dev/null 2>&1 || true
systemctl restart agentlit

AUTH_BLOCK=""
if [[ -n "${AUTH_USER}" && -n "${AUTH_PASS}" ]]; then
  log "为评审生成 basic_auth 口令"
  HASH="$(caddy hash-password --plaintext "${AUTH_PASS}")"
  AUTH_BLOCK="	basic_auth {
		${AUTH_USER} ${HASH}
	}
"
fi

log "写入 Caddy 配置（https://${DOMAIN}）"
cat > /etc/caddy/Caddyfile <<EOF
${DOMAIN} {
	encode gzip zstd

${AUTH_BLOCK}
	reverse_proxy 127.0.0.1:8000 {
		header_up X-Forwarded-For {remote_host}
		transport http {
			read_timeout 300s
			write_timeout 300s
		}
	}

	log {
		output file /var/log/caddy/agentlit.log
		format console
	}
}
EOF

systemctl reload caddy 2>/dev/null || systemctl restart caddy

log "等待服务就绪（首次会解析 15 个 PDF，约 60–90 秒）"
ok=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [[ "${ok}" -ne 1 ]]; then
  journalctl -u agentlit -n 60 --no-pager || true
  die "服务未就绪，请查看上面的日志"
fi
curl -fsS http://127.0.0.1:8000/api/health; echo

log "部署完成：https://${DOMAIN}"
[[ -n "${AUTH_USER}" ]] && log "评审账号：${AUTH_USER}（口令见你本次输入）"
log "把该地址与账号填入 README「在线演示」与 docs/SUBMISSION_MATERIALS.md 2.4 表"
