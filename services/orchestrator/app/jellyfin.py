"""Minimal Jellyfin integration helpers."""

import asyncio
import logging

import httpx

LOGGER = logging.getLogger("orchestrator.jellyfin")


async def trigger_scan(base_url: str, api_key: str, library_id: int) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {"X-Emby-Token": api_key}
        scan_url = f"{base_url}/Library/Refresh?LibraryId={library_id}"
        LOGGER.info("Requesting Jellyfin refresh for library %s", library_id)
        response = await client.post(scan_url, headers=headers)
        response.raise_for_status()


async def trigger_all(config: dict[str, int], base_url: str, api_key: str) -> None:
    tasks = [trigger_scan(base_url, api_key, lib_id) for lib_id in config.values()]
    if tasks:
        await asyncio.gather(*tasks)
