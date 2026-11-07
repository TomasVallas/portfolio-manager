#!/bin/sh
# Starts Portfolio Manager on Linux.
exec python3 "$(dirname "$0")/portfolio_manager.py" "$@"
