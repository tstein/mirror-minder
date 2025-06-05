#!/bin/bash

dir=$(dirname "$0")
cd "$dir" || exit 255
uv run mirror-minder.py "$@"
