#!/bin/sh
# Run from the source tree. Stage the same payload for both package formats.
set -eu
root=${1:?usage: stage.sh STAGING_DIRECTORY}
app="$root/usr/lib/asus-power-controller"
install -d -m 755 "$app" "$app/asus_power" "$app/scripts" "$root/usr/bin"
find asus_power \( -type d \( -name __pycache__ -o -name '.*' \) -prune \) -o \
    \( -type f ! -name '.*' ! -name '*.pyc' ! -name '*.pyo' \
       -exec install -Dm644 '{}' "$app/{}" \; \)
install -m644 scripts/desktop_launcher.py "$app/scripts/"
install -Dm644 packaging/asus-power-controller.desktop "$root/usr/share/applications/asus-power-controller.desktop"
python3 scripts/localize_desktop.py packaging/asus-power-controller.desktop "$root/usr/share/applications/asus-power-controller.desktop"
cat > "$root/usr/bin/asus-power-controller" <<'WRAPPER'
#!/bin/sh
exec /usr/bin/python3 -I /usr/lib/asus-power-controller/scripts/desktop_launcher.py "$@"
WRAPPER
chmod 755 "$root/usr/bin/asus-power-controller"
