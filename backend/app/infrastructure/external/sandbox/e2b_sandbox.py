from __future__ import annotations

import asyncio
import io
import logging
from contextlib import suppress
from typing import BinaryIO, Optional

import httpx
from e2b.exceptions import SandboxException
from e2b_code_interpreter import Sandbox as E2BCodeInterpreterSandbox

from app.core.config import get_settings
from app.domain.external.browser import Browser
from app.domain.external.sandbox import Sandbox
from app.domain.models.tool_result import ToolResult
from app.infrastructure.external.browser.playwright_browser import PlaywrightBrowser

logger = logging.getLogger(__name__)


class E2BSandbox(Sandbox):
    """Sandbox implementation backed by E2B cloud sandboxes."""

    def __init__(
        self,
        sandbox: E2BCodeInterpreterSandbox,
        supervisor_handle=None,
    ) -> None:
        self._sandbox = sandbox
        self._supervisor_handle = supervisor_handle
        base_host = sandbox.get_host(8080)
        self._base_url = f"https://{base_host}"
        self._cdp_url = f"https://{sandbox.get_host(9222)}"
        self._vnc_url = f"wss://{sandbox.get_host(5901)}"
        self.client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=600,
            verify=False,
        )

    @property
    def id(self) -> str:
        return self._sandbox.sandbox_id

    @property
    def cdp_url(self) -> str:
        return self._cdp_url

    @property
    def vnc_url(self) -> str:
        return self._vnc_url

    async def ensure_sandbox(self) -> None:
        max_retries = 30
        retry_interval = 2

        for attempt in range(max_retries):
            try:
                response = await self.client.get("/api/v1/supervisor/status")
                response.raise_for_status()
                tool_result = ToolResult(**response.json())

                if not tool_result.success:
                    logger.warning(
                        "Supervisor status check failed: %s", tool_result.message
                    )
                    await asyncio.sleep(retry_interval)
                    continue

                services = tool_result.data or []
                if not services:
                    logger.warning("No services found in supervisor status")
                    await asyncio.sleep(retry_interval)
                    continue

                non_running = [
                    f"{svc.get('name', 'unknown')}({svc.get('statename', '')})"
                    for svc in services
                    if svc.get("statename") != "RUNNING"
                ]
                if not non_running:
                    logger.info(
                        "All %d services are RUNNING - E2B sandbox is ready",
                        len(services),
                    )
                    return

                logger.info(
                    "Waiting for services to start... Non-running: %s (attempt %d/%d)",
                    ", ".join(non_running),
                    attempt + 1,
                    max_retries,
                )
            except Exception as exc:  # pragma: no cover - best effort logging
                logger.warning(
                    "Failed to check supervisor status (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )

            await asyncio.sleep(retry_interval)

        error_message = (
            f"Sandbox services failed to start after {max_retries} attempts "
            f"({max_retries * retry_interval} seconds)"
        )
        logger.error(error_message)

    async def exec_command(self, session_id: str, exec_dir: str, command: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/shell/exec",
            json={"id": session_id, "exec_dir": exec_dir, "command": command},
        )
        return ToolResult(**response.json())

    async def view_shell(self, session_id: str, console: bool = False) -> ToolResult:
        response = await self.client.post(
            "/api/v1/shell/view",
            json={"id": session_id, "console": console},
        )
        return ToolResult(**response.json())

    async def wait_for_process(
        self, session_id: str, seconds: Optional[int] = None
    ) -> ToolResult:
        response = await self.client.post(
            "/api/v1/shell/wait",
            json={"id": session_id, "seconds": seconds},
        )
        return ToolResult(**response.json())

    async def write_to_process(
        self, session_id: str, input_text: str, press_enter: bool = True
    ) -> ToolResult:
        response = await self.client.post(
            "/api/v1/shell/write",
            json={"id": session_id, "input": input_text, "press_enter": press_enter},
        )
        return ToolResult(**response.json())

    async def kill_process(self, session_id: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/shell/kill",
            json={"id": session_id},
        )
        return ToolResult(**response.json())

    async def file_write(
        self,
        file: str,
        content: str,
        append: bool = False,
        leading_newline: bool = False,
        trailing_newline: bool = False,
        sudo: bool = False,
    ) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/write",
            json={
                "file": file,
                "content": content,
                "append": append,
                "leading_newline": leading_newline,
                "trailing_newline": trailing_newline,
                "sudo": sudo,
            },
        )
        return ToolResult(**response.json())

    async def file_read(
        self,
        file: str,
        start_line: int | None = None,
        end_line: int | None = None,
        sudo: bool = False,
    ) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/read",
            json={
                "file": file,
                "start_line": start_line,
                "end_line": end_line,
                "sudo": sudo,
            },
        )
        return ToolResult(**response.json())

    async def file_exists(self, path: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/exists",
            json={"path": path},
        )
        return ToolResult(**response.json())

    async def file_delete(self, path: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/delete",
            json={"path": path},
        )
        return ToolResult(**response.json())

    async def file_list(self, path: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/list",
            json={"path": path},
        )
        return ToolResult(**response.json())

    async def file_replace(
        self, file: str, old_str: str, new_str: str, sudo: bool = False
    ) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/replace",
            json={
                "file": file,
                "old_str": old_str,
                "new_str": new_str,
                "sudo": sudo,
            },
        )
        return ToolResult(**response.json())

    async def file_search(self, file: str, regex: str, sudo: bool = False) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/search",
            json={"file": file, "regex": regex, "sudo": sudo},
        )
        return ToolResult(**response.json())

    async def file_find(self, path: str, glob_pattern: str) -> ToolResult:
        response = await self.client.post(
            "/api/v1/file/find",
            json={"path": path, "glob": glob_pattern},
        )
        return ToolResult(**response.json())

    async def file_upload(
        self, file_data: BinaryIO, path: str, filename: str | None = None
    ) -> ToolResult:
        files = {"file": (filename or "upload", file_data, "application/octet-stream")}
        data = {"path": path}
        response = await self.client.post(
            "/api/v1/file/upload",
            files=files,
            data=data,
        )
        return ToolResult(**response.json())

    async def file_download(self, path: str) -> BinaryIO:
        response = await self.client.get(
            "/api/v1/file/download",
            params={"path": path},
        )
        response.raise_for_status()
        return io.BytesIO(response.content)

    async def destroy(self) -> bool:
        try:
            if self.client:
                await self.client.aclose()
        except Exception as exc:  # pragma: no cover - cleanup best effort
            logger.warning("Failed to close HTTP client: %s", exc)

        try:
            if self._supervisor_handle is not None:
                with suppress(Exception):
                    self._supervisor_handle.kill()
            await asyncio.to_thread(self._sandbox.kill)
            return True
        except SandboxException as exc:
            logger.error("Failed to destroy E2B sandbox %s: %s", self.id, exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Unexpected error destroying E2B sandbox %s: %s", self.id, exc)
        return False

    async def get_browser(self) -> Browser:
        # 这是一个特殊的逻辑，因为使用了反向代理替换掉了 host 才能拿到 chrome websocket 地址
        # 所以这里要手动获取再进行真实的 域名替换
        json_version = await self.client.get(f"{self._cdp_url}/json/version")
        webSocketDebuggerUrl = json_version.json()["webSocketDebuggerUrl"]
        # 原始地址是一个类似这样的结果：ws://127.0.0.1:8222/devtools/browser/96dd965c-704f-4a67-afcb-2b3151dc5052
        # 把 /devtools/browser/[uuid] 前面的内容替换为 wss://{{this.sandbox.get_host(9222)}}
        cdp_url = webSocketDebuggerUrl.replace("ws://127.0.0.1", f"wss://{self._sandbox.get_host(9222)}")
        print(f"CDP URL: {cdp_url}")
        return PlaywrightBrowser(cdp_url)

    @classmethod
    async def create(cls) -> Sandbox:
        settings = get_settings()
        timeout_seconds: Optional[int] = None
        if settings.sandbox_ttl_minutes:
            timeout_seconds = settings.sandbox_ttl_minutes * 60

        sandbox = await asyncio.to_thread(
            E2BCodeInterpreterSandbox.create,
            template=settings.e2b_template_id,
            timeout=timeout_seconds,
        )

        supervisor_handle = await cls._start_supervisor(sandbox)

        if timeout_seconds:
            await asyncio.to_thread(sandbox.set_timeout, timeout_seconds)

        instance = cls(sandbox, supervisor_handle=supervisor_handle)
        await instance.ensure_sandbox()
        return instance

    @classmethod
    async def get(cls, id: str) -> Optional[Sandbox]:
        try:
            sandbox = await asyncio.to_thread(
                E2BCodeInterpreterSandbox.connect, id
            )
        except SandboxException as exc:
            logger.error("Failed to connect to E2B sandbox %s: %s", id, exc)
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Unexpected error connecting to E2B sandbox %s: %s", id, exc)
            return None

        instance = cls(sandbox)
        await instance.ensure_sandbox()
        return instance

    @staticmethod
    async def _start_supervisor(sandbox: E2BCodeInterpreterSandbox):
        try:
            return await asyncio.to_thread(
                sandbox.commands.run,
                "supervisord -c /app/supervisord.e2b.conf",
                background=True,
                user="root",
                cwd="/app",
                timeout=0,
            )
        except Exception as exc:
            logger.error("Failed to start supervisor in E2B sandbox %s: %s", sandbox.sandbox_id, exc)
            raise
