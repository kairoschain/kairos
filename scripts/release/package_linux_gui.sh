#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
umask 022

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RELEASE_ROOT_DIR="$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)"

usage() {
  cat <<'EOF'
Usage: package_linux_gui.sh BIN_DIR OUTPUT_DIR VERSION PLATFORM

Build a Debian GUI-wallet package from one native release bin directory.
PLATFORM must be linux-x86_64 or linux-aarch64.
EOF
}

if (( $# != 4 )); then
  usage >&2
  exit 2
fi

BIN_DIR=$1
OUTPUT_DIR=$2
VERSION=$3
PLATFORM=$4
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || {
  echo "invalid semantic version: $VERSION" >&2
  exit 1
}
case "$PLATFORM" in
  linux-x86_64) DEBIAN_ARCHITECTURE=amd64 ;;
  linux-aarch64) DEBIAN_ARCHITECTURE=arm64 ;;
  *)
    echo "unsupported Linux GUI platform: $PLATFORM" >&2
    exit 1
    ;;
esac

for command in appstreamcli date dpkg-deb install mktemp sed; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "required command is missing: $command" >&2
    exit 1
  }
done

BIN_DIR="$(CDPATH='' cd -- "$BIN_DIR" && pwd -P)"
mkdir -p -- "$OUTPUT_DIR"
OUTPUT_DIR="$(CDPATH='' cd -- "$OUTPUT_DIR" && pwd -P)"
for binary in kairos-gui kairos; do
  [[ -f $BIN_DIR/$binary && -x $BIN_DIR/$binary ]] || {
    echo "release binary is missing or not executable: $BIN_DIR/$binary" >&2
    exit 1
  }
done

TEMPORARY=$(mktemp -d "${TMPDIR:-/tmp}/kairos-linux-gui.XXXXXX")
cleanup() {
  local status=$?
  if [[ -d $TEMPORARY && $TEMPORARY == "${TMPDIR:-/tmp}"/kairos-linux-gui.* ]]; then
    rm -r -- "$TEMPORARY" || true
  fi
  exit "$status"
}
trap cleanup EXIT

PACKAGE_ROOT="$TEMPORARY/kairos-gui_${VERSION}_${DEBIAN_ARCHITECTURE}"
install -d \
  "$PACKAGE_ROOT/DEBIAN" \
  "$PACKAGE_ROOT/usr/bin" \
  "$PACKAGE_ROOT/usr/lib/kairos" \
  "$PACKAGE_ROOT/usr/share/applications" \
  "$PACKAGE_ROOT/usr/share/doc/kairos-gui" \
  "$PACKAGE_ROOT/usr/share/metainfo"
for size in 16 32 48 64 128 256 512; do
  install -d "$PACKAGE_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
done

install -m 0755 "$BIN_DIR/kairos-gui" "$PACKAGE_ROOT/usr/lib/kairos/kairos-gui"
install -m 0755 "$BIN_DIR/kairos" "$PACKAGE_ROOT/usr/lib/kairos/kairos"
ln -s ../lib/kairos/kairos-gui "$PACKAGE_ROOT/usr/bin/kairos-gui"
install -m 0644 "$RELEASE_ROOT_DIR/LICENSE" \
  "$PACKAGE_ROOT/usr/share/doc/kairos-gui/LICENSE"
install -m 0644 "$RELEASE_ROOT_DIR/NOTICE" \
  "$PACKAGE_ROOT/usr/share/doc/kairos-gui/NOTICE"
install -m 0644 "$SCRIPT_DIR/README.txt" \
  "$PACKAGE_ROOT/usr/share/doc/kairos-gui/README.txt"
install -m 0644 "$RELEASE_ROOT_DIR/docs/reference/contracts.md" \
  "$PACKAGE_ROOT/usr/share/doc/kairos-gui/CONTRACTS.md"

install -m 0644 \
  "$SCRIPT_DIR/gui/linux/xyz.kairoschain.wallet.desktop" \
  "$PACKAGE_ROOT/usr/share/applications/xyz.kairoschain.wallet.desktop"
RELEASE_DATE=$(date -u +%F)
sed \
  -e "s/@VERSION@/$VERSION/g" \
  -e "s/@RELEASE_DATE@/$RELEASE_DATE/g" \
  "$SCRIPT_DIR/gui/linux/xyz.kairoschain.wallet.metainfo.xml.in" \
  > "$PACKAGE_ROOT/usr/share/metainfo/xyz.kairoschain.wallet.metainfo.xml"
chmod 0644 "$PACKAGE_ROOT/usr/share/metainfo/xyz.kairoschain.wallet.metainfo.xml"
appstreamcli validate --no-net \
  "$PACKAGE_ROOT/usr/share/metainfo/xyz.kairoschain.wallet.metainfo.xml"
for size in 16 32 48 64 128 256 512; do
  install -m 0644 \
    "$RELEASE_ROOT_DIR/noid_gui/assets/app-icons/Kairos-${size}.png" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/${size}x${size}/apps/xyz.kairoschain.wallet.png"
done

sed \
  -e "s/@VERSION@/$VERSION/g" \
  -e "s/@ARCHITECTURE@/$DEBIAN_ARCHITECTURE/g" \
  "$SCRIPT_DIR/gui/linux/control.in" \
  > "$PACKAGE_ROOT/DEBIAN/control"
chmod 0644 "$PACKAGE_ROOT/DEBIAN/control"

ARTIFACT="$OUTPUT_DIR/kairos-gui-v${VERSION}-${PLATFORM}.deb"
dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$ARTIFACT" >/dev/null
dpkg-deb --info "$ARTIFACT" >/dev/null
dpkg-deb --contents "$ARTIFACT" >/dev/null
EXTRACTED="$TEMPORARY/extracted"
dpkg-deb --extract "$ARTIFACT" "$EXTRACTED"
[[ -L $EXTRACTED/usr/bin/kairos-gui ]]
[[ -s $EXTRACTED/usr/share/doc/kairos-gui/LICENSE ]]
[[ -s $EXTRACTED/usr/share/doc/kairos-gui/NOTICE ]]
[[ -s $EXTRACTED/usr/share/doc/kairos-gui/README.txt ]]
[[ -s $EXTRACTED/usr/share/doc/kairos-gui/CONTRACTS.md ]]
[[ -s $EXTRACTED/usr/share/metainfo/xyz.kairoschain.wallet.metainfo.xml ]]
[[ ! -e $EXTRACTED/usr/lib/kairos/kairos-cli ]]
[[ ! -e $EXTRACTED/usr/lib/kairos/kairos-miner ]]
"$EXTRACTED/usr/lib/kairos/kairos-gui" --release-self-check >/dev/null
printf '%s\n' "$ARTIFACT"
