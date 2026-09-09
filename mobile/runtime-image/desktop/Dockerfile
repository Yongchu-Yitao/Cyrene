# Build on a Linux Docker host (Docker Desktop also works). The Android engine
# selects the guest architecture from the signed image manifest.
FROM python:3.12-bookworm AS guest
ARG TARGETARCH
ARG NODE_SHA256
ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Retries "3";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80cyrene-network \
    && apt-get update && apt-get install -y --no-install-recommends \
    linux-image-${TARGETARCH} initramfs-tools systemd-sysv udev iproute2 isc-dhcp-client \
    bash git curl ca-certificates ripgrep procps socat e2fsprogs \
    build-essential libsndfile1 libglib2.0-0 libgl1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY source /opt/cyrene
# Official Node 22 release, pinned to its published SHASUMS256.txt digest.
COPY node.tar.xz /tmp/node.tar.xz
RUN echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz --strip-components=1 -C /usr/local \
    && rm /tmp/node.tar.xz
COPY guest/requirements.py /opt/cyrene-mobile/requirements.py
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    python /opt/cyrene-mobile/requirements.py /opt/cyrene/pyproject.toml --apply > /opt/cyrene-mobile/requirements.txt \
    && pip install --timeout 120 --retries 5 uv==0.11.28 \
    && UV_HTTP_TIMEOUT=120 uv pip install --system -r /opt/cyrene-mobile/requirements.txt \
    && UV_HTTP_TIMEOUT=120 uv pip install --system --no-deps /opt/cyrene \
    && pip check \
    && python -c 'import cyrene, mcp, onnxruntime, sherpa_onnx, pypdfium2, tokenizers' \
    && pip freeze > /opt/cyrene-mobile/installed-requirements.txt
# Use Google's official Chrome for Testing origin when the Playwright CDN stalls.
# Restore the installer after downloading; browser revisions remain Playwright-selected.
RUN if [ "$TARGETARCH" = amd64 ]; then python -c 'import pathlib, playwright; root=pathlib.Path(playwright.__file__).parent/"driver/package/lib"; files=[p for p in root.rglob("*.js") if "builds/cft/" in p.read_text()]; assert files; [(p.with_suffix(".js.original").write_text(p.read_text()), p.write_text(p.read_text().replace("builds/cft/", ""))) for p in files]' \
    && PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST=https://storage.googleapis.com/chrome-for-testing-public PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 python -m playwright install --with-deps chromium \
    && python -c 'import pathlib, playwright; root=pathlib.Path(playwright.__file__).parent/"driver/package/lib"; [p.replace(p.with_suffix("")) for p in root.rglob("*.js.original")]'; else \
    NO_PROXY=playwright.download.prss.microsoft.com no_proxy=playwright.download.prss.microsoft.com \
    PLAYWRIGHT_DOWNLOAD_HOST=https://playwright.download.prss.microsoft.com/dbazure/download/playwright \
    PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000 python -m playwright install --with-deps chromium; fi
COPY guest /opt/cyrene-mobile
RUN if [ "$TARGETARCH" = arm64 ]; then sed -i s/ttyS0/ttyAMA0/g /opt/cyrene-mobile/cyrene-serial.service; fi \
    && chmod 755 /opt/cyrene-mobile/desktop-control \
    && mkdir -p /workspace /root /etc/systemd/system/multi-user.target.wants \
    && cp /opt/cyrene-mobile/cyrene-serial.service /etc/systemd/system/ \
    && ln -s /etc/systemd/system/cyrene-serial.service /etc/systemd/system/multi-user.target.wants/cyrene-serial.service \
    && ln -s /dev/null /etc/systemd/system/serial-getty@ttyS0.service \
    && ln -s /dev/null /etc/systemd/system/serial-getty@ttyAMA0.service \
    && printf 'auto lo\niface lo inet loopback\nauto eth0\niface eth0 inet dhcp\n' > /etc/network-interfaces.cyrene \
    && printf 'virtio_pci\nvirtio_blk\next4\n' >> /etc/initramfs-tools/modules \
    && update-initramfs -u -k all

FROM debian:bookworm-slim AS disk
RUN apt-get update && apt-get install -y --no-install-recommends e2fsprogs gzip && rm -rf /var/lib/apt/lists/*
COPY --from=guest / /rootfs/
ARG DISK_MIB=8192
RUN mkdir /bundle && cp /rootfs/boot/vmlinuz-* /bundle/vmlinuz-virt \
    && cp /rootfs/boot/initrd.img-* /bundle/initramfs-cyrene \
    && rm -f /rootfs/etc/machine-id && touch /rootfs/etc/machine-id \
    && truncate -s "${DISK_MIB}M" /rootfs.ext4 \
    && mkfs.ext4 -q -F -L CYRENE_DESKTOP -d /rootfs -E lazy_itable_init=0,lazy_journal_init=0 /rootfs.ext4 \
    && sha256sum /rootfs.ext4 > /bundle/rootfs.sha256 \
    && stat -c %s /rootfs.ext4 > /bundle/rootfs.size \
    && gzip -1 -c /rootfs.ext4 > /bundle/rootfs-template.ext4.gzip \
    && cp /rootfs/opt/cyrene-mobile/installed-requirements.txt /bundle/
FROM scratch AS bundle
COPY --from=disk /bundle/ /
