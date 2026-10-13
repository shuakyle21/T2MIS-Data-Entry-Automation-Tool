#!/bin/zsh
# Launch the T2MIS Encoder desktop app.
cd "$(dirname "$0")"
exec .venv/bin/python -m app.main
