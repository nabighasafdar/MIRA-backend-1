from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from system.config import CONFIG

from api import jobs
from api.runner import parse_request_workflow, start_bookmark_task, start_run_task

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('mira.api')


def _auth(authorization: str | None = Header(default=None)) -> None:
	secret = CONFIG.AGENT_API_SECRET
	if not secret:
		logger.warning('AGENT_API_SECRET not set; refusing requests')
		raise HTTPException(status_code=503, detail='Agent API not configured')
	if not authorization or not authorization.startswith('Bearer '):
		raise HTTPException(status_code=401, detail='Missing bearer token')
	if authorization[7:].strip() != secret:
		raise HTTPException(status_code=403, detail='Invalid token')


class AgentRunRequest(BaseModel):
	user_id: str = Field(..., min_length=1)
	chat_id: str | None = None
	task: str = ''
	workflow_json: dict | None = None
	load_wallet: bool = True


class BookmarkRunRequest(BaseModel):
	user_id: str = Field(..., min_length=1)
	bookmark_id: str = Field(..., min_length=1)
	load_wallet: bool = True


app = FastAPI(title='MIRA Agent API', version='0.1.0')
origins = [o.strip() for o in (CONFIG.CORS_ORIGINS or '').split(',') if o.strip()]
if not origins:
	origins = ['http://localhost:3000']
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.get('/health')
async def health():
	return {'status': 'ok'}


@app.post('/agent/run', dependencies=[Depends(_auth)])
async def agent_run(body: AgentRunRequest):
	wf_dict = parse_request_workflow({'workflow_json': body.workflow_json}) if body.workflow_json else None
	task = body.task.strip()
	if not task and not wf_dict:
		raise HTTPException(status_code=400, detail='task or workflow_json required')
	job_id = await jobs.register_job(body.user_id, body.chat_id)
	await start_run_task(job_id, body.user_id, task, wf_dict, load_wallet=body.load_wallet)
	return {'job_id': job_id}


@app.post('/agent/run-bookmark', dependencies=[Depends(_auth)])
async def agent_run_bookmark(body: BookmarkRunRequest):
	job_id = await jobs.register_job(body.user_id, chat_id=None)
	err = await start_bookmark_task(job_id, body.user_id, body.bookmark_id, load_wallet=body.load_wallet)
	if err:
		jobs.delete_job(job_id)
		raise HTTPException(status_code=400, detail=err)
	return {'job_id': job_id}


@app.get('/agent/jobs/{job_id}/meta', dependencies=[Depends(_auth)])
async def job_meta(job_id: str):
	rec = jobs.get_job(job_id)
	if not rec:
		raise HTTPException(status_code=404, detail='Unknown job')
	return {'user_id': rec.user_id, 'chat_id': rec.chat_id}


async def _job_event_stream(job_id: str):
	rec = jobs.get_job(job_id)
	if not rec:
		yield 'data: ' + json.dumps({'event_type': 'error', 'message': 'Unknown job'}) + '\n\n'
		return
	while True:
		try:
			item = await asyncio.wait_for(rec.queue.get(), timeout=45.0)
		except asyncio.TimeoutError:
			yield 'data: ' + json.dumps({'event_type': 'ping'}) + '\n\n'
			continue
		if item is None:
			break
		yield 'data: ' + json.dumps(item, default=str) + '\n\n'


@app.get('/agent/jobs/{job_id}/events', dependencies=[Depends(_auth)])
async def job_events(job_id: str):
	rec = jobs.get_job(job_id)
	if not rec:
		raise HTTPException(status_code=404, detail='Unknown job')
	return StreamingResponse(_job_event_stream(job_id), media_type='text/event-stream')
