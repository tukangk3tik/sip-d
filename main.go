package main

import (
	"context"
	_ "embed"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

//go:embed migrations.sql
var migrations string

func main() {
	addr := env("SIPD_ADDR", "127.0.0.1:8090")
	dbPath := env("SIPD_DB", "data/sip-d.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0700); err != nil {
		fatal(err)
	}
	app, err := NewApp(dbPath, os.Getenv("SIPD_BASE_URL"), os.Getenv("SIPD_METALS_API_KEY"), os.Getenv("SIPD_FINNHUB_API_KEY"), os.Getenv("SIPD_RAPIDAPI_KEY"))
	if err != nil {
		fatal(err)
	}
	defer app.Close()

	srv := &http.Server{Addr: addr, Handler: app.Routes(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		slog.Info("SIP-D listening", "address", addr)
		if err := srv.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
			fatal(err)
		}
	}()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		slog.Error("shutdown", "error", err)
	}
}

func env(k, fallback string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return fallback
}
func fatal(err error) { slog.Error("fatal", "error", err); os.Exit(1) }
