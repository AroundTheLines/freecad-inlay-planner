#!/usr/bin/env python3
"""Verify and install a local package, retaining any prior add-on outside Mod."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import time
import zipfile


def verify(root):
    manifest=json.loads((root/'build-manifest.json').read_text())
    for name,digest in manifest['files'].items():
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe manifest path')
        if hashlib.sha256((root/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('Installed content differs: '+name)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--module-dir',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    destination=args.module_dir/'InlayPlanner'
    if destination.is_symlink():raise SystemExit('Refusing to replace a symlinked installation')
    args.module_dir.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.inlay-install-',dir=args.module_dir))
    backup=None
    try:
        with zipfile.ZipFile(args.archive) as archive:
            for member in archive.infolist():
                p=Path(member.filename)
                if p.is_absolute() or '..' in p.parts or p.parts[0]!='InlayPlanner':
                    raise ValueError('Unsafe package member')
            archive.extractall(stage)
        manifest=verify(stage/'InlayPlanner')
        if destination.exists():
            backup=args.module_dir.parent/('InlayPlanner-backup-'+time.strftime('%Y%m%d-%H%M%S'))
            if backup.exists():raise FileExistsError(backup)
            destination.rename(backup)
        try:
            (stage/'InlayPlanner').rename(destination)
            verify(destination)
        except BaseException:
            if destination.exists():destination.rename(stage/'failed-installation')
            if backup:backup.rename(destination)
            raise
        receipt={'ok':True,'archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                 'installed_root':str(destination.resolve()),'verified_files':len(manifest['files']),
                 'backup':str(backup) if backup else None,'installed_at':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        args.receipt.parent.mkdir(parents=True,exist_ok=True)
        args.receipt.write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt,indent=2))
    finally:
        shutil.rmtree(stage)


if __name__=='__main__':main()
