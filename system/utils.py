import asyncio
import logging
import os
import platform
import re
import signal
import time
from collections.abc import Callable, Coroutine
from fnmatch import fnmatch
from functools import cache, wraps
from pathlib import Path
from sys import stderr
from typing import Any, ParamSpec, TypeVar
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
load_dotenv()
URL_PATTERN = re.compile('https?://[^\\s<>"\\\']+|www\\.[^\\s<>"\\\']+|[^\\s<>"\\\']+\\.[a-z]{2,}(?:/[^\\s<>"\\\']*)?', re.IGNORECASE)
logger = logging.getLogger(__name__)
_IMPORT_NOT_FOUND: type = type('_ImportNotFound', (), {})
_openai_bad_request_error: type | None = None

def _get_openai_bad_request_error() -> type | None:
    global _openai_bad_request_error
    if _openai_bad_request_error is None:
        try:
            from openai import BadRequestError
            _openai_bad_request_error = BadRequestError
        except ImportError:
            _openai_bad_request_error = _IMPORT_NOT_FOUND
    return _openai_bad_request_error if _openai_bad_request_error is not _IMPORT_NOT_FOUND else None
_exiting = False
R = TypeVar('R')
T = TypeVar('T')
P = ParamSpec('P')

class SignalHandler:

    def __init__(self, loop: asyncio.AbstractEventLoop | None=None, pause_callback: Callable[[], None] | None=None, resume_callback: Callable[[], None] | None=None, custom_exit_callback: Callable[[], None] | None=None, exit_on_second_int: bool=True, interruptible_task_patterns: list[str] | None=None):
        self.loop = loop or asyncio.get_event_loop()
        self.pause_callback = pause_callback
        self.resume_callback = resume_callback
        self.custom_exit_callback = custom_exit_callback
        self.exit_on_second_int = exit_on_second_int
        self.interruptible_task_patterns = interruptible_task_patterns or ['step', 'multi_act', 'get_next_action']
        self.is_windows = platform.system() == 'Windows'
        self._initialize_loop_state()
        self.original_sigint_handler = None
        self.original_sigterm_handler = None

    def _initialize_loop_state(self) -> None:
        setattr(self.loop, 'ctrl_c_pressed', False)
        setattr(self.loop, 'waiting_for_input', False)

    def register(self) -> None:
        try:
            if self.is_windows:

                def windows_handler(sig, frame):
                    print('\n\n🛑 Got Ctrl+C. Exiting immediately on Windows...\n', file=stderr)
                    if self.custom_exit_callback:
                        self.custom_exit_callback()
                    os._exit(0)
                self.original_sigint_handler = signal.signal(signal.SIGINT, windows_handler)
            else:
                self.original_sigint_handler = self.loop.add_signal_handler(signal.SIGINT, lambda: self.sigint_handler())
                self.original_sigterm_handler = self.loop.add_signal_handler(signal.SIGTERM, lambda: self.sigterm_handler())
        except Exception:
            pass

    def unregister(self) -> None:
        try:
            if self.is_windows:
                if self.original_sigint_handler:
                    signal.signal(signal.SIGINT, self.original_sigint_handler)
            else:
                self.loop.remove_signal_handler(signal.SIGINT)
                self.loop.remove_signal_handler(signal.SIGTERM)
                if self.original_sigint_handler:
                    signal.signal(signal.SIGINT, self.original_sigint_handler)
                if self.original_sigterm_handler:
                    signal.signal(signal.SIGTERM, self.original_sigterm_handler)
        except Exception as e:
            logger.warning(f'Error while unregistering signal handlers: {e}')

    def _handle_second_ctrl_c(self) -> None:
        global _exiting
        if not _exiting:
            _exiting = True
            if self.custom_exit_callback:
                try:
                    self.custom_exit_callback()
                except Exception as e:
                    logger.error(f'Error in exit callback: {e}')
        print('\n\n🛑  Got second Ctrl+C. Exiting immediately...\n', file=stderr)
        print('\x1b[?25h', end='', flush=True, file=stderr)
        print('\x1b[?25h', end='', flush=True)
        print('\x1b[0m', end='', flush=True, file=stderr)
        print('\x1b[0m', end='', flush=True)
        print('\x1b[?1l', end='', flush=True, file=stderr)
        print('\x1b[?1l', end='', flush=True)
        print('\x1b[?2004l', end='', flush=True, file=stderr)
        print('\x1b[?2004l', end='', flush=True)
        print('\r', end='', flush=True, file=stderr)
        print('\r', end='', flush=True)
        print('(tip: press [Enter] once to fix escape codes appearing after chrome exit)', file=stderr)
        os._exit(0)

    def sigint_handler(self) -> None:
        global _exiting
        if _exiting:
            os._exit(0)
        if getattr(self.loop, 'ctrl_c_pressed', False):
            if getattr(self.loop, 'waiting_for_input', False):
                return
            if self.exit_on_second_int:
                self._handle_second_ctrl_c()
        setattr(self.loop, 'ctrl_c_pressed', True)
        self._cancel_interruptible_tasks()
        if self.pause_callback:
            try:
                self.pause_callback()
            except Exception as e:
                logger.error(f'Error in pause callback: {e}')
        print('----------------------------------------------------------------------', file=stderr)

    def sigterm_handler(self) -> None:
        global _exiting
        if not _exiting:
            _exiting = True
            print('\n\n🛑 SIGTERM received. Exiting immediately...\n\n', file=stderr)
            if self.custom_exit_callback:
                self.custom_exit_callback()
        os._exit(0)

    def _cancel_interruptible_tasks(self) -> None:
        current_task = asyncio.current_task(self.loop)
        for task in asyncio.all_tasks(self.loop):
            if task != current_task and (not task.done()):
                task_name = task.get_name() if hasattr(task, 'get_name') else str(task)
                if any((pattern in task_name for pattern in self.interruptible_task_patterns)):
                    logger.debug(f'Cancelling task: {task_name}')
                    task.cancel()
                    task.add_done_callback(lambda t: t.exception() if t.cancelled() else None)
        if current_task and (not current_task.done()):
            task_name = current_task.get_name() if hasattr(current_task, 'get_name') else str(current_task)
            if any((pattern in task_name for pattern in self.interruptible_task_patterns)):
                logger.debug(f'Cancelling current task: {task_name}')
                current_task.cancel()

    def wait_for_resume(self) -> None:
        setattr(self.loop, 'waiting_for_input', True)
        original_handler = signal.getsignal(signal.SIGINT)
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
        except ValueError:
            pass
        green = '\x1b[32;1m'
        red = '\x1b[31m'
        blink = '\x1b[33;5m'
        unblink = '\x1b[0m'
        reset = '\x1b[0m'
        try:
            print(f'➡️  Press {green}[Enter]{reset} to resume or {red}[Ctrl+C]{reset} again to exit{blink}...{unblink} ', end='', flush=True, file=stderr)
            input()
            if self.resume_callback:
                self.resume_callback()
        except KeyboardInterrupt:
            self._handle_second_ctrl_c()
        finally:
            try:
                signal.signal(signal.SIGINT, original_handler)
                setattr(self.loop, 'waiting_for_input', False)
            except Exception:
                pass

    def reset(self) -> None:
        if hasattr(self.loop, 'ctrl_c_pressed'):
            setattr(self.loop, 'ctrl_c_pressed', False)
        if hasattr(self.loop, 'waiting_for_input'):
            setattr(self.loop, 'waiting_for_input', False)

def time_execution_sync(additional_text: str='') -> Callable[[Callable[P, R]], Callable[P, R]]:

    def decorator(func: Callable[P, R]) -> Callable[P, R]:

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.time()
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            if execution_time > 0.25:
                self_has_logger = args and getattr(args[0], 'logger', None)
                if self_has_logger:
                    logger = getattr(args[0], 'logger')
                elif 'agent' in kwargs:
                    logger = getattr(kwargs['agent'], 'logger')
                elif 'browser_session' in kwargs:
                    logger = getattr(kwargs['browser_session'], 'logger')
                else:
                    logger = logging.getLogger(__name__)
                logger.debug(f"⏳ {additional_text.strip('-')}() took {execution_time:.2f}s")
            return result
        return wrapper
    return decorator

def time_execution_async(additional_text: str='') -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:

    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start_time = time.time()
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time
            if execution_time > 0.25:
                self_has_logger = args and getattr(args[0], 'logger', None)
                if self_has_logger:
                    logger = getattr(args[0], 'logger')
                elif 'agent' in kwargs:
                    logger = getattr(kwargs['agent'], 'logger')
                elif 'browser_session' in kwargs:
                    logger = getattr(kwargs['browser_session'], 'logger')
                else:
                    logger = logging.getLogger(__name__)
                logger.debug(f"⏳ {additional_text.strip('-')}() took {execution_time:.2f}s")
            return result
        return wrapper
    return decorator

def singleton(cls):
    instance = [None]

    def wrapper(*args, **kwargs):
        if instance[0] is None:
            instance[0] = cls(*args, **kwargs)
        return instance[0]
    return wrapper

def check_env_variables(keys: list[str], any_or_all=all) -> bool:
    return any_or_all((os.getenv(key, '').strip() for key in keys))

def is_unsafe_pattern(pattern: str) -> bool:
    if '://' in pattern:
        _, pattern = pattern.split('://', 1)
    bare_domain = pattern.replace('.*', '').replace('*.', '')
    return '*' in bare_domain

def is_new_tab_page(url: str) -> bool:
    return url in ('about:blank', 'chrome://new-tab-page/', 'chrome://new-tab-page', 'chrome://newtab/', 'chrome://newtab')

def match_url_with_domain_pattern(url: str, domain_pattern: str, log_warnings: bool=False) -> bool:
    try:
        if is_new_tab_page(url):
            return False
        parsed_url = urlparse(url)
        scheme = parsed_url.scheme.lower() if parsed_url.scheme else ''
        domain = parsed_url.hostname.lower() if parsed_url.hostname else ''
        if not scheme or not domain:
            return False
        domain_pattern = domain_pattern.lower()
        if '://' in domain_pattern:
            pattern_scheme, pattern_domain = domain_pattern.split('://', 1)
        else:
            pattern_scheme = 'https'
            pattern_domain = domain_pattern
        if ':' in pattern_domain and (not pattern_domain.startswith(':')):
            pattern_domain = pattern_domain.split(':', 1)[0]
        if not fnmatch(scheme, pattern_scheme):
            return False
        if pattern_domain == '*' or domain == pattern_domain:
            return True
        if '*' in pattern_domain:
            if pattern_domain.count('*.') > 1 or pattern_domain.count('.*') > 1:
                if log_warnings:
                    logger = logging.getLogger(__name__)
                    logger.error(f'⛔️ Multiple wildcards in pattern=[{domain_pattern}] are not supported')
                return False
            if pattern_domain.endswith('.*'):
                if log_warnings:
                    logger = logging.getLogger(__name__)
                    logger.error(f'⛔️ Wildcard TLDs like in pattern=[{domain_pattern}] are not supported for security')
                return False
            bare_domain = pattern_domain.replace('*.', '')
            if '*' in bare_domain:
                if log_warnings:
                    logger = logging.getLogger(__name__)
                    logger.error(f'⛔️ Only *.domain style patterns are supported, ignoring pattern=[{domain_pattern}]')
                return False
            if pattern_domain.startswith('*.'):
                parent_domain = pattern_domain[2:]
                if domain == parent_domain or fnmatch(domain, parent_domain):
                    return True
            if fnmatch(domain, pattern_domain):
                return True
        return False
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f'⛔️ Error matching URL {url} with pattern {domain_pattern}: {type(e).__name__}: {e}')
        return False

def merge_dicts(a: dict, b: dict, path: tuple[str, ...]=()):
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_dicts(a[key], b[key], path + (str(key),))
            elif isinstance(a[key], list) and isinstance(b[key], list):
                a[key] = a[key] + b[key]
            elif a[key] != b[key]:
                raise Exception('Conflict at ' + '.'.join(path + (str(key),)))
        else:
            a[key] = b[key]
    return a

@cache
def get_browser_use_version() -> str:
    try:
        package_root = Path(__file__).parent.parent
        pyproject_path = package_root / 'pyproject.toml'
        if pyproject_path.exists():
            import re
            with open(pyproject_path, encoding='utf-8') as f:
                content = f.read()
                match = re.search('version\\s*=\\s*["\\\']([^"\\\']+)["\\\']', content)
                if match:
                    version = f'{match.group(1)}'
                    os.environ['LIBRARY_VERSION'] = version
                    return version
        from importlib.metadata import version as get_version
        version = str(get_version('browser-use'))
        os.environ['LIBRARY_VERSION'] = version
        return version
    except Exception as e:
        logger.debug(f'Error detecting browser-use version: {type(e).__name__}: {e}')
        return 'unknown'

async def check_latest_browser_use_version() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get('https://pypi.org/pypi/browser-use/json')
            if response.status_code == 200:
                data = response.json()
                return data['info']['version']
    except Exception:
        pass
    return None

@cache
def get_git_info() -> dict[str, str] | None:
    try:
        import subprocess
        package_root = Path(__file__).parent.parent
        git_dir = package_root / '.git'
        if not git_dir.exists():
            return None
        commit_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL).decode().strip()
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL).decode().strip()
        remote_url = subprocess.check_output(['git', 'config', '--get', 'remote.origin.url'], cwd=package_root, stderr=subprocess.DEVNULL).decode().strip()
        commit_timestamp = subprocess.check_output(['git', 'show', '-s', '--format=%ci', 'HEAD'], cwd=package_root, stderr=subprocess.DEVNULL).decode().strip()
        return {'commit_hash': commit_hash, 'branch': branch, 'remote_url': remote_url, 'commit_timestamp': commit_timestamp}
    except Exception as e:
        logger.debug(f'Error getting git info: {type(e).__name__}: {e}')
        return None

def _log_pretty_path(path: str | Path | None) -> str:
    if not path or not str(path).strip():
        return ''
    if not isinstance(path, (str, Path)):
        return f'<{type(path).__name__}>'
    pretty_path = str(path).replace(str(Path.home()), '~').replace(str(Path.cwd().resolve()), '.')
    if pretty_path.strip() and ' ' in pretty_path:
        pretty_path = f'"{pretty_path}"'
    return pretty_path

def _log_pretty_url(s: str, max_len: int | None=22) -> str:
    s = s.replace('https://', '').replace('http://', '').replace('www.', '')
    if max_len is not None and len(s) > max_len:
        return s[:max_len] + '…'
    return s

def create_task_with_error_handling(coro: Coroutine[Any, Any, T], *, name: str | None=None, logger_instance: logging.Logger | None=None, suppress_exceptions: bool=False) -> asyncio.Task[T]:
    task = asyncio.create_task(coro, name=name)
    log = logger_instance or logger

    def _handle_task_exception(t: asyncio.Task[T]) -> None:
        exc_to_raise = None
        try:
            exc = t.exception()
            if exc is not None:
                task_name = t.get_name() if hasattr(t, 'get_name') else 'unnamed'
                if suppress_exceptions:
                    log.error(f'Exception in background task [{task_name}]: {type(exc).__name__}: {exc}', exc_info=exc)
                else:
                    log.warning(f'Exception in background task [{task_name}]: {type(exc).__name__}: {exc}', exc_info=exc)
                    exc_to_raise = exc
        except asyncio.CancelledError:
            pass
        except Exception as e:
            task_name = t.get_name() if hasattr(t, 'get_name') else 'unnamed'
            log.error(f'Error handling exception in task [{task_name}]: {type(e).__name__}: {e}')
        if exc_to_raise is not None:
            raise exc_to_raise
    task.add_done_callback(_handle_task_exception)
    return task

def sanitize_surrogates(text: str) -> str:
    return text.encode('utf-8', errors='ignore').decode('utf-8')