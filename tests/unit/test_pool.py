from pathlib import Path
import time

import pytest

from praetor.adapters.mock import MockAdapter
from praetor.models import TaskResult
from praetor.pool import PoolError, WorkerPool


class SleepyAdapter:
    name = "sleepy"

    def __init__(self, sleep_s: float) -> None:
        self.sleep_s = sleep_s

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        time.sleep(self.sleep_s)
        return TaskResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=int(self.sleep_s * 1000),
        )


class RaisingAdapter:
    name = "raising"

    def exec(self, prompt: str, cwd: Path, timeout_s: float | None = None) -> TaskResult:
        raise RuntimeError("boom")


def test_max_parallel_must_be_positive() -> None:
    with pytest.raises(PoolError, match="max_parallel must be a positive int"):
        WorkerPool(0)

    with pytest.raises(PoolError, match="max_parallel must be a positive int"):
        WorkerPool(-3)

    pool = WorkerPool(1)
    pool.shutdown()


def test_max_parallel_must_be_int() -> None:
    with pytest.raises(PoolError, match="max_parallel must be a positive int"):
        WorkerPool(True)

    with pytest.raises(PoolError, match="max_parallel must be a positive int"):
        WorkerPool(1.5)  # type: ignore[arg-type]


def test_max_parallel_property() -> None:
    pool = WorkerPool(4)
    try:
        assert pool.max_parallel == 4
    finally:
        pool.shutdown()


def test_runs_all_submitted(tmp_path: Path) -> None:
    with WorkerPool(2) as pool:
        futures = [pool.submit(MockAdapter(), "prompt", tmp_path) for _ in range(5)]
        results = [future.result() for future in futures]

    assert len(results) == 5
    assert all(isinstance(result, TaskResult) for result in results)
    assert all(result.exit_code == 0 for result in results)


def test_actual_concurrency_small(tmp_path: Path) -> None:
    adapter = SleepyAdapter(0.2)

    start = time.perf_counter()
    with WorkerPool(3) as pool:
        futures = [pool.submit(adapter, "prompt", tmp_path) for _ in range(3)]
        results = [future.result() for future in futures]
    elapsed = time.perf_counter() - start

    assert len(results) == 3
    assert elapsed < 0.5


def test_actual_concurrency_batched(tmp_path: Path) -> None:
    adapter = SleepyAdapter(0.2)

    start = time.perf_counter()
    with WorkerPool(3) as pool:
        futures = [pool.submit(adapter, "prompt", tmp_path) for _ in range(6)]
        results = [future.result() for future in futures]
    elapsed = time.perf_counter() - start

    assert len(results) == 6
    assert elapsed < 0.8


def test_context_manager_shuts_down(tmp_path: Path) -> None:
    with WorkerPool(2) as pool:
        result = pool.submit(MockAdapter(), "prompt", tmp_path).result()

    assert result.exit_code == 0
    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        pool.submit(MockAdapter(), "prompt", tmp_path)


def test_context_manager_exception_fast_exits(tmp_path: Path) -> None:
    start = time.perf_counter()
    with pytest.raises(RuntimeError, match="oops"):
        with WorkerPool(2) as pool:
            pool.submit(SleepyAdapter(2.0), "prompt", tmp_path)
            raise RuntimeError("oops")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5


def test_shutdown_is_idempotent() -> None:
    pool = WorkerPool(1)

    pool.shutdown()
    pool.shutdown()


def test_submit_after_shutdown_raises(tmp_path: Path) -> None:
    pool = WorkerPool(1)
    pool.shutdown()

    with pytest.raises(RuntimeError, match="cannot schedule new futures after shutdown"):
        pool.submit(MockAdapter(), "prompt", tmp_path)


def test_adapter_exception_propagates_via_future(tmp_path: Path) -> None:
    with WorkerPool(1) as pool:
        future = pool.submit(RaisingAdapter(), "prompt", tmp_path)

        with pytest.raises(RuntimeError, match="boom"):
            future.result()

        result = pool.submit(MockAdapter(), "prompt", tmp_path).result()

    assert result.exit_code == 0
