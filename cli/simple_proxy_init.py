#!/usr/bin/env python3
import argparse, sys, time, json, itertools
import boto3
from botocore.exceptions import ClientError
import logging

def get_or_create_cluster(ecs, name):
    logging.info(f"Getting or creating cluster {name}")
    try:
        ecs.describe_clusters(clusters=[name])
    except ecs.exceptions.ClusterNotFoundException:
        pass
    ecs.create_cluster(clusterName=name)
    return name

def get_default_vpc(ec2):
    logging.info("Getting default VPC")
    vpcs = ec2.describe_vpcs(Filters=[{"Name":"isDefault","Values":["true"]}])["Vpcs"]
    if not vpcs: print("No default VPC. Pass --subnet-ids and --sg-id.", file=sys.stderr); sys.exit(1)
    return vpcs[0]["VpcId"]

def ensure_sg(ec2, vpc_id, name, port):
    logging.info(f"Ensuring security group {name} in VPC {vpc_id} for port {port}")
    sgs = ec2.describe_security_groups(Filters=[{"Name":"group-name","Values":[name]},{"Name":"vpc-id","Values":[vpc_id]}])["SecurityGroups"]
    if sgs:
        sg_id = sgs[0]["GroupId"]
    else:
        sg_id = ec2.create_security_group(GroupName=name, Description=f"Allow {port}/tcp", VpcId=vpc_id)["GroupId"]
    # allow 8080 IPv4/IPv6 (ignore duplicates)
    for ipv, key in [("0.0.0.0/0","IpRanges"), ("::/0","Ipv6Ranges")]:
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{"IpProtocol":"tcp","FromPort":port,"ToPort":port, key:[{"CidrIp":ipv} if key=="IpRanges" else {"CidrIpv6":ipv}]}]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.Duplicate": raise
    return sg_id

def default_public_subnets(ec2, vpc_id):
    # default VPC subnets are typically public; grab all of them
    return [s["SubnetId"] for s in ec2.describe_subnets(Filters=[{"Name":"vpc-id","Values":[vpc_id]}])["Subnets"]]

def ensure_exec_role(iam):
    logging.info("Ensuring ECS task execution role")
    name = "ecsTaskExecutionRole"
    arn = None
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity": raise
        trust = {
          "Version":"2012-10-17",
          "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]
        }
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        iam.attach_role_policy(RoleName=name, PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy")
        # tiny settle
        time.sleep(3)
    return arn

def ensure_log_group(logs, name, retention_days=7):
    logging.info(f"Ensuring CloudWatch log group {name}")
    try:
        logs.create_log_group(logGroupName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException": raise
    # optional: limit retention so logs don't snowball
    try:
        logs.put_retention_policy(logGroupName=name, retentionInDays=retention_days)
    except ClientError as e:
        if e.response["Error"]["Code"] != "OperationAbortedException": raise

def register_task(ecs, family, image, cpu, memory, port, envs, exec_role_arn, log_group, region):
    logging.info(f"Registering task definition {family} with image {image}, CPU {cpu}, memory {memory}, port {port}, environment {envs}, and execution role {exec_role_arn}")
    return ecs.register_task_definition(
        family=family,
        requiresCompatibilities=["FARGATE"],
        networkMode="awsvpc",
        cpu=str(cpu),
        memory=str(memory),
        executionRoleArn=exec_role_arn,
        containerDefinitions=[{
            "name":"app",
            "image":image,
            "essential":True,
            "portMappings":[{"containerPort":port,"protocol":"tcp"}],
            "environment":[{"name":k,"value":v} for k,v in envs],
            "logConfiguration":{"logDriver":"awslogs","options":{
                "awslogs-group":log_group,"awslogs-region":region,
                "awslogs-stream-prefix":"ecs"
            }}
        }]
    )["taskDefinition"]["taskDefinitionArn"]

def run_tasks(ecs, cluster, task_def_arn, subnets, sg_id, count):
    logging.info(f"Running {count} tasks with task definition {task_def_arn} in cluster {cluster} on subnets {subnets} with security group {sg_id}")
    tasks = []
    for batch_size in [10]* (count//10) + [count%10] if count%10 else [10]*(count//10):
        resp = ecs.run_task(
            cluster=cluster, taskDefinition=task_def_arn, launchType="FARGATE",
            count=batch_size,
            networkConfiguration={
              "awsvpcConfiguration":{
                "subnets": subnets,
                "securityGroups": [sg_id],
                "assignPublicIp": "ENABLED"
              }
            }
        )
        failures = resp.get("failures", [])
        if failures: raise RuntimeError(f"ECS run_task failures: {failures}")
        tasks += [t["taskArn"] for t in resp["tasks"]]
    return tasks

def wait_running(ecs, cluster, task_arns):
    logging.info(f"Waiting for {len(task_arns)} tasks to be running in cluster {cluster}")
    while True:
        desc = ecs.describe_tasks(cluster=cluster, tasks=task_arns)["tasks"]
        statuses = [t["lastStatus"] for t in desc]
        if any(s == "STOPPED" for s in statuses):
            for t in desc:
                if t["lastStatus"] == "STOPPED":
                    print("\nSTOPPED task:", t["taskArn"], file=sys.stderr)
                    print("  stoppedReason:", t.get("stoppedReason"), file=sys.stderr)
                    for c in t.get("containers", []):
                        print(f"  container {c['name']} exit={c.get('exitCode')} reason={c.get('reason')}", file=sys.stderr)
            raise SystemExit(1)
        if all(s == "RUNNING" for s in statuses):
            return
        time.sleep(2)

def task_public_addrs(ecs, ec2, cluster, task_arns):
    logging.info(f"Getting public addresses for {len(task_arns)} tasks in cluster {cluster}")
    desc = ecs.describe_tasks(cluster=cluster, tasks=task_arns)["tasks"]
    eni_ids = []
    for t in desc:
        for att in t.get("attachments", []):
            if att.get("type") == "ElasticNetworkInterface":
                for d in att.get("details", []):
                    if d.get("name") == "networkInterfaceId":
                        eni_ids.append(d["value"])
    if not eni_ids: return []
    nis = ec2.describe_network_interfaces(NetworkInterfaceIds=eni_ids)["NetworkInterfaces"]
    out = []
    for ni in nis:
        pub4 = ni.get("Association", {}).get("PublicIp")
        v6s = [x["Ipv6Address"] for x in ni.get("Ipv6Addresses", [])]
        out.append({"eni": ni["NetworkInterfaceId"], "public_ipv4": pub4, "ipv6": v6s})
    return out

def cleanup_resources(ecs, ec2, logs, cluster, sg_name, log_group, vpc_id, deregister_tasks=True):
    """Clean up all resources created by this script."""
    logging.info(f"Starting cleanup for cluster {cluster}")
    
    # 1. Stop all running tasks in the cluster
    try:
        logging.info(f"Listing tasks in cluster {cluster}")
        task_arns = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING")["taskArns"]
        if task_arns:
            logging.info(f"Stopping {len(task_arns)} running tasks")
            for task_arn in task_arns:
                try:
                    ecs.stop_task(cluster=cluster, task=task_arn, reason="Cleanup")
                except ClientError as e:
                    logging.warning(f"Failed to stop task {task_arn}: {e}")
            # Wait for tasks to stop
            logging.info("Waiting for tasks to stop...")
            time.sleep(10)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ClusterNotFoundException":
            logging.warning(f"Error listing/stopping tasks: {e}")
    
    # 2. Deregister task definitions (optional)
    if deregister_tasks:
        try:
            logging.info(f"Listing task definitions for family pattern {cluster}")
            families = ecs.list_task_definition_families(familyPrefix=cluster, status="ACTIVE")["families"]
            for family in families:
                # List all revisions
                task_defs = ecs.list_task_definitions(familyPrefix=family, status="ACTIVE")["taskDefinitionArns"]
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
        logging.info(f"Deleting cluster {cluster}")
        ecs.delete_cluster(cluster=cluster)
        logging.info(f"Cluster {cluster} deleted")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ClusterNotFoundException":
            logging.warning(f"Failed to delete cluster: {e}")
    
    # 4. Delete the security group
    try:
        sgs = ec2.describe_security_groups(Filters=[{"Name":"group-name","Values":[sg_name]},{"Name":"vpc-id","Values":[vpc_id]}])["SecurityGroups"]
        if sgs:
            sg_id = sgs[0]["GroupId"]
            logging.info(f"Deleting security group {sg_id} ({sg_name})")
            # May need to wait a bit for ENIs to be released
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    logging.info(f"Security group {sg_id} deleted")
                    break
                except ClientError as e:
                    if e.response["Error"]["Code"] == "DependencyViolation" and attempt < max_retries - 1:
                        logging.info(f"Security group still in use, waiting... (attempt {attempt + 1}/{max_retries})")
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
    
    logging.info("Cleanup complete!")

def cmd_run(args):
    """Run tasks in ECS Fargate."""
    envs=[]
    for e in args.env:
        if "=" not in e: print(f"--env {e} must be KEY=VALUE", file=sys.stderr); sys.exit(2)
        k,v = e.split("=",1); envs.append((k.strip(), v.strip()))

    session = boto3.session.Session(region_name=args.region)
    ecs = session.client("ecs")
    ec2c = session.client("ec2")
    iam = session.client("iam")
    logs = session.client("logs")

    cluster = get_or_create_cluster(ecs, args.cluster)

    vpc_id = get_default_vpc(ec2c)
    subnets = args.subnet_ids or default_public_subnets(ec2c, vpc_id)
    sg_id = args.sg_id or ensure_sg(ec2c, vpc_id, f"{args.cluster}-{args.port}", args.port)
    exec_role_arn = ensure_exec_role(iam)
    
    # Use a stable log group name based on cluster (not per-run family)
    log_group = f"/ecs/{args.cluster}"
    ensure_log_group(logs, log_group)

    family = f"{args.cluster}-{int(time.time())}"
    task_def = register_task(ecs, family, args.image, args.cpu, args.memory, args.port, envs, exec_role_arn, log_group, args.region)

    task_arns = run_tasks(ecs, cluster, task_def, subnets, sg_id, args.count)
    wait_running(ecs, cluster, task_arns)
    addrs = task_public_addrs(ecs, ec2c, cluster, task_arns)

    print("\nTasks are RUNNING. Public addresses:")
    for a in addrs:
        v6 = (", IPv6: " + ", ".join(a["ipv6"])) if a["ipv6"] else ""
        print(f"- ENI {a['eni']}  IPv4: {a['public_ipv4']}{v6}")

def cmd_cleanup(args):
    """Clean up all resources created by this script."""
    session = boto3.session.Session(region_name=args.region)
    ecs = session.client("ecs")
    ec2c = session.client("ec2")
    logs = session.client("logs")

    vpc_id = get_default_vpc(ec2c)
    sg_name = f"{args.cluster}-{args.port}"
    log_group = f"/ecs/{args.cluster}"
    
    cleanup_resources(ecs, ec2c, logs, args.cluster, sg_name, log_group, vpc_id, 
                     deregister_tasks=not args.keep_task_defs)

def main():
    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser(description="Manage ECS Fargate tasks")
    subparsers = ap.add_subparsers(dest="command", help="Commands")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run tasks in ECS Fargate")
    run_parser.add_argument("--count", type=int, required=True, help="Number of tasks to run")
    run_parser.add_argument("--image", default="calderwhite/simple-proxy:latest", help="Docker image to run")
    run_parser.add_argument("--env", action="append", default=[], help="KEY=VALUE environment variables (repeat)")
    run_parser.add_argument("--region", default="us-east-1", help="AWS region")
    run_parser.add_argument("--cluster", default="oneoff-fargate", help="ECS cluster name")
    run_parser.add_argument("--cpu", default="256", help="CPU units (0.25 vCPU)")
    run_parser.add_argument("--memory", default="512", help="Memory in MB")
    run_parser.add_argument("--port", type=int, default=8080, help="Container port")
    run_parser.add_argument("--subnet-ids", nargs="*", help="Subnet IDs (optional)")
    run_parser.add_argument("--sg-id", help="Security group ID (optional)")
    run_parser.set_defaults(func=cmd_run)
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up all resources")
    cleanup_parser.add_argument("--region", default="us-east-1", help="AWS region")
    cleanup_parser.add_argument("--cluster", default="oneoff-fargate", help="ECS cluster name to clean up")
    cleanup_parser.add_argument("--port", type=int, default=8080, help="Port used for security group name")
    cleanup_parser.add_argument("--keep-task-defs", action="store_true", help="Don't deregister task definitions")
    cleanup_parser.set_defaults(func=cmd_cleanup)
    
    args = ap.parse_args()
    
    if not args.command:
        ap.print_help()
        sys.exit(1)
    
    args.func(args)

if __name__ == "__main__":
    main()
