#!/usr/bin/env python3
"""Refresh a verified, unused desktop template; never read a device's data disk."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--webui', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--signing-key', type=Path, required=True)
    parser.add_argument('--debugfs', default='debugfs')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a new directory')
    base = args.base.resolve() / 'runtime'
    subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(base/'runtime-public-key.pem'),
                    '-signature', str(base/'manifest.sig'), str(base/'manifest.json')], check=True)
    manifest = json.loads((base/'manifest.json').read_text())
    assert manifest['desktop_backend'] and manifest['guest_arch'] == 'aarch64'
    entry = manifest['rootfs']
    assert digest(base/entry['file']) == entry['sha256']
    files = sorted(p for p in args.webui.resolve().rglob('*') if p.is_file())
    assert any(p.name == 'app.js' for p in files)
    assert not any(p.is_symlink() for p in files)
    with tempfile.TemporaryDirectory(prefix='cyrene-template-') as temporary:
        work = Path(temporary)
        disk = work/'rootfs.ext4'
        print('Extracting clean template', flush=True)
        with gzip.open(base/entry['file'], 'rb') as source, disk.open('wb') as target:
            while chunk := source.read(1024*1024):
                if chunk.count(0) == len(chunk): target.seek(len(chunk), 1)
                else: target.write(chunk)
            target.truncate()
        assert digest(disk) == entry['unpacked_sha256']
        destination = '/usr/local/lib/python3.12/site-packages/cyrene/workbench/webui/static/app'
        commands = []
        for directory in sorted((p for p in args.webui.resolve().rglob('*') if p.is_dir()), key=lambda p: len(p.parts)):
            commands.append(f'mkdir "{destination}/{directory.relative_to(args.webui.resolve())}"')
        for file in files:
            name = f'{destination}/{file.relative_to(args.webui.resolve())}'
            assert '"' not in str(file) and '\n' not in str(file)
            commands += [f'rm "{name}"', f'write "{file}" "{name}"']
        command_file = work/'commands'
        command_file.write_text('\n'.join(commands)+'\n')
        with (work/'debugfs.log').open('w') as log:
            subprocess.run([args.debugfs, '-w', '-f', str(command_file), str(disk)], stdout=log, stderr=log, check=True)
        # Verify every installed source byte, including CSS and the compiled bundle.
        dump = work/'verify'
        dump.mkdir()
        subprocess.run([args.debugfs, '-R', f'rdump {destination} {dump}', str(disk)], check=True, stdout=subprocess.DEVNULL)
        for file in files:
            assert digest(file) == digest(dump/'app'/file.relative_to(args.webui.resolve())), file
        output = args.output.resolve()/'runtime'
        output.mkdir(parents=True)
        for file in base.iterdir():
            if file.name != entry['file']: shutil.copy2(file, output/file.name)
        print('Compressing refreshed template', flush=True)
        with disk.open('rb') as source, (output/entry['file']).open('wb') as target:
            with gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=target, mtime=0) as zipped:
                shutil.copyfileobj(source, zipped, 1024*1024)
        entry['sha256'] = digest(output/entry['file'])
        entry['unpacked_sha256'] = digest(disk)
        entry['size'] = disk.stat().st_size
        manifest['version'] = 'debian12-desktop-aarch64-unified-' + entry['sha256'][:12]
        manifest['webui_sha256'] = digest(args.webui/'compiled/app.js')
        (output/'rootfs.sha256').write_text(entry['unpacked_sha256']+'  rootfs.ext4\n')
        (output/'rootfs.size').write_text(str(entry['size'])+'\n')
        (output/'manifest.json').write_text(json.dumps(manifest, sort_keys=True)+'\n')
        subprocess.run(['openssl','dgst','-sha256','-sign',str(args.signing_key),'-out',str(output/'manifest.sig'),str(output/'manifest.json')],check=True)
        subprocess.run(['openssl','pkey','-in',str(args.signing_key),'-pubout','-out',str(output/'runtime-public-key.pem')],check=True)
        print(output, flush=True)

if __name__ == '__main__':
    main()
