from __future__ import annotations

import asyncio

from app.jobs import JobManager, JobStatus, JobStatusUpdate


def test_job_lifecycle(fake_redis, tmp_path, monkeypatch):
    async def _run():
        monkeypatch.setattr(
            "app.jobs.redis.from_url", lambda url, decode_responses=True: fake_redis
        )
        manager = JobManager("redis://test", visibility_timeout=1)

        media_path = tmp_path / "sample.mkv"
        media_path.write_bytes(b"demo")

        job = await manager.add_job(str(media_path), "movies", "mobile")
        assert job.status == JobStatus.PENDING

        jobs = await manager.list_jobs()
        assert jobs and jobs[0].id == job.id

        message = await manager.acquire_next("worker-1")
        assert message is not None
        delivery_id, acquired = message
        assert acquired.status == JobStatus.RUNNING

        updated = await manager.update_job(
            job.id, JobStatusUpdate(status=JobStatus.COMPLETED, progress=100, message="done")
        )
        assert updated.status == JobStatus.COMPLETED
        assert updated.progress == 100
        assert updated.message == "done"

        await manager.acknowledge(delivery_id, job.id)

        state = await manager.queue_state()
        assert state["depth"] == 0
        assert state["pending"] == 0

        failed = await manager.update_job(job.id, JobStatusUpdate(status=JobStatus.FAILED))
        assert failed.status == JobStatus.FAILED

    asyncio.run(_run())


def test_pause_and_resume(fake_redis, tmp_path, monkeypatch):
    async def _run():
        monkeypatch.setattr(
            "app.jobs.redis.from_url", lambda url, decode_responses=True: fake_redis
        )
        manager = JobManager("redis://test", visibility_timeout=1)

        await manager.pause("maintenance")
        paused_state = await manager.queue_state()
        assert paused_state["paused"] is True
        assert paused_state["reason"] == "maintenance"

        await manager.resume()
        resumed_state = await manager.queue_state()
        assert resumed_state["paused"] is False
        assert resumed_state["reason"] is None

    asyncio.run(_run())


def test_acquire_stalled_handles_tuple_shape(fake_redis, monkeypatch):
    async def _run():
        monkeypatch.setattr(
            "app.jobs.redis.from_url", lambda url, decode_responses=True: fake_redis
        )
        manager = JobManager("redis://test", visibility_timeout=1)
        stalled = await manager._acquire_stalled("worker-1")
        assert stalled == (None, None)

    asyncio.run(_run())
