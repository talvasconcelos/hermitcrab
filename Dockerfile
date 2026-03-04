FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Copy source and install package
COPY pyproject.toml README.md LICENSE ./
COPY hermitcrab/ hermitcrab/
RUN uv pip install --system --no-cache .

# Create config directory
RUN mkdir -p /root/.hermitcrab

# Gateway default port
EXPOSE 18790

ENTRYPOINT ["hermitcrab"]
CMD ["status"]
