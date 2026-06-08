from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from types import TracebackType

from praetor.models import AgentAdapter, TaskResult


class PoolError(RuntimeError):
    """Raised on pool invariant violations (e.g. invalid max_parallel)."""


class WorkerPool:
    """Thread-based pool for concurrent AgentAdapter.exec() calls.

    Uses concurrent.futures.ThreadPoolExecutor. Threads (not processes) because
    adapter.exec() shells out to a subprocess and blocks on its completion -
    the GIL is released during subprocess.run, so threads give real parallelism
    for this workload without the pickling and IPC overhead of processes.

    Submitting after shutdown lets the underlying executor's RuntimeError
    propagate.
    """

    def __init__(self, max_parallel: int) -> None:
        if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
            raise PoolError("max_parallel must be a positive int")

        self._max_parallel = max_parallel
        self._executor = ThreadPoolExecutor(max_workers=max_parallel)

    @property
    def max_parallel(self) -> int:
        return self._max_parallel

    def submit(
        self,
        adapter: AgentAdapter,
        prompt: str,
        cwd: Path,
        timeout_s: float | None = None,
    ) -> Future[TaskResult]:
        return self._executor.submit(adapter.exec, prompt, cwd, timeout_s=timeout_s)

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """Shutdown the pool.

        cancel_pending cancels queued-but-not-started futures. Already-running
        calls cannot be interrupted because Python threads cannot be forcefully
        stopped.
        """

        self._executor.shutdown(wait=wait, cancel_futures=cancel_pending)

    def __enter__(self) -> "WorkerPool":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.shutdown(wait=True, cancel_pending=False)
            return

        self.shutdown(wait=False, cancel_pending=True)
