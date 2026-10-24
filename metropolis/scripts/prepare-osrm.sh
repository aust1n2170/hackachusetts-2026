#!/usr/bin/env bash
# Download the Massachusetts road network and build OSRM routing files (one time, ~10 min).
set -euo pipefail

IMAGE=osrm/osrm-backend:v5.27.1
DIR="$(cd "$(dirname "$0")/.." && pwd)/osrm/data"
mkdir -p "$DIR"
cd "$DIR"

if [ ! -f massachusetts-latest.osm.pbf ]; then
  echo "Downloading Massachusetts extract from Geofabrik..."
  curl -fL -o massachusetts-latest.osm.pbf https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf
fi

docker run --rm -t -v "$DIR:/data" "$IMAGE" osrm-extract -p /opt/car.lua /data/massachusetts-latest.osm.pbf
docker run --rm -t -v "$DIR:/data" "$IMAGE" osrm-partition /data/massachusetts-latest.osrm
docker run --rm -t -v "$DIR:/data" "$IMAGE" osrm-customize /data/massachusetts-latest.osrm

echo
echo "Done. Set OSRM_URL=http://osrm:5000 in .env, then run: docker compose --profile routing up"
