#!/bin/bash
# Quick start script for local demo

echo "Starting VideoRAG..."

# Start backend
cd backend
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Created .env — please add your OPENAI_API_KEY before continuing"
  exit 1
fi
uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!
echo "✅ Backend running on http://localhost:8000 (PID $BACKEND_PID)"

# Start frontend
cd ../frontend
npm start &
FRONTEND_PID=$!
echo "✅ Frontend running on http://localhost:3000 (PID $FRONTEND_PID)"

echo ""
echo "Press Ctrl+C to stop both servers"
trap "kill $BACKEND_PID $FRONTEND_PID" SIGINT
wait
