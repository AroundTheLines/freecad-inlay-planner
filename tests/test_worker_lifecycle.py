"""Real bundled-Python worker and actual Qt Calculation lifecycle tests.

The Qt harness is a headless FreeCAD controller, not a GUI/Apply substitute.
Run this module's pytest cases outside macOS process-enumeration restrictions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

ROOT = Path(os.environ.get('INLAY_AUDIT_ADDON_ROOT', Path(__file__).resolve().parents[1])).resolve()
BUNDLED = Path('/Applications/FreeCAD.app/Contents/Resources/bin/python')
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12mm" height="12mm"><rect x="3" y="3" width="6" height="6"/></svg>'
IMPORT_REQUEST = {'schema_version': 1, 'action': 'import_svg', 'text': SVG, 'tolerance': .001}


def qt_harness(case, out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(BUNDLED.parents[1]/'lib'))
    import FreeCAD as App
    from PySide6 import QtCore
    # FreeCAD initialization prepends its discovered Mod directories. Select
    # the exact audited package afterward, within the isolated test profile.
    sys.path.insert(0, str(ROOT))
    from inlay_planner.process import Calculation

    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    events, directories, pids = [], [], []
    report = {'case': case, 'ok': False, 'scope': 'Actual Qt Calculation and worker; no GUI document/Apply assertion',
              'addon_root': str(ROOT),
              'freecad_user_data': str(Path(App.getUserAppDataDir()).resolve()),
              'controller_module': str(Path(sys.modules['inlay_planner.process'].__file__).resolve())}
    assert report['freecad_user_data'] == str(Path(os.environ['FREECAD_USER_DATA']).resolve()), report
    assert report['controller_module'] == str(ROOT/'inlay_planner'/'process.py'), report
    sentinel = out/'previous reviewed output.txt'
    sentinel.write_text('previous approved native output is outside calculation directories')

    def attach(calculation, owner):
        calculation.completed.connect(lambda result: events.append({'kind': 'completed', 'owner': owner, 'result': result}))
        calculation.failed.connect(lambda message: events.append({'kind': 'failed', 'owner': owner, 'message': message}))
        calculation.stopped.connect(lambda: events.append({'kind': 'stopped', 'owner': owner}))

    def wait(predicate, seconds=8.):
        deadline = time.monotonic()+seconds
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError('Actual Qt lifecycle event did not complete')
            app.processEvents()
            time.sleep(.01)
        app.processEvents()

    def start(calculation):
        calculation.start(dict(IMPORT_REQUEST))
        if calculation.directory:
            directories.append(str(calculation.directory))
        if calculation.process:
            wait(lambda: calculation.process is None or calculation.process.state() != QtCore.QProcess.Starting)
            if calculation.process:
                pids.append(int(calculation.process.processId()))

    first = Calculation()
    attach(first, 'first')
    second = None
    original_home = App.getHomePath
    try:
        fault = case if case in ('crash', 'dependency', 'memory', 'partial', 'version') else None
        if case in ('cancel', 'timeout', 'nonresponsive', 'double_start', 'cross_instance'):
            fault = 'timeout'
        os.environ['INLAY_TEST_MODE'] = '1'
        os.environ['INLAY_TEST_TIMEOUT'] = '.15' if case == 'timeout' else '60'
        if fault:
            os.environ['INLAY_TEST_FAULT'] = fault
        else:
            os.environ.pop('INLAY_TEST_FAULT', None)
        if case == 'failed_start':
            fake_home = out/'invalid installation'
            (fake_home/'bin').mkdir(parents=True)
            (fake_home/'bin'/'python').write_text('not an executable')
            (fake_home/'bin'/'python').chmod(0o600)
            App.getHomePath = lambda: str(fake_home)
        start(first)
        App.getHomePath = original_home
        if case == 'stale_id':
            first.run_id = 'superseded-run-identity'
        if case in ('double_start', 'cross_instance'):
            target = first
            if case == 'cross_instance':
                second = Calculation()
                attach(second, 'second')
                target = second
            try:
                target.start(dict(IMPORT_REQUEST))
                raise AssertionError('A second worker was allowed while one was running')
            except RuntimeError as exc:
                report['concurrent_start_rejected'] = str(exc)
        if case in ('cancel', 'nonresponsive', 'double_start', 'cross_instance'):
            if case == 'nonresponsive':
                os.kill(int(first.process.processId()), signal.SIGSTOP)
            first.cancel()
        wait(lambda: not first.busy)
        initial = list(events)
        assert all(not Path(path).exists() for path in directories), 'Calculation left temporary files behind'
        if case == 'success':
            assert [event['kind'] for event in initial] == ['completed'], initial
        elif case in ('cancel', 'nonresponsive', 'double_start', 'cross_instance'):
            assert any(event['kind'] == 'stopped' for event in initial), initial
            assert not any(event['kind'] == 'completed' for event in initial), initial
        elif case == 'timeout':
            assert any(event['kind'] == 'failed' and 'time budget' in event['message'] for event in initial), initial
            assert not any(event['kind'] == 'completed' for event in initial), initial
        else:
            assert any(event['kind'] == 'failed' for event in initial), initial
            assert not any(event['kind'] == 'completed' for event in initial), initial
        if case == 'stale_id':
            assert 'another run' in next(event['message'] for event in initial if event['kind'] == 'failed')
        if case == 'version':
            assert 'incompatible version' in next(event['message'] for event in initial if event['kind'] == 'failed')

        # Retry through the same production object (or the formerly rejected
        # second object), proving application-wide admission was released.
        os.environ.pop('INLAY_TEST_FAULT', None)
        os.environ['INLAY_TEST_TIMEOUT'] = '60'
        retry = second or first
        previous_event_count = len(events)
        start(retry)
        wait(lambda: not retry.busy)
        later = events[previous_event_count:]
        assert [event['kind'] for event in later] == ['completed'], later
        assert later[0]['result']['regions']
        assert all(not Path(path).exists() for path in directories)
        assert sentinel.read_text() == 'previous approved native output is outside calculation directories'
        report.update(ok=True, initial_events=[{k: v for k, v in event.items() if k != 'result'} for event in initial],
                      retry_completed=True, temporary_directories_removed=True, worker_pids=pids,
                      freecad_version=App.Version(), qt_version=QtCore.qVersion())
    except BaseException:
        report['error'] = traceback.format_exc()
        if first.busy:
            first.cancel()
        if second and second.busy:
            second.cancel()
        try:
            wait(lambda: not first.busy and not (second and second.busy), 3.)
        except BaseException:
            pass
    finally:
        App.getHomePath = original_home
        (out/'harness-result.json').write_text(json.dumps(report, indent=2))
    return 0 if report['ok'] else 1


def memory_guard_probe():
    # Exercise the shipped guard itself under actual RSS pressure, distinct
    # from the worker's lightweight injected MemoryError branch.
    import threading
    sys.path.insert(0, str(ROOT))
    from inlay_planner.worker import memory_guard
    done = threading.Event()
    threading.Thread(target=memory_guard, args=(done,), daemon=True).start()
    held = []
    for _ in range(110):
        held.append(bytearray(8*1024*1024))
        time.sleep(.035)
    done.set()
    raise AssertionError('The production RSS guard did not terminate the pressured subprocess')


if __name__ == '__main__':
    if sys.argv[1] == '--memory-guard-probe':
        memory_guard_probe()
    elif sys.argv[1] == '--qt-harness':
        raise SystemExit(qt_harness(sys.argv[2], sys.argv[3]))
    else:
        raise SystemExit('Unknown lifecycle harness command')


import pytest
import psutil

from tools.run_resource_audit import run_worker, sample_process, small_spec, worker_environment
from inlay_planner.schema import seal_spec

pytestmark = pytest.mark.skipif(sys.platform != 'darwin' or not BUNDLED.is_file(),
                                reason='Requires the installed macOS FreeCAD runtime; not a passing platform result')


def harness_environment(out):
    # User-installed workbenches must neither shadow the audited package nor
    # initialize against the user's preferences during a headless test.
    env = dict(worker_environment(), INLAY_AUDIT_ADDON_ROOT=str(ROOT))
    for suffix in ('HOME', 'DATA', 'TEMP'):
        profile = Path(out).resolve()/'freecad-profile'/suffix.lower()
        profile.mkdir(parents=True, exist_ok=True)
        env['FREECAD_USER_'+suffix] = str(profile)
    return env


@pytest.mark.parametrize('case', ['success', 'crash', 'dependency', 'memory', 'partial', 'version',
                                'stale_id', 'cancel', 'timeout', 'nonresponsive', 'failed_start',
                                'double_start', 'cross_instance'])
def test_actual_qt_controller_rejects_failures_cleans_up_and_retries(case, tmp_path):
    out = tmp_path/('worker '+case+' 木')
    record = sample_process([str(BUNDLED), '-I', str(Path(__file__).resolve()), '--qt-harness', case, str(out)],
                            out, timeout=20., env=harness_environment(out), offline=True)
    result_file = out/'harness-result.json'
    assert result_file.exists(), (record, (out/'stderr.log').read_text())
    result = json.loads(result_file.read_text())
    assert record['exit_code'] == 0, result
    assert result['freecad_user_data'] == str((out/'freecad-profile'/'data').resolve())
    assert result['controller_module'] == str(ROOT/'inlay_planner'/'process.py')
    assert result['ok'], result
    assert result['retry_completed']
    assert not record['monitor_errors'], record['monitor_errors']
    assert record['peak_process_count'] >= 1
    assert not any(psutil.pid_exists(pid) for pid in result['worker_pids'] if pid), 'A calculation worker survived cleanup'
    record['all_worker_pids_exited'] = True
    (out/'audit.json').write_text(json.dumps(record, indent=2))


def test_actual_worker_solve_uses_review_hash_and_returns_progress(tmp_path):
    spec = seal_spec(small_spec())
    record = run_worker({'action': 'solve', 'spec': spec}, tmp_path/'solve 木', addon=ROOT)
    assert record['exit_code'] == 0, record['stderr']
    assert record['payload']['run_id'] == record['run_id']
    result = record['payload']['result']
    assert result['input_hash'] == spec['input_hash']
    assert result['status'] == 'needs_acceptance'
    assert result['verification']['status'] == 'passed'
    assert '"type": "progress"' in record['stdout']
    assert record['offline_network_denied']
    assert not record['partial_exists']


@pytest.mark.parametrize('bad_request', ['json', 'schema', 'hash', 'nonfinite', 'action'])
def test_actual_worker_rejects_malformed_or_stale_inputs(bad_request, tmp_path):
    request = dict(IMPORT_REQUEST)
    raw = None
    if bad_request == 'json':
        raw = '{"incomplete":'
    elif bad_request == 'schema':
        request['schema_version'] = 999
    elif bad_request == 'hash':
        spec = seal_spec(small_spec())
        spec['fit']['value'] = 2.
        request = {'action': 'solve', 'spec': spec}
    elif bad_request == 'nonfinite':
        raw = '{"schema_version":1,"action":"import_svg","tolerance":NaN}'
    elif bad_request == 'action':
        request['action'] = 'unrecognized'
    record = run_worker(request, tmp_path/bad_request, raw_request=raw, addon=ROOT)
    assert record['exit_code'] != 0
    assert record['payload'] and not record['payload']['ok']
    assert record['payload']['error']
    assert 'result' not in record['payload']
    assert not record['partial_exists']


def test_interrupted_atomic_write_never_exposes_result(tmp_path):
    record = run_worker(IMPORT_REQUEST, tmp_path/'partial', fault='partial', addon=ROOT)
    assert record['exit_code'] == 75
    assert not record['result_exists']
    assert record['partial_exists']
    assert (tmp_path/'partial'/'result.tmp').read_text() == '{"incomplete":'


def test_hash_fault_is_observable_at_downstream_review_boundary(tmp_path):
    spec = seal_spec(small_spec())
    record = run_worker({'action': 'solve', 'spec': spec}, tmp_path/'stale-output', fault='hash', addon=ROOT)
    assert record['exit_code'] == 0
    assert record['payload']['result']['input_hash'] != spec['input_hash']
    # The Task panel/native Apply gate owns rejection; this test does not
    # mislabel standalone envelope generation as GUI rejection evidence.


def test_production_memory_guard_trips_under_measured_real_rss_pressure(tmp_path):
    out = tmp_path/'rss-guard'
    record = sample_process([str(BUNDLED), '-I', str(Path(__file__).resolve()), '--memory-guard-probe'],
                            out, timeout=10., env=harness_environment(out), offline=True)
    assert record['exit_code'] == 74, (record, (out/'stderr.log').read_text())
    assert '768 MiB memory budget' in (out/'stdout.log').read_text()
    assert record['peak_process_tree_rss'] >= 768*1024*1024
    assert record['peak_process_tree_rss'] < 896*1024*1024
    assert not record['monitor_errors']
    (out/'audit.json').write_text(json.dumps(record, indent=2))
