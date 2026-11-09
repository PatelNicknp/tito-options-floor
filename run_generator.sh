#!/bin/sh
# Build docs/data/latest.json from free public data. Research only; never trades.
cd "$(dirname "$0")" && PYTHONPATH=generator python3 -m tito.build "$@"
