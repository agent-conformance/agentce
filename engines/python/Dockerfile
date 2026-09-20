# Container image for the reference (Python) engine. Built from the repository root
# (`docker build -f engines/python/Dockerfile .`); the root .dockerignore keeps private and dev paths
# out of the build context. Two stages: the builder turns the engine into a wheel and exports its
# hash-pinned dependency set from the lock; the runtime stage installs only those, so the image holds
# the installed package and its data, never the repository. The quickstart workflow builds this image
# and runs it with no network without pushing it (pre-GA guardrails); at general availability the base
# image is pinned by digest.
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv==0.12.14

WORKDIR /src
COPY engines/python/ ./

RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /dist/requirements.txt

FROM python:3.12-slim

COPY --from=builder /dist/requirements.txt /tmp/requirements.txt
COPY --from=builder /dist/*.whl /tmp/wheel/
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && pip install --no-cache-dir --no-deps /tmp/wheel/*.whl \
 && rm -rf /tmp/requirements.txt /tmp/wheel \
 && useradd --no-create-home --uid 10001 agentce

# Unprivileged, with an empty working directory that any user id can enter: `docker run --user` maps
# the caller's own id, and the engine reads its current directory.
USER agentce
WORKDIR /work

# `docker run <image>` assesses the quickstart project shipped inside the package, end to end.
ENTRYPOINT ["agentce"]
CMD ["quickstart", "--out", "/tmp/report"]
