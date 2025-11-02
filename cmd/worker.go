package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/joho/godotenv"
	utils "github.com/simple-proxy"
)

type proxyServer struct {
	passwordHash []byte
	username     string
}

func NewProxyServer(username, passwordHashHex string) (*proxyServer, error) {
	hash, err := hex.DecodeString(passwordHashHex)
	if err != nil {
		slog.Error("Failed to decode password hash", "error", err)
		return nil, err
	}

	if len(hash) != 32 {
		slog.Error("Invalid SHA256 hash length", "expected", 32, "got", len(hash))
		return nil, errors.New("invalid SHA256 hash")
	}

	slog.Info("Password hash loaded successfully")

	return &proxyServer{
		passwordHash: hash,
		username:     username,
	}, nil
}

func (s *proxyServer) Run() error {
	port := utils.GetEnvOrDefault("PROXY_PORT", "8080")

	slog.Info("Simple Proxy is starting...", "port", port)

	server := &http.Server{
		Addr:         "[::]:" + port,
		Handler:      http.HandlerFunc(s.proxyHandler),
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
	}

	slog.Info("Proxy server listening", "address", server.Addr)
	if err := server.ListenAndServe(); err != nil {
		slog.Error("Server failed to start", "error", err)
		return err
	}

	return nil
}

func (s *proxyServer) proxyHandler(w http.ResponseWriter, r *http.Request) {
	slog.Info("Request received",
		"method", r.Method,
		"host", r.Host,
		"remote_addr", r.RemoteAddr,
	)

	if !s.authenticate(r) {
		w.Header().Set("Proxy-Authenticate", "Basic realm=\"Proxy\"")
		http.Error(w, "Proxy authentication required", http.StatusProxyAuthRequired)
		slog.Warn("Authentication failed", "remote_addr", r.RemoteAddr)
		return
	}

	if r.Method == http.MethodConnect {
		s.handleTunnel(w, r)
		return
	}

	// For non-CONNECT methods, do basic HTTP forwarding
	s.handleHTTP(w, r)
}

func (s *proxyServer) authenticate(r *http.Request) bool {
	auth := r.Header.Get("Proxy-Authorization")
	if auth == "" {
		return false
	}

	const prefix = "Basic "
	if !strings.HasPrefix(auth, prefix) {
		return false
	}

	// Decode the base64 credentials
	encoded := auth[len(prefix):]
	decoded, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return false
	}

	// Split username:password
	credentials := string(decoded)
	parts := strings.SplitN(credentials, ":", 2)
	if len(parts) != 2 {
		return false
	}

	username := parts[0]
	password := parts[1]

	// Check username matches
	if username != s.username {
		return false
	}

	hash := sha256.Sum256([]byte(password))

	return subtle.ConstantTimeCompare(hash[:], s.passwordHash) == 1
}

func (s *proxyServer) handleTunnel(w http.ResponseWriter, r *http.Request) {
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

func (s *proxyServer) handleHTTP(w http.ResponseWriter, r *http.Request) {
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

func main() {
	err := godotenv.Load()
	if err != nil {
		slog.Error("Error loading .env file", "error", err)
	}

	utils.InitLogger()

	username := utils.ExpectEnvVar("PROXY_USER")
	passwordHashHex := utils.ExpectEnvVar("PROXY_PASSWORD_SHA256")
	server, err := NewProxyServer(username, passwordHashHex)
	if err != nil {
		slog.Error("Failed to create proxy server", "error", err)
		panic(err)
	}

	server.Run()
}
