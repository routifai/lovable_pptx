#!/bin/bash
set -e

if [ ! -f .env ]; then
    echo "Error: .env not found. Run ./install.sh first."
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Error: venv not found. Run ./install.sh first."
    exit 1
fi

source venv/bin/activate

echo "Starting backend (port 8000)..."
uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!

echo "Starting frontend (port 3000)..."
cd frontend && npm run dev &
FRONTEND_PID=$!

echo ""
echo "App:  http://localhost:3000"
echo "API:  http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
