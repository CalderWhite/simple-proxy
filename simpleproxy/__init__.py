"""
Simple Proxy - Deploy proxy servers to cloud providers with ease
"""

__version__ = "0.1.0"

from simpleproxy.src.cloud_provider import CloudProvider, ProxyConfig, ProxyAddress
from simpleproxy.src.providers.aws_fargate import AwsFargateProvider

__all__ = [
    "CloudProvider",
    "ProxyConfig", 
    "ProxyAddress",
    "AwsFargateProvider",
]

