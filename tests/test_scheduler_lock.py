"""P0 调度器：领导锁互斥/释放 + SchedulerService 开关/tick 隔离/重启。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import AppConfig, SchedulerSettings
from flyai.proactor import LeadLock, SchedulerService


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _state(tmp_path, **sched_kw):
    cfg = AppConfig()
    if sched_kw:
        cfg = cfg.model_copy(
            update={"scheduler": SchedulerSettings(**sched_kw)}
        )
    return SimpleNamespace(config=cfg, data_dir=tmp_path)


def test_lead_lock_acquire_release_cycle(tmp_path):
    lock = tmp_path / ".scheduler.lock"
    assert not LeadLock.held()
    assert LeadLock.try_acquire(lock)
    assert LeadLock.held()
    assert LeadLock.try_acquire(lock)  # 本进程已持有：幂等成功
    LeadLock.release()
    assert not LeadLock.held()
    assert lock.exists()  # 锁文件保留，仅释放字节锁


def test_lead_lock_contention(tmp_path):
    lock = tmp_path / ".scheduler.lock"
    other = open(lock, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            other.seek(0)
            msvcrt.locking(other.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert LeadLock.try_acquire(lock) is False
        assert not LeadLock.held()
    finally:
        other.close()
    # 别的进程释放后可正常拿锁
    assert LeadLock.try_acquire(lock)
    LeadLock.release()


class _OkTicker:
    def __init__(self):
        self.inited = 0
        self.ticks = 0

    async def initialize(self):
        self.inited += 1

    async def tick(self):
        self.ticks += 1


class _BoomTicker:
    async def initialize(self):
        return None

    async def tick(self):
        raise RuntimeError("boom")


def test_service_disabled_stands_by(tmp_path):
    svc = SchedulerService(_state(tmp_path, enabled=False))
    _run(svc.start())
    assert not svc.is_leader
    _run(svc.aclose())  # 未启动也能安全关闭


def test_service_enabled_runs_ticker_and_isolates_failure(tmp_path):
    state = _state(tmp_path, enabled=True, tick_seconds=30)
    svc = SchedulerService(state)
    ok = _OkTicker()
    svc.add_ticker(ok)
    svc.add_ticker(_BoomTicker())
    _run(svc.start())
    assert svc.is_leader
    assert ok.inited == 1
    # 直接驱动一次安全 tick：坏 ticker 不影响好 ticker
    _run(svc._safe_tick())
    assert ok.ticks == 1
    # 领导锁确实落在 data 目录
    assert LeadLock.held()
    _run(svc.aclose())
    assert not svc.is_leader
    assert not LeadLock.held()


def test_service_restart_applies_new_config(tmp_path):
    state = _state(tmp_path, enabled=True, tick_seconds=30)
    svc = SchedulerService(state)
    _run(svc.start())
    assert svc.is_leader
    state.config = state.config.model_copy(
        update={"scheduler": SchedulerSettings(enabled=False)}
    )
    _run(svc.restart())
    assert not svc.is_leader  # 关闭后按新配置不再争取领导锁
    _run(svc.aclose())


def test_service_without_data_dir(tmp_path):
    state = _state(tmp_path, enabled=True)
    state.data_dir = None
    svc = SchedulerService(state)
    _run(svc.start())
    assert not svc.is_leader
    _run(svc.aclose())
