#!/bin/sh
# =============================================================================
# postprovision.sh — post-deploy wiring (POSIX). Owner: Tank + Trinity.
# =============================================================================
# Thin wrapper: azd exports outputs as env vars; the Python worker does the work.
# -----------------------------------------------------------------------------
set -e
echo "Running postprovision..."
python "$(dirname "$0")/postprovision.py"
