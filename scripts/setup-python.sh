#!/usr/bin/env bash
# KB Python 環境セットアップスクリプト

set -euo pipefail

echo "=== KB Python Environment Setup ==="
echo ""

# Python 確認
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found"
    echo "Install Python 3: brew install python3"
    exit 1
fi

echo "✓ Python 3: $(python3 --version)"

# pip 確認
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 not found"
    exit 1
fi

echo "✓ pip3: $(pip3 --version)"

echo ""
echo "Installing llama-cpp-python..."
echo "(Attempting system pip install; will fallback to a virtualenv if the environment is externally managed)"
echo ""

# 推奨: Metal を有効にしてビルド
export CMAKE_ARGS="-DGGML_METAL=on"

if pip3 install -U -r requirements.txt; then
    echo "\n✓ llama-cpp-python installed system-wide"
else
    echo "\n⚠️ System pip install failed — creating a virtualenv and installing inside it"

    # 仮想環境作成
    VENV_DIR=".venv"
    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        echo "Created virtualenv at $VENV_DIR"
    fi

    # Activate and install
    # shellcheck source=/dev/null
    . "$VENV_DIR/bin/activate"

    python -m pip install --upgrade pip setuptools wheel

    echo "Installing llama-cpp-python into virtualenv (this may take a while)..."
    if CMAKE_ARGS="$CMAKE_ARGS" pip install -U -r requirements.txt; then
        echo "\n✓ llama-cpp-python installed in virtualenv"
        echo "To activate the virtualenv for development, run:"
        echo "  source $VENV_DIR/bin/activate"
    else
        echo "\n✗ Failed to install llama-cpp-python in virtualenv"
        echo "Check the output above for build errors (C/C++ toolchain, Xcode command line tools, etc.)"
        exit 1
    fi
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. (If you used the virtualenv) activate it: source .venv/bin/activate"
echo "2. Start MCP Server: bun run src/mcp/server.ts"
echo "3. Use KB CLI: kb \"your prompt\""
