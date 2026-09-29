#!/usr/bin/env python3
"""Measure real offline worker subprocesses; never infer a 4 GiB desktop claim."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PYTHON = Path('/Applications/FreeCAD.app/Contents/Resources/bin/python')
sys.path.insert(0, str(ROOT))
from inlay_planner.schema import default_spec, seal_spec

WORKER_SOURCES = ('worker.py', 'solver.py', 'geometry.py', 'motion.py', 'verify.py',
                  'motion_storage.py', 'schema.py', 'svg_input.py')


def source_hashes(addon=ROOT):
    return {name: hashlib.sha256((addon/'inlay_planner'/name).read_bytes()).hexdigest()
            for name in WORKER_SOURCES}


def worker_environment(fault=None):
    env = {key: os.environ[key] for key in ('HOME', 'USER', 'LOGNAME', 'LANG', 'TMPDIR') if key in os.environ}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', OPENBLAS_NUM_THREADS='1',
               OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    if fault:
        env.update(INLAY_TEST_MODE='1', INLAY_TEST_FAULT=fault)
    return env


def sample_process(command, out, *, timeout=180., env=None, offline=False):
    """External RSS includes native allocations and all visible descendants."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if offline:
        sandbox = Path('/usr/bin/sandbox-exec')
        if not sandbox.is_file():
            raise RuntimeError('A native network-denying sandbox is required for this offline audit')
        command = [str(sandbox), '-p', '(version 1)(allow default)(deny network*)', *command]
    started = time.monotonic()
    samples, errors = [], set()
    peak, peak_count = 0, 0
    timed_out = False
    with (out/'stdout.log').open('w') as stdout, (out/'stderr.log').open('w') as stderr:
        process = subprocess.Popen(command, cwd=out, env=env, stdout=stdout,
                                   stderr=stderr, start_new_session=True)
        while process.poll() is None:
            elapsed = time.monotonic()-started
            try:
                root = psutil.Process(process.pid)
                children = []
                try:
                    children = root.children(recursive=True)
                except (psutil.Error, OSError) as exc:
                    errors.add('Process-tree enumeration unavailable: '+str(exc))
                tree = [root, *children]
                rss, count = 0, 0
                for member in tree:
                    try:
                        rss += member.memory_info().rss
                        count += 1
                    except psutil.NoSuchProcess:
                        pass
                peak, peak_count = max(peak, rss), max(peak_count, count)
                if not samples or elapsed-samples[-1]['seconds'] >= .5:
                    samples.append({'seconds': round(elapsed, 3), 'rss_bytes': rss, 'process_count': count})
            except (psutil.Error, OSError) as exc:
                errors.add(str(exc))
            if elapsed > timeout:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(2.)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                break
            time.sleep(.05)
        code = process.wait()
    return {'command': command, 'exit_code': code, 'elapsed_s': time.monotonic()-started,
            'external_timeout': timed_out, 'peak_process_tree_rss': peak,
            'peak_process_count': peak_count, 'memory_samples': samples,
            'monitor_errors': sorted(errors), 'sampling_interval_s': .05,
            'offline_network_denied': offline}


def run_worker(request, out, *, addon=ROOT, python=BUNDLED_PYTHON, fault=None,
               timeout=180., raw_request=None, offline=True):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    request = dict(request, schema_version=request.get('schema_version', 1),
                   run_id=request.get('run_id', uuid.uuid4().hex))
    request_path, result_path = out/'request.json', out/'result.json'
    request_path.write_text(raw_request if raw_request is not None else json.dumps(request, allow_nan=False))
    before = source_hashes(addon)
    record = sample_process([str(python), '-I', str(addon/'inlay_planner'/'worker.py'),
                             '--request', str(request_path), '--result', str(result_path)],
                            out, timeout=timeout, env=worker_environment(fault), offline=offline)
    record.update(run_id=request['run_id'], fault=fault, source_hashes=before,
                  source_stable=before == source_hashes(addon),
                  request_sha256=hashlib.sha256(request_path.read_bytes()).hexdigest(),
                  result_exists=result_path.is_file(), partial_exists=result_path.with_suffix('.tmp').exists())
    record['stdout'] = (out/'stdout.log').read_text(errors='replace')[-1024*1024:]
    record['stderr'] = (out/'stderr.log').read_text(errors='replace')[-65536:]
    record['payload'] = None
    if result_path.is_file():
        record['result_bytes'] = result_path.stat().st_size
        if record['result_bytes'] <= 128*1024*1024:
            try:
                record['payload'] = json.loads(result_path.read_text())
            except (ValueError, UnicodeError) as exc:
                record['parse_error'] = str(exc)
        else:
            record['parse_error'] = 'The result exceeds the native 128 MiB review-data budget'
    return record


def small_spec(style='flat'):
    spec = default_spec()
    for face in spec['faces'].values():
        face.update(stock=[12., 12., 6.], bounds=[0., 0., 12., 12.], clearance_height=2.)
    spec['faces']['plug']['design_to_face'] = [-1., 0., 0., 1., 12., 0.]
    spec['resolution'] = {'xy': .05, 'z': .05}
    spec['fit'].update(value=1., glue_gap=.2, side_clearance=.2, backing_clearance=.5)
    spec['artwork']['regions'] = [{'id': 'circle', 'polygons': [{'outer': [
        [6+3*math.cos(i*math.tau/48), 6+3*math.sin(i*math.tau/48)] for i in range(48)], 'holes': []}]}]
    spec['style'] = style
    spec['tools'] = [{'id': 't', 'number': 1, 'name': 'Resource fixture cutter', 'shape': style,
                      'diameter': 2., 'cutting_length': 5., 'corner_radius': .25,
                      'included_angle_deg': 90., 'tip_diameter': .1, 'max_stepdown': .5,
                      'stepover': .8, 'feed': 500., 'plunge_feed': 200., 'travel_feed': 1000., 'spindle': 10000.}]
    spec['selected_tool_ids'] = ['t']
    return spec


def large_spec():
    spec = small_spec()
    for face in spec['faces'].values():
        face.update(stock=[609.6, 609.6, 19.05], bounds=[0., 0., 609.6, 609.6])
    spec['faces']['plug']['design_to_face'] = [-1., 0., 0., 1., 609.6, 0.]
    spec['artwork']['regions'] = [{'id': 'large-medallion', 'polygons': [{'outer': [
        [234.8, 234.8], [374.8, 234.8], [374.8, 374.8], [234.8, 374.8]], 'holes': []}]}]
    spec['resolution'] = {'xy': .01, 'z': .01}
    spec['fit'].update(value=3., glue_gap=.2, side_clearance=.15, backing_clearance=1.)
    spec['tools'][0].update(diameter=12.7, cutting_length=20., max_stepdown=1.5, stepover=4.)
    return spec


def corpus_spec(imported, canvas):
    spec = small_spec()
    width, height = canvas
    for face in spec['faces'].values():
        face.update(stock=[width, height, 6.], bounds=[0., 0., width, height])
    spec['faces']['plug']['design_to_face'] = [-1., 0., 0., 1., width, 0.]
    spec['artwork']['regions'] = imported['regions']
    spec['fit'].update(value=.5, glue_gap=.1, side_clearance=.1, backing_clearance=.3)
    spec['tools'][0].update(diameter=1., stepover=.4)
    return spec


def summarize(record):
    payload = record.get('payload') or {}
    result = payload.get('result', {})
    return {k: v for k, v in record.items() if k not in ('payload', 'stdout', 'stderr')} | {
        'ok': payload.get('ok'), 'result_status': result.get('status'),
        'diagnostics': result.get('diagnostics', []), 'worker_resource_stats': result.get('resource_stats', {}),
        'verification_status': result.get('verification', {}).get('status'),
        'motion_count': sum(len(o.get('moves', [])) for o in result.get('operations', [])),
        'error': payload.get('error'), 'progress_messages': sum('"type": "progress"' in line for line in record.get('stdout', '').splitlines())}


def assess(record, expected_blocked=None):
    payload = record.get('payload') or {}
    result = payload.get('result', {})
    issues = []
    if record['external_timeout']:
        issues.append('The external qualification time budget expired')
    if record['monitor_errors']:
        issues.append('Full external process-tree monitoring was unavailable')
    if not record['source_stable']:
        issues.append('Worker sources changed during this case')
    if record['exit_code'] or not payload.get('ok'):
        issues.append(payload.get('error', 'Worker exited without a successful result'))
    if payload.get('run_id') != record['run_id']:
        issues.append('Result run identity mismatch')
    if record['peak_process_tree_rss'] > 768*1024*1024:
        issues.append('Observed process-tree RSS exceeded the worker ceiling')
    if expected_blocked:
        codes = {d['code'] for d in result.get('diagnostics', [])}
        if result.get('status') != 'blocked' or not codes.intersection(expected_blocked):
            issues.append('Expected explicit input/tool conflict was not reported')
    elif result.get('status') not in ('ready', 'needs_acceptance') or result.get('verification', {}).get('status') != 'passed':
        issues.append('Positive qualification geometry did not complete with passing verification')
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--addon', type=Path, default=ROOT)
    parser.add_argument('--python', type=Path, default=BUNDLED_PYTHON)
    parser.add_argument('--case', action='append', help='small, v, bull, large, dense, or an example stem; repeatable')
    parser.add_argument('--timeout', type=float, default=240.)
    options = parser.parse_args()
    addon = options.addon.resolve()
    out = (options.out or ROOT/'artifacts'/('resource-'+time.strftime('%Y%m%d-%H%M%S'))).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((addon/'examples'/'fixture_manifest.json').read_text())['fixtures']
    selected = options.case or ['small', 'v', 'bull', 'large', *[Path(f['filename']).stem for f in manifest]]
    selected = ['dense_decorative_field' if name == 'dense' else name for name in selected]
    report = {'schema_version': 1, 'run_id': uuid.uuid4().hex, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'python_executable': str(options.python), 'addon_root': str(addon),
              'host': {'platform': platform.platform(), 'machine': platform.machine(), 'total_ram_bytes': psutil.virtual_memory().total},
              'scope': 'Externally sampled actual standalone worker process trees with network denied; GUI/native Apply/save/reopen memory is outside this report.',
              'whole_machine_4_gib_validated': False, 'gui_aggregate_2_gib_validated': False,
              'budgets': {'worker_target_bytes': 512*1024*1024, 'worker_ceiling_bytes': 768*1024*1024,
                          'result_bytes': 128*1024*1024}, 'cases': [], 'ok': True}
    for name in selected:
        case_dir = out/name
        case_dir.mkdir()
        expected_blocked = None
        try:
            if name in ('small', 'v', 'bull', 'large'):
                spec = large_spec() if name == 'large' else small_spec('flat' if name == 'small' else name)
                fixture = {'generated': name, 'components': len(spec['artwork']['regions'])}
            else:
                fixture = next(f for f in manifest if Path(f['filename']).stem == name)
                source = addon/'examples'/fixture['filename']
                imported = run_worker({'action': 'import_svg', 'text': source.read_text(), 'tolerance': .0005},
                                      case_dir/'import', addon=addon, python=options.python, timeout=options.timeout)
                (case_dir/'import-audit.json').write_text(json.dumps(summarize(imported), indent=2))
                if imported['exit_code'] or not (imported['payload'] or {}).get('ok'):
                    raise RuntimeError('Actual worker SVG import failed: '+str((imported['payload'] or {}).get('error')))
                geometry = imported['payload']['result']
                spec = corpus_spec(geometry, fixture['canvas_mm'])
                if name == 'fine_detail_ladder':
                    expected_blocked = {'TOOL_TOO_LARGE', 'NO_MATING_MATERIAL'}
                elif name == 'boundary_crossing_ornament':
                    expected_blocked = {'ARTWORK_OUT_OF_BOUNDS'}
                polygons = [p for r in geometry['regions'] for p in r['polygons']]
                fixture = dict(fixture, measured_components=len(polygons),
                               measured_boundary_vertices=sum(len(p['outer'])-int(p['outer'][0] == p['outer'][-1]) for p in polygons),
                               svg_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                del imported, geometry
            sealed = seal_spec(spec)
            record = run_worker({'action': 'solve', 'spec': sealed}, case_dir/'solve', addon=addon,
                                python=options.python, timeout=options.timeout)
            summary = summarize(record)
            issues = assess(record, expected_blocked)
            payload = record.get('payload') or {}
            if payload.get('ok') and payload.get('result', {}).get('input_hash') != sealed['input_hash']:
                issues.append('Result input hash does not match the exact request')
            summary.update(name=name, fixture=fixture, expected_blocked_codes=sorted(expected_blocked or []),
                           issues=issues, status='failed' if issues else 'passed')
            report['cases'].append(summary)
            report['ok'] = report['ok'] and not issues
            print(json.dumps({'case': name, 'status': summary['status'], 'result': summary['result_status'],
                              'seconds': round(summary['elapsed_s'], 3), 'peak_rss': summary['peak_process_tree_rss'],
                              'issues': issues}), flush=True)
            del record
        except BaseException as exc:
            report['cases'].append({'name': name, 'status': 'failed', 'error': str(exc)})
            report['ok'] = False
        (out/'report.json').write_text(json.dumps(report, indent=2))
    print('Evidence:', out/'report.json')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
