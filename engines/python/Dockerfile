# Container image for the reference (Python) engine. Built from the repository root
# (`docker build -f engines/python/Dockerfile .`) so the vendored quickstart project and the base
# catalog are present. The quickstart workflow builds this image without pushing it (pre-GA
# guardrails); at general availability the base image is pinned by digest.
FROM python:3.12-slim

WORKDIR /agentce
RUN pip install --no-cache-dir uv

# The whole repository: the engine, the base catalog, and the vendored quickstart project.
COPY . .

# Resolve the engine's pinned dependencies into the image (offline afterwards).
RUN uv sync --frozen --project engines/python

# `docker run <image>` assesses the vendored quickstart project end to end.
ENTRYPOINT ["uv", "run", "--project", "engines/python", "agentce"]
CMD ["quickstart", "--out", "/tmp/report"]
