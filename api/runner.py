from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from system.agent.service import Agent
from system.agent.workflow import WorkflowTemplate
from system.config import CONFIG
from system.llm.google.chat import ChatGoogle
from system.llm.openai.chat import ChatOpenAI

from api import jobs
from api.browser_pool import acquire_session, release_session, wrap_task_for_continuation
from api.mira_persona import build_extend_system_message
from api.supabase_data import (
	bookmark_workflow_to_template,
	fetch_bookmark_workflow,
	fetch_profile_llm_key,
	fetch_wallet_items,
	wallet_to_extend_message,
)

logger = logging.getLogger(__name__)

def _b64_png_to_data_url(screenshot_b64: str | None) -> str | None:
	if not screenshot_b64:
		return None
	return f'data:image/png;base64,{screenshot_b64}'


def _build_llm(profile_key: str | None):
	key = (profile_key or '').strip()
	if key and key != 'default_key':
		if key.startswith('sk-'):
			return ChatOpenAI(model='gpt-4o-mini', api_key=key)
		return ChatGoogle(model='gemini-2.5-flash', api_key=key)
	if not CONFIG.GOOGLE_API_KEY:
		raise ValueError('No LLM key: set GOOGLE_API_KEY or profile llm_api_key in Supabase')
	return ChatGoogle(model='gemini-2.5-flash', api_key=CONFIG.GOOGLE_API_KEY)


async def _run_agent_core(
	job_id: str,
	user_id: str,
	chat_id: str | None,
	task: str,
	workflow_template: WorkflowTemplate | None,
	extend_system_message: str | None,
) -> None:
	rec = jobs.get_job(job_id)
	if not rec:
		return
	queue = rec.queue

	captured_workflow_json: str | None = None

	def emit(data: dict) -> None:
		nonlocal captured_workflow_json
		if data.get('event_type') == 'workflow_macro_recorded' and data.get('workflow_json'):
			captured_workflow_json = data['workflow_json']
		try:
			loop = asyncio.get_running_loop()
		except RuntimeError:
			return
		loop.create_task(queue.put(data))

	llm_key = await fetch_profile_llm_key(user_id)
	llm = _build_llm(llm_key)

	session, continuing = await acquire_session(user_id, chat_id)
	task = wrap_task_for_continuation(task, continuing=continuing)

	try:
		async def on_step(browser_state_summary, model_output, step_number: int):  # type: ignore[no-untyped-def]
			emit({
				'event_type': 'agent_step',
				'step': step_number,
				'url': getattr(browser_state_summary, 'url', None),
				'screenshot_url': _b64_png_to_data_url(getattr(browser_state_summary, 'screenshot', None)),
				'thinking': getattr(getattr(model_output, 'current_state', None), 'thinking', None) if model_output else None,
			})

		agent = Agent(
			task=task,
			llm=llm,
			browser_session=session,
			frontend_event_callback=emit,
			register_new_step_callback=on_step,
			workflow_template=workflow_template,
			extend_system_message=extend_system_message,
			directly_open_url=not continuing,
		)
		if rec:
			rec.agent = agent
		history = await agent.run()
		final_result = ''
		ok = False
		if history:
			ok = bool(history.is_successful())
			try:
				final_result = history.final_result() or ''
			except Exception:
				final_result = ''
		await queue.put({
			'event_type': 'done',
			'final_result': final_result,
			'successful': ok,
			'workflow_json': captured_workflow_json,
			'original_task': task,
		})
	except asyncio.CancelledError:
		logger.info('Agent job %s cancelled by user', job_id)
		await queue.put({'event_type': 'cancelled', 'message': 'Task stopped.'})
	except Exception as e:
		logger.exception('Agent job failed')
		await queue.put({'event_type': 'error', 'message': str(e)})
	finally:
		await release_session(user_id, chat_id, close_browser=False)
		await queue.put(None)


async def start_run_task(
	job_id: str,
	user_id: str,
	task: str,
	workflow_dict: dict | None = None,
	load_wallet: bool = True,
	chat_id: str | None = None,
) -> None:
	wt: WorkflowTemplate | None = None
	if workflow_dict:
		wt = WorkflowTemplate.model_validate(workflow_dict)
		if not task.strip():
			task = wt.original_task or 'Execute the saved workflow macro faithfully. If playback fails at any step, recover autonomously to finish the intent.'

	extend = None
	if load_wallet:
		items = await fetch_wallet_items(user_id)
		extend = build_extend_system_message(wallet_to_extend_message(items))
	else:
		extend = build_extend_system_message(None)

	if chat_id is None:
		rec = jobs.get_job(job_id)
		chat_id = rec.chat_id if rec else None

	t = asyncio.create_task(_run_agent_core(job_id, user_id, chat_id, task, wt, extend))
	jobs.attach_runner_task(job_id, t)


async def start_bookmark_task(job_id: str, user_id: str, bookmark_id: str, load_wallet: bool = True) -> None | str:
	row = await fetch_bookmark_workflow(user_id, bookmark_id)
	if not row:
		return 'Bookmark not found'
	wf = bookmark_workflow_to_template(row.get('agent_workflow'))
	if wf is None:
		return 'Bookmark has no runnable WorkflowTemplate JSON (save a workflow macro JSON from an agent run, or paste workflow JSON)'
	task = row.get('name') or 'Run bookmark workflow'
	await start_run_task(job_id, user_id, task, wf, load_wallet=load_wallet)
	return None


def parse_request_workflow(payload: dict[str, Any]) -> dict | None:
	wfj = payload.get('workflow_json') or payload.get('workflow_template')
	if isinstance(wfj, dict):
		return wfj
	if isinstance(wfj, str) and wfj.strip().startswith('{'):
		try:
			return json.loads(wfj)
		except json.JSONDecodeError:
			return None
	return None
