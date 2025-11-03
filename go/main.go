package main

import (
	"context"

	"github.com/CalderWhite/simple-proxy/go/simpleproxy"
	"github.com/joho/godotenv"
	"github.com/spf13/cobra"
)

func main() {
	// Only load the .env file if it exists
	godotenv.Load()

	cobra.CheckErr(simpleproxy.NewCLI().ExecuteContext(context.Background()))
}
