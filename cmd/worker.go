package main

import (
	"io"
	"log/slog"
	"net/http"
	"os"
	"time"

	utils "github.com/simple-proxy"
)

func main() {
	utils.InitLogger()

	port := os.Getenv("PROXY_PORT")
	if port == "" {
		port = "8080"
	}

	slog.Info("Simple Proxy is starting...", "port", port)

	server := &http.Server{
		Addr:         "[::]:" + port,
		Handler:      http.HandlerFunc(proxyHandler),
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
	}

	slog.Info("Proxy server listening", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil {
		slog.Error("Server failed to start", "error", err)
		os.Exit(1)
	}
}

func proxyHandler(w http.ResponseWriter, r *http.Request) {
	slog.Info("Proxying request",
		"method", r.Method,
		"url", r.URL.String(),
		"remote_addr", r.RemoteAddr,
	)

	targetURL := r.URL.String()
	if r.URL.Scheme == "" {
		// If no scheme is provided, assume http
		targetURL = "http://" + r.Host + r.URL.Path
		if r.URL.RawQuery != "" {
			targetURL += "?" + r.URL.RawQuery
		}
	}

	proxyReq, err := http.NewRequest(r.Method, targetURL, r.Body)
	if err != nil {
		slog.Error("Failed to create proxy request", "error", err)
		http.Error(w, "Failed to create proxy request", http.StatusInternalServerError)
		return
	}

	for key, values := range r.Header {
		for _, value := range values {
			proxyReq.Header.Add(key, value)
		}
	}

	client := &http.Client{
		Timeout: 30 * time.Second,
	}
	resp, err := client.Do(proxyReq)
	if err != nil {
		slog.Error("Failed to execute proxy request", "error", err)
		http.Error(w, "Failed to proxy request", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for key, values := range resp.Header {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}

	w.WriteHeader(resp.StatusCode)

	_, err = io.Copy(w, resp.Body)
	if err != nil {
		slog.Error("Failed to copy response body", "error", err)
		return
	}

	slog.Info("Request proxied successfully",
		"method", r.Method,
		"url", targetURL,
		"status", resp.StatusCode,
	)
}
