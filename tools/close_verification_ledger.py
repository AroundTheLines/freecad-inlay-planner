#!/usr/bin/env python3
"""Close the finite acceptance ledger only against explicit final receipts."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def read(p): return json.loads((ROOT/p).read_text())
def sha(p): return hashlib.sha256((ROOT/p).read_bytes()).hexdigest()

def main():
    mapping=read('tests/coverage_mapping.json'); prefix=mapping['artifact_prefix']
    ledger=read('tests/requirement_ledger.json'); artifact=read('dist/artifact.json')
    ledger['archive_sha256']=artifact['sha256']
    ledger['scope']='macOS arm64 FreeCAD 1.1.3; finite tested configurations; constant-section stock; Windows, physical cuts and whole-machine 4 GB qualification deferred'
    common=['artifacts/final-mac-82c512f1/unit-receipt.json','tests/oracles/test-runs/checkpoint-82c512f1.json']
    native=['artifacts/native-20260928-212241/report.json','tests/oracles/test-runs/export-native-20260928-212241.json']
    resource=['artifacts/final-resource-package-82c512f1/qualification.json','artifacts/final-resource-package-82c512f1/computational-equivalence.json']
    records={}
    for row in ledger['cases']:
        row['evidence']=[]; row['supporting_evidence']=common.copy()
        if row['id']=='E25':
            row.update(status='optional_unimplemented',required=False,remaining='Optional planning-task restoration is not shipped. Native CAM output is retained and editable without the add-on.')
            continue
        for name in mapping['rows'][row['id']]:
            relative=f'{prefix}/{name}/report.json'; report=read(relative)
            assert report.get('ok') and report.get('exit_code')==0 and report['archive_sha256']==artifact['sha256'] and report.get('offline_network_denied'),relative
            assert report['run_id']==report['result']['run_id'],relative
            evidence=[relative]
            folder=ROOT/prefix/name
            if (folder/'spec.json').exists() and (folder/'board.ngc').exists():
                audit=f'{prefix}/{name}/independent-audit.json'
                assert read(audit)['passed'],audit
                evidence.append(audit)
            if name.startswith('portable'):
                audit=f'{prefix}/{name}/independent-portable-audit.json'
                assert read(audit)['passed'],audit
                evidence.append(audit)
            row['evidence']+=evidence
            records[relative]={'run_id':report['run_id'],'artifact_sha256':sha(relative),'status':'passed','assertions':len(report['result']['checks'])}
        row['status']='passed'
        row['remaining']='No remaining Mac software gate within the recorded finite acceptance scope; see explicit qualifications.'
        if row['id'] in ('E04','E18','E23','E24','E27'):row['supporting_evidence']+=native
        if row['id']=='E04':row['qualification']='Existing face capture is limited to constant-thickness prisms. Irregular outlines and through holes are tested; taper and hidden recess are rejected.'
        if row['id']=='E18':row['qualification']='Repeated A→A→B→A order is covered by the native adapter fixture. The generator emits one operation per selected cutter per face; its reachable GUI cap states are independently tested.'
        if row['id'] in ('E23','E24','E27'):row['qualification']='Native commands remain editable without the extension. Six-decimal native feed rounding is reported independently; manual edits invalidate the prior geometry certificate. Unsuitable export options are detected by test oracles, not blocked by an extension exporter.'
        if row['id']=='E30':
            row['supporting_evidence']+=resource
            row['qualification']='Offline execution, process budgets and recoverable dense-case rejection are tested. This is not qualification of a complete 4 GB computer.'
    ledger['final_runs']=records
    ledger['historical_supporting_runs']=ledger.pop('supporting_runs',ledger.get('historical_supporting_runs',{}))
    ledger['supporting_evidence_policy']='Reused computational receipts are bound by exact source and dependency hashes; no rerun is implied. Historical failed receipts remain historical, never final passes.'
    (ROOT/'tests/requirement_ledger.json').write_text(json.dumps(ledger,indent=2)+'\n')
    print(json.dumps({'passed':sum(r['status']=='passed' for r in ledger['cases']),'optional_unimplemented':sum(r['status']=='optional_unimplemented' for r in ledger['cases']),'gui_runs':len(records)},indent=2))

if __name__=='__main__':main()
