// Package config reads the service settings from the environment.
package config

import (
	"os"
	"time"
)

// Config holds every setting the service reads. Each has a default, so PORT and DB_PATH are enough to start.
type Config struct {
	Port        string
	DBPath      string
	TokenSecret string
	TokenTTL    time.Duration
}

// Load reads the environment: PORT, DB_PATH, TOKEN_SECRET and TOKEN_TTL (a Go duration such as "12h").
func Load() Config {
	ttl, err := time.ParseDuration(getenv("TOKEN_TTL", "24h"))
	if err != nil || ttl <= 0 {
		ttl = 24 * time.Hour
	}
	return Config{
		Port:        getenv("PORT", "8080"),
		DBPath:      getenv("DB_PATH", "shelfie.db"),
		TokenSecret: getenv("TOKEN_SECRET", "shelfie-dev-secret-change-me"),
		TokenTTL:    ttl,
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
