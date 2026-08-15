#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 || $# -gt 5 ]]; then
  echo "usage: ssm_deploy.sh <region> <registry> <repository> <tag> [bedrock_model]" >&2
  exit 2
fi

aws_region="$1"
registry="$2"
repository="$3"
image_tag="$4"
bedrock_model="${5:-}"

safe_component='^[A-Za-z0-9._:/@-]+$'
for value in "${aws_region}" "${registry}" "${repository}" "${image_tag}"; do
  if [[ ! "${value}" =~ ${safe_component} ]]; then
    echo "unsafe deployment argument: ${value}" >&2
    exit 2
  fi
done
if [[ -n "${bedrock_model}" && ! "${bedrock_model}" =~ ${safe_component} ]]; then
  echo "unsafe Bedrock model id" >&2
  exit 2
fi

container_name="ai-rec-diagnostics"
data_volume="ai-rec-data"
image_uri="${registry}/${repository}:${image_tag}"
previous_image=""
if [[ "$(docker inspect --format '{{.State.Running}}' "${container_name}" 2>/dev/null || true)" == "true" ]]; then
  previous_image="$(docker inspect --format '{{.Config.Image}}' "${container_name}")"
fi
legacy_service_was_active=false
deployment_started=false
health_file="$(mktemp)"

aws ecr get-login-password --region "${aws_region}" |
  docker login --username AWS --password-stdin "${registry}"
docker pull "${image_uri}"
docker volume create "${data_volume}" >/dev/null

run_container() {
  local target_image="$1"
  local args=(
    run --detach
    --name "${container_name}"
    --restart unless-stopped
    --publish 8000:8000
    --env "AWS_DEFAULT_REGION=${aws_region}"
    --env "MODE=auto"
    --env "PORT=8000"
    --volume "${data_volume}:/app/backend/data"
  )
  if [[ -n "${bedrock_model}" ]]; then
    args+=(--env "BEDROCK_MODEL=${bedrock_model}")
  fi
  docker "${args[@]}" "${target_image}"
}

rollback() {
  echo "deployment failed; restoring the previous runtime" >&2
  docker rm --force "${container_name}" >/dev/null 2>&1 || true
  if [[ -n "${previous_image}" ]]; then
    echo "rolling back to ${previous_image}" >&2
    if run_container "${previous_image}" >/dev/null; then
      return
    fi
    echo "container rollback failed" >&2
  fi
  if [[ "${legacy_service_was_active}" == "true" ]]; then
    echo "restarting legacy backend.service" >&2
    systemctl start backend.service || true
  fi
}

cleanup() {
  local rc=$?
  trap - EXIT
  rm -f "${health_file}"
  if [[ ${rc} -ne 0 && "${deployment_started}" == "true" ]]; then
    rollback
  fi
  exit "${rc}"
}
trap cleanup EXIT

if systemctl is-active --quiet backend.service; then
  legacy_service_was_active=true
  systemctl stop backend.service
fi
deployment_started=true

# Migrate the database and model cache from the pre-Docker deployment. The
# marker is written only after a healthy container starts, so a failed first
# migration will copy a fresh snapshot again on the next attempt.
volume_mount="$(docker volume inspect --format '{{.Mountpoint}}' "${data_volume}")"
migration_pending=false
if [[ -d /opt/app/backend/data && ! -e "${volume_mount}/.cicd-migrated" ]]; then
  echo "migrating legacy backend data into ${data_volume}"
  cp -a /opt/app/backend/data/. "${volume_mount}/"
  migration_pending=true
fi

# A volume created by an older, root-running image may not be writable by the
# current non-root application user. Normalize it before starting the service.
docker run --rm --user 0 \
  --volume "${data_volume}:/data" \
  --entrypoint sh "${image_uri}" \
  -c 'chown -R app:app /data'

if docker container inspect "${container_name}" >/dev/null 2>&1; then
  docker rm --force "${container_name}" >/dev/null
fi
run_container "${image_uri}" >/dev/null

healthy=false
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 20 \
    http://127.0.0.1:8000/health >"${health_file}"; then
    healthy=true
    break
  fi
  sleep 4
done

if [[ "${healthy}" != "true" ]]; then
  echo "new container failed its health check" >&2
  docker logs --tail 200 "${container_name}" >&2 || true
  exit 1
fi

if ! docker exec "${container_name}" python -c '
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/impact-demo", timeout=20) as response:
    case = json.load(response)
assert case["before"]["version"] == 1
assert case["after"]["version"] == 2
assert case["before"]["product_id"] == case["after"]["product_id"]
assert len(case["competitor_refs"]) >= 2
'; then
  echo "new container did not expose a valid seeded P4 impact demo" >&2
  docker logs --tail 200 "${container_name}" >&2 || true
  exit 1
fi

if [[ "${legacy_service_was_active}" == "true" ]]; then
  systemctl disable backend.service >/dev/null 2>&1 || true
fi
if [[ "${migration_pending}" == "true" ]]; then
  touch "${volume_mount}/.cicd-migrated"
fi
deployment_started=false
echo "deployed ${image_uri}"
cat "${health_file}"
