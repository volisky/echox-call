FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PYTHONPATH=/app/src \
    ECHOX_CALL_KILL_PORTS=0

WORKDIR /app

ARG PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG PYTORCH_CPU_TRUSTED_HOST=download.pytorch.org

RUN set -eux; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    printf '%s\n' \
        'APT::Update::Post-Invoke "";' \
        'APT::Update::Post-Invoke-Success "";' \
        'DPkg::Post-Invoke "";' \
        > /etc/apt/apt.conf.d/99disable-post-invoke; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
            -e 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            /etc/apt/sources.list; \
    fi; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
            -e 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            -e 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

# Keep the default image CPU-only. GPU deployment can use a CUDA base image
# and install the matching torch/torchaudio wheels without changing app code.
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        --index-url "${PYTORCH_CPU_INDEX_URL}" \
        --trusted-host "${PYTORCH_CPU_TRUSTED_HOST}" \
        torch==2.8.0 \
        torchaudio==2.8.0 \
    && sed '/^torch==/d;/^torchaudio==/d' requirements.txt > /tmp/requirements-without-torch.txt \
    && python -m pip install -r /tmp/requirements-without-torch.txt \
    && rm -f /tmp/requirements-without-torch.txt

COPY . /app

RUN mkdir -p /app/data/postcall /app/data/console_uploads

EXPOSE 8000 8001

CMD ["python", "-m", "echox_call.cli.api", "--host", "0.0.0.0", "--port", "8000"]
