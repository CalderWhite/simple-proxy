from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

@dataclass
class VkeConfig:
    """Vultr VKE-specific configuration options."""
    region: str = "ewr"  # Vultr region (e.g., ewr, lax, scl)
    version: str = "v1.33.5+1"  # Kubernetes version (use stable, not latest)
    node_plan: str = "vc2-1c-2gb"  # Node plan (cheapest VKE-compatible)

@dataclass
class ProxyConfig:
    username: str
    password_hash: str  # SHA256 hash of the password
    count: int
    region: str = "us-east-1"  # Used by AWS Fargate
    cluster: str = "oneoff-fargate"
    cpu: str = "256"
    memory: str = "512"
    port: int = 8080
    image: str = "calderwhite/simple-proxy:latest"
    
    # Provider-specific configurations
    vke_config: Optional[VkeConfig] = None
    
    # Kubernetes-specific cleanup options
    delete_namespace: bool = False  # If True, deletes namespace during cleanup
    delete_cluster: bool = False  # If True, deletes cluster during cleanup

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