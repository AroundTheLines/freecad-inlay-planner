#!/usr/bin/env python3
"""Run and retain a single-artifact, offline, actual-FreeCAD GUI checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CASES=['positive_flat','positive_v','positive_bull','positive_flat_v','positive_multi_flat',
               'review_svg','review_placement','review_lifecycle','review_failures','review_layout',
               'stock_faces','document_lifetime','preview_pick','count_boundaries','transaction_recovery',
               'source_recovery','filesystem_recovery','portable']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',default=DEFAULT_CASES)
    args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    archive=ROOT/'dist/InlayPlanner-0.1.0-macos-arm64.zip'
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    def source_matches():
        with zipfile.ZipFile(archive) as z:
            manifest=json.loads(z.read('InlayPlanner/build-manifest.json'))
        return all((ROOT/p).is_file() and hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
                   for p,h in manifest['files'].items())
    if not source_matches(): raise SystemExit('Build the current source before the final suite.')
    status={'archive_sha256':digest,'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'cases':[],'ok':False}
    for case in args.cases:
        destination=out/case
        report=destination/'report.json'
        if report.exists():
            prior=json.loads(report.read_text())
            if not prior.get('ok') or prior['archive_sha256']!=digest or not prior.get('offline_network_denied'):
                raise SystemExit('Existing case cannot be resumed: '+str(report))
        else:
            command=[sys.executable,str(ROOT/'tools/run_e2e.py'),'--case',case,'--offline',
                     '--archive',str(archive),'--out',str(destination),'--timeout','600']
            if case=='portable':
                command+=['--without-addon','--document',str(out/'positive_multi_flat/paired-inlay.FCStd')]
            print('Running final case: '+case,flush=True)
            with (out/(case+'-runner.log')).open('w') as log:
                completed=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
            if completed.returncode:
                status['failed_case']=case
                (out/'suite-progress.json').write_text(json.dumps(status,indent=2))
                raise SystemExit('GUI case failed; inspect '+str(report))
        record=json.loads(report.read_text())
        if (destination/'spec.json').exists() and (destination/'board.ngc').exists():
            command=[sys.executable,str(ROOT/'tools/audit_gui_output.py'),str(destination)]
            with (out/(case+'-audit.log')).open('w') as log:
                checked=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
            if checked.returncode: raise SystemExit('Independent export audit failed: '+case)
        status['cases'].append({'case':case,'report':str(report.relative_to(ROOT)),
                               'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
                               'assertions':len(record['result']['checks']),'exit_code':record['exit_code'],
                               'peak_process_tree_rss':record['peak_process_tree_rss']})
        status['source_stable']=source_matches() and hashlib.sha256(archive.read_bytes()).hexdigest()==digest
        if not status['source_stable']:raise SystemExit('Packaged production source changed during final suite')
        (out/'suite-progress.json').write_text(json.dumps(status,indent=2))
        print(case+': passed',flush=True)
    status['ok']=True
    status['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
    (out/('suite-'+time.strftime('%H%M%S')+'.json')).write_text(json.dumps(status,indent=2))
    print(json.dumps({'ok':True,'cases':len(status['cases']),'archive_sha256':digest}),flush=True)


if __name__=='__main__':main()
