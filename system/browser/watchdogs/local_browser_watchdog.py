import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
import psutil
from bubus import BaseEvent
from pydantic import PrivateAttr
from system.browser.events import BrowserKillEvent, BrowserLaunchEvent, BrowserLaunchResult, BrowserStopEvent
from system.browser.watchdog_base import BaseWatchdog
from system.observability import observe_debug
if TYPE_CHECKING:
    pass

class LocalBrowserWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [BrowserLaunchEvent, BrowserKillEvent, BrowserStopEvent]
    EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []
    _subprocess: psutil.Process | None = PrivateAttr(default=None)
    _owns_browser_resources: bool = PrivateAttr(default=True)
    _temp_dirs_to_cleanup: list[Path] = PrivateAttr(default_factory=list)
    _original_user_data_dir: str | None = PrivateAttr(default=None)

    @observe_debug(ignore_input=True, ignore_output=True, name='browser_launch_event')
    async def on_BrowserLaunchEvent(self, event: BrowserLaunchEvent) -> BrowserLaunchResult:
        try:
            self.logger.debug('[LocalBrowserWatchdog] Received BrowserLaunchEvent, launching local browser...')
            process, cdp_url = await self._launch_browser()
            self._subprocess = process
            return BrowserLaunchResult(cdp_url=cdp_url)
        except Exception as e:
            self.logger.error(f'[LocalBrowserWatchdog] Exception in on_BrowserLaunchEvent: {e}', exc_info=True)
            raise

    async def on_BrowserKillEvent(self, event: BrowserKillEvent) -> None:
        self.logger.debug('[LocalBrowserWatchdog] Killing local browser process')
        if self._subprocess:
            await self._cleanup_process(self._subprocess)
            self._subprocess = None
        for temp_dir in self._temp_dirs_to_cleanup:
            self._cleanup_temp_dir(temp_dir)
        self._temp_dirs_to_cleanup.clear()
        if self._original_user_data_dir is not None:
            self.browser_session.browser_profile.user_data_dir = self._original_user_data_dir
            self._original_user_data_dir = None
        self.logger.debug('[LocalBrowserWatchdog] Browser cleanup completed')

    async def on_BrowserStopEvent(self, event: BrowserStopEvent) -> None:
        if self.browser_session.is_local and self._subprocess:
            self.logger.debug('[LocalBrowserWatchdog] BrowserStopEvent received, dispatching BrowserKillEvent')
            self.event_bus.dispatch(BrowserKillEvent())

    @observe_debug(ignore_input=True, ignore_output=True, name='launch_browser_process')
    async def _launch_browser(self, max_retries: int=3) -> tuple[psutil.Process, str]:
        profile = self.browser_session.browser_profile
        self._original_user_data_dir = str(profile.user_data_dir) if profile.user_data_dir else None
        self._temp_dirs_to_cleanup = []
        for attempt in range(max_retries):
            try:
                launch_args = profile.get_args()
                debug_port = self._find_free_port()
                launch_args.extend([f'--remote-debugging-port={debug_port}'])
                assert '--user-data-dir' in str(launch_args), 'User data dir must be set somewhere in launch args to a non-default path, otherwise Chrome will not let us attach via CDP'
                if profile.executable_path:
                    browser_path = profile.executable_path
                    self.logger.debug(f'[LocalBrowserWatchdog] 📦 Using custom local browser executable_path= {browser_path}')
                else:
                    browser_path = self._find_installed_browser_path()
                    if not browser_path:
                        self.logger.error('[LocalBrowserWatchdog] ⚠️ No local browser binary found, installing browser using playwright subprocess...')
                        browser_path = await self._install_browser_with_playwright()
                self.logger.debug(f'[LocalBrowserWatchdog] 📦 Found local browser installed at executable_path= {browser_path}')
                if not browser_path:
                    raise RuntimeError('No local Chrome/Chromium install found, and failed to install with playwright')
                self.logger.debug(f'[LocalBrowserWatchdog] 🚀 Launching browser subprocess with {len(launch_args)} args...')
                self.logger.debug(f'[LocalBrowserWatchdog] 📂 user_data_dir={profile.user_data_dir}, profile_directory={profile.profile_directory}')
                subprocess = await asyncio.create_subprocess_exec(browser_path, *launch_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                self.logger.debug(f'[LocalBrowserWatchdog] 🎭 Browser running with browser_pid= {subprocess.pid} 🔗 listening on CDP port :{debug_port}')
                process = psutil.Process(subprocess.pid)
                cdp_url = await self._wait_for_cdp_url(debug_port)
                currently_used_dir = str(profile.user_data_dir)
                unused_temp_dirs = [tmp_dir for tmp_dir in self._temp_dirs_to_cleanup if str(tmp_dir) != currently_used_dir]
                for tmp_dir in unused_temp_dirs:
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
                if currently_used_dir and 'browseruse-tmp-' in currently_used_dir:
                    self._temp_dirs_to_cleanup = [Path(currently_used_dir)]
                else:
                    self._temp_dirs_to_cleanup = []
                return (process, cdp_url)
            except Exception as e:
                error_str = str(e).lower()
                if any((err in error_str for err in ['singletonlock', 'user data directory', 'cannot create', 'already in use'])):
                    self.logger.warning(f'Browser launch failed (attempt {attempt + 1}/{max_retries}): {e}')
                    if attempt < max_retries - 1:
                        tmp_dir = Path(tempfile.mkdtemp(prefix='browseruse-tmp-'))
                        self._temp_dirs_to_cleanup.append(tmp_dir)
                        profile.user_data_dir = str(tmp_dir)
                        self.logger.debug(f'Retrying with temporary user_data_dir: {tmp_dir}')
                        await asyncio.sleep(0.5)
                        continue
                if self._original_user_data_dir is not None:
                    profile.user_data_dir = self._original_user_data_dir
                for tmp_dir in self._temp_dirs_to_cleanup:
                    try:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                    except Exception:
                        pass
                raise
        if self._original_user_data_dir is not None:
            profile.user_data_dir = self._original_user_data_dir
        raise RuntimeError(f'Failed to launch browser after {max_retries} attempts')

    @staticmethod
    def _find_installed_browser_path() -> str | None:
        import glob
        import platform
        from pathlib import Path
        system = platform.system()
        patterns = []
        playwright_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
        if system == 'Darwin':
            if not playwright_path:
                playwright_path = '~/Library/Caches/ms-playwright'
            patterns = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', f'{playwright_path}/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium', '/Applications/Chromium.app/Contents/MacOS/Chromium', '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary', '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser', f'{playwright_path}/chromium_headless_shell-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium']
        elif system == 'Linux':
            if not playwright_path:
                playwright_path = '~/.cache/ms-playwright'
            patterns = ['/usr/bin/google-chrome-stable', '/usr/bin/google-chrome', '/usr/local/bin/google-chrome', f'{playwright_path}/chromium-*/chrome-linux*/chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/local/bin/chromium', '/snap/bin/chromium', '/usr/bin/google-chrome-beta', '/usr/bin/google-chrome-dev', '/usr/bin/brave-browser', f'{playwright_path}/chromium_headless_shell-*/chrome-linux*/chrome']
        elif system == 'Windows':
            if not playwright_path:
                playwright_path = '%LOCALAPPDATA%\\ms-playwright'
            patterns = ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe', '%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe', '%PROGRAMFILES%\\Google\\Chrome\\Application\\chrome.exe', '%PROGRAMFILES(X86)%\\Google\\Chrome\\Application\\chrome.exe', f'{playwright_path}\\chromium-*\\chrome-win\\chrome.exe', 'C:\\Program Files\\Chromium\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Chromium\\Application\\chrome.exe', '%LOCALAPPDATA%\\Chromium\\Application\\chrome.exe', 'C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe', 'C:\\Program Files (x86)\\BraveSoftware\\Brave-Browser\\Application\\brave.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe', '%LOCALAPPDATA%\\Microsoft\\Edge\\Application\\msedge.exe', f'{playwright_path}\\chromium_headless_shell-*\\chrome-win\\chrome.exe']
        for pattern in patterns:
            expanded_pattern = Path(pattern).expanduser()
            if system == 'Windows':
                pattern_str = str(expanded_pattern)
                for env_var in ['%LOCALAPPDATA%', '%PROGRAMFILES%', '%PROGRAMFILES(X86)%']:
                    if env_var in pattern_str:
                        env_key = env_var.strip('%').replace('(X86)', ' (x86)')
                        env_value = os.environ.get(env_key, '')
                        if env_value:
                            pattern_str = pattern_str.replace(env_var, env_value)
                expanded_pattern = Path(pattern_str)
            pattern_str = str(expanded_pattern)
            if '*' in pattern_str:
                matches = glob.glob(pattern_str)
                if matches:
                    matches.sort()
                    browser_path = matches[-1]
                    if Path(browser_path).exists() and Path(browser_path).is_file():
                        return browser_path
            elif expanded_pattern.exists() and expanded_pattern.is_file():
                return str(expanded_pattern)
        return None

    async def _install_browser_with_playwright(self) -> str:
        import platform
        cmd = ['uvx', 'playwright', 'install', 'chrome']
        if platform.system() == 'Linux':
            cmd.append('--with-deps')
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
            self.logger.debug(f'[LocalBrowserWatchdog] 📦 Playwright install output: {stdout}')
            browser_path = self._find_installed_browser_path()
            if browser_path:
                return browser_path
            self.logger.error(f'[LocalBrowserWatchdog] ❌ Playwright local browser installation error: \n{stdout}\n{stderr}')
            raise RuntimeError('No local browser path found after: uvx playwright install chrome')
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError('Timeout getting browser path from playwright')
        except Exception as e:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise RuntimeError(f'Error getting browser path: {e}')

    @staticmethod
    def _find_free_port() -> int:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    @staticmethod
    async def _wait_for_cdp_url(port: int, timeout: float=30) -> str:
        import aiohttp
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f'http://127.0.0.1:{port}/json/version') as resp:
                        if resp.status == 200:
                            return f'http://127.0.0.1:{port}/'
                        else:
                            await asyncio.sleep(0.1)
            except Exception:
                await asyncio.sleep(0.1)
        raise TimeoutError(f'Browser did not start within {timeout} seconds')

    @staticmethod
    async def _cleanup_process(process: psutil.Process) -> None:
        if not process:
            return
        try:
            process.terminate()
            for _ in range(50):
                if not process.is_running():
                    return
                await asyncio.sleep(0.1)
            if process.is_running():
                process.kill()
                await asyncio.sleep(0.1)
        except psutil.NoSuchProcess:
            pass
        except Exception:
            pass

    def _cleanup_temp_dir(self, temp_dir: Path | str) -> None:
        if not temp_dir:
            return
        try:
            temp_path = Path(temp_dir)
            if 'browseruse-tmp-' in str(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)
        except Exception as e:
            self.logger.debug(f'Failed to cleanup temp dir {temp_dir}: {e}')

    @property
    def browser_pid(self) -> int | None:
        if self._subprocess:
            return self._subprocess.pid
        return None

    @staticmethod
    async def get_browser_pid_via_cdp(browser) -> int | None:
        try:
            cdp_session = await browser.new_browser_cdp_session()
            result = await cdp_session.send('SystemInfo.getProcessInfo')
            process_info = result.get('processInfo', {})
            pid = process_info.get('id')
            await cdp_session.detach()
            return pid
        except Exception:
            return None