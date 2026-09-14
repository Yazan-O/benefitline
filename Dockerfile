# Why it is needed: `agentcore add agent --build Container` makes the CLI look for a
# Dockerfile at the code location, and `agentcore deploy` stops with
# "Container agents require a Dockerfile" when it is missing (reproduced 2026-09-13,
# CLI 0.29.0). CodeZip is not an alternative: the dependency closure of
# requirements.txt measures 639 MB installed on this machine, past the 250 MB code limit.
#
# ARM64 is mandatory for AgentCore Runtime (../12_AGENTCORE_RUNTIME.md).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 AWS_REGION=us-east-1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The runtime reads gallery/ground_truth.json and data/ at request time, so both have to
# be in the image; src/ holds the entrypoint.
COPY src/ ./src/
COPY gallery/ ./gallery/
COPY data/ ./data/

# Do NOT try to "warm" policyengine_us with a build-time import. Two separate processes on
# this machine each paid 36.7 s and 39.7 s for `import policyengine_us`, so the cost is
# per-process computation (building the tax-benefit system), not bytecode or page cache. A
# build-time import buys nothing at run time. The fix is a lazy import inside
# src/benefitline/engine.py (defect D12); it cannot be done from the Dockerfile.

EXPOSE 8080
CMD ["python", "src/app.py"]

# The build context is the repo root and it contains .env. Copy dockerignore.suggested
# beside this file to <repo root>/.dockerignore BEFORE the first build (defect D13).
