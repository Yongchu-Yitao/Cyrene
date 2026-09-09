#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_DIR="$ROOT_DIR/runtime-app/src/main/assets/runtime"
JNI_DIR="$ROOT_DIR/runtime-app/src/main/jniLibs"
WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cyrene-runtime.XXXXXX")
trap 'rm -rf "$WORK_DIR"' EXIT

MKFS_EXT4=${MKFS_EXT4:-$(command -v mkfs.ext4 || true)}
if [ -z "$MKFS_EXT4" ] && [ -x /opt/homebrew/opt/e2fsprogs/sbin/mkfs.ext4 ]; then
    MKFS_EXT4=/opt/homebrew/opt/e2fsprogs/sbin/mkfs.ext4
fi
if [ -z "$MKFS_EXT4" ]; then
    echo "mkfs.ext4 is required (install e2fsprogs or set MKFS_EXT4)" >&2
    exit 1
fi

LIMBO_VERSION="6.0.1"
ALPINE_VERSION="3.24"
KERNEL_VERSION="6.18.41-r0"
LIMBO_URL="${CYRENE_LIMBO_URL:-https://github.com/limboemu/limbo/releases/download/v6.0.1-LimboEmulator/limbo-android-x86-6.0.1-qemu-5.1.0.apk}"
MINIROOTFS_URL="${CYRENE_MINIROOTFS_URL:-https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/alpine-minirootfs-3.24.0-x86_64.tar.gz}"
KERNEL_URL="${CYRENE_KERNEL_URL:-https://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/linux-virt-6.18.41-r0.apk}"

# Pinned source artifacts. Update these only as part of a reviewed runtime
# image release, then sign the resulting manifest with the release key.
LIMBO_SHA256="38d8a68a92c709d3f21682fbc317246a527779648c5c8962926d19a12468a0b6"
MINIROOTFS_SHA256="de9a11c0e0e7e9c94db3ed8af7b450eafc0b13687bd7e9199d55050f20aa0a89"
KERNEL_SHA256="ce464df88b25e73f85e7f74557fabc82eea39c76232ef6bfae004136f2c514b9"

fetch() {
    local url=$1 target=$2 expected=$3
    curl --http1.1 -fL --retry 5 --retry-all-errors -o "$target" "$url"
    local actual
    actual=$(shasum -a 256 "$target" | awk '{print $1}')
    if [ "$actual" != "$expected" ]; then
        echo "SHA-256 mismatch for $url: expected $expected, got $actual" >&2
        exit 1
    fi
}

fetch "$LIMBO_URL" "$WORK_DIR/limbo.apk" "$LIMBO_SHA256"
fetch "$MINIROOTFS_URL" "$WORK_DIR/minirootfs.tar.gz" "$MINIROOTFS_SHA256"
fetch "$KERNEL_URL" "$WORK_DIR/linux-virt.apk" "$KERNEL_SHA256"

rm -rf "$OUTPUT_DIR" "$JNI_DIR/arm64-v8a" "$JNI_DIR/x86_64"
mkdir -p "$OUTPUT_DIR" "$JNI_DIR/arm64-v8a" "$JNI_DIR/x86_64" \
    "$WORK_DIR/kernel" "$WORK_DIR/initramfs-root" "$WORK_DIR/rootfs"

for abi in arm64-v8a x86_64; do
    unzip -q "$WORK_DIR/limbo.apk" "lib/$abi/*.so" -d "$WORK_DIR/limbo"
    cp "$WORK_DIR/limbo/lib/$abi/"*.so "$JNI_DIR/$abi/"
done
for firmware in bios-256k.bin kvmvapic.bin linuxboot.bin linuxboot_dma.bin efi-virtio.rom pxe-virtio.rom; do
    unzip -p "$WORK_DIR/limbo.apk" "assets/roms/$firmware" > "$OUTPUT_DIR/$firmware"
done

bsdtar -xf "$WORK_DIR/linux-virt.apk" -C "$WORK_DIR/kernel"
tar -xzf "$WORK_DIR/minirootfs.tar.gz" -C "$WORK_DIR/initramfs-root"
tar -xzf "$WORK_DIR/minirootfs.tar.gz" -C "$WORK_DIR/rootfs"
cp "$ROOT_DIR/runtime-image/guest/init" "$WORK_DIR/initramfs-root/init"
cp "$ROOT_DIR/runtime-image/guest/runtime-init" "$WORK_DIR/rootfs/sbin/cyrene-runtime-init"
chmod 755 "$WORK_DIR/initramfs-root/init" "$WORK_DIR/rootfs/sbin/cyrene-runtime-init"

module_root="$WORK_DIR/kernel/lib/modules/6.18.41-0-virt"
guest_module_root="$WORK_DIR/initramfs-root/lib/modules/6.18.41-0-virt"
mkdir -p "$guest_module_root"
cp "$module_root/modules.dep" "$module_root/modules.alias" "$module_root/modules.builtin" "$guest_module_root/"
for relative in \
    kernel/fs/netfs/netfs.ko.gz \
    kernel/net/9p/9pnet.ko.gz \
    kernel/net/9p/9pnet_virtio.ko.gz \
    kernel/fs/9p/9p.ko.gz \
    kernel/net/core/failover.ko.gz \
    kernel/drivers/net/net_failover.ko.gz \
    kernel/drivers/net/virtio_net.ko.gz \
    kernel/drivers/block/virtio_blk.ko.gz \
    kernel/lib/crc/crc16.ko.gz \
    kernel/fs/mbcache.ko.gz \
    kernel/fs/jbd2/jbd2.ko.gz \
    kernel/fs/ext4/ext4.ko.gz \
    kernel/net/packet/af_packet.ko.gz; do
    mkdir -p "$guest_module_root/$(dirname "$relative")"
    cp "$module_root/$relative" "$guest_module_root/$relative"
done

mkdir -p "$WORK_DIR/rootfs/etc/apk" "$WORK_DIR/rootfs/workspace"
cat > "$WORK_DIR/rootfs/etc/apk/repositories" <<EOF
https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/main
https://dl-cdn.alpinelinux.org/alpine/v${ALPINE_VERSION}/community
EOF

# Normalize copied source mtimes so cpio and mke2fs inputs are reproducible.
find "$WORK_DIR/initramfs-root" "$WORK_DIR/rootfs" -exec touch -h -t 202401010000 {} +

cp "$WORK_DIR/kernel/boot/vmlinuz-virt" "$OUTPUT_DIR/vmlinuz-virt"
cat > "$OUTPUT_DIR/host-resolv.conf" <<EOF
# Signed fallback consumed by QEMU slirp on the Android host side. Runtime uses
# Android LinkProperties to replace this copy with the active network's IPv4 DNS.
nameserver 1.1.1.1
EOF
(
    cd "$WORK_DIR/initramfs-root"
    find . -print0 | LC_ALL=C sort -z | cpio --null -o --format=newc --quiet | gzip -9n > "$OUTPUT_DIR/initramfs-cyrene"
)

rootfs_template="$WORK_DIR/rootfs-template.ext4"
truncate -s 536870912 "$rootfs_template"
E2FSPROGS_FAKE_TIME=1704067200 "$MKFS_EXT4" \
    -q -F -L CYRENE_ROOT -U 63797265-6e65-4000-8000-000000000002 \
    -d "$WORK_DIR/rootfs" -E lazy_itable_init=0,lazy_journal_init=0 "$rootfs_template"
gzip -9n -c "$rootfs_template" > "$OUTPUT_DIR/rootfs-template.ext4.gzip"

unzip -p "$WORK_DIR/limbo.apk" assets/LICENSE > "$OUTPUT_DIR/LIMBO-GPL-2.0.txt"
cat > "$OUTPUT_DIR/NOTICE.txt" <<EOF
Cyrene Linux Runtime bundle

QEMU Android engine: Limbo Emulator ${LIMBO_VERSION} / QEMU 5.1.0 (GPL-2.0)
Source: https://github.com/limboemu/limbo/tree/v6.0.1-LimboEmulator
Guest userspace: Alpine Linux ${ALPINE_VERSION} (see /etc/apk for package licenses)
Guest kernel package: linux-virt ${KERNEL_VERSION} (GPL-2.0-only)

The pinned build inputs and source location are recorded in
runtime-image/build-runtime-bundle.sh so the corresponding native engine can
be audited and rebuilt.
EOF

manifest="$OUTPUT_DIR/manifest.json"
kernel_hash=$(shasum -a 256 "$OUTPUT_DIR/vmlinuz-virt" | awk '{print $1}')
initramfs_hash=$(shasum -a 256 "$OUTPUT_DIR/initramfs-cyrene" | awk '{print $1}')
rootfs_hash=$(shasum -a 256 "$rootfs_template" | awk '{print $1}')
rootfs_bundle_hash=$(shasum -a 256 "$OUTPUT_DIR/rootfs-template.ext4.gzip" | awk '{print $1}')
host_resolver_hash=$(shasum -a 256 "$OUTPUT_DIR/host-resolv.conf" | awk '{print $1}')
firmware_json=""
for firmware in bios-256k.bin kvmvapic.bin linuxboot.bin linuxboot_dma.bin efi-virtio.rom pxe-virtio.rom; do
    firmware_hash=$(shasum -a 256 "$OUTPUT_DIR/$firmware" | awk '{print $1}')
    [ -z "$firmware_json" ] || firmware_json="$firmware_json,"
    firmware_json="$firmware_json{\"file\":\"$firmware\",\"sha256\":\"$firmware_hash\"}"
done
cat > "$manifest" <<EOF
{"schema":"cyrene-runtime-image-v2","version":"alpine-3.24-qemu-x86_64-v2","guest_arch":"x86_64","engine":"qemu-5.1.0-tcg","kernel":{"file":"vmlinuz-virt","sha256":"${kernel_hash}"},"initramfs":{"file":"initramfs-cyrene","sha256":"${initramfs_hash}"},"rootfs":{"file":"rootfs-template.ext4.gzip","sha256":"${rootfs_bundle_hash}","installed_file":"rootfs-template.ext4","unpacked_sha256":"${rootfs_hash}","size":536870912},"host_resolver":{"file":"host-resolv.conf","sha256":"${host_resolver_hash}"},"firmware":[${firmware_json}]}
EOF

if [ -n "${CYRENE_RUNTIME_SIGNING_KEY:-}" ]; then
    openssl dgst -sha256 -sign "$CYRENE_RUNTIME_SIGNING_KEY" -out "$OUTPUT_DIR/manifest.sig" "$manifest"
    openssl pkey -in "$CYRENE_RUNTIME_SIGNING_KEY" -pubout -out "$OUTPUT_DIR/runtime-public-key.pem"
else
    echo "Bundle built but not signed. Set CYRENE_RUNTIME_SIGNING_KEY to a PEM private key." >&2
fi

echo "Runtime bundle written to $OUTPUT_DIR"
