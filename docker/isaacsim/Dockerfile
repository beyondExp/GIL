# Isaac Sim 5.1 + Isaac Lab (pip). Used when NGC nvcr.io/nvidia/isaac-sim is unavailable.
FROM nvidia/cuda:12.8.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    ACCEPT_EULA=Y \
    PRIVACY_CONSENT=Y \
    OMNI_KIT_ACCEPT_EULA=YES \
    OMNI_KIT_ALLOW_ROOT=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev python3-pip \
        git git-lfs wget curl ca-certificates \
        cmake build-essential \
        libgl1 libglib2.0-0 libx11-6 libxext6 libsm6 libice6 \
        libvulkan1 vulkan-tools \
        libglu1-mesa \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python3

RUN python -m pip install --upgrade pip \
    && python -m pip install "isaacsim[all,extscache]==5.1.0" --extra-index-url https://pypi.nvidia.com

WORKDIR /opt
# Isaac Sim 5.1 wheels are cp311; pin Isaac Lab to a 3.11-compatible release.
# Isolated builds pull a setuptools that dropped pkg_resources; disable isolation.
RUN python -m pip install --upgrade "setuptools==75.8.2" wheel \
    && git clone --depth 1 --branch v2.2.0 https://github.com/isaac-sim/IsaacLab.git \
    && python -m pip install --no-build-isolation -e /opt/IsaacLab/source/isaaclab \
    && python -m pip install --no-build-isolation -e /opt/IsaacLab/source/isaaclab_assets \
    && python -m pip install --no-build-isolation -e /opt/IsaacLab/source/isaaclab_tasks \
    && python -m pip install --no-build-isolation -e /opt/IsaacLab/source/isaaclab_rl \
    && python -m pip install --no-build-isolation websockets

ENV PYTHONPATH=/opt/IsaacLab/source/isaaclab:/opt/IsaacLab/source/isaaclab_tasks:/opt/IsaacLab/source/isaaclab_assets:/opt/IsaacLab/source/isaaclab_rl:/workspace/gil/src

WORKDIR /workspace/gil
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxrandr2 libxt6 libxinerama1 libxi6 libxcursor1 libxkbcommon0 libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /opt/entrypoint.sh
RUN sed -i 's/\r$//' /opt/entrypoint.sh && chmod +x /opt/entrypoint.sh

EXPOSE 49100 47998/udp 8211 8766 6769
ENTRYPOINT ["/opt/entrypoint.sh"]
CMD ["livestream"]
