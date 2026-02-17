# ── Stage 1: Build frontend ────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + serve built frontend ────
FROM python:3.12-slim
WORKDIR /app

# Install backend dependencies
COPY pyproject.toml ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir -e "."

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Copy config files
COPY backend/.env.example ./.env.example

# Expose port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "job_monitor.main:app", "--host", "0.0.0.0", "--port", "8000"]
