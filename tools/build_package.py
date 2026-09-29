#!/usr/bin/env python3
"""Build an offline macOS arm64 add-on archive with per-file provenance."""
import hashlib
import json
from pathlib import Path
import platform
import zipfile


def main():
    root=Path(__file__).resolve().parents[1]
    output=root/'dist'
    output.mkdir(exist_ok=True)
    if not (root/'vendor'/'shapely').is_dir():
        raise SystemExit('Stage pinned private worker dependencies in vendor before building.')
    files=[]
    for name in ('Init.py','InitGui.py','package.xml','inlay_planner','resources','vendor','examples','README.md','LICENSE','THIRD_PARTY_NOTICES.md','worker-wheels.lock.json'):
        source=root/name
        if not source.exists():
            continue
        candidates=[source] if source.is_file() else sorted(source.rglob('*'))
        files.extend(p for p in candidates if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc','.pyo') and not p.name.startswith('.DS_Store'))
    manifest={'schema_version':1,'name':'InlayPlanner','version':'0.1.0','platform':'macos-arm64',
              'runtime':'FreeCAD 1.1.3 / Python 3.11','files':{},'dependency_wheels':{}}
    for path in files:
        manifest['files'][str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    lock=json.loads((root/'worker-wheels.lock.json').read_text())
    for record in lock['wheels']:
        path=root/'.cache'/'wheels'/record['filename']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
            raise SystemExit('Runtime wheel differs from its lock: '+path.name)
        manifest['dependency_wheels'][path.name]=record['sha256']
    archive=output/'InlayPlanner-0.1.0-macos-arm64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in files:
            info=zipfile.ZipInfo('InlayPlanner/'+str(path.relative_to(root)),(2026,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o755 if path.stat().st_mode&0o111 else 0o644)<<16
            z.writestr(info,path.read_bytes())
        info=zipfile.ZipInfo('InlayPlanner/build-manifest.json',(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        z.writestr(info,json.dumps(manifest,sort_keys=True,indent=2))
    identity={'archive':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'files':len(files),'bytes':archive.stat().st_size}
    (output/'build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2))
    (output/'artifact.json').write_text(json.dumps(identity,indent=2))
    print(json.dumps(identity))


if __name__=='__main__':
    main()
