# hr_ai image. One Dockerfile, one torch build per accelerator; start.sh / start.ps1 pick the build args from the
# host hardware, so normally you never pass them by hand.
#
#   TORCH_INDEX    cu128 | cu126 | cu118 | rocm7.0 | cpu   (download.pytorch.org/whl/<index>)
#   TORCH_VERSION  torch version available on that index
#   INSTALL_BNB    1 = bitsandbytes (4-bit on small NVIDIA GPUs), 0 = skip (CPU, AMD, ARM: no usable wheel)
FROM python:3.13-slim-bookworm

ARG TORCH_INDEX=cpu
ARG TORCH_VERSION=2.11.0
ARG INSTALL_BNB=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: OpenMP for torch. libnuma1: ROCm runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libnuma1 \
    && rm -rf /var/lib/apt/lists/*

# torch gets its own layer: it is the slow download, and app changes must not invalidate it.
RUN pip install "torch==${TORCH_VERSION}" --index-url "https://download.pytorch.org/whl/${TORCH_INDEX}"

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
# Runtime deps only: drop torch (already installed from the right index) and the dev section.
RUN sed -e '/^# dev/,$d' -e '/^torch==/d' /tmp/requirements.txt > /tmp/runtime.txt \
    && if [ "${INSTALL_BNB}" != "1" ]; then sed -i '/^bitsandbytes==/d' /tmp/runtime.txt; fi \
    && pip install -r /tmp/runtime.txt

COPY app ./app
COPY references ./references

# Unprivileged user. /models and /app/logs are created here so named volumes inherit the ownership.
RUN useradd --uid 10001 --create-home hrai \
    && mkdir -p /models /app/logs \
    && chown -R hrai:hrai /models /app/logs
USER hrai

EXPOSE 8001

# Exactly one worker and no --reload (CLAUDE.md §3.4): each worker would load its own copy of the model.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", \
     "--log-level", "info", "--no-access-log"]
