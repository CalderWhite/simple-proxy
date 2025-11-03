import argparse
import sys
from hashlib import sha256
from simpleproxy.src.cloud_provider import ProxyConfig
from simpleproxy.src.providers.aws_fargate import AwsFargateProvider


class Cli:
    def __init__(self):
        self.providers = {
            "aws_fargate": AwsFargateProvider()
        }

    @staticmethod
    def _get_password_hash(password: str) -> str:
        return sha256(password.encode()).hexdigest()

    def cmd_run(self, args):
        """Run proxy tasks."""
        # Get the provider
        provider = self.providers.get(args.provider)
        if not provider:
            print(f"Error: Unknown provider '{args.provider}'", file=sys.stderr)
            sys.exit(1)

        # Hash the password
        password_hash = self._get_password_hash(args.password)

        # Build config
        config = ProxyConfig(
            username=args.username,
            password_hash=password_hash,
            count=args.count,
            region=args.region,
            cluster=args.cluster,
            cpu=args.cpu,
            memory=args.memory,
            port=args.port,
            image=args.image
        )

        # Create proxies
        print(f"Creating {config.count} proxy instances in {config.region}...")
        addresses = provider.create_proxies(config)

        # Display results
        print("\nProxies are RUNNING. Public addresses:")
        for addr in addresses:
            v6 = (", IPv6: " + ", ".join(addr.ipv6)) if addr.ipv6 else ""
            print(f"- ENI {addr.eni}  IPv4: {addr.public_ipv4}{v6}")

    def cmd_cleanup(self, args):
        """Clean up all resources."""
        # Get the provider
        provider = self.providers.get(args.provider)
        if not provider:
            print(f"Error: Unknown provider '{args.provider}'", file=sys.stderr)
            sys.exit(1)

        # Build minimal config for cleanup
        config = ProxyConfig(
            username="",         # Not needed for cleanup
            password_hash="",    # Not needed for cleanup
            count=0,             # Not needed for cleanup
            region=args.region,
            cluster=args.cluster,
            port=args.port
        )

        # Clean up
        print(f"Cleaning up resources in cluster '{config.cluster}'...")
        provider.cleanup(config, keep_task_defs=args.keep_task_defs)
        print("Cleanup complete!")

    def main(self):
        parser = argparse.ArgumentParser(
            description="Simple Proxy - Deploy proxy servers to cloud providers"
        )
        subparsers = parser.add_subparsers(dest="command", help="Commands")

        # Run command
        run_parser = subparsers.add_parser("run", help="Run proxy instances")
        run_parser.add_argument("--username", required=True, help="Proxy username")
        run_parser.add_argument("--password", required=True, help="Proxy password")
        run_parser.add_argument("--count", type=int, required=True, help="Number of proxy instances")
        run_parser.add_argument("--provider", default="aws_fargate", choices=list(self.providers.keys()), help="Cloud provider")
        run_parser.add_argument("--region", default="us-east-1", help="Cloud region (default: us-east-1)")
        run_parser.add_argument("--cluster", default="oneoff-fargate", help="Cluster name (default: oneoff-fargate)")
        run_parser.add_argument("--cpu", default="256", help="CPU units (default: 256)")
        run_parser.add_argument("--memory", default="512", help="Memory in MB (default: 512)")
        run_parser.add_argument("--port", type=int, default=8080, help="Proxy port (default: 8080)")
        run_parser.add_argument("--image", default="calderwhite/simple-proxy:latest", help="Docker image")
        run_parser.set_defaults(func=self.cmd_run)

        # Cleanup command
        cleanup_parser = subparsers.add_parser("cleanup", help="Clean up all resources")
        cleanup_parser.add_argument("--provider", default="aws_fargate", choices=list(self.providers.keys()), help="Cloud provider (default: aws_fargate)")
        cleanup_parser.add_argument("--region", default="us-east-1", help="Cloud region (default: us-east-1)")
        cleanup_parser.add_argument("--cluster", default="oneoff-fargate", help="Cluster name (default: oneoff-fargate)")
        cleanup_parser.add_argument("--port", type=int, default=8080, help="Port used for security group (default: 8080)")
        cleanup_parser.add_argument("--keep-task-defs", action="store_true", help="Don't deregister task definitions")
        cleanup_parser.set_defaults(func=self.cmd_cleanup)

        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            sys.exit(1)

        args.func(args)