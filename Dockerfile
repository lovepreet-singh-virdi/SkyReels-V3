FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV HF_HOME=/cache/huggingface
ENV TRANSFORMERS_CACHE=/cache/huggingface
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    git \
    ffmpeg \
    libsndfile1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

RUN python3 -m venv /opt/venv && \
    pip install --upgrade pip setuptools wheel

COPY requirements.txt requirements-web.txt README.md generate_video.py ./
COPY skyreels_v3 ./skyreels_v3
COPY webapp ./webapp
COPY scripts ./scripts

RUN pip install -r requirements-web.txt && \
    pip install -r requirements.txt

EXPOSE 7860

CMD ["python", "webapp/app.py"]
