"""
Vultr VKE (Vultr Kubernetes Engine) provider for simple-proxy.
Deploys proxy containers on Vultr's managed Kubernetes service.
"""
import os
import sys
import time
import json
import tempfile
from typing import Optional
import requests
from simpleproxy.src.cloud_provider import ProxyConfig
from simpleproxy.src.providers.kubernetes_base import KubernetesBaseProvider


class VultrVkeProvider(KubernetesBaseProvider):
    """
    Vultr VKE provider.
    Creates VKE clusters and deploys proxy containers.
    """
    
    VULTR_API = "https://api.vultr.com/v2"
    
    def __init__(self):
        super().__init__()
        self.api_key = os.environ.get("VULTR_API_KEY")
        if not self.api_key:
            print("Error: VULTR_API_KEY environment variable is required", file=sys.stderr)
            sys.exit(1)
    
    def _headers(self) -> dict:
        """Get API headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _get_cluster_by_label(self, label: str) -> Optional[dict]:
        """Find cluster by label."""
        try:
            response = requests.get(
                f"{self.VULTR_API}/kubernetes/clusters",
                headers=self._headers(),
                timeout=30
            )
            response.raise_for_status()
            
            for cluster in response.json().get("vke_clusters", []):
                if cluster.get("label") == label:
                    return cluster
            return None
        except requests.RequestException as e:
            self.logger.error(f"Error fetching clusters: {e}")
            return None
    
    def _create_vke_cluster(
        self,
        label: str,
        region: str,
        version: str,
        node_plan: str,
        node_count: int
    ) -> str:
        """
        Create a VKE cluster.
        Returns cluster ID.
        """
        payload = {
            "label": label,
            "region": region,
            "version": version,
            "node_pools": [{
                "node_quantity": node_count,
                "label": f"{label}-pool",
                "plan": node_plan,
                "auto_scaler": False
            }]
        }
        
        self.logger.info(f"Creating VKE cluster '{label}' in {region}")
        try:
            response = requests.post(
                f"{self.VULTR_API}/kubernetes/clusters",
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            cluster = result.get("vke_cluster", result)
            cluster_id = cluster.get("id")
            
            if not cluster_id:
                raise RuntimeError(f"Failed to get cluster ID from response: {result}")
            
            self.logger.info(f"Cluster created with ID: {cluster_id}")
            return cluster_id
        
        except requests.RequestException as e:
            self.logger.error(f"Error creating cluster: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.text}")
            raise
    
    def _wait_for_cluster_ready(self, cluster_id: str, timeout_seconds: int = 1800) -> None:
        """Wait for cluster to be ready."""
        self.logger.info(f"Waiting for cluster {cluster_id} to be ready (timeout: {timeout_seconds}s)")
        deadline = time.time() + timeout_seconds
        
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"{self.VULTR_API}/kubernetes/clusters/{cluster_id}",
                    headers=self._headers(),
                    timeout=30
                )
                response.raise_for_status()
                
                cluster = response.json().get("vke_cluster", response.json())
                status = cluster.get("status", "unknown")
                
                self.logger.info(f"Cluster status: {status}")
                
                if status == "active":
                    self.logger.info("Cluster is ready!")
                    return
                elif status in ["error", "failed"]:
                    raise RuntimeError(f"Cluster creation failed with status: {status}")
                
                time.sleep(15)
            
            except requests.RequestException as e:
                self.logger.warning(f"Error checking cluster status: {e}")
                time.sleep(15)
        
        raise TimeoutError(f"Cluster {cluster_id} did not become ready within {timeout_seconds}s")
    
    def _get_kubeconfig(self, cluster_id: str) -> str:
        """
        Download kubeconfig for the cluster.
        Returns path to temporary kubeconfig file.
        """
        self.logger.info(f"Downloading kubeconfig for cluster {cluster_id}")
        
        try:
            response = requests.get(
                f"{self.VULTR_API}/kubernetes/clusters/{cluster_id}/config",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30
            )
            response.raise_for_status()
            
            # Vultr returns the kubeconfig base64-encoded in a JSON response
            import base64
            import yaml
            
            response_data = response.json()
            kubeconfig_b64 = response_data.get('kube_config', '')
            
            if not kubeconfig_b64:
                raise RuntimeError("No kube_config found in Vultr API response")
            
            # Decode base64
            kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode('utf-8')
            kubeconfig = yaml.safe_load(kubeconfig_yaml)
            
            # Vultr's kubeconfig might be missing current-context
            # If so, set it to the first context
            if 'current-context' not in kubeconfig and 'contexts' in kubeconfig:
                if kubeconfig['contexts']:
                    kubeconfig['current-context'] = kubeconfig['contexts'][0]['name']
                    self.logger.info(f"Added missing current-context: {kubeconfig['current-context']}")
            
            # Save to temporary file
            fd, path = tempfile.mkstemp(prefix="vke-", suffix="-kubeconfig.yaml")
            with os.fdopen(fd, 'w') as f:
                yaml.dump(kubeconfig, f)
            
            self.logger.info(f"Kubeconfig saved to {path}")
            return path
        
        except requests.RequestException as e:
            self.logger.error(f"Error downloading kubeconfig: {e}")
            raise
    
    def _delete_vke_cluster(self, cluster_id: str) -> None:
        """Delete VKE cluster."""
        self.logger.info(f"Deleting VKE cluster {cluster_id}")
        
        try:
            response = requests.delete(
                f"{self.VULTR_API}/kubernetes/clusters/{cluster_id}",
                headers=self._headers(),
                timeout=30
            )
            
            if response.status_code not in (200, 204):
                response.raise_for_status()
            
            self.logger.info(f"Cluster {cluster_id} deletion initiated")
        
        except requests.RequestException as e:
            self.logger.error(f"Error deleting cluster: {e}")
            raise
    
    def _get_node_pool_id(self, cluster_id: str) -> Optional[str]:
        """Get the first node pool ID for a cluster."""
        try:
            response = requests.get(
                f"{self.VULTR_API}/kubernetes/clusters/{cluster_id}/node-pools",
                headers=self._headers(),
                timeout=30
            )
            response.raise_for_status()
            node_pools = response.json().get("node_pools", [])
            if node_pools:
                return node_pools[0].get("id")
            return None
        except requests.RequestException as e:
            self.logger.error(f"Error fetching node pools: {e}")
            return None
    
    def _update_node_pool_count(self, cluster_id: str, node_pool_id: str, new_count: int) -> None:
        """Update the node count for a node pool."""
        self.logger.info(f"Updating node pool {node_pool_id} to {new_count} nodes")
        
        try:
            payload = {"node_quantity": new_count}
            response = requests.patch(
                f"{self.VULTR_API}/kubernetes/clusters/{cluster_id}/node-pools/{node_pool_id}",
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=30
            )
            response.raise_for_status()
            self.logger.info(f"Node pool updated successfully")
        except requests.RequestException as e:
            self.logger.error(f"Error updating node pool: {e}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.text}")
            raise
    
    def _get_or_create_cluster(self, config: ProxyConfig) -> str:
        """
        Get existing cluster or create new one.
        Resizes cluster if node count doesn't match.
        Returns cluster ID.
        """
        # Check if cluster already exists
        existing = self._get_cluster_by_label(config.cluster)
        if existing:
            cluster_id = existing.get("id")
            self.logger.info(f"Found existing cluster '{config.cluster}' with ID: {cluster_id}")
            
            # Check node count and resize if needed
            node_pools = existing.get("node_pools", [])
            if node_pools:
                current_count = node_pools[0].get("node_quantity", 0)
                if current_count != config.count:
                    self.logger.info(f"Cluster has {current_count} nodes, but {config.count} requested")
                    node_pool_id = self._get_node_pool_id(cluster_id)
                    if node_pool_id:
                        self._update_node_pool_count(cluster_id, node_pool_id, config.count)
                        self.logger.info(f"Waiting for cluster to update...")
                        time.sleep(10)  # Give it a moment to start updating
                        self._wait_for_cluster_ready(cluster_id)
                    else:
                        self.logger.warning("Could not find node pool to update")
                else:
                    self.logger.info(f"Cluster already has {config.count} nodes")
            
            return cluster_id
        
        # Get VKE config (should be set by CLI)
        if not config.vke_config:
            self.logger.error("VKE config is required but not provided")
            raise ValueError("vke_config must be set in ProxyConfig for Vultr VKE provider")
        
        vke = config.vke_config
        
        # Create cluster with count nodes (one per proxy for different IPs)
        cluster_id = self._create_vke_cluster(
            label=config.cluster,
            region=vke.region,
            version=vke.version,
            node_plan=vke.node_plan,
            node_count=config.count  # One node per proxy
        )
        
        # Wait for cluster to be ready
        self._wait_for_cluster_ready(cluster_id)
        
        return cluster_id
    
    def _get_kubeconfig_path(self, cluster_id: str, config: ProxyConfig) -> str:
        """Get kubeconfig file path."""
        return self._get_kubeconfig(cluster_id)
    
    def _delete_cluster(self, cluster_id: str, config: ProxyConfig) -> None:
        """Delete the cluster."""
        self._delete_vke_cluster(cluster_id)
    
    def cleanup(self, config: ProxyConfig, keep_task_defs: bool = False) -> None:
        """
        Clean up resources.
        If delete_cluster is set in config, also deletes the VKE cluster.
        """
        # Find cluster
        cluster = self._get_cluster_by_label(config.cluster)
        if not cluster:
            self.logger.warning(f"Cluster '{config.cluster}' not found")
            return
        
        cluster_id = cluster.get("id")
        
        try:
            # Get kubeconfig and clean up k8s resources
            kubeconfig_path = self._get_kubeconfig(cluster_id)
            
            namespace = config.cluster
            delete_namespace = getattr(config, 'delete_namespace', False)
            
            self.logger.info(f"Cleaning up Kubernetes resources in namespace '{namespace}'")
            self._cleanup_kubernetes_resources(
                kubeconfig_path,
                namespace,
                delete_namespace
            )
            
            # Delete kubeconfig temp file
            try:
                os.unlink(kubeconfig_path)
            except Exception as e:
                self.logger.warning(f"Failed to delete temp kubeconfig: {e}")
            
            # Optionally delete the cluster
            delete_cluster = getattr(config, 'delete_cluster', False)
            if delete_cluster:
                self.logger.info("Deleting VKE cluster...")
                self._delete_cluster(cluster_id, config)
                self.logger.info("Cluster deletion initiated")
            else:
                self.logger.info("Keeping VKE cluster (use --delete-cluster to remove it)")
        
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            raise

