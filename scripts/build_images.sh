#!/usr/bin/env bash
set -euo pipefail

# Build all arbitrage service images locally.
# Optionally provide REGISTRY (e.g. 123456789012.dkr.ecr.us-west-1.amazonaws.com)
# and TAG (default: latest):
#
#   REGISTRY=123456789012.dkr.ecr.us-west-1.amazonaws.com TAG=main ./scripts/build_images.sh
#

services=(ingest matcher signals execution api ui)
registry="${REGISTRY:-}"
tag="${TAG:-latest}"

for service in "${services[@]}"; do
  image_name="arbitrage-${service}:${tag}"
  if [[ -n "${registry}" ]]; then
    image_name="${registry}/arbitrage-${service}:${tag}"
  fi

  echo "Building image: ${image_name}"
  docker build \
    --build-arg SERVICE_NAME="${service}" \
    -t "${image_name}" \
    -f Dockerfile \
    .
done

echo "Build complete. Images tagged with suffix ':${tag}'."
