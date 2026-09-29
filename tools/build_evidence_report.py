#!/usr/bin/env python3
"""Validate retained receipts and produce a local, reviewable proof index."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path,help='Explicit list of reviewed final receipts')
    options=parser.parse_args()
    config=json.loads(options.config.read_text())
    archive=ROOT/config['archive']
    digest=sha(archive)
    proof_files={archive,options.config.resolve()}
    rows=[]
    assertions=0
    for title,relative in config['gui_runs'].items():
        report_path=ROOT/relative
        report=json.loads(report_path.read_text())
        if not(report.get('ok') and report.get('exit_code')==0 and report['archive_sha256']==digest and report.get('offline_network_denied')):
            raise ValueError('Not a successful final offline GUI run: '+relative)
        result=report['result']
        if result['version'][:3]!=['1','1','3']:raise ValueError('Unexpected runtime')
        if result['run_id']!=report['run_id']:raise ValueError('Mismatched completion token')
        folder=report_path.parent
        for name,expected in report['harness_sha256'].items():
            if sha(folder/'harness'/name)!=expected:raise ValueError('Executed harness changed')
        if (folder/'spec.json').exists() and (folder/'board.ngc').exists():
            audit=json.loads((folder/'independent-audit.json').read_text())
            if not audit.get('passed') or not audit.get('geometry_source') or audit['archive_sha256']!=digest:raise ValueError('Missing exported-motion geometry audit')
            for name,expected in audit['artifact_sha256'].items():
                if sha(folder/name)!=expected:raise ValueError('Audited artifact changed: '+name)
            for name,expected in audit['oracle_source_sha256'].items():
                if sha(ROOT/name)!=expected:raise ValueError('Exported-motion oracle changed: '+name)
        if report['case'].startswith('portable'):
            audit=json.loads((folder/'independent-portable-audit.json').read_text())
            if not(audit.get('passed') and audit.get('source_snapshot_stable')
                   and audit['archive_sha256']==digest and audit['gui_run_id']==report['run_id']):
                raise ValueError('Missing or stale independent portable audit: '+relative)
            for name,expected in audit['artifact_sha256'].items():
                path=Path(name) if Path(name).is_absolute() else ROOT/name
                if sha(path)!=expected:raise ValueError('Portable audited artifact changed: '+name)
                proof_files.add(path)
            for name,expected in audit['oracle_sha256'].items():
                if sha(ROOT/name)!=expected:raise ValueError('Portable oracle changed: '+name)
        assertions+=len(result['checks'])
        rows.append((title,len(result['checks']),report['elapsed_s'],report['peak_process_tree_rss'],report_path))
        for path in folder.rglob('*'):
            if not path.is_file():continue
            parts=path.relative_to(folder).parts
            if any(p in ('profile','uninstalled-package','previous-package','rollback-package','new-package','__pycache__') for p in parts):continue
            if path.suffix=='.png' and not path.name.endswith(('-screen.png','-viewport.png')):continue
            proof_files.add(path)
    unit=ROOT/config['unit_xml']
    suites=list(ET.parse(unit).getroot().iter('testsuite'))
    unit_count=sum(int(s.get('tests',0)) for s in suites)
    if any(int(s.get('failures',0))+int(s.get('errors',0))+int(s.get('skipped',0)) for s in suites):
        raise ValueError('Final test suite contains failures, errors or skips')
    unit_receipt=json.loads((unit.parent/'unit-receipt.json').read_text())
    if not(unit_receipt.get('ok') and unit_receipt.get('source_snapshot_stable')
           and unit_receipt.get('production_matches_archive') and unit_receipt['archive_sha256']==digest
           and unit_receipt['xml_sha256']==sha(unit)):
        raise ValueError('Unit test receipt does not bind the final archive and XML')
    for name,expected in unit_receipt['source_sha256'].items():
        if sha(ROOT/name)!=expected:raise ValueError('Unit tested source changed: '+name)
    proof_files.add(unit)
    for relative in config['supporting_files']:
        path=ROOT/relative
        if path.is_dir():
            proof_files.update(p for p in path.rglob('*') if p.is_file()
                and not any(x in ('profile','__pycache__','extracted','package','vendor') for x in p.relative_to(path).parts)
                and p.suffix not in ('.pyc','.pyo'))
        elif path.is_file():proof_files.add(path)
        else:raise FileNotFoundError(path)
    report=ROOT/'docs/FINAL_VERIFICATION.md'
    source_checkpoint=json.loads((ROOT/'evidence/source-checkpoint.json').read_text())
    if source_checkpoint['archive_sha256']!=digest:raise ValueError('Source checkpoint targets another package')
    lines=['# Mac verification and review files','',
           f'The reviewed package is `{archive.name}`, SHA256 `{digest}`. This checkpoint uses FreeCAD 1.1.3 on macOS arm64.', '',
           f'Implementation source commit: `{source_checkpoint["source_commit"]}`. Final acceptance records are committed separately. The extension is installed locally; restart FreeCAD and choose **CAM → Create Inlay…**. [Installation receipt](../evidence/local-install.json)', '',
           f'**{len(rows)} actual GUI journeys passed, with {assertions:,} assertions and normal process exits. {unit_count} combined tests passed without failures or skips.** Every listed GUI run used the same package with network access denied. Repeated cases count as runs, not additional independent requirements.', '',
           'The finite acceptance matrix is in [requirement_ledger.json](../tests/requirement_ledger.json). The geometry checks challenge the production verifier with separate cutter equations, native OCCT solids, deliberately corrupted results, and independently replayed exported coordinates. Finite probes do not prove every possible input or a physical cut.', '',
           '## Inspect a complete result','']
    for title,relative in config['review_artifacts'].items():
        path=ROOT/relative
        if not path.is_file():raise FileNotFoundError(path)
        lines.append(f'- [{title}](<{os.path.relpath(path,report.parent)}>)')
        proof_files.add(path)
    lines+=['','## GUI results','', '| Journey | Assertions | Elapsed seconds | Peak sampled tree MiB | Receipt |',
            '| --- | ---: | ---: | ---: | --- |']
    for title,count,elapsed,memory,path in rows:
        lines.append(f'| {title} | {count} | {elapsed:.2f} | {memory/1024**2:.1f} | [JSON](<{os.path.relpath(path,report.parent)}>) |')
    lines+=['','Durations include startup and test-driver actions; they are observations on the development Mac, not low-end hardware benchmarks. Process-tree memory is externally sampled and can miss brief peaks; resource receipts also retain the measured process high-water values.', '',
            '## Scope and remaining external validation','',
            '- This is a local Mac build. Windows qualification is deferred.',
            '- Existing stock capture supports constant-thickness prisms, including irregular outlines and through holes. Planar faces on tapered or recessed solids are rejected; arbitrary-solid capture is not qualified.',
            '- The 4 GB target is for the whole computer and remains unproven. See [PERFORMANCE.md](PERFORMANCE.md) for worker budgets, the dense-case native Apply guard, and recovery evidence.',
            '- Physical wooden test cuts and user-specific finished fit remain untested. No test controls a CNC machine.',
            '- Reopening and editing native CAM jobs requires no extension or source artwork. Optional restoration of the planning task for automatic regeneration is not implemented.',
            '- Native Custom command feeds use mm/s. Saving and reopening can round these values: an original 500 mm/min can become 499.999980 mm/min. The independent replay records this separately from intentional edits and checks original coordinates and XY/Z grids independently. Manually edited paths do not retain the original geometry certificate.',
            '- A→A→B→A tool-change arithmetic is tested with an ordered native adapter fixture. The current generator emits one operation per selected cutter on each face; its GUI count tests exercise reachable planned orders and separate caps.',
            '- Original project code and documentation use MIT. Third-party licenses remain separate; runtime operation is local and free.', '',
            '## Reproduce','',
            'The copied harness in each receipt is the exact executed driver. `tools/run_final_suite.py` orchestrates offline actual-GUI runs, and `tools/audit_gui_output.py` audits generated native exports. The package manifest pins every shipped file and private runtime wheel. [VERIFICATION.md](VERIFICATION.md) describes the independent oracles and their limits.', '']
    report.write_text('\n'.join(lines))
    proof_files.add(report)
    proof_files.update(ROOT/p for p in ('docs/VERIFICATION.md','docs/PERFORMANCE.md','tests/requirement_ledger.json',
                                      'dist/build-manifest.json','dist/artifact.json','WORK_LOG.md'))
    identity={'schema_version':1,'archive_sha256':digest,'gui_runs':len(rows),'gui_assertions':assertions,
              'unit_tests':unit_count,'files':{str(p.relative_to(ROOT)):sha(p) for p in sorted(proof_files)}}
    index=ROOT/'evidence/final-artifacts.json'
    index.write_text(json.dumps(identity,indent=2))
    bundle=ROOT/'dist/InlayPlanner-Mac-verification.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(proof_files|{index}):z.write(path,'InlayPlanner-evidence/'+str(path.relative_to(ROOT)))
    print(json.dumps({'report':str(report),'bundle':str(bundle),'bundle_sha256':sha(bundle),
                      'gui_runs':len(rows),'gui_assertions':assertions,'unit_tests':unit_count},indent=2))


if __name__=='__main__':main()
