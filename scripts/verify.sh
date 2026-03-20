#!/usr/bin/env bash
set -e

# Environment variables (絶対パス推奨)
export VAULT_PATH=${VAULT_PATH:-}
export LLAMA_CLI_PATH=${LLAMA_CLI_PATH:-/opt/homebrew/bin/llama-cli}
export MODEL_PATH=${MODEL_PATH:-}

echo "=== KB CLI Verification ==="
echo

# Check llama-cli
echo "1. Checking llama-cli..."
if ! command -v llama-cli &> /dev/null; then
  echo "❌ llama-cli not found"
  exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "❌ Model file not found: $MODEL_PATH"
  exit 1
fi

echo "✓ llama-cli found: $LLAMA_CLI_PATH"
echo "✓ Model found: $MODEL_PATH"
echo "✓ Vault path: $VAULT_PATH"
echo

# Check ripgrep
echo "2. Checking ripgrep..."
if ! command -v rg &> /dev/null; then
  echo "❌ ripgrep not found"
  echo "Install with: brew install ripgrep"
  exit 1
fi
echo "✓ ripgrep found"
echo

# Check Bun
echo "3. Checking Bun..."
if ! command -v bun &> /dev/null; then
  echo "❌ Bun not found"
  exit 1
fi
echo "✓ Bun found: $(bun --version)"
echo

# Test LLM
echo "4. Testing LLM..."
OUTPUT=$($LLAMA_CLI_PATH -m "$MODEL_PATH" -p "ping" -n 10 --no-display-prompt 2>&1)
if [ $? -ne 0 ]; then
  echo "❌ LLM test failed"
  exit 1
fi
echo "✓ LLM responding"
echo

# Test MCP Server
echo "5. Testing MCP Server..."
mkdir -p "$VAULT_PATH"
echo '{"cmd":"create","args":["Health Check Test"]}' | bun run src/mcp/server.ts > /dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "❌ MCP Server test failed"
  exit 1
fi
echo "✓ MCP Server working"
echo

# Test CLI (if linked)
echo "6. Testing CLI..."
if command -v kb &> /dev/null; then
  kb create "Verification Test $(date +%s)" > /dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "⚠️  CLI test failed (but kb is installed)"
  else
    echo "✓ CLI working"
  fi
else
  echo "⚠️  CLI not linked (run: bun link)"
fi
echo

echo "=== All checks passed! ==="
echo
echo "Next steps:"
echo "  bun link          # Link the CLI globally"
echo "  kb create \"Test\"  # Create a document"
echo "  kb search \"test\"  # Search documents"
