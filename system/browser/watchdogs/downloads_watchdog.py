import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse
import anyio
from bubus import BaseEvent
from cdp_use.cdp.browser import DownloadProgressEvent as CDPDownloadProgressEvent
from cdp_use.cdp.browser import DownloadWillBeginEvent
from cdp_use.cdp.network import ResponseReceivedEvent
from cdp_use.cdp.target import SessionID, TargetID
from pydantic import PrivateAttr
from system.browser.events import BrowserLaunchEvent, BrowserStateRequestEvent, BrowserStoppedEvent, DownloadProgressEvent, DownloadStartedEvent, FileDownloadedEvent, NavigationCompleteEvent, TabClosedEvent, TabCreatedEvent
from system.browser.watchdog_base import BaseWatchdog
from system.utils import create_task_with_error_handling
if TYPE_CHECKING:
    pass

class DownloadsWatchdog(BaseWatchdog):
    LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [BrowserLaunchEvent, BrowserStateRequestEvent, BrowserStoppedEvent, TabCreatedEvent, TabClosedEvent, NavigationCompleteEvent]
    EMITS: ClassVar[list[type[BaseEvent[Any]]]] = [DownloadProgressEvent, DownloadStartedEvent, FileDownloadedEvent]
    _sessions_with_listeners: set[str] = PrivateAttr(default_factory=set)
    _active_downloads: dict[str, Any] = PrivateAttr(default_factory=dict)
    _pdf_viewer_cache: dict[str, bool] = PrivateAttr(default_factory=dict)
    _download_cdp_session_setup: bool = PrivateAttr(default=False)
    _download_cdp_session: Any = PrivateAttr(default=None)
    _cdp_event_tasks: set[asyncio.Task] = PrivateAttr(default_factory=set)
    _cdp_downloads_info: dict[str, dict[str, Any]] = PrivateAttr(default_factory=dict)
    _session_pdf_urls: dict[str, str] = PrivateAttr(default_factory=dict)
    _initial_downloads_snapshot: set[str] = PrivateAttr(default_factory=set)
    _network_monitored_targets: set[str] = PrivateAttr(default_factory=set)
    _detected_downloads: set[str] = PrivateAttr(default_factory=set)
    _network_callback_registered: bool = PrivateAttr(default=False)
    _download_start_callbacks: list[Any] = PrivateAttr(default_factory=list)
    _download_progress_callbacks: list[Any] = PrivateAttr(default_factory=list)
    _download_complete_callbacks: list[Any] = PrivateAttr(default_factory=list)

    def register_download_callbacks(self, on_start: Any | None=None, on_progress: Any | None=None, on_complete: Any | None=None) -> None:
        self.logger.debug(f'[DownloadsWatchdog] Registering callbacks: start={on_start is not None}, progress={on_progress is not None}, complete={on_complete is not None}')
        if on_start:
            self._download_start_callbacks.append(on_start)
            self.logger.debug(f'[DownloadsWatchdog] Registered start callback, now have {len(self._download_start_callbacks)} start callbacks')
        if on_progress:
            self._download_progress_callbacks.append(on_progress)
        if on_complete:
            self._download_complete_callbacks.append(on_complete)

    def unregister_download_callbacks(self, on_start: Any | None=None, on_progress: Any | None=None, on_complete: Any | None=None) -> None:
        if on_start and on_start in self._download_start_callbacks:
            self._download_start_callbacks.remove(on_start)
        if on_progress and on_progress in self._download_progress_callbacks:
            self._download_progress_callbacks.remove(on_progress)
        if on_complete and on_complete in self._download_complete_callbacks:
            self._download_complete_callbacks.remove(on_complete)

    async def on_BrowserLaunchEvent(self, event: BrowserLaunchEvent) -> None:
        self.logger.debug(f'[DownloadsWatchdog] Received BrowserLaunchEvent, EventBus ID: {id(self.event_bus)}')
        downloads_path = self.browser_session.browser_profile.downloads_path
        if downloads_path:
            expanded_path = Path(downloads_path).expanduser().resolve()
            expanded_path.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f'[DownloadsWatchdog] Ensured downloads directory exists: {expanded_path}')
            if expanded_path.exists():
                for f in expanded_path.iterdir():
                    if f.is_file() and (not f.name.startswith('.')):
                        self._initial_downloads_snapshot.add(f.name)
                self.logger.debug(f'[DownloadsWatchdog] Captured initial downloads: {len(self._initial_downloads_snapshot)} files')

    async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
        assert self.browser_session.browser_profile.downloads_path is not None, 'Downloads path must be configured'
        if event.target_id:
            await self.attach_to_target(event.target_id)
        else:
            self.logger.warning(f'[DownloadsWatchdog] No target found for tab {event.target_id}')

    async def on_TabClosedEvent(self, event: TabClosedEvent) -> None:
        pass

    async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> None:
        self.logger.debug(f'[DownloadsWatchdog] on_BrowserStateRequestEvent started, event_id={event.event_id[-4:]}')
        try:
            cdp_session = await self.browser_session.get_or_create_cdp_session()
        except ValueError:
            self.logger.warning(f'[DownloadsWatchdog] No valid focus, skipping BrowserStateRequestEvent {event.event_id[-4:]}')
            return
        self.logger.debug(f"[DownloadsWatchdog] About to call get_current_page_url(), target_id={(cdp_session.target_id[-4:] if cdp_session.target_id else 'None')}")
        url = await self.browser_session.get_current_page_url()
        self.logger.debug(f"[DownloadsWatchdog] Got URL: {(url[:80] if url else 'None')}")
        if not url:
            self.logger.warning(f'[DownloadsWatchdog] No URL found for BrowserStateRequestEvent {event.event_id[-4:]}')
            return
        target_id = cdp_session.target_id
        self.logger.debug(f'[DownloadsWatchdog] About to dispatch NavigationCompleteEvent for target {target_id[-4:]}')
        self.event_bus.dispatch(NavigationCompleteEvent(event_type='NavigationCompleteEvent', url=url, target_id=target_id, event_parent_id=event.event_id))
        self.logger.debug('[DownloadsWatchdog] Successfully completed BrowserStateRequestEvent')

    async def on_BrowserStoppedEvent(self, event: BrowserStoppedEvent) -> None:
        for task in list(self._cdp_event_tasks):
            if not task.done():
                task.cancel()
        if self._cdp_event_tasks:
            await asyncio.gather(*self._cdp_event_tasks, return_exceptions=True)
        self._cdp_event_tasks.clear()
        self._download_cdp_session = None
        self._download_cdp_session_setup = False
        self._sessions_with_listeners.clear()
        self._active_downloads.clear()
        self._pdf_viewer_cache.clear()
        self._session_pdf_urls.clear()
        self._network_monitored_targets.clear()
        self._detected_downloads.clear()
        self._initial_downloads_snapshot.clear()
        self._network_callback_registered = False

    async def on_NavigationCompleteEvent(self, event: NavigationCompleteEvent) -> None:
        self.logger.debug(f'[DownloadsWatchdog] NavigationCompleteEvent received for {event.url}, tab #{event.target_id[-4:]}')
        if event.url in self._pdf_viewer_cache:
            del self._pdf_viewer_cache[event.url]
        auto_download_enabled = self._is_auto_download_enabled()
        if not auto_download_enabled:
            return
        target_id = event.target_id
        self.logger.debug(f'[DownloadsWatchdog] Got target_id={target_id} for tab #{event.target_id[-4:]}')
        is_pdf = await self.check_for_pdf_viewer(target_id)
        if is_pdf:
            self.logger.debug(f'[DownloadsWatchdog] 📄 PDF detected at {event.url}, triggering auto-download...')
            download_path = await self.trigger_pdf_download(target_id)
            if not download_path:
                self.logger.warning(f'[DownloadsWatchdog] ⚠️ PDF download failed for {event.url}')

    def _is_auto_download_enabled(self) -> bool:
        return self.browser_session.browser_profile.auto_download_pdfs

    async def attach_to_target(self, target_id: TargetID) -> None:

        def download_will_begin_handler(event: DownloadWillBeginEvent, session_id: SessionID | None) -> None:
            self.logger.debug(f'[DownloadsWatchdog] Download will begin: {event}')
            guid = event.get('guid', '')
            url = event.get('url', '')
            suggested_filename = event.get('suggestedFilename', 'download')
            try:
                assert suggested_filename, 'CDP DownloadWillBegin missing suggestedFilename'
                self._cdp_downloads_info[guid] = {'url': url, 'suggested_filename': suggested_filename, 'handled': False}
            except (AssertionError, KeyError):
                pass
            download_info = {'guid': guid, 'url': url, 'suggested_filename': suggested_filename, 'auto_download': False}
            self.logger.debug(f'[DownloadsWatchdog] Calling {len(self._download_start_callbacks)} start callbacks')
            for callback in self._download_start_callbacks:
                try:
                    self.logger.debug(f'[DownloadsWatchdog] Calling start callback: {callback}')
                    callback(download_info)
                except Exception as e:
                    self.logger.debug(f'[DownloadsWatchdog] Error in download start callback: {e}')
            self.event_bus.dispatch(DownloadStartedEvent(guid=guid, url=url, suggested_filename=suggested_filename, auto_download=False))
            task = create_task_with_error_handling(self._handle_cdp_download(event, target_id, session_id), name='handle_cdp_download', logger_instance=self.logger, suppress_exceptions=True)
            self._cdp_event_tasks.add(task)
            task.add_done_callback(lambda t: self._cdp_event_tasks.discard(t))

        def download_progress_handler(event: CDPDownloadProgressEvent, session_id: SessionID | None) -> None:
            guid = event.get('guid', '')
            state = event.get('state', '')
            received_bytes = int(event.get('receivedBytes', 0))
            total_bytes = int(event.get('totalBytes', 0))
            progress_info = {'guid': guid, 'received_bytes': received_bytes, 'total_bytes': total_bytes, 'state': state}
            for callback in self._download_progress_callbacks:
                try:
                    callback(progress_info)
                except Exception as e:
                    self.logger.debug(f'[DownloadsWatchdog] Error in download progress callback: {e}')
            from system.browser.events import DownloadProgressEvent as DownloadProgressEventInternal
            self.event_bus.dispatch(DownloadProgressEventInternal(guid=guid, received_bytes=received_bytes, total_bytes=total_bytes, state=state))
            if state == 'completed':
                file_path = event.get('filePath')
                if self.browser_session.is_local:
                    if file_path:
                        self.logger.debug(f'[DownloadsWatchdog] Download completed: {file_path}')
                        self._track_download(file_path, guid=guid)
                        try:
                            if guid in self._cdp_downloads_info:
                                self._cdp_downloads_info[guid]['handled'] = True
                        except (KeyError, AttributeError):
                            pass
                    else:
                        self.logger.debug('[DownloadsWatchdog] No filePath in progress event; detecting via filesystem')
                        downloads_path = self.browser_session.browser_profile.downloads_path
                        if downloads_path:
                            downloads_dir = Path(downloads_path).expanduser().resolve()
                            if downloads_dir.exists():
                                for f in downloads_dir.iterdir():
                                    if f.is_file() and (not f.name.startswith('.')) and (f.name not in self._initial_downloads_snapshot):
                                        if f.stat().st_size > 4:
                                            self._initial_downloads_snapshot.add(f.name)
                                            self.logger.debug(f'[DownloadsWatchdog] Detected new download: {f.name}')
                                            self._track_download(str(f))
                                            try:
                                                if guid in self._cdp_downloads_info:
                                                    self._cdp_downloads_info[guid]['handled'] = True
                                            except (KeyError, AttributeError):
                                                pass
                                            break
                else:
                    info = self._cdp_downloads_info.get(guid, {})
                    try:
                        suggested_filename = info.get('suggested_filename') or (Path(file_path).name if file_path else 'download')
                        downloads_path = str(self.browser_session.browser_profile.downloads_path or '')
                        effective_path = file_path or str(Path(downloads_path) / suggested_filename)
                        file_name = Path(effective_path).name
                        file_ext = Path(file_name).suffix.lower().lstrip('.')
                        self.event_bus.dispatch(FileDownloadedEvent(guid=guid, url=info.get('url', ''), path=str(effective_path), file_name=file_name, file_size=0, file_type=file_ext if file_ext else None))
                        self.logger.debug(f'[DownloadsWatchdog] ✅ (remote) Download completed: {effective_path}')
                    finally:
                        if guid in self._cdp_downloads_info:
                            del self._cdp_downloads_info[guid]
        try:
            downloads_path_raw = self.browser_session.browser_profile.downloads_path
            if not downloads_path_raw:
                return
            if self._download_cdp_session_setup:
                self.logger.debug('[DownloadsWatchdog] Download listener already set up for browser session')
                return
            if not self._download_cdp_session_setup:
                cdp_client = self.browser_session.cdp_client
                downloads_path = self.browser_session.browser_profile.downloads_path
                if not downloads_path:
                    self.logger.warning('[DownloadsWatchdog] No downloads path configured, skipping CDP download setup')
                    return
                expanded_downloads_path = Path(downloads_path).expanduser().resolve()
                await cdp_client.send.Browser.setDownloadBehavior(params={'behavior': 'allow', 'downloadPath': str(expanded_downloads_path), 'eventsEnabled': True})
                cdp_client.register.Browser.downloadWillBegin(download_will_begin_handler)
                cdp_client.register.Browser.downloadProgress(download_progress_handler)
                self._download_cdp_session_setup = True
                self.logger.debug('[DownloadsWatchdog] Set up CDP download listeners')
        except Exception as e:
            self.logger.warning(f'[DownloadsWatchdog] Failed to set up CDP download listener for target {target_id}: {e}')
        await self._setup_network_monitoring(target_id)

    async def _setup_network_monitoring(self, target_id: TargetID) -> None:
        if target_id in self._network_monitored_targets:
            self.logger.debug(f'[DownloadsWatchdog] Network monitoring already enabled for target {target_id[-4:]}')
            return
        if not self._is_auto_download_enabled():
            self.logger.debug('[DownloadsWatchdog] Auto-download disabled, skipping network monitoring')
            return
        try:
            cdp_client = self.browser_session.cdp_client
            if not self._network_callback_registered:

                def on_response_received(event: ResponseReceivedEvent, session_id: str | None) -> None:
                    try:
                        if not self.browser_session.session_manager:
                            self.logger.warning('[DownloadsWatchdog] Session manager not found, skipping network monitoring')
                            return
                        event_target_id = self.browser_session.session_manager.get_target_id_from_session_id(session_id)
                        if not event_target_id:
                            return
                        if event_target_id not in self._network_monitored_targets:
                            return
                        response = event.get('response', {})
                        url = response.get('url', '')
                        content_type = response.get('mimeType', '').lower()
                        headers = {k.lower(): v for k, v in response.get('headers', {}).items()}
                        request_type = event.get('type', '')
                        if not url.startswith('http'):
                            return
                        if request_type in ('Fetch', 'XHR'):
                            return
                        is_pdf = 'application/pdf' in content_type
                        content_disposition = str(headers.get('content-disposition', '')).lower()
                        is_download_attachment = 'attachment' in content_disposition
                        unwanted_content_types = ['image/', 'video/', 'audio/', 'text/css', 'text/javascript', 'application/javascript', 'application/x-javascript', 'text/html', 'application/json', 'font/', 'application/font', 'application/x-font']
                        is_unwanted_type = any((content_type.startswith(prefix) for prefix in unwanted_content_types))
                        if is_unwanted_type:
                            return
                        url_lower = url.lower().split('?')[0]
                        unwanted_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico', '.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.mp4', '.webm', '.mp3', '.wav', '.ogg']
                        if any((url_lower.endswith(ext) for ext in unwanted_extensions)):
                            return
                        if not (is_pdf or is_download_attachment):
                            return
                        existing_path = self._session_pdf_urls.get(url)
                        if existing_path:
                            if os.path.exists(existing_path):
                                return
                            del self._session_pdf_urls[url]
                        if url in self._detected_downloads:
                            self.logger.debug(f'[DownloadsWatchdog] Already detected download: {url[:80]}...')
                            return
                        self._detected_downloads.add(url)
                        suggested_filename = None
                        if 'filename=' in content_disposition:
                            import re
                            filename_match = re.search('filename[^;=\\n]*=(([\\\'"]).*?\\2|[^;\\n]*)', content_disposition)
                            if filename_match:
                                suggested_filename = filename_match.group(1).strip('\'"')
                        self.logger.info(f'[DownloadsWatchdog] 🔍 Detected downloadable content via network: {url[:80]}...')
                        self.logger.debug(f'[DownloadsWatchdog]   Content-Type: {content_type}, Is PDF: {is_pdf}, Is Attachment: {is_download_attachment}')

                        async def download_in_background():
                            try:
                                download_path = await self.download_file_from_url(url=url, target_id=event_target_id, content_type=content_type, suggested_filename=suggested_filename)
                                if download_path:
                                    self.logger.info(f'[DownloadsWatchdog] ✅ Successfully downloaded: {download_path}')
                                else:
                                    self.logger.warning(f'[DownloadsWatchdog] ⚠️  Failed to download: {url[:80]}...')
                            except Exception as e:
                                self.logger.error(f'[DownloadsWatchdog] Error downloading in background: {type(e).__name__}: {e}')
                            finally:
                                self._detected_downloads.discard(url)
                        task = create_task_with_error_handling(download_in_background(), name='download_in_background', logger_instance=self.logger, suppress_exceptions=True)
                        self._cdp_event_tasks.add(task)
                        task.add_done_callback(lambda t: self._cdp_event_tasks.discard(t))
                    except Exception as e:
                        self.logger.error(f'[DownloadsWatchdog] Error in network response handler: {type(e).__name__}: {e}')
                cdp_client.register.Network.responseReceived(on_response_received)
                self._network_callback_registered = True
                self.logger.debug('[DownloadsWatchdog] ✅ Registered global network response callback')
            cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
            await cdp_client.send.Network.enable(session_id=cdp_session.session_id)
            self.logger.debug(f'[DownloadsWatchdog] Enabled Network domain for target {target_id[-4:]}')
            self._network_monitored_targets.add(target_id)
            self.logger.debug(f'[DownloadsWatchdog] ✅ Network monitoring enabled for target {target_id[-4:]}')
        except Exception as e:
            self.logger.warning(f'[DownloadsWatchdog] Failed to set up network monitoring for target {target_id}: {e}')

    async def download_file_from_url(self, url: str, target_id: TargetID, content_type: str | None=None, suggested_filename: str | None=None) -> str | None:
        if not self.browser_session.browser_profile.downloads_path:
            self.logger.warning('[DownloadsWatchdog] No downloads path configured')
            return None
        if url in self._session_pdf_urls:
            existing_path = self._session_pdf_urls[url]
            if os.path.exists(existing_path):
                self.logger.debug(f'[DownloadsWatchdog] File already downloaded in session: {existing_path}')
                return existing_path
            self.logger.debug(f'[DownloadsWatchdog] Cached download path no longer exists, re-downloading: {existing_path}')
            del self._session_pdf_urls[url]
        try:
            temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
            if suggested_filename:
                filename = suggested_filename
            else:
                filename = os.path.basename(url.split('?')[0])
                if not filename or '.' not in filename:
                    if content_type and 'pdf' in content_type:
                        filename = 'document.pdf'
                    else:
                        filename = 'download'
            downloads_dir = str(self.browser_session.browser_profile.downloads_path)
            os.makedirs(downloads_dir, exist_ok=True)
            final_filename = filename
            existing_files = os.listdir(downloads_dir)
            if filename in existing_files:
                base, ext = os.path.splitext(filename)
                counter = 1
                while f'{base} ({counter}){ext}' in existing_files:
                    counter += 1
                final_filename = f'{base} ({counter}){ext}'
                self.logger.debug(f'[DownloadsWatchdog] File exists, using: {final_filename}')
            self.logger.debug(f'[DownloadsWatchdog] Downloading from: {url[:100]}...')
            escaped_url = json.dumps(url)
            result = await asyncio.wait_for(temp_session.cdp_client.send.Runtime.evaluate(params={'expression': f"\n\t\t\t\t(async () => {{\n\t\t\t\t\ttry {{\n\t\t\t\t\t\tconst response = await fetch({escaped_url}, {{\n\t\t\t\t\t\t\tcache: 'force-cache'\n\t\t\t\t\t\t}});\n\t\t\t\t\t\tif (!response.ok) {{\n\t\t\t\t\t\t\tthrow new Error(`HTTP error! status: ${{response.status}}`);\n\t\t\t\t\t\t}}\n\t\t\t\t\t\tconst blob = await response.blob();\n\t\t\t\t\t\tconst arrayBuffer = await blob.arrayBuffer();\n\t\t\t\t\t\tconst uint8Array = new Uint8Array(arrayBuffer);\n\n\t\t\t\t\t\treturn {{\n\t\t\t\t\t\t\tdata: Array.from(uint8Array),\n\t\t\t\t\t\t\tresponseSize: uint8Array.length\n\t\t\t\t\t\t}};\n\t\t\t\t\t}} catch (error) {{\n\t\t\t\t\t\tthrow new Error(`Fetch failed: ${{error.message}}`);\n\t\t\t\t\t}}\n\t\t\t\t}})()\n\t\t\t\t", 'awaitPromise': True, 'returnByValue': True}, session_id=temp_session.session_id), timeout=15.0)
            download_result = result.get('result', {}).get('value', {})
            if download_result and download_result.get('data') and (len(download_result['data']) > 0):
                download_path = os.path.join(downloads_dir, final_filename)
                async with await anyio.open_file(download_path, 'wb') as f:
                    await f.write(bytes(download_result['data']))
                if os.path.exists(download_path):
                    actual_size = os.path.getsize(download_path)
                    self.logger.debug(f'[DownloadsWatchdog] File written: {download_path} ({actual_size} bytes)')
                    file_ext = Path(final_filename).suffix.lower().lstrip('.')
                    mime_type = content_type or f'application/{file_ext}'
                    self._session_pdf_urls[url] = download_path
                    self.logger.debug(f'[DownloadsWatchdog] Dispatching FileDownloadedEvent for {final_filename}')
                    self.event_bus.dispatch(FileDownloadedEvent(url=url, path=download_path, file_name=final_filename, file_size=actual_size, file_type=file_ext if file_ext else None, mime_type=mime_type, auto_download=True))
                    return download_path
                else:
                    self.logger.error(f'[DownloadsWatchdog] Failed to write file: {download_path}')
                    return None
            else:
                self.logger.warning(f'[DownloadsWatchdog] No data received when downloading from {url}')
                return None
        except TimeoutError:
            self.logger.warning(f'[DownloadsWatchdog] Download timed out: {url[:80]}...')
            return None
        except Exception as e:
            self.logger.warning(f'[DownloadsWatchdog] Download failed: {type(e).__name__}: {e}')
            return None

    def _track_download(self, file_path: str, guid: str | None=None) -> None:
        try:
            path = Path(file_path)
            if path.exists():
                file_size = path.stat().st_size
                self.logger.debug(f'[DownloadsWatchdog] Tracked download: {path.name} ({file_size} bytes)')
                file_ext = path.suffix.lower().lstrip('.')
                complete_info = {'guid': guid, 'url': str(path), 'path': str(path), 'file_name': path.name, 'file_size': file_size, 'file_type': file_ext if file_ext else None, 'auto_download': False}
                for callback in self._download_complete_callbacks:
                    try:
                        callback(complete_info)
                    except Exception as e:
                        self.logger.debug(f'[DownloadsWatchdog] Error in download complete callback: {e}')
                from system.browser.events import FileDownloadedEvent
                self.event_bus.dispatch(FileDownloadedEvent(guid=guid, url=str(path), path=str(path), file_name=path.name, file_size=file_size))
            else:
                self.logger.warning(f'[DownloadsWatchdog] Downloaded file not found: {file_path}')
        except Exception as e:
            self.logger.error(f'[DownloadsWatchdog] Error tracking download: {e}')

    async def _handle_cdp_download(self, event: DownloadWillBeginEvent, target_id: TargetID, session_id: SessionID | None) -> None:
        downloads_dir = Path(self.browser_session.browser_profile.downloads_path or f'{tempfile.gettempdir()}/browser_use_downloads.{str(self.browser_session.id)[-4:]}').expanduser().resolve()
        unique_filename = None
        file_size = 0
        expected_path = None
        download_result = None
        download_url = event.get('url', '')
        suggested_filename = event.get('suggestedFilename', 'download')
        guid = event.get('guid', '')
        try:
            self.logger.debug(f'[DownloadsWatchdog] ⬇️ File download starting: {suggested_filename} from {download_url[:100]}...')
            self.logger.debug(f'[DownloadsWatchdog] Full CDP event: {event}')
            expected_path = downloads_dir / suggested_filename
            if not self.browser_session.is_local:
                return
        except Exception as e:
            self.logger.error(f'[DownloadsWatchdog] ❌ Error handling CDP download: {type(e).__name__} {e}')
        self.logger.debug(f'[DownloadsWatchdog] Checking if browser auto-download saved the file for us: {suggested_filename}')
        max_wait = 20
        start_time = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_time < max_wait:
            await asyncio.sleep(5.0)
            if Path(downloads_dir).exists():
                for file_path in Path(downloads_dir).iterdir():
                    if file_path.is_file() and (not file_path.name.startswith('.')) and (file_path.name not in self._initial_downloads_snapshot):
                        self._initial_downloads_snapshot.add(file_path.name)
                        try:
                            file_size = file_path.stat().st_size
                            if file_size > 4:
                                self.logger.debug(f'[DownloadsWatchdog] ✅ Found downloaded file: {file_path} ({file_size} bytes)')
                                file_ext = file_path.suffix.lower().lstrip('.')
                                file_type = file_ext if file_ext else None
                                info = self._cdp_downloads_info.get(guid, {})
                                if info.get('handled'):
                                    return
                                self.event_bus.dispatch(FileDownloadedEvent(guid=guid, url=download_url, path=str(file_path), file_name=file_path.name, file_size=file_size, file_type=file_type))
                            try:
                                if guid in self._cdp_downloads_info:
                                    self._cdp_downloads_info[guid]['handled'] = True
                            except (KeyError, AttributeError):
                                pass
                            return
                        except Exception as e:
                            self.logger.debug(f'[DownloadsWatchdog] Error checking file {file_path}: {e}')
        self.logger.warning(f'[DownloadsWatchdog] Download did not complete within {max_wait} seconds')

    async def _handle_download(self, download: Any) -> None:
        download_id = f'{id(download)}'
        self._active_downloads[download_id] = download
        self.logger.debug(f'[DownloadsWatchdog] ⬇️ Handling download: {download.suggested_filename} from {download.url[:100]}...')
        failure = await download.failure()
        self.logger.warning(f'[DownloadsWatchdog] ❌ Download state - canceled: {failure}, url: {download.url}')
        try:
            current_step = 'getting_download_info'
            url = download.url
            suggested_filename = download.suggested_filename
            current_step = 'determining_download_directory'
            downloads_dir = self.browser_session.browser_profile.downloads_path
            if not downloads_dir:
                downloads_dir = str(Path.home() / 'Downloads')
            else:
                downloads_dir = str(downloads_dir)
            original_path = Path(downloads_dir) / suggested_filename
            if original_path.exists() and original_path.stat().st_size > 0:
                self.logger.debug(f'[DownloadsWatchdog] File already downloaded by Playwright: {original_path} ({original_path.stat().st_size} bytes)')
                download_path = original_path
                file_size = original_path.stat().st_size
                unique_filename = suggested_filename
            else:
                current_step = 'generating_unique_filename'
                unique_filename = await self._get_unique_filename(downloads_dir, suggested_filename)
                download_path = Path(downloads_dir) / unique_filename
                self.logger.debug(f'[DownloadsWatchdog] Download started: {unique_filename} from {url[:100]}...')
                current_step = 'calling_save_as'
                self.logger.debug(f'[DownloadsWatchdog] Saving download to: {download_path}')
                self.logger.debug(f'[DownloadsWatchdog] Download path exists: {download_path.parent.exists()}')
                self.logger.debug(f'[DownloadsWatchdog] Download path writable: {os.access(download_path.parent, os.W_OK)}')
                try:
                    self.logger.debug('[DownloadsWatchdog] About to call download.save_as()...')
                    await download.save_as(str(download_path))
                    self.logger.debug(f'[DownloadsWatchdog] Successfully saved download to: {download_path}')
                    current_step = 'save_as_completed'
                except Exception as save_error:
                    self.logger.error(f'[DownloadsWatchdog] save_as() failed with error: {save_error}')
                    raise save_error
                file_size = download_path.stat().st_size if download_path.exists() else 0
            file_ext = download_path.suffix.lower().lstrip('.')
            file_type = file_ext if file_ext else None
            mime_type = None
            auto_download = False
            if file_type == 'pdf':
                auto_download = self._is_auto_download_enabled()
            self.event_bus.dispatch(FileDownloadedEvent(url=url, path=str(download_path), file_name=suggested_filename, file_size=file_size, file_type=file_type, mime_type=mime_type, from_cache=False, auto_download=auto_download))
            self.logger.debug(f'[DownloadsWatchdog] ✅ Download completed: {suggested_filename} ({file_size} bytes) saved to {download_path}')
        except Exception as e:
            self.logger.error(f'''[DownloadsWatchdog] Error handling download at step "{locals().get('current_step', 'unknown')}", error: {e}''')
            self.logger.error(f'[DownloadsWatchdog] Download state - URL: {download.url}, filename: {download.suggested_filename}')
        finally:
            if download_id in self._active_downloads:
                del self._active_downloads[download_id]

    async def check_for_pdf_viewer(self, target_id: TargetID) -> bool:
        self.logger.debug(f'[DownloadsWatchdog] Checking if target {target_id} is PDF viewer...')
        try:
            session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
        except ValueError as e:
            self.logger.warning(f'[DownloadsWatchdog] No session found for {target_id}: {e}')
            return False
        target = self.browser_session.session_manager.get_target(target_id)
        if not target:
            self.logger.warning(f'[DownloadsWatchdog] No target found for {target_id}')
            return False
        page_url = target.url
        if page_url in self._pdf_viewer_cache:
            cached_result = self._pdf_viewer_cache[page_url]
            self.logger.debug(f'[DownloadsWatchdog] Using cached PDF check result for {page_url}: {cached_result}')
            return cached_result
        try:
            url_is_pdf = self._check_url_for_pdf(page_url)
            if url_is_pdf:
                self.logger.debug(f'[DownloadsWatchdog] PDF detected via URL pattern: {page_url}')
                self._pdf_viewer_cache[page_url] = True
                return True
            chrome_pdf_viewer = self._is_chrome_pdf_viewer_url(page_url)
            if chrome_pdf_viewer:
                self.logger.debug(f'[DownloadsWatchdog] Chrome PDF viewer detected: {page_url}')
                self._pdf_viewer_cache[page_url] = True
                return True
            self._pdf_viewer_cache[page_url] = False
            return False
        except Exception as e:
            self.logger.warning(f'[DownloadsWatchdog] ❌ Error checking for PDF viewer: {e}')
            self._pdf_viewer_cache[page_url] = False
            return False

    def _check_url_for_pdf(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        if url_lower.endswith('.pdf'):
            return True
        if '.pdf' in url_lower:
            return True
        if any((param in url_lower for param in ['content-type=application/pdf', 'content-type=application%2fpdf', 'mimetype=application/pdf', 'type=application/pdf'])):
            return True
        return False

    def _is_chrome_pdf_viewer_url(self, url: str) -> bool:
        if not url:
            return False
        url_lower = url.lower()
        if 'chrome-extension://' in url_lower and 'pdf' in url_lower:
            return True
        if url_lower.startswith('chrome://') and 'pdf' in url_lower:
            return True
        return False

    async def _check_network_headers_for_pdf(self, target_id: TargetID) -> bool:
        try:
            import asyncio
            temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
            history = await asyncio.wait_for(temp_session.cdp_client.send.Page.getNavigationHistory(session_id=temp_session.session_id), timeout=3.0)
            current_entry = history.get('entries', [])
            if current_entry:
                current_index = history.get('currentIndex', 0)
                if 0 <= current_index < len(current_entry):
                    current_url = current_entry[current_index].get('url', '')
                    if self._check_url_for_pdf(current_url):
                        return True
            return False
        except Exception as e:
            self.logger.debug(f'[DownloadsWatchdog] Network headers check failed (non-critical): {e}')
            return False

    async def trigger_pdf_download(self, target_id: TargetID) -> str | None:
        self.logger.debug(f'[DownloadsWatchdog] trigger_pdf_download called for target_id={target_id}')
        if not self.browser_session.browser_profile.downloads_path:
            self.logger.warning('[DownloadsWatchdog] ❌ No downloads path configured, cannot save PDF download')
            return None
        downloads_path = self.browser_session.browser_profile.downloads_path
        self.logger.debug(f'[DownloadsWatchdog] Downloads path: {downloads_path}')
        try:
            import asyncio
            self.logger.debug(f'[DownloadsWatchdog] Creating CDP session for PDF download from target {target_id}')
            temp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)
            result = await asyncio.wait_for(temp_session.cdp_client.send.Runtime.evaluate(params={'expression': '\n\t\t\t\t(() => {\n\t\t\t\t\t// For Chrome\'s PDF viewer, the actual URL is in window.location.href\n\t\t\t\t\t// The embed element\'s src is often "about:blank"\n\t\t\t\t\tconst embedElement = document.querySelector(\'embed[type="application/x-google-chrome-pdf"]\') ||\n\t\t\t\t\t\t\t\t\t\tdocument.querySelector(\'embed[type="application/pdf"]\');\n\t\t\t\t\tif (embedElement) {\n\t\t\t\t\t\t// Chrome PDF viewer detected - use the page URL\n\t\t\t\t\t\treturn { url: window.location.href };\n\t\t\t\t\t}\n\t\t\t\t\t// Fallback to window.location.href anyway\n\t\t\t\t\treturn { url: window.location.href };\n\t\t\t\t})()\n\t\t\t\t', 'returnByValue': True}, session_id=temp_session.session_id), timeout=5.0)
            pdf_info = result.get('result', {}).get('value', {})
            pdf_url = pdf_info.get('url', '')
            if not pdf_url:
                self.logger.warning(f'[DownloadsWatchdog] ❌ Could not determine PDF URL for download {pdf_info}')
                return None
            pdf_filename = os.path.basename(pdf_url.split('?')[0])
            if not pdf_filename or not pdf_filename.endswith('.pdf'):
                parsed = urlparse(pdf_url)
                pdf_filename = os.path.basename(parsed.path) or 'document.pdf'
                if not pdf_filename.endswith('.pdf'):
                    pdf_filename += '.pdf'
            self.logger.debug(f'[DownloadsWatchdog] Generated filename: {pdf_filename}')
            self.logger.debug(f'[DownloadsWatchdog] PDF_URL: {pdf_url}, session_pdf_urls: {self._session_pdf_urls}')
            if pdf_url in self._session_pdf_urls:
                existing_path = self._session_pdf_urls[pdf_url]
                self.logger.debug(f'[DownloadsWatchdog] PDF already downloaded in session: {existing_path}')
                return existing_path
            downloads_dir = str(self.browser_session.browser_profile.downloads_path)
            os.makedirs(downloads_dir, exist_ok=True)
            final_filename = pdf_filename
            existing_files = os.listdir(downloads_dir)
            if pdf_filename in existing_files:
                base, ext = os.path.splitext(pdf_filename)
                counter = 1
                while f'{base} ({counter}){ext}' in existing_files:
                    counter += 1
                final_filename = f'{base} ({counter}){ext}'
                self.logger.debug(f'[DownloadsWatchdog] File exists, using: {final_filename}')
            self.logger.debug(f'[DownloadsWatchdog] Starting PDF download from: {pdf_url[:100]}...')
            try:
                escaped_pdf_url = json.dumps(pdf_url)
                result = await asyncio.wait_for(temp_session.cdp_client.send.Runtime.evaluate(params={'expression': f"\n\t\t\t\t\t(async () => {{\n\t\t\t\t\t\ttry {{\n\t\t\t\t\t\t\t// Use fetch with cache: 'force-cache' to prioritize cached version\n\t\t\t\t\t\t\tconst response = await fetch({escaped_pdf_url}, {{\n\t\t\t\t\t\t\t\tcache: 'force-cache'\n\t\t\t\t\t\t\t}});\n\t\t\t\t\t\t\tif (!response.ok) {{\n\t\t\t\t\t\t\t\tthrow new Error(`HTTP error! status: ${{response.status}}`);\n\t\t\t\t\t\t\t}}\n\t\t\t\t\t\t\tconst blob = await response.blob();\n\t\t\t\t\t\t\tconst arrayBuffer = await blob.arrayBuffer();\n\t\t\t\t\t\t\tconst uint8Array = new Uint8Array(arrayBuffer);\n\t\t\t\t\t\t\t\n\t\t\t\t\t\t\t// Check if served from cache\n\t\t\t\t\t\t\tconst fromCache = response.headers.has('age') || \n\t\t\t\t\t\t\t\t\t\t\t !response.headers.has('date');\n\t\t\t\t\t\t\t\t\t\t\t \n\t\t\t\t\t\t\treturn {{ \n\t\t\t\t\t\t\t\tdata: Array.from(uint8Array),\n\t\t\t\t\t\t\t\tfromCache: fromCache,\n\t\t\t\t\t\t\t\tresponseSize: uint8Array.length,\n\t\t\t\t\t\t\t\ttransferSize: response.headers.get('content-length') || 'unknown'\n\t\t\t\t\t\t\t}};\n\t\t\t\t\t\t}} catch (error) {{\n\t\t\t\t\t\t\tthrow new Error(`Fetch failed: ${{error.message}}`);\n\t\t\t\t\t\t}}\n\t\t\t\t\t}})()\n\t\t\t\t\t", 'awaitPromise': True, 'returnByValue': True}, session_id=temp_session.session_id), timeout=10.0)
                download_result = result.get('result', {}).get('value', {})
                if download_result and download_result.get('data') and (len(download_result['data']) > 0):
                    downloads_dir = str(self.browser_session.browser_profile.downloads_path)
                    os.makedirs(downloads_dir, exist_ok=True)
                    download_path = os.path.join(downloads_dir, final_filename)
                    async with await anyio.open_file(download_path, 'wb') as f:
                        await f.write(bytes(download_result['data']))
                    if os.path.exists(download_path):
                        actual_size = os.path.getsize(download_path)
                        self.logger.debug(f'[DownloadsWatchdog] PDF file written successfully: {download_path} ({actual_size} bytes)')
                    else:
                        self.logger.error(f'[DownloadsWatchdog] ❌ Failed to write PDF file to: {download_path}')
                        return None
                    cache_status = 'from cache' if download_result.get('fromCache') else 'from network'
                    response_size = download_result.get('responseSize', 0)
                    self.logger.debug(f'[DownloadsWatchdog] ✅ Auto-downloaded PDF ({cache_status}, {response_size:,} bytes): {download_path}')
                    self._session_pdf_urls[pdf_url] = download_path
                    self.logger.debug(f'[DownloadsWatchdog] Dispatching FileDownloadedEvent for {final_filename}')
                    self.event_bus.dispatch(FileDownloadedEvent(url=pdf_url, path=download_path, file_name=final_filename, file_size=response_size, file_type='pdf', mime_type='application/pdf', from_cache=download_result.get('fromCache', False), auto_download=True))
                    return download_path
                else:
                    self.logger.warning(f'[DownloadsWatchdog] No data received when downloading PDF from {pdf_url}')
                    return None
            except Exception as e:
                self.logger.warning(f'[DownloadsWatchdog] Failed to auto-download PDF from {pdf_url}: {type(e).__name__}: {e}')
                return None
        except TimeoutError:
            self.logger.debug('[DownloadsWatchdog] PDF download operation timed out')
            return None
        except Exception as e:
            self.logger.error(f'[DownloadsWatchdog] Error in PDF download: {type(e).__name__}: {e}')
            return None

    @staticmethod
    async def _get_unique_filename(directory: str, filename: str) -> str:
        base, ext = os.path.splitext(filename)
        counter = 1
        new_filename = filename
        while os.path.exists(os.path.join(directory, new_filename)):
            new_filename = f'{base} ({counter}){ext}'
            counter += 1
        return new_filename