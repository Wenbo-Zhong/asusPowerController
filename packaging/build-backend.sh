#!/bin/sh
# Package Rust binaries already built in the supplied upstream checkout.
set -eu
source=$(realpath "${1:?usage: build-backend.sh UPSTREAM_CHECKOUT OUTPUT_DIRECTORY}")
mkdir -p "${2:?output directory required}"
output=$(realpath "$2")
upstream_version=$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1], "rb"))["workspace"]["package"]["version"])' "$source/Cargo.toml")
revision=$(git -C "$source" rev-parse HEAD)
build_number=${GITHUB_RUN_NUMBER:-0}
version="$upstream_version+apc.$build_number"
arch=$(dpkg --print-architecture)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
root="$work/root"
mkdir -p "$root/DEBIAN" "$work/debian"
for program in asusctl asusd asus-shutdown; do
    install -Dm755 "$source/target/release/$program" "$root/usr/bin/$program"
done
make -C "$source" DESTDIR="$root" install-data-asusd
# asusd.service is static in some upstream versions. Let dpkg own the
# activation links so they disappear when the backend package is removed.
wants="$root/usr/lib/systemd/system/multi-user.target.wants"
install -d "$wants"
for unit in asusd.service asus-shutdown.service; do
    test -f "$root/usr/lib/systemd/system/$unit"
    ln -s "../$unit" "$wants/$unit"
done
cat > "$root/DEBIAN/prerm" <<'SCRIPT'
#!/bin/sh
set -e
if [ "$1" = remove ] && [ -d /run/systemd/system ]; then
    systemctl stop asus-shutdown.service asusd.service
fi
SCRIPT
cat > "$root/DEBIAN/postrm" <<'SCRIPT'
#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi
SCRIPT
chmod 755 "$root/DEBIAN/prerm" "$root/DEBIAN/postrm"

install -Dm644 "$source/LICENSE" "$root/usr/share/doc/asusctl/copyright"
{
    printf 'Upstream: https://github.com/OpenGamingCollective/asusctl/tree/%s\n' "$revision"
    if [ -n "${GITHUB_RUN_ID:-}" ]; then
        printf 'Build run: %s/%s/actions/runs/%s\n' "$GITHUB_SERVER_URL" "$GITHUB_REPOSITORY" "$GITHUB_RUN_ID"
    fi
} > "$root/usr/share/doc/asusctl/source"
cat > "$work/debian/control" <<'CONTROL'
Source: asusctl
Section: utils
Priority: optional
Maintainer: ASUS Power Controller contributors <maintainers@asus-power-controller.invalid>

Package: asusctl
Architecture: any
Description: ASUS laptop hardware backend
CONTROL
deps=$(cd "$work" && dpkg-shlibdeps -O -e"$root/usr/bin/asusd" \
    -e"$root/usr/bin/asusctl" -e"$root/usr/bin/asus-shutdown")
cat > "$root/DEBIAN/control" <<CONTROL
Package: asusctl
Version: $version
Architecture: $arch
Section: utils
Priority: optional
Maintainer: ASUS Power Controller contributors <maintainers@asus-power-controller.invalid>
Depends: systemd, udev, dbus, ${deps#shlibs:Depends=}
Description: ASUS laptop hardware backend
 Provides the asusd service, asusctl command, and shutdown integration.
CONTROL
dpkg-deb --root-owner-group --build "$root" "$output/asusctl_${version}_${arch}.deb"
install -Dm644 "$source/Cargo.lock" "$output/Cargo.lock"
git -C "$source" archive --prefix=asusctl/ --format=tar.gz -o "$output/asusctl-source.tar.gz" "$revision"
