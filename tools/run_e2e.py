#!/usr/bin/env python3
"""Install the built archive and drive real FreeCAD GUI journeys."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time
import uuid
import zipfile

import psutil


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',default='smoke')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--timeout',type=float,default=600)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--without-addon',action='store_true',help='Verify package then keep it outside FreeCAD module search paths')
    parser.add_argument('--document',type=Path,help='Existing test document for a portability journey')
    parser.add_argument('--offline',action='store_true',help='Deny network access to the application and its children with the native macOS sandbox')
    parser.add_argument('--previous-addon',type=Path,help='Verified previous extracted package for reversible installation transition tests')
    parser.add_argument('--installed-addon',type=Path,help='Read an already installed add-on through an isolated-profile symlink')
    options=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    lock_path=repo/'test-runs'/'.gui-session.lock'
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    # Cocoa application modality and focus are shared even when profiles are
    # isolated. Waiting for this lock does not consume the journey deadline.
    with lock_path.open('a+') as lock:
        print('Waiting for isolated GUI session lock',flush=True)
        fcntl.flock(lock,fcntl.LOCK_EX)
        print('Acquired isolated GUI session lock',flush=True)
        try:
            return run(options,repo)
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def run(options,repo):
    archive=options.archive or repo/'dist'/'InlayPlanner-0.1.0-macos-arm64.zip'
    run_id=uuid.uuid4().hex
    out=(options.out or repo/'test-runs'/('gui-'+options.case+'-'+time.strftime('%Y%m%d-%H%M%S'))).resolve()
    out.mkdir(parents=True,exist_ok=False)
    profile=out/'profile'
    for name in ('home','data','temp'):
        (profile/name).mkdir(parents=True)
    # These preferences belong only to this isolated test process. Avoid the
    # bundled first-start page's PySide/Qt destruction bug during GUI shutdown.
    (profile/'user.cfg').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<FCParameters><FCParamGroup Name="Root"><FCParamGroup Name="BaseApp"><FCParamGroup Name="Preferences">
<FCParamGroup Name="General"><FCText Name="AutoloadModule">CAMWorkbench</FCText></FCParamGroup>
<FCParamGroup Name="Mod"><FCParamGroup Name="Start"><FCBool Name="ShowOnStartup" Value="0"/>
<FCBool Name="Migration2024Complete" Value="1"/></FCParamGroup>
<FCParamGroup Name="CAM"><FCText Name="PostProcessorOutputPolicy">Open File Dialog</FCText></FCParamGroup></FCParamGroup>
</FCParamGroup></FCParamGroup></FCParamGroup></FCParameters>''')
    module_dir=profile/'data'/'Mod'
    module_dir.mkdir()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if Path(member.filename).is_absolute() or '..' in Path(member.filename).parts:
                raise ValueError('Archive contains an unsafe path')
        z.extractall(module_dir)
    addon=module_dir/'InlayPlanner'
    if options.installed_addon:
        if options.previous_addon or options.without_addon:
            raise ValueError('Installed-source verification cannot update or remove that installation')
        shutil.rmtree(addon)
        installed=options.installed_addon.resolve(strict=True)
        addon.symlink_to(installed,target_is_directory=True)
        addon=installed
    manifest=json.loads((addon/'build-manifest.json').read_text())
    with zipfile.ZipFile(archive) as z:
        if manifest!=json.loads(z.read('InlayPlanner/build-manifest.json')):
            raise ValueError('Installed manifest is not the selected package')
    for relative,digest in manifest['files'].items():
        if hashlib.sha256((addon/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError('Installed file hash mismatch: '+relative)
    transitions=[]
    if options.previous_addon:
        previous=options.previous_addon.resolve(strict=True)
        old_manifest=json.loads((previous/'build-manifest.json').read_text())
        old=out/'previous-package'
        old.mkdir()
        for relative,digest in old_manifest['files'].items():
            if Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise ValueError('Unsafe previous manifest path')
            source=previous/relative
            if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:
                raise ValueError('Previous package differs from its manifest: '+relative)
            target=old/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
        shutil.copy2(previous/'build-manifest.json',old/'build-manifest.json')
        current=out/'new-package'
        backup=out/'rollback-package'
        addon.rename(current);old.rename(addon)
        sentinel=module_dir/'unrelated-addon.txt'
        sentinel.write_text('Preserve this unrelated module')
        def verify(expected,action):
            for relative,digest in expected['files'].items():
                if hashlib.sha256((addon/relative).read_bytes()).hexdigest()!=digest:
                    raise ValueError(action+' changed '+relative)
            if sentinel.read_text()!='Preserve this unrelated module':
                raise ValueError('Installation changed an unrelated module')
            transitions.append({'action':action,'manifest_sha256':hashlib.sha256((addon/'build-manifest.json').read_bytes()).hexdigest(),
                                'verified_files':len(expected['files']),'unrelated_module_preserved':True})
        verify(old_manifest,'Install verified previous package')
        addon.rename(backup);current.rename(addon);verify(manifest,'Update to final package')
        addon.rename(current);backup.rename(addon);verify(old_manifest,'Restore previous package')
        addon.rename(backup);current.rename(addon);verify(manifest,'Restore final package for GUI launch')
        (out/'installation-transitions.json').write_text(json.dumps(transitions,indent=2))
    if options.without_addon:
        disabled=out/'uninstalled-package'
        addon.rename(disabled)
        addon=disabled
    harness=out/'harness'
    harness.mkdir()
    for source in (repo/'tests'/'gui').glob('*.py'):
        shutil.copy2(source,harness/source.name)
    harness_hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in harness.glob('*.py')}
    identity={'schema_version':1,'run_id':run_id,'case':options.case,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'harness_sha256':harness_hashes,
              'addon_enabled':not options.without_addon,
              'offline_network_denied':options.offline,'installation_transitions':transitions,
              'installed_root':str(addon),'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'ok':False}
    resources=Path('/Applications/FreeCAD.app/Contents/Resources')
    env={key:os.environ[key] for key in ('HOME','USER','LOGNAME','LANG','TMPDIR','DISPLAY') if key in os.environ}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',PREFIX=str(resources),PYTHONHOME=str(resources),PYTHONPATH=str(resources),
        LD_LIBRARY_PATH=str(resources/'lib'),SSL_CERT_FILE=str(resources/'ssl'/'cacert.pem'),MallocNanoZone='0',
        FONTCONFIG_FILE='/etc/fonts/fonts.conf',FONTCONFIG_PATH='/etc/fonts',
        FREECAD_USER_HOME=str(profile/'home'),FREECAD_USER_DATA=str(profile/'data'),FREECAD_USER_TEMP=str(profile/'temp'),
        INLAY_E2E_OUT=str(out),INLAY_E2E_RUN_ID=run_id,INLAY_E2E_CASE=options.case,INLAY_E2E_ADDON=str(addon),
        INLAY_E2E_REPO=str(repo),INLAY_TEST_MODE='1')
    source_fixtures=[]
    if options.document:
        document=options.document.resolve(strict=True)
        env['INLAY_E2E_DOCUMENT']=str(document)
        identity['document']=str(document)
        identity['document_sha256']=hashlib.sha256(document.read_bytes()).hexdigest()
        if options.case=='portable':
            source_fixtures=[(path,hashlib.sha256(path.read_bytes()).hexdigest()) for path in document.parent.glob('*.svg')]
    macro=out/'run.FCMacro'
    macro.write_text('import sys\nfrom PySide6 import QtCore\nsys.path.insert(0,'+repr(str(harness))+')\n'
                     'from journeys import execute\nQtCore.QTimer.singleShot(1000,execute)\n')
    cmd=[str(resources/'bin'/'freecad'),'--user-cfg',str(profile/'user.cfg'),'--system-cfg',str(profile/'system.cfg'),
         '--log-file',str(out/'freecad.log'),str(macro)]
    if options.offline:
        cmd=['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)']+cmd
    peak=0
    samples=[]
    monitoring=[]
    start=time.monotonic()
    with (out/'stdout.log').open('w') as log:
        process=subprocess.Popen(cmd,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while process.poll() is None:
                elapsed=time.monotonic()-start
                if elapsed>options.timeout:
                    raise TimeoutError('Real GUI journey exceeded its timeout')
                try:
                    p=psutil.Process(process.pid)
                    tree=[p]+p.children(recursive=True)
                    rss=sum(q.memory_info().rss for q in tree if q.is_running())
                    peak=max(peak,rss)
                    if not samples or elapsed-samples[-1]['seconds']>=1:
                        samples.append({'seconds':round(elapsed,2),'rss_bytes':rss,'process_count':len(tree)})
                except (psutil.NoSuchProcess,psutil.AccessDenied) as exc:
                    monitoring.append(str(exc))
                time.sleep(.1)
            identity['exit_code']=process.returncode
            result_path=out/'result.json'
            if not result_path.exists():
                raise RuntimeError('No completed GUI result record; inspect stdout.log and freecad.log')
            result=json.loads(result_path.read_text())
            identity['result']=result
            if result.get('run_id')!=run_id or not result.get('ok') or process.returncode:
                raise RuntimeError(result.get('error','GUI result failed or belongs to another run'))
            identity['ok']=True
        except BaseException as exc:
            identity['error']=str(exc)
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait()
        finally:
            # The native application can fail before the test's Python finally
            # block. Restore only the exact test-source files recorded above.
            for path,digest in source_fixtures:
                hidden=path.with_suffix(path.suffix+'.unavailable')
                if not path.exists() and hidden.is_file():
                    if hashlib.sha256(hidden.read_bytes()).hexdigest()!=digest:
                        identity['ok']=False
                        identity['error']='Hidden test source changed; automatic restoration refused: '+str(hidden)
                    else:
                        hidden.rename(path)
            identity.update(elapsed_s=time.monotonic()-start,peak_process_tree_rss=peak,
                            memory_samples=samples,memory_monitor_errors=monitoring)
            (out/'report.json').write_text(json.dumps(identity,indent=2))
    print(json.dumps({k:v for k,v in identity.items() if k not in ('memory_samples','result')},indent=2))
    print('Evidence:',out/'report.json')
    raise SystemExit(0 if identity['ok'] else 1)


if __name__=='__main__':
    main()
