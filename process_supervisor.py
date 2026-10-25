from __future__ import annotations

import subprocess
import threading
import time
import os
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import psutil

from windows_process import hidden_process_flags


@dataclass(frozen=True)
class ProcessExecution:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False


@dataclass(frozen=True)
class ActiveProcess:
    pid: int
    label: str
    started_at: float


class _IOCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IOCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _WindowsJob:
    _KILL_ON_CLOSE = 0x00002000

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self._closed = False

    @classmethod
    def assign(cls, process: subprocess.Popen[str]) -> "_WindowsJob | None":
        if os.name != "nt":
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        job = cls(int(handle))
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = cls._KILL_ON_CLOSE
        configured = kernel32.SetInformationJobObject(
            wintypes.HANDLE(job.handle),
            9,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(job.handle), wintypes.HANDLE(int(process._handle))
        )
        if not assigned:
            job.close()
            return None
        return job

    def active_processes(self) -> int:
        if self._closed:
            return 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        accounting = _BasicAccountingInformation()
        ok = kernel32.QueryInformationJobObject(
            wintypes.HANDLE(self.handle),
            1,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        )
        return int(accounting.ActiveProcesses) if ok else 0

    def terminate(self) -> None:
        if self._closed:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject(wintypes.HANDLE(self.handle), 1)

    def close(self) -> None:
        if self._closed:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
            wintypes.HANDLE(self.handle)
        )
        self._closed = True


@dataclass
class _ManagedProcess:
    process: subprocess.Popen[str]
    active: ActiveProcess
    job: _WindowsJob | None


class ProcessSupervisor:
    """Tracks approved child processes and can terminate their process trees."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[int, _ManagedProcess] = {}
        self._cancelled_pids: set[int] = set()

    @staticmethod
    def _creation_flags() -> int:
        return hidden_process_flags(
            int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        )

    def _register(self, process: subprocess.Popen[str], label: str) -> ActiveProcess:
        active = ActiveProcess(process.pid, label, time.time())
        managed = _ManagedProcess(process, active, _WindowsJob.assign(process))
        with self._lock:
            self._processes[process.pid] = managed
        return active

    def _was_cancelled(self, pid: int) -> bool:
        with self._lock:
            return pid in self._cancelled_pids

    def active(self) -> tuple[ActiveProcess, ...]:
        with self._lock:
            return tuple(managed.active for managed in self._processes.values())

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        label: str,
    ) -> ProcessExecution:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=self._creation_flags(),
        )
        self._register(process, label)
        deadline = time.monotonic() + timeout
        timed_out = False
        stdout = ""
        stderr = ""
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    self.cancel(process.pid)
                    remaining = 3
                try:
                    stdout, stderr = process.communicate(timeout=min(max(remaining, 0.05), 0.2))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            cancelled = self._was_cancelled(process.pid)
            with self._lock:
                managed = self._processes.pop(process.pid, None)
                self._cancelled_pids.discard(process.pid)
            if managed is not None and managed.job is not None:
                managed.job.close()
        return ProcessExecution(
            argv=tuple(str(part) for part in argv),
            returncode=int(process.returncode if process.returncode is not None else -1),
            stdout=stdout or "",
            stderr=stderr or "",
            cancelled=cancelled and not timed_out,
            timed_out=timed_out,
        )

    def start_background(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        label: str,
    ) -> ActiveProcess:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            shell=False,
            creationflags=self._creation_flags(),
        )
        active = self._register(process, label)

        def watch() -> None:
            process.wait()
            with self._lock:
                managed = self._processes.get(process.pid)
            if managed is not None and managed.job is not None:
                while managed.job.active_processes() > 0:
                    time.sleep(0.2)
                managed.job.close()
            with self._lock:
                self._processes.pop(process.pid, None)
                self._cancelled_pids.discard(process.pid)

        threading.Thread(target=watch, name=f"process-{process.pid}", daemon=True).start()
        return active

    def cancel(self, pid: int) -> bool:
        with self._lock:
            record = self._processes.get(pid)
            if record is None:
                return False
            process = record.process
            self._cancelled_pids.add(pid)
        self._terminate_tree(process, record.job)
        return True

    def cancel_all(self) -> tuple[ActiveProcess, ...]:
        with self._lock:
            records = list(self._processes.values())
            self._cancelled_pids.update(record.process.pid for record in records)
        for record in records:
            self._terminate_tree(record.process, record.job)
        return tuple(record.active for record in records)

    @staticmethod
    def _terminate_tree(
        process: subprocess.Popen[str], job: _WindowsJob | None = None
    ) -> None:
        if job is not None:
            job.terminate()
            return
        try:
            parent = psutil.Process(process.pid)
            descendants = parent.children(recursive=True)
            targets = [*descendants, parent]
            for target in targets:
                try:
                    target.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            _gone, alive = psutil.wait_procs(targets, timeout=1.5)
            for target in alive:
                try:
                    target.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if alive:
                psutil.wait_procs(alive, timeout=1.5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
