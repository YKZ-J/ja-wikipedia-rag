#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/log-db-volume.sh [container_name] [ledger_path]
# Default container_name: supabase_db_mcp-sever
# Default ledger_path: docs/operational/operations_ledger.md

CONTAINER=${1:-supabase_db_mcp-sever}
LEDGER=${2:-./docs/operational/operations_ledger.md}

mkdir -p "$(dirname "$LEDGER")"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Try to get Mounts as JSON; fallback to full inspect
MOUNTS_JSON=$(docker inspect --format '{{json .Mounts}}' "$CONTAINER" 2>/dev/null || docker inspect "$CONTAINER" | sed -n '1,200p')

cat >> "$LEDGER" <<EOF
### $TIMESTAMP - Container: $CONTAINER

Mounts:










































































































































































EOF

# Append the JSON for clarity
printf "%s\n" "
" >> "$LEDGER"
printf "%s\n" "
" >> "$LEDGER"
printf "%s\n" "
" >> "$LEDGER"

# Append mounts JSON
echo '```json' >> "$LEDGER"
echo "$MOUNTS_JSON" >> "$LEDGER"
echo '```' >> "$LEDGER"

echo "Logged mounts for $CONTAINER at $TIMESTAMP to $LEDGER"
