#!/bin/bash
set -e

echo "Installing LovablePPTX dependencies..."

# Check for .env file
if [ ! -f .env ]; then
    echo "Creating .env template..."
    echo "ANTHROPIC_API_KEY=" > .env
    echo "Edit .env and add your ANTHROPIC_API_KEY before running the app."
fi

# Python venv
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate

echo "Installing Python dependencies..."
pip install -r requirements.txt

# Root node deps (pptxgenjs used by agent)
if [ ! -d "node_modules" ]; then
    echo "Installing root Node.js dependencies (pptxgenjs)..."
    npm install
fi

# Frontend deps
if [ ! -d "frontend/node_modules" ]; then
    echo "Installing frontend dependencies..."
    cd frontend && npm install && cd ..
fi

echo ""
echo "Done. Run ./start.sh to start the app."
