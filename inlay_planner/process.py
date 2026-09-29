"""Nonblocking, single-worker lifetime with revision-safe result delivery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import FreeCAD as App

from .qt_compat import QtCore

_active_calculation = None


class Calculation(QtCore.QObject):
    progress = QtCore.Signal(dict)
    completed = QtCore.Signal(dict)
    failed = QtCore.Signal(str)
    stopped = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.directory = None
        self.run_id = None
        self.cancelled = False
        self.stop_reason = None
        self.output = bytearray()
        self.errors = bytearray()
        self.started = 0.
        self.timeout = QtCore.QTimer(self)
        self.timeout.setInterval(1000)
        self.timeout.timeout.connect(self._check_timeout)
        self.last_payload = None

    @property
    def busy(self):
        return self.process is not None

    def start(self, request):
        global _active_calculation
        if self.busy:
            raise RuntimeError('A calculation is already running. Cancel it before recalculating.')
        if _active_calculation is not None and _active_calculation.busy:
            raise RuntimeError('Another inlay calculation is running. Wait for it or cancel it before starting this one.')
        self.run_id = uuid.uuid4().hex
        self.directory = Path(tempfile.mkdtemp(prefix='freecad-inlay-'))
        request = dict(request, run_id=self.run_id, schema_version=1)
        request_file = self.directory/'request.json'
        try:
            request_file.write_text(json.dumps(request, allow_nan=False))
        except BaseException:
            shutil.rmtree(self.directory)
            self.directory = None
            raise
        binary = Path(App.getHomePath())/'bin'/'python'
        if not binary.is_file():
            self._cleanup()
            raise RuntimeError('This FreeCAD bundle has no Python calculation runtime.')
        self.cancelled = False
        self.stop_reason = None
        self.last_payload = None
        self.output.clear()
        self.errors.clear()
        self.process = QtCore.QProcess(self)
        _active_calculation = self
        environment=QtCore.QProcessEnvironment.systemEnvironment()
        for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
            environment.insert(key,'1')
        self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.started = time.monotonic()
        self.timeout.start()
        script = Path(__file__).with_name('worker.py')
        self.process.start(str(binary), ['-I', str(script), '--request', str(request_file),
                                         '--result', str(self.directory/'result.json')])

    def _read_output(self):
        if not self.process:
            return
        self.output.extend(bytes(self.process.readAllStandardOutput()))
        if len(self.output) > 1024*1024:
            self.cancel('Calculation returned an oversized status message.')
            return
        while b'\n' in self.output:
            line, _, rest = self.output.partition(b'\n')
            self.output = bytearray(rest)
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if value.get('type') == 'progress':
                p = value.get('value', {})
                self.progress.emit(p if isinstance(p, dict) else {'message': str(p)})
            elif value.get('type') == 'error':
                self.errors.extend(str(value.get('message','')).encode())

    def _read_error(self):
        if self.process:
            self.errors.extend(bytes(self.process.readAllStandardError()))
            if len(self.errors) > 65536:
                del self.errors[:-65536]

    def _process_error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            message = self.process.errorString()
            self._cleanup()
            self.failed.emit('Could not start the calculation process: '+message)

    def _finished(self, code, exit_status):
        if not self.process:
            return
        self._read_output()
        self._read_error()
        directory, cancelled = self.directory, self.cancelled
        result = None
        error = None
        if not cancelled:
            try:
                path = directory/'result.json'
                if not path.is_file():
                    details = self.errors.decode(errors='replace')[-4000:]
                    raise ValueError('Calculation ended without a complete result. '+details)
                if path.stat().st_size > 128*1024*1024:
                    raise ValueError('Result exceeds the 128 MiB review-data budget. Reduce artwork complexity.')
                payload = json.loads(path.read_text())
                self.last_payload = payload
                if payload.get('run_id') != self.run_id:
                    raise ValueError('Calculation result belongs to another run; recalculate.')
                if not payload.get('ok'):
                    raise ValueError(payload.get('error', 'Calculation failed.'))
                if code != 0:
                    raise ValueError('Calculation process failed after writing its result.')
                result = payload['result']
                if result.get('schema_version') != 1:
                    raise ValueError('Calculation result has an incompatible version; recalculate.')
            except BaseException as exc:
                error = str(exc)
        self._cleanup()
        if cancelled:
            if self.stop_reason:
                self.failed.emit(self.stop_reason)
            else:
                self.stopped.emit()
        elif error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)

    def _cleanup(self):
        global _active_calculation
        self.timeout.stop()
        if self.process:
            self.process.deleteLater()
        self.process = None
        if self.directory and self.directory.is_dir():
            shutil.rmtree(self.directory)
        self.directory = None
        if _active_calculation is self:
            _active_calculation = None

    def cancel(self, reason=None):
        if not self.process:
            return
        self.cancelled = True
        self.stop_reason = self.stop_reason or reason
        process = self.process
        process.terminate()
        def force():
            if self.process is process and process.state() != QtCore.QProcess.NotRunning:
                process.kill()
        QtCore.QTimer.singleShot(1500, force)

    def _check_timeout(self):
        limit = 1800
        if os.environ.get('INLAY_TEST_MODE') == '1':
            limit = float(os.environ.get('INLAY_TEST_TIMEOUT', limit))
        if self.busy and time.monotonic()-self.started > limit:
            self.cancel('Calculation exceeded its time budget. The draft is preserved; refine the selection and retry.')
