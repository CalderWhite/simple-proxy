package main

import (
	"io"
	"log/slog"
	"net"
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
	slog.Info("Request received",
		"method", r.Method,
		"host", r.Host,
		"remote_addr", r.RemoteAddr,
	)

	if r.Method == http.MethodConnect {
		handleTunnel(w, r)
		return
	}

	// For non-CONNECT methods, do basic HTTP forwarding
	handleHTTP(w, r)
}

func handleTunnel(w http.ResponseWriter, r *http.Request) {
	targetConn, err := net.DialTimeout("tcp", r.Host, 10*time.Second)
	if err != nil {
		slog.Error("Failed to connect to target", "host", r.Host, "error", err)
		http.Error(w, "Failed to connect to target", http.StatusBadGateway)
		return
	}
	defer targetConn.Close()

	hijacker, ok := w.(http.Hijacker)
	if !ok {
		slog.Error("Hijacking not supported")
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}

	clientConn, _, err := hijacker.Hijack()
	if err != nil {
		slog.Error("Failed to hijack connection", "error", err)
		http.Error(w, "Failed to hijack connection", http.StatusInternalServerError)
		return
	}
	defer clientConn.Close()

	_, err = clientConn.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n"))
	if err != nil {
		slog.Error("Failed to send connection established", "error", err)
		return
	}

	slog.Info("Tunnel established", "host", r.Host)

	// Bidirectional copy
	go io.Copy(targetConn, clientConn)
	io.Copy(clientConn, targetConn)

	slog.Info("Tunnel closed", "host", r.Host)
}

func handleHTTP(w http.ResponseWriter, r *http.Request) {
	targetURL := r.URL.String()
	if !r.URL.IsAbs() && r.Host != "" {
		targetURL = "http://" + r.Host + r.URL.Path
		if r.URL.RawQuery != "" {
			targetURL += "?" + r.URL.RawQuery
		}
	}

	outReq, err := http.NewRequest(r.Method, targetURL, r.Body)
	if err != nil {
		slog.Error("Failed to create request", "error", err)
		http.Error(w, "Bad Request", http.StatusBadRequest)
		return
	}

	for key, values := range r.Header {
		for _, value := range values {
			outReq.Header.Add(key, value)
		}
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(outReq)
	if err != nil {
		slog.Error("Failed to send request", "error", err, "url", targetURL)
		http.Error(w, "Bad Gateway", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for key, values := range resp.Header {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}

	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)

	slog.Info("HTTP request proxied", "url", targetURL, "status", resp.StatusCode)
}
