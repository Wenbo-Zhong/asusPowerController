#!/bin/sh
set -eu
backend=${3:-}
if [ -n "$backend" ]; then
    backend=$(realpath "$backend")
fi
cd "$(dirname "$0")/.."
format=${1:?usage: packaging/build.sh deb|rpm [OUTPUT_DIRECTORY] [ASUSCTL_DEB]}
mkdir -p "${2:-dist}"
output=$(realpath "${2:-dist}")
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
name=asus-power-controller
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
case "$format" in
    deb)
        root="$work/$name"
        sh packaging/stage.sh "$root"
        install -Dm644 LICENSE "$root/usr/share/doc/$name/copyright"
        architecture=all
        revision=1
        if [ -n "$backend" ]; then
            [ "$(dpkg-deb -f "$backend" Package)" = asusctl ]
            revision="1+backend.${BACKEND_RUN_ID:-0}"
            architecture=$(dpkg-deb -f "$backend" Architecture)
            [ "$architecture" = "$(dpkg --print-architecture)" ]
            install -Dm644 "$backend" "$root/usr/lib/$name/backend/asusctl.deb"
            install -Dm755 packaging/install-backend "$root/usr/lib/$name/install-backend"
        fi
        mkdir -p "$root/DEBIAN"
        cat > "$root/DEBIAN/postinst" <<'SCRIPT'
#!/bin/sh
set -eu
if [ "$1" = configure ]; then
    # dpkg preserves permissions on directories that already exist.
    chmod 755 /usr/lib/asus-power-controller
fi
SCRIPT
        chmod 755 "$root/DEBIAN/postinst"
        cat > "$root/DEBIAN/control" <<CONTROL
Package: $name
Version: $version-$revision
Section: utils
Priority: optional
Architecture: $architecture
Maintainer: ASUS Power Controller contributors <maintainers@asus-power-controller.invalid>
Depends: python3 (>= 3.11), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.4), libayatana-appindicator3-1, pkexec, systemd
Suggests: asusctl
Description: Desktop power and cooling controls for ASUS laptops
 GTK controls for ASUS laptop power profiles, fans, battery and lighting.
 Hardware operations require asusctl and a working asusd service.
CONTROL
        desktop-file-validate "$root/usr/share/applications/$name.desktop"
        dpkg-deb --root-owner-group --build "$root" "$output/${name}_${version}-${revision}_${architecture}.deb"
        ;;
    rpm)
        source="$work/$name-$version"
        mkdir -p "$source" "$work/rpm/SOURCES"
        cp -R asus_power scripts packaging LICENSE "$source/"
        find "$source" -type d -name __pycache__ -prune -exec rm -rf {} +
        tar -C "$work" -czf "$work/rpm/SOURCES/$name-$version.tar.gz" "$name-$version"
        rpmbuild -bb --define "_topdir $work/rpm" --define "app_version $version" packaging/asus-power-controller.spec
        cp "$work"/rpm/RPMS/noarch/*.rpm "$output/"
        ;;
    *)
        printf 'Unknown package format: %s\n' "$format" >&2
        exit 2
        ;;
esac
