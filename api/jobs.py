from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

_job_lock = asyncio.Lock()
_jobs: dict[str, JobRecord] = {}


@dataclass
class JobRecord:
	job_id: str
	user_id: str
	chat_id: str | None
	queue: asyncio.Queue[Any | None]
	task: asyncio.Task | None = None


async def register_job(user_id: str, chat_id: str | None) -> str:
	job_id = str(uuid.uuid4())
	async with _job_lock:
		_jobs[job_id] = JobRecord(job_id=job_id, user_id=user_id, chat_id=chat_id, queue=asyncio.Queue(), task=None)
	return job_id


def attach_runner_task(job_id: str, t: asyncio.Task) -> None:
	rec = _jobs.get(job_id)
	if rec:
		rec.task = t


async def enqueue_event(job_id: str, payload: dict) -> None:
	rec = _jobs.get(job_id)
	if not rec:
		return
	await rec.queue.put(payload)


async def close_job_stream(job_id: str) -> None:
	rec = _jobs.get(job_id)
	if not rec:
		return
	await rec.queue.put(None)


def get_job(job_id: str) -> JobRecord | None:
	return _jobs.get(job_id)


def verify_job_user(job_id: str, user_id: str) -> bool:
	rec = _jobs.get(job_id)
	return bool(rec and rec.user_id == user_id)


def delete_job(job_id: str) -> None:
	_jobs.pop(job_id, None)
