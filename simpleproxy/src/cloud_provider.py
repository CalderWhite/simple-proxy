from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ProxyConfig:
    username: str
    password_hash: str  # SHA256 hash of the password
    count: int
    region: str = "us-east-1"
    cluster: str = "oneoff-fargate"
    cpu: str = "256"
    memory: str = "512"
    port: int = 8080
    image: str = "calderwhite/simple-proxy:latest"

@dataclass
class ProxyAddress:
    eni: str
    public_ipv4: str
    ipv6: list[str]

class CloudProvider(ABC):

    @abstractmethod
    def create_proxies(self, config: ProxyConfig) -> list[ProxyAddress]:
        """
        Given a config, produce a list of addresses running simple-proxy.
        """
        pass

    @abstractmethod
    def cleanup(self, config: ProxyConfig, keep_task_defs: bool = False) -> None:
        """
        Clean up all resources created by create_proxies()
        """
        pass