# Stage 1: build the dashboard
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the Python app with the built dashboard next to it
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 EVALKIT_DB=/data/evalkit.db EVALKIT_WEB_DIR=/app/web_dist
COPY pyproject.toml README.md ./
COPY evalkit/ evalkit/
COPY examples/ examples/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist /app/web_dist
VOLUME /data
EXPOSE 8800
CMD ["evalkit", "ui", "--host", "0.0.0.0", "--no-open"]
