#!/bin/sh
# Chessfish server: install or update in one command (on the Docker VM).
#
#   curl -fsSL https://raw.githubusercontent.com/NAKE-1/chessfish/main/deploy/install.sh | sh -s -- <pairing-key>
#
# Leave out <pairing-key> to keep the key already in ~/chessfish/.env, or to generate a new one on first install.
# Re-running it pulls the latest code from GitHub and rebuilds. Settings: CHESSFISH_DIR (default ~/chessfish),
# CHESSFISH_BRANCH (default main).
set -eu

REPO="NAKE-1/chessfish"
BRANCH="${CHESSFISH_BRANCH:-main}"
DIR="${CHESSFISH_DIR:-$HOME/chessfish}"
KEY="${1:-}"

command -v docker >/dev/null 2>&1 || { echo "docker is not installed" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "the docker compose plugin is not installed" >&2; exit 1; }

mkdir -p "$DIR"
cd "$DIR"
curl -fsSL "https://raw.githubusercontent.com/$REPO/$BRANCH/deploy/docker-compose.yml" -o docker-compose.yml

generated=""
if [ -n "$KEY" ]; then
    # 32 bytes as urlsafe base64 without padding = 43 chars
    echo "$KEY" | grep -Eq '^[A-Za-z0-9_-]{43}$' || { echo "that doesn't look like a Chessfish pairing key" >&2; exit 1; }
elif [ -f .env ] && grep -q '^CHESSFISH_PSK=' .env; then
    KEY=$(sed -n 's/^CHESSFISH_PSK=//p' .env)
else
    KEY=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')
    generated=1
fi
umask 077
printf 'CHESSFISH_PSK=%s\nCHESSFISH_GIT=https://github.com/%s.git#%s\n' "$KEY" "$REPO" "$BRANCH" > .env

docker compose up -d --build

printf 'Waiting for the server'
i=0
until curl -fs http://127.0.0.1:6767/healthz >/dev/null 2>&1; do
    i=$((i + 1)); [ $i -gt 30 ] && { echo; echo "not healthy yet, check: docker compose -f $DIR/docker-compose.yml logs" >&2; exit 1; }
    printf '.'; sleep 1
done
echo " up."
echo "Friend website : http://<this-vm>:6767"
echo "Host link      : <this-vm>:6768"
if [ -n "$generated" ]; then
    echo
    echo "New pairing key (paste it into the Chessfish setup wizard on the gaming PC):"
    echo "  $KEY"
fi
