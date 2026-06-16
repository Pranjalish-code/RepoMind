# ── Build stage ──────────────────────────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# Install deps
COPY package.json package-lock.json* ./
RUN npm install

# Build app
COPY . .
RUN npm run build

# ── Production stage ─────────────────────────────────────────────────────────
FROM node:20-slim

WORKDIR /app

# We use 'serve' to host the built static files
RUN npm install -g serve

COPY --from=builder /app/dist ./dist

EXPOSE 5173

# Run on port 5173 to match the expected localhost:5173 output requirement
CMD ["serve", "-s", "dist", "-l", "5173"]
