#!/usr/bin/env bash
# Deploy one image on the server, verify it, and roll back to the previous image on failure.
#
# Usage (as root on the EC2 host; GitHub Actions runs it through Systems Manager):
#   deploy.sh <image-uri>
#
# Data lives on the separate data disk at $DATA_DIR and is mounted into every container at
# /app/data. Deploys and rollbacks only swap containers; they never touch that directory.
set -euo pipefail

IMAGE="${1:?usage: deploy.sh <image-uri>}"
NAME="${CONTAINER_NAME:-dockside}"
DATA_DIR="${DATA_DIR:-/srv/dockside/data}"
STATE_DIR="${STATE_DIR:-/srv/dockside/state}"
PORT="${PORT:-8787}"
CHECK_SECONDS="${CHECK_SECONDS:-300}"
BASE="http://127.0.0.1:${PORT}"
SNAPSHOTS="${DATA_DIR}/raw/prospective_2026.jsonl"

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

mkdir -p "$DATA_DIR/raw" "$STATE_DIR"
touch "$STATE_DIR/env"            # optional overrides, e.g. NETWORK_ARCHIVE=on (one per line)
chown 1000:1000 "$DATA_DIR" "$DATA_DIR/raw"
previous_image="$(cat "$STATE_DIR/current_image" 2>/dev/null || true)"

start_container() {
  docker run -d --name "$NAME" --restart unless-stopped \
    -p "127.0.0.1:${PORT}:8787" \
    -v "${DATA_DIR}:/app/data" \
    --env-file "$STATE_DIR/env" \
    --log-opt max-size=10m --log-opt max-file=3 \
    "$1" >/dev/null
}

# Passes when the new container is healthy, has written a fresh snapshot with model
# predictions after it started, and serves champion risks that are probabilities.
checks_pass() {
  local since="$1"
  python3 - "$BASE" "$SNAPSHOTS" "$since" <<'PY'
import json, sys, urllib.request, datetime as dt
base, snapshots, since = sys.argv[1], sys.argv[2], float(sys.argv[3])
def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.load(r)
try:
    health = get("/api/health")
except Exception as exc:
    print(f"  health: not reachable yet ({exc.__class__.__name__})"); sys.exit(1)
if not health.get("ok"):
    print("  health: no successful poll yet"); sys.exit(1)
last = dt.datetime.fromisoformat(health["last_success"]).timestamp()
if last < since:
    print("  health: last poll is older than this deploy"); sys.exit(1)
try:
    with open(snapshots, "rb") as f:
        f.seek(0, 2); f.seek(max(0, f.tell() - 200_000))
        frame = json.loads(f.read().splitlines()[-1])
except Exception as exc:
    print(f"  snapshot: unreadable ({exc})"); sys.exit(1)
if dt.datetime.fromisoformat(frame["collected_at"]).timestamp() < since:
    print("  snapshot: no new snapshot written yet"); sys.exit(1)
if not any(s.get("predictions") for s in frame.get("stations", [])):
    print("  snapshot: no model predictions logged"); sys.exit(1)
stations = get("/api/stations")["stations"]
risks = [s["experimental_risk"] for s in stations if s.get("experimental_risk")]
bad = [r for r in risks for v in r.values() if not (isinstance(v, (int, float)) and 0 <= v <= 1)]
if bad:
    print(f"  predictions: {len(bad)} values outside [0, 1]"); sys.exit(2)
if len(risks) < 3:
    print(f"  predictions: only {len(risks)} stations have a champion estimate"); sys.exit(1)
print(f"  ok: champion {health.get('champion')} ({health.get('model_version')}), {len(risks)}/{len(stations)} stations estimated")
PY
}

wait_for_checks() {
  local since="$1" deadline=$(( $(date +%s) + CHECK_SECONDS )) rc
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      log "container is not running"; return 1
    fi
    if checks_pass "$since"; then return 0; else rc=$?; fi
    [ "$rc" -eq 2 ] && return 1     # invalid predictions: fail immediately, don't wait
    sleep 10
  done
  log "checks did not pass within ${CHECK_SECONDS}s"
  return 1
}

log "deploying ${IMAGE}"
log "previous image: ${previous_image:-none}"

# One writer at a time: stop the running container before the new one starts collecting.
docker rm -f "${NAME}-previous" >/dev/null 2>&1 || true
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker stop -t 30 "$NAME" >/dev/null
  docker rename "$NAME" "${NAME}-previous"
fi

since="$(date +%s)"
start_container "$IMAGE"
if wait_for_checks "$since"; then
  echo "$IMAGE" > "$STATE_DIR/current_image"
  [ -n "$previous_image" ] && [ "$previous_image" != "$IMAGE" ] && echo "$previous_image" > "$STATE_DIR/previous_image"
  echo "$(date -u +%FT%TZ) deployed ${IMAGE}" >> "$STATE_DIR/history.log"
  docker rm -f "${NAME}-previous" >/dev/null 2>&1 || true
  docker image prune -f >/dev/null || true
  log "deploy succeeded"
  exit 0
fi

log "deploy FAILED; last container logs:"
docker logs --tail 60 "$NAME" 2>&1 | sed 's/^/    /' || true
docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "$(date -u +%FT%TZ) FAILED ${IMAGE}" >> "$STATE_DIR/history.log"

if docker ps -a --format '{{.Names}}' | grep -qx "${NAME}-previous"; then
  log "rolling back: restarting the previous container"
  docker rename "${NAME}-previous" "$NAME"
  docker start "$NAME" >/dev/null
elif [ -n "$previous_image" ]; then
  log "rolling back: starting previous image ${previous_image}"
  start_container "$previous_image"
else
  log "no previous version to roll back to (first deploy)"
  exit 1
fi
since="$(date +%s)"
if CHECK_SECONDS=180 wait_for_checks "$since"; then
  log "rollback healthy; collected data untouched"
else
  log "WARNING: rollback did not pass checks either; inspect with: docker logs ${NAME}"
fi
exit 1
