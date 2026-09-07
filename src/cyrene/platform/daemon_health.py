"""Lightweight authenticated daemon discovery and startup readiness."""

import time

import httpx


def is_healthy_response(response: httpx.Response) -> bool:
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return (isinstance(payload, dict) and payload.get("service") == "cyrene"
            and payload.get("status") == "ok")


def wait_for_daemon(url: str, headers: dict[str, str]) -> bool:
    for _ in range(30):
        try:
            response = httpx.get(
                f"{url}/api/health", timeout=3.0, trust_env=False, headers=headers,
            )
            if is_healthy_response(response):
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False
