package main

import (
	"log/slog"

	utils "github.com/simple-proxy"
)

func main() {
	utils.InitLogger()

	slog.Info("Simple Proxy is starting...")
}
