package simpleproxy

import (
	"log/slog"

	"github.com/spf13/cobra"
)

func NewCLI() *cobra.Command {
	rootCmd := &cobra.Command{
		Use:           "simple-proxy",
		Short:         "A simple HTTP/HTTPS proxy server with authentication",
		SilenceUsage:  true,
		SilenceErrors: true,
		CompletionOptions: cobra.CompletionOptions{
			DisableDefaultCmd: true,
		},
		Run: func(cmd *cobra.Command, args []string) {
			cmd.Print(cmd.UsageString())
		},
	}

	workerCmd := &cobra.Command{
		Use:   "worker",
		Short: "Run the proxy worker server",
		Args:  cobra.NoArgs,
		RunE:  WorkerHandler,
	}

	loadbalancerCmd := &cobra.Command{
		Use:   "loadbalancer",
		Short: "Run the load balancer server",
		Args:  cobra.NoArgs,
		RunE:  LoadBalancerHandler,
	}

	rootCmd.AddCommand(
		workerCmd,
		loadbalancerCmd,
	)

	return rootCmd
}

func WorkerHandler(cmd *cobra.Command, args []string) error {
	InitLogger()

	username := ExpectEnvVar("PROXY_USER")
	passwordHashHex := ExpectEnvVar("PROXY_PASSWORD_SHA256")
	server, err := NewProxyServer(username, passwordHashHex)
	if err != nil {
		slog.Error("Failed to create proxy server", "error", err)
		return err
	}

	return server.Run()
}

func LoadBalancerHandler(cmd *cobra.Command, args []string) error {
	// TODO: Implement load balancer
	slog.Info("Load balancer not yet implemented")
	return nil
}
