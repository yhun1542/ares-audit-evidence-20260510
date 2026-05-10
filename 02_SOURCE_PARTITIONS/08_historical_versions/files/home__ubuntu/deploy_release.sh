#!/usr/bin/env bash
set -e

VERSION="$1"
if [ -z "$VERSION" ]; then
  echo "Usage: ./deploy_release.sh <version>"
  exit 1
fi

BASE="/home/ubuntu"
RELEASE_DIR="$BASE/ares_releases/$VERSION"

if [ ! -d "$RELEASE_DIR" ]; then
  echo "Release $VERSION not found"
  exit 1
fi

echo "Switching to release $VERSION"
rm -f "$BASE/aub-trading-system"
ln -s "$RELEASE_DIR" "$BASE/aub-trading-system"

pm2 restart all
pm2 save

echo "Deployment complete."
