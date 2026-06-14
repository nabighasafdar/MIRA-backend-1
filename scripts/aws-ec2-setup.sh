#!/usr/bin/env bash
# Install Docker on Ubuntu EC2 and prepare MIRA backend for deployment.
# Run on EC2 as ubuntu user: bash scripts/aws-ec2-setup.sh
#
# After this script: create .env, docker build, docker run (see AWS_DEPLOYMENT.md)

set -euo pipefail

REPO_URL="${MIRA_REPO_URL:-https://github.com/nabighasafdar/MIRA-backend-1.git}"
REPO_BRANCH="${MIRA_REPO_BRANCH:-deployment-backend}"
INSTALL_DIR="${MIRA_INSTALL_DIR:-$HOME/MIRA-backend-1}"

echo "==> MIRA AWS EC2 setup"
echo "    Install dir: ${INSTALL_DIR}"
echo "    Branch: ${REPO_BRANCH}"

if ! command -v docker &>/dev/null; then
  echo "==> Installing Docker..."
  sudo apt-get update -qq
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update -qq
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  echo "==> Docker installed. You may need to log out and back in for group membership."
else
  echo "==> Docker already installed: $(docker --version)"
fi

if [[ -f "${INSTALL_DIR}/Dockerfile" ]]; then
  echo "==> Repo already present at ${INSTALL_DIR} (Dockerfile found)"
elif [[ -d "${INSTALL_DIR}" ]] && [[ ! -f "${INSTALL_DIR}/Dockerfile" ]]; then
  echo "==> ${INSTALL_DIR} exists but has no Dockerfile — removing and re-cloning"
  rm -rf "${INSTALL_DIR}"
  git clone -b "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
elif command -v git &>/dev/null; then
  echo "==> Cloning repo..."
  if ! git clone -b "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"; then
    echo ""
    echo "ERROR: git clone failed (private repo needs a GitHub token)."
    echo "Either:"
    echo "  1. Set MIRA_REPO_URL with token:"
    echo "     MIRA_REPO_URL=https://USER:ghp_TOKEN@github.com/nabighasafdar/MIRA-backend-1.git bash scripts/aws-ec2-setup.sh"
    echo "  2. Copy code from your Mac with scp (see AWS_DEPLOYMENT.md)"
    exit 1
  fi
else
  sudo apt-get install -y git
  git clone -b "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}" || {
    echo "ERROR: git clone failed. See AWS_DEPLOYMENT.md for scp alternative."
    exit 1
  }
fi

cd "${INSTALL_DIR}"

if [[ ! -f .env ]]; then
  if [[ -f .env.production.example ]]; then
    cp .env.production.example .env
    echo "==> Created .env from .env.production.example — edit before docker run"
  else
    echo "==> No .env found. Create one from .env.production.example or .env.example"
  fi
fi

echo ""
echo "==> Setup complete. Next steps:"
echo ""
echo "  cd ${INSTALL_DIR}"
echo "  nano .env                    # fill in production secrets"
echo "  docker build -t mira-agent ."
echo "  docker run -d --name mira-agent --restart unless-stopped \\"
echo "    -p 8000:8000 --env-file .env mira-agent"
echo "  curl http://localhost:8000/health"
echo ""
echo "See AWS_DEPLOYMENT.md for Vercel env vars and full checklist."
