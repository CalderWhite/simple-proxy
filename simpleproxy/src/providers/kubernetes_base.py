"""
Generic Kubernetes base provider for deploying simple-proxy containers.
This class can be extended by any provider that uses Kubernetes (e.g., VKE, GKE, EKS, etc.)
"""
import logging
import time
from abc import abstractmethod
from typing import Optional, List
from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client.rest import ApiException
from simpleproxy.src.cloud_provider import CloudProvider, ProxyConfig, ProxyAddress


class KubernetesBaseProvider(CloudProvider):
    """
    Base class for Kubernetes-based providers.
    Subclasses must implement cluster lifecycle methods.
    """
    
    def __init__(self):
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @abstractmethod
    def _get_or_create_cluster(self, config: ProxyConfig) -> str:
        """
        Get or create a Kubernetes cluster.
        Returns: cluster_id
        """
        pass
    
    @abstractmethod
    def _get_kubeconfig_path(self, cluster_id: str, config: ProxyConfig) -> str:
        """
        Get the kubeconfig file path for the cluster.
        Returns: path to kubeconfig file
        """
        pass
    
    @abstractmethod
    def _delete_cluster(self, cluster_id: str, config: ProxyConfig) -> None:
        """
        Delete the Kubernetes cluster.
        """
        pass
    
    def _ensure_namespace(self, v1: client.CoreV1Api, namespace: str) -> None:
        """Ensure namespace exists."""
        try:
            v1.read_namespace(namespace)
            self.logger.info(f"Namespace {namespace} already exists")
        except ApiException as e:
            if e.status == 404:
                self.logger.info(f"Creating namespace {namespace}")
                v1.create_namespace(
                    client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
                )
            else:
                raise
    
    def _create_or_replace_secret(
        self, v1: client.CoreV1Api, namespace: str, name: str, data: dict
    ) -> None:
        """Create or replace a Kubernetes secret."""
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name),
            string_data=data,
            type="Opaque",
        )
        try:
            v1.create_namespaced_secret(namespace, body)
            self.logger.info(f"Created secret {name}")
        except ApiException as e:
            if e.status == 409:
                self.logger.info(f"Replacing existing secret {name}")
                v1.replace_namespaced_secret(name, namespace, body)
            else:
                raise
    
    def _tcp_probe(self, port: int) -> client.V1Probe:
        """Create a TCP probe for health checks."""
        return client.V1Probe(
            tcp_socket=client.V1TCPSocketAction(port=port),
            initial_delay_seconds=5,
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=6,
        )
    
    def _create_deployment(
        self,
        name: str,
        namespace: str,
        image: str,
        port: int,
        secret_name: str,
        labels: dict,
        cpu: str = "256m",
        memory: str = "512Mi",
    ) -> client.V1Deployment:
        """Create a Deployment object."""
        # Convert cpu/memory from AWS format to k8s format if needed
        # AWS uses cpu units (256 = 0.25 vCPU), k8s uses millicores
        # Use 100m CPU to fit on small nodes (vc2-1c-2gb) with system pods
        if cpu.isdigit():
            cpu = "100m"  # Low enough to coexist with system pods on 1 vCPU nodes
        if memory.isdigit():
            memory = f"{memory}Mi"
        
        container = client.V1Container(
            name="simpleproxy",
            image=image,
            ports=[client.V1ContainerPort(container_port=port)],
            env=[
                client.V1EnvVar(
                    name="PROXY_USER",
                    value_from=client.V1EnvVarSource(
                        secret_key_ref=client.V1SecretKeySelector(
                            name=secret_name, key="PROXY_USER"
                        )
                    ),
                ),
                client.V1EnvVar(
                    name="PROXY_PASSWORD_SHA256",
                    value_from=client.V1EnvVarSource(
                        secret_key_ref=client.V1SecretKeySelector(
                            name=secret_name, key="PROXY_PASSWORD_SHA256"
                        )
                    ),
                ),
                client.V1EnvVar(name="PROXY_PORT", value=str(port)),
            ],
            readiness_probe=self._tcp_probe(port),
            liveness_probe=self._tcp_probe(port),
            resources=client.V1ResourceRequirements(
                requests={"cpu": cpu, "memory": memory},
                limits={"cpu": cpu, "memory": memory},
            ),
        )
        
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels=labels),
            spec=client.V1PodSpec(containers=[container])
        )
        
        spec = client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels=labels),
            template=template
        )
        
        return client.V1Deployment(
            metadata=client.V1ObjectMeta(name=name, labels=labels),
            spec=spec
        )
    
    def _create_service_lb(
        self, name: str, namespace: str, selector: dict, port: int
    ) -> client.V1Service:
        """Create a LoadBalancer service."""
        return client.V1Service(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1ServiceSpec(
                type="LoadBalancer",
                selector=selector,
                ports=[
                    client.V1ServicePort(
                        port=port,
                        target_port=port,
                        name="tcp"
                    )
                ],
                # Preserve client IPs and helps with per-pod routing
                external_traffic_policy="Local",
            ),
        )
    
    def _deploy_proxies_single_lb(
        self,
        apps: client.AppsV1Api,
        core: client.CoreV1Api,
        namespace: str,
        config: ProxyConfig,
        secret_name: str
    ) -> List[str]:
        """
        Deploy one deployment with N replicas behind a single LoadBalancer.
        Returns list with single service name.
        """
        labels = {"app": "simpleproxy"}
        dep_name = "simpleproxy"
        svc_name = "simpleproxy-lb"
        
        # Create deployment with N replicas
        deployment = self._create_deployment(
            name=dep_name,
            namespace=namespace,
            image=config.image,
            port=config.port,
            secret_name=secret_name,
            labels=labels,
            cpu=config.cpu,
            memory=config.memory,
        )
        # Set replicas to config.count
        deployment.spec.replicas = config.count
        
        # Add pod anti-affinity to spread pods across nodes (one per node)
        deployment.spec.template.spec.affinity = client.V1Affinity(
            pod_anti_affinity=client.V1PodAntiAffinity(
                required_during_scheduling_ignored_during_execution=[
                    client.V1PodAffinityTerm(
                        label_selector=client.V1LabelSelector(match_labels=labels),
                        topology_key="kubernetes.io/hostname"
                    )
                ]
            )
        )
        
        try:
            apps.create_namespaced_deployment(namespace, deployment)
            self.logger.info(f"Created deployment {dep_name} with {config.count} replicas")
        except ApiException as e:
            if e.status == 409:
                self.logger.info(f"Replacing existing deployment {dep_name}")
                apps.replace_namespaced_deployment(dep_name, namespace, deployment)
            else:
                raise
        
        # Create single LoadBalancer service
        service = self._create_service_lb(svc_name, namespace, labels, config.port)
        
        try:
            core.create_namespaced_service(namespace, service)
            self.logger.info(f"Created service {svc_name}")
        except ApiException as e:
            if e.status == 409:
                self.logger.info(f"Service {svc_name} already exists, deleting and recreating")
                try:
                    core.delete_namespaced_service(svc_name, namespace)
                    time.sleep(2)  # Wait for deletion to complete
                except ApiException:
                    pass
                core.create_namespaced_service(namespace, service)
                self.logger.info(f"Recreated service {svc_name}")
            else:
                raise
        
        return [svc_name]
    
    def _deploy_proxies_with_separate_ips(
        self,
        apps: client.AppsV1Api,
        core: client.CoreV1Api,
        namespace: str,
        config: ProxyConfig,
        secret_name: str
    ) -> List[str]:
        """
        Deploy N deployments, each with its own LoadBalancer service.
        Returns list of service names.
        Note: This is more expensive as each LB costs extra.
        """
        service_names = []
        
        for i in range(config.count):
            labels = {"app": "simpleproxy", "instance": str(i)}
            dep_name = f"simpleproxy-{i}"
            svc_name = f"simpleproxy-{i}"
            
            # Create deployment
            deployment = self._create_deployment(
                name=dep_name,
                namespace=namespace,
                image=config.image,
                port=config.port,
                secret_name=secret_name,
                labels=labels,
                cpu=config.cpu,
                memory=config.memory,
            )
            
            try:
                apps.create_namespaced_deployment(namespace, deployment)
                self.logger.info(f"Created deployment {dep_name}")
            except ApiException as e:
                if e.status == 409:
                    self.logger.info(f"Replacing existing deployment {dep_name}")
                    apps.replace_namespaced_deployment(dep_name, namespace, deployment)
                else:
                    raise
            
            # Create LoadBalancer service
            service = self._create_service_lb(svc_name, namespace, labels, config.port)
            
            try:
                core.create_namespaced_service(namespace, service)
                self.logger.info(f"Created service {svc_name}")
            except ApiException as e:
                if e.status == 409:
                    self.logger.info(f"Replacing existing service {svc_name}")
                    core.replace_namespaced_service(svc_name, namespace, service)
                else:
                    raise
            
            service_names.append(svc_name)
        
        return service_names
    
    def _wait_for_service_ips(
        self,
        core: client.CoreV1Api,
        namespace: str,
        service_names: List[str],
        timeout_seconds: int = 600
    ) -> List[ProxyAddress]:
        """
        Wait for all LoadBalancer services to get external IPs.
        Returns list of ProxyAddress objects.
        """
        self.logger.info(f"Waiting for {len(service_names)} service(s) to get external IPs...")
        
        deadline = time.time() + timeout_seconds
        addresses = []
        
        while time.time() < deadline:
            addresses = []
            all_ready = True
            
            for svc_name in service_names:
                try:
                    service = core.read_namespaced_service(svc_name, namespace)
                    if service.status and service.status.load_balancer and service.status.load_balancer.ingress:
                        for ingress in service.status.load_balancer.ingress:
                            ip = ingress.ip or ingress.hostname
                            if ip:
                                addresses.append(
                                    ProxyAddress(
                                        eni=svc_name,  # Use service name as identifier
                                        public_ipv4=ip,
                                        ipv6=[]  # VKE LBs typically provide v4, v6 may be in hostname
                                    )
                                )
                                break
                    else:
                        all_ready = False
                except ApiException as e:
                    self.logger.warning(f"Error reading service {svc_name}: {e}")
                    all_ready = False
            
            if all_ready and len(addresses) == len(service_names):
                self.logger.info(f"All {len(addresses)} service(s) have external IPs")
                return addresses
            
            time.sleep(5)
        
        raise TimeoutError(
            f"Timed out waiting for service IPs. Got {len(addresses)}/{len(service_names)}"
        )
    
    def _cleanup_old_separate_deployments(
        self,
        apps: client.AppsV1Api,
        core: client.CoreV1Api,
        namespace: str
    ) -> None:
        """Clean up old deployments/services with numbered names (simpleproxy-0, simpleproxy-1, etc.)"""
        try:
            # Delete old numbered services
            services = core.list_namespaced_service(namespace).items
            for svc in services:
                # Delete services matching pattern simpleproxy-<number>
                if svc.metadata.name.startswith("simpleproxy-") and svc.metadata.name[-1].isdigit():
                    self.logger.info(f"Cleaning up old service {svc.metadata.name}")
                    core.delete_namespaced_service(svc.metadata.name, namespace)
            
            # Delete old numbered deployments
            deployments = apps.list_namespaced_deployment(namespace).items
            for dep in deployments:
                # Delete deployments matching pattern simpleproxy-<number>
                if dep.metadata.name.startswith("simpleproxy-") and dep.metadata.name[-1].isdigit():
                    self.logger.info(f"Cleaning up old deployment {dep.metadata.name}")
                    apps.delete_namespaced_deployment(dep.metadata.name, namespace)
        except ApiException as e:
            if e.status != 404:
                self.logger.warning(f"Error cleaning up old resources: {e}")
    
    def _cleanup_kubernetes_resources(
        self,
        kubeconfig_path: str,
        namespace: str,
        delete_namespace: bool = False
    ) -> None:
        """Clean up Kubernetes resources."""
        kube_config.load_kube_config(config_file=kubeconfig_path)
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        
        try:
            # Delete services first (to stop LB billing promptly)
            self.logger.info(f"Deleting services in namespace {namespace}")
            try:
                services = core.list_namespaced_service(namespace).items
                for svc in services:
                    if svc.metadata.name.startswith("simpleproxy"):
                        core.delete_namespaced_service(svc.metadata.name, namespace)
                        self.logger.info(f"Deleted service {svc.metadata.name}")
            except ApiException as e:
                if e.status != 404:
                    self.logger.warning(f"Error deleting services: {e}")
            
            # Delete deployments
            self.logger.info(f"Deleting deployments in namespace {namespace}")
            try:
                deployments = apps.list_namespaced_deployment(namespace).items
                for dep in deployments:
                    if dep.metadata.name.startswith("simpleproxy"):
                        apps.delete_namespaced_deployment(dep.metadata.name, namespace)
                        self.logger.info(f"Deleted deployment {dep.metadata.name}")
            except ApiException as e:
                if e.status != 404:
                    self.logger.warning(f"Error deleting deployments: {e}")
            
            # Delete secrets
            try:
                core.delete_namespaced_secret("simpleproxy-env", namespace)
                self.logger.info(f"Deleted secret simpleproxy-env")
            except ApiException as e:
                if e.status != 404:
                    self.logger.warning(f"Error deleting secret: {e}")
            
            # Delete namespace if requested
            if delete_namespace:
                self.logger.info(f"Deleting namespace {namespace}")
                try:
                    core.delete_namespace(namespace)
                except ApiException as e:
                    if e.status != 404:
                        self.logger.warning(f"Error deleting namespace: {e}")
        
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
            raise
    
    def create_proxies(self, config: ProxyConfig) -> List[ProxyAddress]:
        """
        Create proxy instances on Kubernetes.
        This is the main entry point called by the CLI.
        """
        # Get or create cluster
        cluster_id = self._get_or_create_cluster(config)
        self.logger.info(f"Using cluster: {cluster_id}")
        
        # Get kubeconfig
        kubeconfig_path = self._get_kubeconfig_path(cluster_id, config)
        self.logger.info(f"Using kubeconfig: {kubeconfig_path}")
        
        # Load kubeconfig
        kube_config.load_kube_config(config_file=kubeconfig_path)
        core = client.CoreV1Api()
        apps = client.AppsV1Api()
        
        # Use cluster name as namespace
        namespace = config.cluster
        
        # Setup namespace and secret
        self._ensure_namespace(core, namespace)
        self._create_or_replace_secret(
            core,
            namespace,
            "simpleproxy-env",
            {
                "PROXY_USER": config.username,
                "PROXY_PASSWORD_SHA256": config.password_hash,
            }
        )
        
        # Clean up any old separate services/deployments from previous architecture
        self._cleanup_old_separate_deployments(apps, core, namespace)
        
        # Give Kubernetes a moment to finalize deletions
        time.sleep(3)
        
        # Deploy proxies behind single load balancer
        # With one pod per node, each proxy uses its node's IP for outbound traffic
        self.logger.info(f"Deploying {config.count} proxies behind a single load balancer")
        self.logger.info(f"Each proxy will run on its own node with unique outbound IP")
        service_names = self._deploy_proxies_single_lb(
            apps, core, namespace, config, "simpleproxy-env"
        )
        
        # Wait for and collect IPs
        addresses = self._wait_for_service_ips(core, namespace, service_names)
        
        return addresses

