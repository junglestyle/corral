FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
EXPOSE 8700
CMD ["uv", "run", "--no-sync", "corral", "server", "--db", "/data/corral.sqlite", "--host", "0.0.0.0"]
