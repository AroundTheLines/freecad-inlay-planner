#!/usr/bin/env python3
"""Reproduce private worker packages from exact hash-locked wheel archives."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true',help='Allow downloading missing wheels from their exact locked URLs')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    lock=json.loads((root/'worker-wheels.lock.json').read_text())
    cache=root/'.cache'/'wheels'
    cache.mkdir(parents=True,exist_ok=True)
    wheels=[]
    for record in lock['wheels']:
        wheel=cache/record['filename']
        if not wheel.exists():
            if not args.download:
                raise SystemExit('Missing cached wheel '+wheel.name+'; use --download during build preparation.')
            with urllib.request.urlopen(record['url']) as response:
                data=response.read()
            if hashlib.sha256(data).hexdigest()!=record['sha256']:
                raise SystemExit('Downloaded wheel hash mismatch: '+wheel.name)
            wheel.write_bytes(data)
        if hashlib.sha256(wheel.read_bytes()).hexdigest()!=record['sha256']:
            raise SystemExit('Cached wheel hash mismatch: '+wheel.name)
        wheels.append(wheel)
    staging=Path(tempfile.mkdtemp(prefix='vendor-stage-',dir=root/'.cache'))
    try:
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    relative=Path(member.filename)
                    if relative.is_absolute() or '..' in relative.parts:
                        raise ValueError('Unsafe wheel member: '+member.filename)
                    if '.data' in relative.parts:
                        raise ValueError('Wheel needs a non-purelib install scheme: '+member.filename)
                    archive.extract(member,staging)
                    target=staging/relative
                    if target.is_file():
                        target.chmod(0o755 if member.external_attr>>16&0o111 else 0o644)
        destination=root/'vendor'
        old=root/'.cache'/'vendor-previous'
        if old.exists(): shutil.rmtree(old)
        if destination.exists(): destination.rename(old)
        staging.rename(destination)
        if old.exists(): shutil.rmtree(old)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Staged',len(wheels),'hash-verified private runtime wheels without modifying FreeCAD.')


if __name__=='__main__':
    main()
