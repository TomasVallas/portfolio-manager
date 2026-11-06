#!/bin/sh
# Käynnistää Salkkukartan Linuxissa.
exec python3 "$(dirname "$0")/salkkukartta.py" "$@"
