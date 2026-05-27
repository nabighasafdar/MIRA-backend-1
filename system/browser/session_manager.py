import asyncio
from typing import TYPE_CHECKING
from cdp_use.cdp.target import AttachedToTargetEvent, DetachedFromTargetEvent, SessionID, TargetID
from system.utils import create_task_with_error_handling
if TYPE_CHECKING:
    from system.browser.session import BrowserSession, CDPSession, Target

class SessionManager:

    def __init__(self, browser_session: 'BrowserSession'):
        self.browser_session = browser_session
        self.logger = browser_session.logger
        self._targets: dict[TargetID, 'Target'] = {}
        self._sessions: dict[SessionID, 'CDPSession'] = {}
        self._target_sessions: dict[TargetID, set[SessionID]] = {}
        self._session_to_target: dict[SessionID, TargetID] = {}
        self._lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._recovery_in_progress: bool = False
        self._recovery_complete_event: asyncio.Event | None = None
        self._recovery_task: asyncio.Task | None = None

    async def start_monitoring(self) -> None:
        if not self.browser_session._cdp_client_root:
            raise RuntimeError('CDP client not initialized')
        cdp_client = self.browser_session._cdp_client_root
        await cdp_client.send.Target.setDiscoverTargets(params={'discover': True, 'filter': [{'type': 'page'}, {'type': 'iframe'}]})

        def on_attached(event: AttachedToTargetEvent, session_id: SessionID | None=None):
            create_task_with_error_handling(self._handle_target_attached(event), name='handle_target_attached', logger_instance=self.logger, suppress_exceptions=True)

        def on_detached(event: DetachedFromTargetEvent, session_id: SessionID | None=None):
            create_task_with_error_handling(self._handle_target_detached(event), name='handle_target_detached', logger_instance=self.logger, suppress_exceptions=True)

        def on_target_info_changed(event, session_id: SessionID | None=None):
            create_task_with_error_handling(self._handle_target_info_changed(event), name='handle_target_info_changed', logger_instance=self.logger, suppress_exceptions=True)
        cdp_client.register.Target.attachedToTarget(on_attached)
        cdp_client.register.Target.detachedFromTarget(on_detached)
        cdp_client.register.Target.targetInfoChanged(on_target_info_changed)
        self.logger.debug('[SessionManager] Event monitoring started')
        await self._initialize_existing_targets()

    def _get_session_for_target(self, target_id: TargetID) -> 'CDPSession | None':
        session_ids = self._target_sessions.get(target_id, set())
        if not session_ids:
            if self.browser_session.agent_focus_target_id == target_id:
                self.logger.warning(f'[SessionManager] ⚠️ Attempted to get session for stale focused target {target_id[:8]}... Clearing stale focus and triggering recovery.')
                self.browser_session.agent_focus_target_id = None
                if not self._recovery_in_progress:
                    self.logger.warning('[SessionManager] Recovery was not in progress! Triggering now.')
                    self._recovery_task = create_task_with_error_handling(self._recover_agent_focus(target_id), name='recover_agent_focus_from_stale_get', logger_instance=self.logger, suppress_exceptions=False)
            return None
        return self._sessions.get(next(iter(session_ids)))

    def get_all_page_targets(self) -> list:
        page_targets = []
        for target in self._targets.values():
            if target.target_type in ('page', 'tab'):
                page_targets.append(target)
        return page_targets

    async def validate_session(self, target_id: TargetID) -> bool:
        if target_id not in self._target_sessions:
            return False
        return len(self._target_sessions[target_id]) > 0

    async def clear(self) -> None:
        async with self._lock:
            self._targets.clear()
            self._sessions.clear()
            self._target_sessions.clear()
            self._session_to_target.clear()
        self.logger.info('[SessionManager] Cleared all owned data (targets, sessions, mappings)')

    async def is_target_valid(self, target_id: TargetID) -> bool:
        if target_id not in self._target_sessions:
            return False
        return len(self._target_sessions[target_id]) > 0

    def get_target_id_from_session_id(self, session_id: SessionID) -> TargetID | None:
        return self._session_to_target.get(session_id)

    def get_target(self, target_id: TargetID) -> 'Target | None':
        return self._targets.get(target_id)

    def get_all_targets(self) -> dict[TargetID, 'Target']:
        return self._targets

    def get_all_target_ids(self) -> list[TargetID]:
        return list(self._targets.keys())

    def get_all_sessions(self) -> dict[SessionID, 'CDPSession']:
        return self._sessions

    def get_session(self, session_id: SessionID) -> 'CDPSession | None':
        return self._sessions.get(session_id)

    def get_all_sessions_for_target(self, target_id: TargetID) -> list['CDPSession']:
        session_ids = self._target_sessions.get(target_id, set())
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    def get_target_sessions_mapping(self) -> dict[TargetID, set[SessionID]]:
        return self._target_sessions

    def get_focused_target(self) -> 'Target | None':
        if not self.browser_session.agent_focus_target_id:
            return None
        return self.get_target(self.browser_session.agent_focus_target_id)

    async def ensure_valid_focus(self, timeout: float=3.0) -> bool:
        if not self.browser_session.agent_focus_target_id:
            if self._recovery_in_progress and self._recovery_complete_event:
                try:
                    await asyncio.wait_for(self._recovery_complete_event.wait(), timeout=timeout)
                    focus_id = self.browser_session.agent_focus_target_id
                    return bool(focus_id and self._get_session_for_target(focus_id))
                except TimeoutError:
                    self.logger.error(f'[SessionManager] ❌ Timed out waiting for recovery after {timeout}s')
                    return False
            return False
        cdp_session = self._get_session_for_target(self.browser_session.agent_focus_target_id)
        if cdp_session:
            is_valid = await self.validate_session(self.browser_session.agent_focus_target_id)
            if is_valid:
                return True
        stale_target_id = self.browser_session.agent_focus_target_id
        self.logger.warning(f"[SessionManager] ⚠️ Stale agent_focus detected (target {(stale_target_id[:8] if stale_target_id else 'None')}... detached), waiting for recovery...")
        if not self._recovery_in_progress:
            self.logger.warning('[SessionManager] ⚠️ Recovery not in progress for stale focus! This indicates a bug - recovery should have been triggered.')
            return False
        if self._recovery_complete_event:
            try:
                start_time = asyncio.get_event_loop().time()
                await asyncio.wait_for(self._recovery_complete_event.wait(), timeout=timeout)
                elapsed = asyncio.get_event_loop().time() - start_time
                focus_id = self.browser_session.agent_focus_target_id
                if focus_id and self._get_session_for_target(focus_id):
                    self.logger.info(f'[SessionManager] ✅ Agent focus recovered to {self.browser_session.agent_focus_target_id[:8]}... after {elapsed * 1000:.0f}ms')
                    return True
                else:
                    self.logger.error(f'[SessionManager] ❌ Recovery completed but focus still invalid after {elapsed * 1000:.0f}ms')
                    return False
            except TimeoutError:
                self.logger.error(f"[SessionManager] ❌ Recovery timed out after {timeout}s (was: {(stale_target_id[:8] if stale_target_id else 'None')}..., now: {(self.browser_session.agent_focus_target_id[:8] if self.browser_session.agent_focus_target_id else 'None')})")
                return False
        else:
            self.logger.error('[SessionManager] ❌ Recovery event not initialized')
            return False

    async def _handle_target_attached(self, event: AttachedToTargetEvent) -> None:
        target_id = event['targetInfo']['targetId']
        session_id = event['sessionId']
        target_type = event['targetInfo']['type']
        target_info = event['targetInfo']
        waiting_for_debugger = event.get('waitingForDebugger', False)
        self.logger.debug(f'[SessionManager] Target attached: {target_id[:8]}... (session={session_id[:8]}..., type={target_type}, waitingForDebugger={waiting_for_debugger})')
        if self.browser_session._cdp_client_root is None:
            self.logger.debug(f'[SessionManager] Skipping target attach for {target_id[:8]}... - browser shutting down (no CDP client)')
            return
        try:
            await self.browser_session._cdp_client_root.send.Target.setAutoAttach(params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}, session_id=session_id)
        except Exception as e:
            error_str = str(e)
            if '-32001' not in error_str and 'Session with given id not found' not in error_str:
                self.logger.debug(f'[SessionManager] Auto-attach failed for {target_type}: {e}')
        async with self._lock:
            if target_id not in self._target_sessions:
                self._target_sessions[target_id] = set()
            self._target_sessions[target_id].add(session_id)
            self._session_to_target[session_id] = target_id
        if target_id not in self._targets:
            from system.browser.session import Target
            target = Target(target_id=target_id, target_type=target_type, url=target_info.get('url', 'about:blank'), title=target_info.get('title', 'Unknown title'))
            self._targets[target_id] = target
            self.logger.debug(f'[SessionManager] Created target {target_id[:8]}... (type={target_type})')
        else:
            existing_target = self._targets[target_id]
            existing_target.url = target_info.get('url', existing_target.url)
            existing_target.title = target_info.get('title', existing_target.title)
        from system.browser.session import CDPSession
        assert self.browser_session._cdp_client_root is not None, 'Root CDP client required'
        cdp_session = CDPSession(cdp_client=self.browser_session._cdp_client_root, target_id=target_id, session_id=session_id)
        self._sessions[session_id] = cdp_session
        try:
            proxy_cfg = self.browser_session.browser_profile.proxy
            username = proxy_cfg.username if proxy_cfg else None
            password = proxy_cfg.password if proxy_cfg else None
            if username and password:
                await cdp_session.cdp_client.send.Fetch.enable(params={'handleAuthRequests': True}, session_id=cdp_session.session_id)
                self.logger.debug(f'[SessionManager] Fetch.enable(handleAuthRequests=True) on session {session_id[:8]}...')
        except Exception as e:
            self.logger.debug(f'[SessionManager] Fetch.enable on attached session failed: {type(e).__name__}: {e}')
        self.logger.debug(f'[SessionManager] Created session {session_id[:8]}... for target {target_id[:8]}... (total sessions: {len(self._sessions)})')
        if target_type in ('page', 'tab'):
            await self._enable_page_monitoring(cdp_session)
        if waiting_for_debugger:
            try:
                assert self.browser_session._cdp_client_root is not None
                await self.browser_session._cdp_client_root.send.Runtime.runIfWaitingForDebugger(session_id=session_id)
            except Exception as e:
                self.logger.warning(f'[SessionManager] Failed to resume execution: {e}')

    async def _handle_target_info_changed(self, event: dict) -> None:
        target_info = event.get('targetInfo', {})
        target_id = target_info.get('targetId')
        if not target_id:
            return
        async with self._lock:
            if target_id in self._targets:
                target = self._targets[target_id]
                target.title = target_info.get('title', target.title)
                target.url = target_info.get('url', target.url)

    async def _handle_target_detached(self, event: DetachedFromTargetEvent) -> None:
        session_id = event['sessionId']
        target_id = event.get('targetId')
        if not target_id:
            async with self._lock:
                target_id = self._session_to_target.get(session_id)
        if not target_id:
            self.logger.warning(f'[SessionManager] Session detached but target unknown (session={session_id[:8]}...)')
            return
        agent_focus_lost = False
        target_fully_removed = False
        target_type = None
        async with self._lock:
            if target_id in self._target_sessions:
                self._target_sessions[target_id].discard(session_id)
                remaining_sessions = len(self._target_sessions[target_id])
                self.logger.debug(f'[SessionManager] Session detached: target={target_id[:8]}... session={session_id[:8]}... (remaining={remaining_sessions})')
                if remaining_sessions == 0:
                    self.logger.debug(f'[SessionManager] No sessions remain for target {target_id[:8]}..., removing target')
                    target_fully_removed = True
                    agent_focus_lost = self.browser_session.agent_focus_target_id == target_id
                    if agent_focus_lost:
                        self.logger.debug(f'[SessionManager] Clearing stale agent_focus_target_id {target_id[:8]}... to prevent operations on detached target')
                        self.browser_session.agent_focus_target_id = None
                    target = self._targets.get(target_id)
                    target_type = target.target_type if target else None
                    if target_id in self._targets:
                        self._targets.pop(target_id)
                        self.logger.debug(f'[SessionManager] Removed target {target_id[:8]}... (remaining targets: {len(self._targets)})')
                    del self._target_sessions[target_id]
            else:
                self.logger.debug(f'[SessionManager] Session detached from untracked target: target={target_id[:8]}... session={session_id[:8]}... (target was already removed or attach event was missed)')
            if session_id in self._sessions:
                self._sessions.pop(session_id)
                self.logger.debug(f'[SessionManager] Removed session {session_id[:8]}... (remaining sessions: {len(self._sessions)})')
            if session_id in self._session_to_target:
                del self._session_to_target[session_id]
        if target_fully_removed:
            if target_type in ('page', 'tab'):
                from system.browser.events import TabClosedEvent
                self.browser_session.event_bus.dispatch(TabClosedEvent(target_id=target_id))
                self.logger.debug(f'[SessionManager] Dispatched TabClosedEvent for page target {target_id[:8]}...')
            elif target_type:
                self.logger.debug(f'[SessionManager] Target {target_id[:8]}... fully removed (type={target_type}) - not dispatching TabClosedEvent')
        if agent_focus_lost:
            if not self._recovery_in_progress:
                self._recovery_task = create_task_with_error_handling(self._recover_agent_focus(target_id), name='recover_agent_focus', logger_instance=self.logger, suppress_exceptions=False)

    async def _recover_agent_focus(self, crashed_target_id: TargetID) -> None:
        try:
            async with self._recovery_lock:
                if self._recovery_in_progress:
                    self.logger.debug('[SessionManager] Recovery already in progress, waiting for it to complete')
                    if self._recovery_complete_event:
                        try:
                            await asyncio.wait_for(self._recovery_complete_event.wait(), timeout=5.0)
                        except TimeoutError:
                            self.logger.error('[SessionManager] Timed out waiting for ongoing recovery')
                    return
                self._recovery_in_progress = True
                self._recovery_complete_event = asyncio.Event()
                if self.browser_session._cdp_client_root is None:
                    self.logger.debug('[SessionManager] Skipping focus recovery - browser shutting down (no CDP client)')
                    return
                if self.browser_session.agent_focus_target_id and self.browser_session.agent_focus_target_id != crashed_target_id:
                    self.logger.debug(f'[SessionManager] Agent focus already recovered by concurrent operation (now: {self.browser_session.agent_focus_target_id[:8]}...), skipping recovery')
                    return
                current_focus_desc = f'{self.browser_session.agent_focus_target_id[:8]}...' if self.browser_session.agent_focus_target_id else 'None (already cleared)'
                self.logger.warning(f'[SessionManager] Agent focus target {crashed_target_id[:8]}... detached! Current focus: {current_focus_desc}. Auto-recovering by switching to another target...')
            page_targets = self.get_all_page_targets()
            new_target_id = None
            is_existing_tab = False
            if page_targets:
                new_target_id = page_targets[-1].target_id
                is_existing_tab = True
                self.logger.info(f'[SessionManager] Switching agent_focus to existing tab {new_target_id[:8]}...')
            else:
                self.logger.warning('[SessionManager] No tabs remain! Creating new tab for agent...')
                new_target_id = await self.browser_session._cdp_create_new_page('about:blank')
                self.logger.info(f'[SessionManager] Created new tab {new_target_id[:8]}... for agent')
                from system.browser.events import TabCreatedEvent
                self.browser_session.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=new_target_id))
            new_session = None
            for attempt in range(20):
                await asyncio.sleep(0.1)
                new_session = self._get_session_for_target(new_target_id)
                if new_session:
                    break
            if new_session:
                self.browser_session.agent_focus_target_id = new_target_id
                self.logger.info(f'[SessionManager] ✅ Agent focus recovered: {new_target_id[:8]}...')
                if is_existing_tab:
                    try:
                        assert self.browser_session._cdp_client_root is not None
                        await self.browser_session._cdp_client_root.send.Target.activateTarget(params={'targetId': new_target_id})
                        self.logger.debug(f'[SessionManager] Activated tab {new_target_id[:8]}... in browser UI')
                    except Exception as e:
                        self.logger.debug(f'[SessionManager] Failed to activate tab visually: {e}')
                target = self.get_target(new_target_id)
                target_url = target.url if target else 'about:blank'
                from system.browser.events import AgentFocusChangedEvent
                self.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=new_target_id, url=target_url))
                return
            self.logger.error(f'[SessionManager] ❌ Failed to get session for {new_target_id[:8]}... after 2s, creating emergency fallback tab')
            fallback_target_id = await self.browser_session._cdp_create_new_page('about:blank')
            self.logger.warning(f'[SessionManager] Created emergency fallback tab {fallback_target_id[:8]}...')
            for _ in range(20):
                await asyncio.sleep(0.1)
                fallback_session = self._get_session_for_target(fallback_target_id)
                if fallback_session:
                    self.browser_session.agent_focus_target_id = fallback_target_id
                    self.logger.warning(f'[SessionManager] ⚠️ Agent focus set to emergency fallback: {fallback_target_id[:8]}...')
                    from system.browser.events import AgentFocusChangedEvent, TabCreatedEvent
                    self.browser_session.event_bus.dispatch(TabCreatedEvent(url='about:blank', target_id=fallback_target_id))
                    self.browser_session.event_bus.dispatch(AgentFocusChangedEvent(target_id=fallback_target_id, url='about:blank'))
                    return
            self.logger.critical('[SessionManager] 🚨 CRITICAL: Failed to recover agent_focus even with fallback! Agent may be in broken state.')
        except Exception as e:
            self.logger.error(f'[SessionManager] ❌ Error during agent_focus recovery: {type(e).__name__}: {e}')
        finally:
            if self._recovery_complete_event:
                self._recovery_complete_event.set()
            self._recovery_in_progress = False
            self._recovery_task = None
            self.logger.debug('[SessionManager] Recovery state reset')

    async def _initialize_existing_targets(self) -> None:
        cdp_client = self.browser_session._cdp_client_root
        assert cdp_client is not None
        targets_result = await cdp_client.send.Target.getTargets()
        existing_targets = targets_result.get('targetInfos', [])
        self.logger.debug(f'[SessionManager] Discovered {len(existing_targets)} existing targets')
        target_ids_to_wait_for = []
        for target in existing_targets:
            target_id = target['targetId']
            target_type = target.get('type', 'unknown')
            try:
                await cdp_client.send.Target.attachToTarget(params={'targetId': target_id, 'flatten': True})
                target_ids_to_wait_for.append(target_id)
            except Exception as e:
                self.logger.debug(f'[SessionManager] Failed to attach to existing target {target_id[:8]}... (type={target_type}): {e}')
        ready_event = asyncio.Event()

        async def check_all_ready():
            while True:
                ready_count = 0
                for tid in target_ids_to_wait_for:
                    session = self._get_session_for_target(tid)
                    if session:
                        target = self._targets.get(tid)
                        target_type = target.target_type if target else 'unknown'
                        if target_type in ('page', 'tab'):
                            if hasattr(session, '_lifecycle_events') and session._lifecycle_events is not None:
                                ready_count += 1
                        else:
                            ready_count += 1
                if ready_count == len(target_ids_to_wait_for):
                    ready_event.set()
                    return
                await asyncio.sleep(0.05)
        check_task = create_task_with_error_handling(check_all_ready(), name='check_all_targets_ready', logger_instance=self.logger)
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=2.0)
        except TimeoutError:
            ready_count = 0
            for tid in target_ids_to_wait_for:
                session = self._get_session_for_target(tid)
                if session:
                    target = self._targets.get(tid)
                    target_type = target.target_type if target else 'unknown'
                    if target_type in ('page', 'tab'):
                        if hasattr(session, '_lifecycle_events') and session._lifecycle_events is not None:
                            ready_count += 1
                    else:
                        ready_count += 1
            self.logger.warning(f'[SessionManager] Initialization timeout after 2.0s: {ready_count}/{len(target_ids_to_wait_for)} sessions ready')
        finally:
            check_task.cancel()
            try:
                await check_task
            except asyncio.CancelledError:
                pass

    async def _enable_page_monitoring(self, cdp_session: 'CDPSession') -> None:
        try:
            await cdp_session.cdp_client.send.Page.enable(session_id=cdp_session.session_id)
            await cdp_session.cdp_client.send.Page.setLifecycleEventsEnabled(params={'enabled': True}, session_id=cdp_session.session_id)
            await cdp_session.cdp_client.send.Network.enable(session_id=cdp_session.session_id)
            from collections import deque
            cdp_session._lifecycle_events = deque(maxlen=50)
            cdp_session._lifecycle_lock = asyncio.Lock()

            def on_lifecycle_event(event, session_id=None):
                event_name = event.get('name', 'unknown')
                event_loader_id = event.get('loaderId', 'none')
                target_id_from_event = None
                if session_id:
                    target_id_from_event = self.get_target_id_from_session_id(session_id)
                if target_id_from_event == cdp_session.target_id:
                    event_data = {'name': event_name, 'loaderId': event_loader_id, 'timestamp': asyncio.get_event_loop().time()}
                    try:
                        cdp_session._lifecycle_events.append(event_data)
                    except Exception as e:
                        self.logger.error(f'[SessionManager] Failed to store lifecycle event: {e}')
            cdp_session.cdp_client.register.Page.lifecycleEvent(on_lifecycle_event)
        except Exception as e:
            error_str = str(e)
            if '-32001' in error_str or 'Session with given id not found' in error_str:
                self.logger.debug(f'[SessionManager] Target {cdp_session.target_id[:8]}... detached before monitoring could be enabled (normal for short-lived targets)')
            else:
                self.logger.warning(f'[SessionManager] Failed to enable monitoring for target {cdp_session.target_id[:8]}...: {e}')