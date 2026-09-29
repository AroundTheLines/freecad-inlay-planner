"""Independent modal replay of a completed extension-absent native GUI run."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT/'tests'))
from oracles.audit_export import compare, expected_from_snapshot
from oracles.gcode import parse_program


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original_contract(run, report, snapshots, paths):
    """Separate original geometry preservation from documented native feed drift."""
    source = Path(report['document']).parent
    spec_path,plan_path = source/'spec.json',source/'plan.json'
    if not spec_path.exists() or not plan_path.exists():
        return {'available':False,'errors':['Original accepted spec/plan are unavailable for restored-coordinate comparison']}
    paths.extend((spec_path,plan_path))
    spec,plan = json.loads(spec_path.read_text()),json.loads(plan_path.read_text())
    expected = {o['id']:o for o in plan['operations']}
    tools = {t['id']:t for t in spec['tools']}
    errors,stages = [],[]
    limit,feed_roundoff = Decimal('1e-9'),Decimal('1e-9')
    serialization_feed_bound = Decimal('0.00003')  # 0.5*10^-6 native mm/s *60.
    for filename,snapshot in snapshots.items():
        deviations,edits = {},[]
        maximum_endpoint_error = Decimal(0)
        maximum_lattice_error = Decimal(0)
        for face,operations in snapshot.items():
            ids = [op['id'] for op in operations]
            if set(ids) != {op['id'] for op in plan['operations'] if op['face']==face} or len(ids)!=len(set(ids)):
                errors.append(filename+': original operation identities changed')
            for operation in operations:
                if operation['id'] not in expected: continue
                planned = expected[operation['id']]
                motions = expected_from_snapshot([operation])
                wanted = [planned['moves'][0]]+planned['moves']
                if len(motions) != len(wanted):
                    errors.append(filename+': original motion count changed for '+operation['id'])
                tool = tools[planned['tool_id']]
                if operation['tool'] != tool['number'] or abs(operation['diameter']-tool['diameter']) > 1e-9:
                    errors.append(filename+': original physical cutter changed for '+operation['id'])
                for index,(actual,move) in enumerate(zip(motions,wanted)):
                    for axis,coordinate in zip('xyz',actual['end']):
                        if index==0 and axis in 'xy':
                            if coordinate is not None: errors.append(filename+': initial unknown XY was fabricated')
                            continue
                        target = Decimal(str(move[axis]))
                        if coordinate is None:
                            errors.append(filename+': original known coordinate became unknown'); continue
                        delta = abs(coordinate-target)
                        maximum_endpoint_error = max(maximum_endpoint_error,delta)
                        if delta > limit: errors.append(filename+': original accepted coordinate changed for '+operation['id'])
                        quantum = Decimal(str(spec['resolution']['z' if axis=='z' else 'xy']))
                        step_error = abs(coordinate/quantum-(coordinate/quantum).to_integral_value())
                        maximum_lattice_error = max(maximum_lattice_error,step_error)
                        if step_error > Decimal('1e-8'):
                            errors.append(filename+': restored coordinate leaves the original '+axis+' lattice')
                    if actual['kind'] != move['kind']:
                        errors.append(filename+': original movement kind changed')
                    if actual['kind'] != 'feed': continue
                    target = Decimal(str(move['feed_mm_min']))
                    feed = actual['feed']
                    if feed is None:
                        errors.append(filename+': restored feed is missing'); continue
                    delta = feed-target
                    if abs(delta) <= feed_roundoff: continue
                    if face=='board' and index==0 and feed==540:
                        edits.append({'operation':operation['id'],'native_word':'F9',
                                      'original_mm_min':str(target),'edited_mm_min':'540',
                                      'scope':'Deliberate native GUI edit of the initial clearance-only Z establishment'})
                    else:
                        key = (str(target),str(feed),str(delta))
                        deviations[key] = deviations.get(key,0)+1
                        if abs(delta) > serialization_feed_bound+feed_roundoff:
                            errors.append(filename+': unexplained feed change exceeds six-decimal native serialization bound')
        if len(edits) != 1:
            errors.append(filename+': expected one deliberate native F9 edit was not isolated')
        stages.append({'snapshot':filename,'maximum_endpoint_error_mm':str(maximum_endpoint_error),
                       'maximum_lattice_error_steps':str(maximum_lattice_error),'intentional_feed_edits':edits,
                       'native_serialization_feed_drift':[{'original_mm_min':a,'restored_mm_min':b,
                           'difference_mm_min':c,'motion_count':count} for (a,b,c),count in deviations.items()],
                       'all_unedited_feeds_exact_within_roundoff':not deviations})
    return {'available':True,'source_spec':str(spec_path),'source_plan':str(plan_path),
            'original_resolution_mm':spec['resolution'],'errors':errors,'stages':stages,
            'six_decimal_feed_rounding_bound_mm_min':str(serialization_feed_bound),
            'meaning':'Coordinate preservation and lattice are tested against the original accepted plan by operation identity, independently of a native reorder. Changed native F9 is deliberate. Other measured native feed drift is disclosed, not treated as exact preservation of the original entered speed or as a still-valid original machining certificate.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    sources = [Path(__file__), PROJECT/'tests/oracles/audit_export.py', PROJECT/'tests/oracles/gcode.py']
    before = {str(p.relative_to(PROJECT)): sha(p) for p in sources}
    report = json.loads((run/'report.json').read_text())
    errors = []
    if not report.get('ok') or report.get('exit_code') != 0 or report.get('addon_enabled') is not False:
        errors.append('A completed normal-exit extension-absent GUI run is required')
    checks,snapshots = [],{}
    paths = [run/'report.json',run/'result.json']
    controls = {'portable_precision':'board_metric_0.ngc','portable_mode':'board_g64.ngc',
                'portable_inch':'board_inch_9.ngc'}
    control = controls.get(report.get('case'))
    details = json.loads((run/'result.json').read_text())
    if details != report.get('result'):
        errors.append('Runner and application completion receipts disagree')
    if control:
        if details.get('negative_control') != control or details.get('expected_independent_replay_pass') is not False:
            errors.append('Fresh-process negative control has an unexpected declaration')
        names = [control]
        restored = {'available':False,'errors':[],
                    'meaning':'This separate fresh-process case tests deliberately unsuitable native export settings against its actual native snapshot; it makes no edited/reopened original-contract claim.'}
    else:
        for filename,suffix in (('native-snapshot.json',''),('reopened-native-snapshot.json','_reopened')):
            source = run/filename
            snapshot = json.loads(source.read_text()); paths.append(source)
            snapshots[filename] = snapshot
            for face in ('board','plug'):
                path = run/(face+suffix+'.ngc'); paths.append(path)
                result = compare(snapshot[face],path.read_text())
                checks.append(dict(result, source=filename, file=path.name, expected_pass=True))
        names = details.get('negative_controls')
        if not isinstance(names,list) or any(name not in controls.values() for name in names) or len(set(names)) != len(names):
            errors.append('Portable run must explicitly declare its distinct negative-control files, including an empty list when run separately')
            names = []
        restored = original_contract(run,report,snapshots,paths)
        errors.extend(restored['errors'])
    snapshot_path = run/'native-snapshot.json'
    snapshot = json.loads(snapshot_path.read_text())
    if snapshot_path not in paths: paths.append(snapshot_path)
    for name in names:
        path = run/name; paths.append(path)
        program = path.read_text()
        result = compare(snapshot['board'],program)
        result['units_seen'] = parse_program(program).units_seen
        checks.append(dict(result,source='native-snapshot.json',file=name,expected_pass=False))
        reason = ('exact-path mode is absent' if name=='board_g64.ngc' else ' expected ')
        if not any(reason in message for message in result['errors']):
            errors.append('Negative export did not reveal its intended mode/coordinate defect: '+name)
        if name=='board_inch_9.ngc' and 'inch' not in result['units_seen']:
            errors.append('Inch control did not contain an actual executable G20 unit selection')
    errors.extend('Unexpected replay outcome: '+c['file'] for c in checks if c['passed'] != c['expected_pass'])
    stable = before == {str(p.relative_to(PROJECT)):sha(p) for p in sources}
    if not stable: errors.append('Independent audit source changed during execution')
    notes = []
    for name in ('stdout.log','freecad.log'):
        path = run/name
        if path.exists():
            paths.append(path)
            count = path.read_text(errors='replace').count('has no attribute \'setModel\'')
            if count:
                notes.append({'file':name,'native_job_editor_setModel_messages':count,
                              'meaning':'Native FreeCAD editor logged this ancillary error; this receipt proves resulting command/export semantics, not exception-free UI'})
    result = {'schema_version':1,'completed_utc':datetime.now(timezone.utc).isoformat(),
              'gui_run_id':report['run_id'],'gui_run':str(run),'archive_sha256':report['archive_sha256'],
              'audit_kind':'fresh_process_native_export_negative' if control else 'native_edit_save_reopen',
              'negative_controls_checked':names,
              'passed':not errors,'errors':errors,'checks':checks,'source_snapshot_stable':stable,
              'oracle_sha256':before,'artifact_sha256':{str(p):sha(p) for p in paths},
              'original_accepted_contract':restored,
              'application_log_notes':notes,
              'scope':('Independent native-command to LinuxCNC modal replay of one explicitly declared unsuitable setting in a completed fresh native GUI process.' if control else
                       'Independent native-command to LinuxCNC modal replay before/after extension-absent GUI resave/reopen, plus only the explicitly declared negative controls. An empty list makes no negative-control claim.')+
                      ' Manual edits invalidate the original geometry certificate; this receipt does not certify recut geometry or an exception-free native UI.'}
    path = run/'independent-portable-audit.json'
    path.write_text(json.dumps(result,indent=2))
    print(json.dumps({'passed':result['passed'],'errors':errors,'checks':[
        {'file':c['file'],'passed':c['passed'],'expected_pass':c['expected_pass'],'errors':len(c['errors'])} for c in checks],
        'application_log_notes':notes,'evidence':str(path)}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
