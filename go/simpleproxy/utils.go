package simpleproxy

import (
	"log/slog"
	"os"

	"github.com/lmittmann/tint"
)

func InitLogger() {
	if os.Getenv("SIMPLE_PROXY_ENV") == "development" {
		logger := slog.New(tint.NewHandler(os.Stdout, nil))
		slog.SetDefault(logger)
	} else {
		logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
		slog.SetDefault(logger)
	}
}

func ExpectEnvVar(name string) string {
	value := os.Getenv(name)
	if value == "" {
		slog.Error("Environment variable is not set", "name", name)
		panic("Environment variable is not set")
	}
	return value
}

func GetEnvOrDefault(name, defaultValue string) string {
	value := os.Getenv(name)
	if value == "" {
		return defaultValue
	}
	return value
}
