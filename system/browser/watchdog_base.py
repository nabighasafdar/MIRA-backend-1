import asyncio
import inspect
import time
from collections.abc import Iterable
from typing import Any, ClassVar
from bubus import BaseEvent, EventBus
from pydantic import BaseModel, ConfigDict, Field
from system.browser.session import BrowserSession

class BaseWatchdog(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid', validate_assignment=False, revalidate_instances='never')
    LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = []
    EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []
    event_bus: EventBus = Field()
    browser_session: BrowserSession = Field()

    @property
    def logger(self):
        return self.browser_session.logger

    @staticmethod
    def attach_handler_to_session(browser_session: 'BrowserSession', event_class: type[BaseEvent[Any]], handler) -> None:
        event_bus = browser_session.event_bus
        assert hasattr(handler, '__name__'), 'Handler must have a __name__ attribute'
        assert handler.__name__.startswith('on_'), f'Handler {handler.__name__} must start with "on_"'
        assert handler.__name__.endswith(event_class.__name__), f'Handler {handler.__name__} must end with event type {event_class.__name__}'
        watchdog_instance = getattr(handler, '__self__', None)
        watchdog_class_name = watchdog_instance.__class__.__name__ if watchdog_instance else 'Unknown'
        LIFECYCLE_EVENT_NAMES = frozenset({'BrowserStartEvent', 'BrowserStopEvent', 'BrowserStoppedEvent', 'BrowserLaunchEvent', 'BrowserErrorEvent', 'BrowserKillEvent', 'BrowserReconnectingEvent', 'BrowserReconnectedEvent'})

        def make_unique_handler(actual_handler):

            async def unique_handler(event):
                if event.event_type not in LIFECYCLE_EVENT_NAMES and (not browser_session.is_cdp_connected):
                    if browser_session.is_reconnecting:
                        wait_timeout = browser_session.RECONNECT_WAIT_TIMEOUT
                        browser_session.logger.debug(f'🚌 [{watchdog_class_name}.{actual_handler.__name__}] ⏳ Waiting for reconnection ({wait_timeout}s)...')
                        try:
                            await asyncio.wait_for(browser_session._reconnect_event.wait(), timeout=wait_timeout)
                        except TimeoutError:
                            raise ConnectionError(f'[{watchdog_class_name}.{actual_handler.__name__}] Reconnection wait timed out after {wait_timeout}s')
                        if not browser_session.is_cdp_connected:
                            raise ConnectionError(f'[{watchdog_class_name}.{actual_handler.__name__}] Reconnection failed — CDP still not connected')
                    else:
                        browser_session.logger.debug(f'🚌 [{watchdog_class_name}.{actual_handler.__name__}] ⚡ Skipped — CDP not connected')
                        return None
                parent_event = event_bus.event_history.get(event.event_parent_id) if event.event_parent_id else None
                grandparent_event = event_bus.event_history.get(parent_event.event_parent_id) if parent_event and parent_event.event_parent_id else None
                parent = f'↲  triggered by on_{parent_event.event_type}#{parent_event.event_id[-4:]}' if parent_event else '👈 by Agent'
                grandparent = (f'↲  under {grandparent_event.event_type}#{grandparent_event.event_id[-4:]}' if grandparent_event else '👈 by Agent') if parent_event else ''
                event_str = f'#{event.event_id[-4:]}'
                time_start = time.time()
                watchdog_and_handler_str = f'[{watchdog_class_name}.{actual_handler.__name__}({event_str})]'.ljust(54)
                browser_session.logger.debug(f'🚌 {watchdog_and_handler_str} ⏳ Starting...       {parent} {grandparent}')
                try:
                    result = await actual_handler(event)
                    if isinstance(result, Exception):
                        raise result
                    time_end = time.time()
                    time_elapsed = time_end - time_start
                    result_summary = '' if result is None else f' ➡️ <{type(result).__name__}>'
                    parents_summary = f' {parent}'.replace('↲  triggered by ', '⤴  returned to  ').replace('👈 by Agent', '👉 returned to  Agent')
                    browser_session.logger.debug(f'🚌 {watchdog_and_handler_str} Succeeded ({time_elapsed:.2f}s){result_summary}{parents_summary}')
                    return result
                except Exception as e:
                    time_end = time.time()
                    time_elapsed = time_end - time_start
                    original_error = e
                    browser_session.logger.error(f'🚌 {watchdog_and_handler_str} ❌ Failed ({time_elapsed:.2f}s): {type(e).__name__}: {e}')
                    try:
                        if browser_session.agent_focus_target_id:
                            target_id_to_restore = browser_session.agent_focus_target_id
                            browser_session.logger.debug(f'🚌 {watchdog_and_handler_str} ⚠️ Session error detected, waiting for CDP events to sync (target: {target_id_to_restore})')
                            await browser_session.get_or_create_cdp_session(target_id=target_id_to_restore, focus=True)
                        else:
                            await browser_session.get_or_create_cdp_session(target_id=None, focus=True)
                    except Exception as sub_error:
                        if 'ConnectionClosedError' in str(type(sub_error)) or 'ConnectionError' in str(type(sub_error)):
                            browser_session.logger.error(f'🚌 {watchdog_and_handler_str} ❌ Browser closed or CDP Connection disconnected by remote. {type(sub_error).__name__}: {sub_error}\n')
                            raise
                        else:
                            browser_session.logger.error(f'🚌 {watchdog_and_handler_str} ❌ CDP connected but failed to re-create CDP session after error "{type(original_error).__name__}: {original_error}" in {actual_handler.__name__}({event.event_type}#{event.event_id[-4:]}): due to {type(sub_error).__name__}: {sub_error}\n')
                    raise
            return unique_handler
        unique_handler = make_unique_handler(handler)
        unique_handler.__name__ = f'{watchdog_class_name}.{handler.__name__}'
        existing_handlers = event_bus.handlers.get(event_class.__name__, [])
        handler_names = [getattr(h, '__name__', str(h)) for h in existing_handlers]
        if unique_handler.__name__ in handler_names:
            raise RuntimeError(f'[{watchdog_class_name}] Duplicate handler registration attempted! Handler {unique_handler.__name__} is already registered for {event_class.__name__}. This likely means attach_to_session() was called multiple times.')
        event_bus.on(event_class, unique_handler)

    @staticmethod
    def detach_handler_from_session(browser_session: 'BrowserSession', event_class: type[BaseEvent[Any]], handler) -> None:
        event_bus = browser_session.event_bus
        watchdog_instance = getattr(handler, '__self__', None)
        watchdog_class_name = watchdog_instance.__class__.__name__ if watchdog_instance else 'Unknown'
        unique_handler_name = f'{watchdog_class_name}.{handler.__name__}'
        existing_handlers = event_bus.handlers.get(event_class.__name__, [])
        for existing_handler in existing_handlers[:]:
            if getattr(existing_handler, '__name__', '') == unique_handler_name:
                existing_handlers.remove(existing_handler)
                break

    def attach_to_session(self) -> None:
        assert self.browser_session is not None, 'Root CDP client not initialized - browser may not be connected yet'
        from system.browser import events
        event_classes = {}
        for name in dir(events):
            obj = getattr(events, name)
            if inspect.isclass(obj) and issubclass(obj, BaseEvent) and (obj is not BaseEvent):
                event_classes[name] = obj
        registered_events = set()
        for method_name in dir(self):
            if method_name.startswith('on_') and callable(getattr(self, method_name)):
                event_name = method_name[3:]
                if event_name in event_classes:
                    event_class = event_classes[event_name]
                    if self.LISTENS_TO:
                        assert event_class in self.LISTENS_TO, f'[{self.__class__.__name__}] Handler {method_name} listens to {event_name} but {event_name} is not declared in LISTENS_TO: {[e.__name__ for e in self.LISTENS_TO]}'
                    handler = getattr(self, method_name)
                    self.attach_handler_to_session(self.browser_session, event_class, handler)
                    registered_events.add(event_class)
        if self.LISTENS_TO:
            missing_handlers = set(self.LISTENS_TO) - registered_events
            if missing_handlers:
                missing_names = [e.__name__ for e in missing_handlers]
                self.logger.warning(f"[{self.__class__.__name__}] LISTENS_TO declares {missing_names} but no handlers found (missing on_{'_, on_'.join(missing_names)} methods)")

    def __del__(self) -> None:
        try:
            for attr_name in dir(self):
                if attr_name.startswith('_') and attr_name.endswith('_task'):
                    try:
                        task = getattr(self, attr_name)
                        if hasattr(task, 'cancel') and callable(task.cancel) and (not task.done()):
                            task.cancel()
                    except Exception:
                        pass
                if attr_name.startswith('_') and attr_name.endswith('_tasks') and isinstance(getattr(self, attr_name), Iterable):
                    for task in getattr(self, attr_name):
                        try:
                            if hasattr(task, 'cancel') and callable(task.cancel) and (not task.done()):
                                task.cancel()
                        except Exception:
                            pass
        except Exception as e:
            from system.utils import logger
            logger.error(f'⚠️ Error during BrowserSession {self.__class__.__name__} garbage collection __del__(): {type(e)}: {e}')