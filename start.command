#!/bin/bash
cd -- "$(dirname -- "$0")"
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
./run.sh
