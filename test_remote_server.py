"""
This is a simple test to ensure your proxy is working. You will need to update
the .test.env file with the remote address of your server in the format of
http://<user>:<password>@<ip>:<port>
"""
import requests
import os
import dotenv

dotenv.load_dotenv('.test.env')

def get_ip(proxies: dict[str, str] | None = None) -> str:
    """Fetches the ip of the requester."""
    res = requests.request(
        "GET",
        "http://ip.oxylabs.io/location",
        proxies=proxies,
    )

    return res.json()["ip"]

def test_remote_server() -> None:
    """
    Ensures that when we make a request using the proxy, the ip identified by
    the host is different from our local ip.
    """

    remote_address = os.getenv("REMOTE_TEST_SERVER")
    local_ip = get_ip()
    remote_ip = get_ip(proxies={"http": remote_address, "https": remote_address})

    assert remote_ip != local_ip, "The remote ip and local ip should not be the same. Ensure your .test.env is set correctly."