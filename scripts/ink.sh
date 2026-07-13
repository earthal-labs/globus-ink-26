#!/usr/bin/env bash
# Compiles, uploads, and monitors the ink firmware. Run on the Pi
set -euo pipefail

FQBN="arduino:renesas_uno:nanor4"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKETCH="$ROOT/ink"
BRINGUP="$ROOT/ink/bringup"
PORT="${PORT:-$(arduino-cli board list 2>/dev/null | awk '/tty/ {print $1; exit}')}"
PORT="${PORT:-/dev/ttyACM0}"

usage() {
    echo "Usage: ink.sh [compile|upload|monitor|execute|bringup]"
    echo "  bringup  — flash the freerunning motor-0 A/B sketch (no V protocol)"
    echo "Override the port with PORT=/dev/ttyACM1 ink.sh upload"
    exit 1
}

do_compile() {
    echo "[ink] compiling..."
    arduino-cli compile --fqbn "$FQBN" "$SKETCH"
}

do_upload() {
    echo "[ink] uploading to $PORT..."
    arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"
}

do_monitor() {
    echo "[ink] monitoring $PORT (Ctrl-C to exit)..."
    arduino-cli monitor -p "$PORT" --config baudrate=115200
}

do_bringup() {
    echo "[ink] compiling bringup (freerun motor 0)..."
    arduino-cli compile --fqbn "$FQBN" "$BRINGUP"
    echo "[ink] uploading bringup to $PORT..."
    arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$BRINGUP"
    do_monitor
}

case "${1:-}" in
    compile) do_compile ;;
    upload) do_upload ;;
    monitor) do_monitor ;;
    execute)
        do_compile
        do_upload
        do_monitor
        ;;
    bringup) do_bringup ;;
    *) usage ;;
esac
