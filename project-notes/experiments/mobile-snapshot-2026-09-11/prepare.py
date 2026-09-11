"""Prepare isolated inputs from a supplied preview APK; no user data is copied."""
import argparse
import gzip
from pathlib import Path
import shutil
import subprocess
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--apk', type=Path, required=True)
parser.add_argument('--qemu-img', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
repo = root.parents[2]
image = Path('/tmp/cyrene-snapshot-image')
image.mkdir(exist_ok=True)
with zipfile.ZipFile(args.apk) as archive:
    for name in archive.namelist():
        if not name.startswith('assets/runtime/') or name.endswith('/'):
            continue
        basename = name.rsplit('/', 1)[1]
        if basename.endswith('.gzip'):
            with gzip.GzipFile(fileobj=archive.open(name)) as source, (image/'rootfs.raw').open('wb') as target:
                shutil.copyfileobj(source, target, 1024*1024)
        else:
            (image/basename).write_bytes(archive.read(name))
subprocess.run([str(args.qemu_img), 'convert', '-f', 'raw', '-O', 'qcow2', str(image/'rootfs.raw'), str(image/'rootfs.qcow2')], check=True)
(image/'rootfs.raw').unlink()
shutil.copy2(image/'manifest.json', root/'image-manifest.json')
source = repo/'mobile/runtime-app/src/main'
target = root/'harness/app/src/main'
shutil.copytree(source/'jniLibs/arm64-v8a', target/'jniLibs/arm64-v8a', dirs_exist_ok=True)
shutil.copytree(source/'java', target/'java', dirs_exist_ok=True)

# A fresh AVD configuration, never an existing userdata image.
avd = Path('/tmp/cyrene-snapshot-avd')
(avd/'SnapshotResearch.avd').mkdir(parents=True, exist_ok=True)
(avd/'SnapshotResearch.ini').write_text(f'avd.ini.encoding=UTF-8\npath={avd}/SnapshotResearch.avd\ntarget=android-35\n')
(avd/'SnapshotResearch.avd/config.ini').write_text(
    'avd.id=SnapshotResearch\navd.name=SnapshotResearch\n'
    'abi.type=arm64-v8a\nhw.cpu.arch=arm64\nhw.cpu.ncore=4\nhw.ramSize=8192\n'
    'image.sysdir.1=system-images/android-35/google_apis/arm64-v8a/\n'
    'disk.dataPartition.size=32G\nhw.lcd.width=1080\nhw.lcd.height=2400\n'
    'hw.lcd.density=420\nhw.keyboard=yes\nhw.gpu.enabled=yes\n'
    'tag.id=google_apis\ntag.display=Google APIs\ntarget=android-35\n')
