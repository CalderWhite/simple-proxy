# Builder
FROM golang:1.25.3-bookworm AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -ldflags="-w -s" -o simple-proxy ./cmd/worker.go

# Worker
FROM scratch
WORKDIR /app
COPY --from=builder /app/simple-proxy .
EXPOSE 8080
CMD ["./simple-proxy"]
