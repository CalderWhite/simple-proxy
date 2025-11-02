package utils

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
