import sys
import time
import json
import logging
import boto3
from botocore.exceptions import ClientError
from simpleproxy.src.cloud_provider import CloudProvider, ProxyConfig, ProxyAddress


class AwsFargateProvider(CloudProvider):
    def __init__(self):
        logging.basicConfig(level=logging.INFO)

    def _get_or_create_cluster(self, ecs, name):
        logging.info(f"Getting or creating cluster {name}")
        try:
            ecs.describe_clusters(clusters=[name])
        except ecs.exceptions.ClusterNotFoundException:
            pass
        ecs.create_cluster(clusterName=name)
        return name

    def _get_default_vpc(self, ec2):
        logging.info("Getting default VPC")
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            print("No default VPC found.", file=sys.stderr)
            sys.exit(1)
        return vpcs[0]["VpcId"]

    def _ensure_sg(self, ec2, vpc_id, name, port):
        logging.info(f"Ensuring security group {name} in VPC {vpc_id} for port {port}")
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc_id]}]
        )["SecurityGroups"]
        if sgs:
            sg_id = sgs[0]["GroupId"]
        else:
            sg_id = ec2.create_security_group(
                GroupName=name, Description=f"Allow {port}/tcp", VpcId=vpc_id
            )["GroupId"]
        # allow port IPv4/IPv6 (ignore duplicates)
        for ipv, key in [("0.0.0.0/0", "IpRanges"), ("::/0", "Ipv6Ranges")]:
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[{
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        key: [{"CidrIp": ipv} if key == "IpRanges" else {"CidrIpv6": ipv}]
                    }]
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                    raise
        return sg_id

    def _default_public_subnets(self, ec2, vpc_id):
        # default VPC subnets are typically public; grab all of them
        return [s["SubnetId"] for s in ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["Subnets"]]

    def _ensure_exec_role(self, iam):
        logging.info("Ensuring ECS task execution role")
        name = "ecsTaskExecutionRole"
        arn = None
        try:
            arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
            trust = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }
            arn = iam.create_role(
                RoleName=name, AssumeRolePolicyDocument=json.dumps(trust)
            )["Role"]["Arn"]
            iam.attach_role_policy(
                RoleName=name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
            )
            # tiny settle
            time.sleep(3)
        return arn

    def _ensure_log_group(self, logs, name, retention_days=7):
        logging.info(f"Ensuring CloudWatch log group {name}")
        try:
            logs.create_log_group(logGroupName=name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
        # optional: limit retention so logs don't snowball
        try:
            logs.put_retention_policy(logGroupName=name, retentionInDays=retention_days)
        except ClientError as e:
            if e.response["Error"]["Code"] != "OperationAbortedException":
                raise

    def _register_task(self, ecs, family, image, cpu, memory, port, envs, exec_role_arn, log_group, region):
        logging.info(f"Registering task definition {family}")
        return ecs.register_task_definition(
            family=family,
            requiresCompatibilities=["FARGATE"],
            networkMode="awsvpc",
            cpu=str(cpu),
            memory=str(memory),
            executionRoleArn=exec_role_arn,
            containerDefinitions=[{
                "name": "app",
                "image": image,
                "essential": True,
                "portMappings": [{"containerPort": port, "protocol": "tcp"}],
                "environment": [{"name": k, "value": v} for k, v in envs],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": log_group,
                        "awslogs-region": region,
                        "awslogs-stream-prefix": "ecs"
                    }
                }
            }]
        )["taskDefinition"]["taskDefinitionArn"]

    def _run_tasks(self, ecs, cluster, task_def_arn, subnets, sg_id, count):
        logging.info(f"Running {count} tasks")
        tasks = []
        for batch_size in [10] * (count // 10) + ([count % 10] if count % 10 else []):
            resp = ecs.run_task(
                cluster=cluster,
                taskDefinition=task_def_arn,
                launchType="FARGATE",
                count=batch_size,
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": subnets,
                        "securityGroups": [sg_id],
                        "assignPublicIp": "ENABLED"
                    }
                }
            )
            failures = resp.get("failures", [])
            if failures:
                raise RuntimeError(f"ECS run_task failures: {failures}")
            tasks += [t["taskArn"] for t in resp["tasks"]]
        return tasks

    def _wait_running(self, ecs, cluster, task_arns):
        logging.info(f"Waiting for {len(task_arns)} tasks to be running")
        while True:
            desc = ecs.describe_tasks(cluster=cluster, tasks=task_arns)["tasks"]
            statuses = [t["lastStatus"] for t in desc]
            if any(s == "STOPPED" for s in statuses):
                for t in desc:
                    if t["lastStatus"] == "STOPPED":
                        print("\nSTOPPED task:", t["taskArn"], file=sys.stderr)
                        print("  stoppedReason:", t.get("stoppedReason"), file=sys.stderr)
                        for c in t.get("containers", []):
                            print(
                                f"  container {c['name']} exit={c.get('exitCode')} reason={c.get('reason')}",
                                file=sys.stderr
                            )
                raise SystemExit(1)
            if all(s == "RUNNING" for s in statuses):
                return
            time.sleep(2)

    def _task_public_addrs(self, ecs, ec2, cluster, task_arns):
        logging.info(f"Getting public addresses for {len(task_arns)} tasks")
        desc = ecs.describe_tasks(cluster=cluster, tasks=task_arns)["tasks"]
        eni_ids = []
        for t in desc:
            for att in t.get("attachments", []):
                if att.get("type") == "ElasticNetworkInterface":
                    for d in att.get("details", []):
                        if d.get("name") == "networkInterfaceId":
                            eni_ids.append(d["value"])
        if not eni_ids:
            return []
        nis = ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)["NetworkInterfaces"]
        out = []
        for ni in nis:
            pub4 = ni.get("Association", {}).get("PublicIp")
            v6s = [x["Ipv6Address"] for x in ni.get("Ipv6Addresses", [])]
            out.append(ProxyAddress(eni=ni["NetworkInterfaceId"], public_ipv4=pub4, ipv6=v6s))
        return out

    def create_proxies(self, config: ProxyConfig) -> list[ProxyAddress]:
        """Create proxy instances on AWS ECS Fargate."""
        # Build environment variables for the proxy
        envs = [
            ("PROXY_USER", config.username),
            ("PROXY_PASSWORD_SHA256", config.password_hash)
        ]

        # Create AWS clients
        session = boto3.session.Session(region_name=config.region)
        ecs = session.client("ecs")
        ec2c = session.client("ec2")
        iam = session.client("iam")
        logs = session.client("logs")

        # Setup infrastructure
        cluster = self._get_or_create_cluster(ecs, config.cluster)
        vpc_id = self._get_default_vpc(ec2c)
        subnets = self._default_public_subnets(ec2c, vpc_id)
        sg_id = self._ensure_sg(ec2c, vpc_id, f"{config.cluster}-{config.port}", config.port)
        exec_role_arn = self._ensure_exec_role(iam)

        # Setup logging
        log_group = f"/ecs/{config.cluster}"
        self._ensure_log_group(logs, log_group)

        # Register and run tasks
        family = f"{config.cluster}-{int(time.time())}"
        task_def = self._register_task(
            ecs, family, config.image, config.cpu, config.memory,
            config.port, envs, exec_role_arn, log_group, config.region
        )

        task_arns = self._run_tasks(ecs, cluster, task_def, subnets, sg_id, config.count)
        self._wait_running(ecs, cluster, task_arns)
        addresses = self._task_public_addrs(ecs, ec2c, cluster, task_arns)

        return addresses

    def cleanup(self, config: ProxyConfig, keep_task_defs: bool = False) -> None:
        """Clean up all resources created by create_proxies()."""
        session = boto3.session.Session(region_name=config.region)
        ecs = session.client("ecs")
        ec2c = session.client("ec2")
        logs = session.client("logs")

        vpc_id = self._get_default_vpc(ec2c)
        sg_name = f"{config.cluster}-{config.port}"
        log_group = f"/ecs/{config.cluster}"

        logging.info(f"Starting cleanup for cluster {config.cluster}")

        # 1. Stop all running tasks in the cluster
        try:
            logging.info(f"Listing tasks in cluster {config.cluster}")
            task_arns = ecs.list_tasks(cluster=config.cluster, desiredStatus="RUNNING")["taskArns"]
            if task_arns:
                logging.info(f"Stopping {len(task_arns)} running tasks")
                for task_arn in task_arns:
                    try:
                        ecs.stop_task(cluster=config.cluster, task=task_arn, reason="Cleanup")
                    except ClientError as e:
                        logging.warning(f"Failed to stop task {task_arn}: {e}")
                # Wait for tasks to stop
                logging.info("Waiting for tasks to stop...")
                time.sleep(10)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ClusterNotFoundException":
                logging.warning(f"Error listing/stopping tasks: {e}")

        # 2. Deregister task definitions (optional)
        if not keep_task_defs:
            try:
                logging.info(f"Listing task definitions for family pattern {config.cluster}")
                families = ecs.list_task_definition_families(
                    familyPrefix=config.cluster, status="ACTIVE"
                )["families"]
                for family in families:
                    # List all revisions
                    task_defs = ecs.list_task_definitions(
                        familyPrefix=family, status="ACTIVE"
                    )["taskDefinitionArns"]
                    for task_def_arn in task_defs:
                        try:
                            logging.info(f"Deregistering task definition {task_def_arn}")
                            ecs.deregister_task_definition(taskDefinition=task_def_arn)
                        except ClientError as e:
                            logging.warning(f"Failed to deregister {task_def_arn}: {e}")
            except ClientError as e:
                logging.warning(f"Error deregistering task definitions: {e}")

        # 3. Delete the cluster
        try:
            logging.info(f"Deleting cluster {config.cluster}")
            ecs.delete_cluster(cluster=config.cluster)
            logging.info(f"Cluster {config.cluster} deleted")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ClusterNotFoundException":
                logging.warning(f"Failed to delete cluster: {e}")

        # 4. Delete the security group
        try:
            sgs = ec2c.describe_security_groups(
                Filters=[
                    {"Name": "group-name", "Values": [sg_name]},
                    {"Name": "vpc-id", "Values": [vpc_id]}
                ]
            )["SecurityGroups"]
            if sgs:
                sg_id = sgs[0]["GroupId"]
                logging.info(f"Deleting security group {sg_id} ({sg_name})")
                # May need to wait a bit for ENIs to be released
                max_retries = 5
                for attempt in range(max_retries):
                    try:
                        ec2c.delete_security_group(GroupId=sg_id)
                        logging.info(f"Security group {sg_id} deleted")
                        break
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "DependencyViolation" and attempt < max_retries - 1:
                            logging.info(
                                f"Security group still in use, waiting... (attempt {attempt + 1}/{max_retries})"
                            )
                            time.sleep(10)
                        else:
                            logging.warning(f"Failed to delete security group: {e}")
                            break
        except ClientError as e:
            logging.warning(f"Error deleting security group: {e}")

        # 5. Delete the log group
        try:
            logging.info(f"Deleting log group {log_group}")
            logs.delete_log_group(logGroupName=log_group)
            logging.info(f"Log group {log_group} deleted")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                logging.warning(f"Failed to delete log group: {e}")