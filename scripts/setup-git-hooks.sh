#!/usr/bin/env bash
set -euo pipefail
mkdir -p .git/hooks
cp -f scripts/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "pre-commit hook installed"
