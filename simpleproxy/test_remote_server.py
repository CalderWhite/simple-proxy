"""
This is a simple test to ensure your proxy is working. You will need to update
the .test.env file with the remote address of your server in the format of
http://<user>:<password>@<ip>:<port>
"""
import aiohttp
import asyncio
import os
import dotenv
import pytest

dotenv.load_dotenv('.test.env')


async def get_ip(proxies: str | None = None, protocol: str = "http") -> str:
    """Fetches the ip of the requester."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{protocol}://ip.oxylabs.io/location",
            proxy=proxies,
        ) as response:
            data = await response.json()
            return data["ip"]


@pytest.mark.asyncio
async def test_remote_server_http() -> None:
    """
    Ensures that when we make a request using the proxy, the ip identified by
    the host is different from our local ip.
    """
    remote_address = os.getenv("REMOTE_TEST_SERVER")
    local_ip = await get_ip()
    remote_ip = await get_ip(proxies=remote_address)
    
    assert remote_ip != local_ip, "The remote ip and local ip should not be the same. Ensure your .test.env is set correctly."


@pytest.mark.asyncio
async def test_remote_server_https() -> None:
    """
    Ensures HTTPS is functional for the proxy servers.
    """
    remote_address = os.getenv("REMOTE_TEST_SERVER")
    local_ip = await get_ip(protocol="https")
    remote_ip = await get_ip(proxies=remote_address, protocol="https")
    
    assert remote_ip != local_ip, "The remote ip and local ip should not be the same. Ensure your .test.env is set correctly."


@pytest.mark.asyncio
@pytest.mark.skip(reason="Optional cardinality test - requires cluster with multiple IPs")
async def test_ensure_minimum_cardinality() -> None:
    """
    This test is optional, and ensures that after 100 parallel requests, we see at least <n> many IPs.
    You must provision the cluster with <n> ips in the first place for this test to pass.

    This works for providers with a load balancer, such as vultr_vke.

    """
    n = 10
    remote_address = os.getenv("REMOTE_TEST_SERVER")
    
    tasks = [get_ip(proxies=remote_address) for _ in range(100)]
    ips = await asyncio.gather(*tasks)
    unique_ips = set(ips)

    assert len(unique_ips) >= n, f"Expected at least {n} unique IPs, but got {len(unique_ips)}: {unique_ips}"