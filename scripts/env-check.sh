#!/usr/bin/env bash
set -euo pipefail
# [C7-ID: ENV-SEC-003] Валидация локального .env по схеме
# Context7 best practice: используем Python jsonschema вместо Node.js ajv-cli
python3 "$(dirname "$0")/env-check.py"
