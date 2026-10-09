// A toy Go service for lattice.spec.check's process runtime (tests/spec/test_process_runtime.py).
//
// Plain net/http and a pure-Go SQLite driver (modernc.org/sqlite, no cgo). Its schema is
// written by the test's db factory; the service only reads and writes rows.
//
//	GET  /health                  200 once the database opens
//	POST /login                   {"username", "password"} -> {"access_token"}
//	POST /configs/{cid}/runs      queue a run; at most one live (queued) run per config
//	POST /runs/{rid}/cancel       cancel a queued run
//	GET  /runs/{rid}              a run
//
// Environment: PORT, DB_PATH; MODE=safe (a mutex around check-then-insert) or racy (a
// sleep between the check and the insert); BUG=nocheck (create skips the check) or
// nocancel (cancel answers 200 but writes nothing); STARTUP_FAIL=1 exits at once.
package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

type server struct {
	db   *sql.DB
	mode string
	bug  string
	mu   sync.Mutex
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func detail(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"detail": msg})
}

func (s *server) caller(r *http.Request) (int64, bool) {
	scheme, token, _ := strings.Cut(r.Header.Get("Authorization"), " ")
	if !strings.EqualFold(scheme, "bearer") {
		return 0, false
	}
	var id int64
	if err := s.db.QueryRow("SELECT id FROM users WHERE token = ?", token).Scan(&id); err != nil {
		return 0, false
	}
	return id, true
}

type run struct {
	ID       int64  `json:"id"`
	ConfigID int64  `json:"config_id"`
	Status   string `json:"status"`
}

func (s *server) readRun(id int64) (run, error) {
	var x run
	err := s.db.QueryRow("SELECT id, config_id, status FROM runs WHERE id = ?", id).
		Scan(&x.ID, &x.ConfigID, &x.Status)
	return x, err
}

func (s *server) login(w http.ResponseWriter, r *http.Request) {
	var body struct{ Username, Password string }
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		detail(w, 422, "bad body")
		return
	}
	var token string
	err := s.db.QueryRow("SELECT token FROM users WHERE name = ? AND password = ?",
		body.Username, body.Password).Scan(&token)
	if err != nil {
		detail(w, 401, "Incorrect username or password")
		return
	}
	writeJSON(w, 200, map[string]string{"access_token": token, "token_type": "bearer"})
}

func (s *server) createRun(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.caller(r); !ok {
		detail(w, 401, "Not authenticated")
		return
	}
	cid, err := strconv.ParseInt(r.PathValue("cid"), 10, 64)
	if err != nil {
		detail(w, 422, "bad id")
		return
	}
	var one int
	if s.db.QueryRow("SELECT 1 FROM configs WHERE id = ?", cid).Scan(&one) != nil {
		detail(w, 404, "Not found")
		return
	}
	if s.mode == "safe" {
		s.mu.Lock()
		defer s.mu.Unlock()
	}
	if s.bug != "nocheck" {
		var live int
		err := s.db.QueryRow(
			"SELECT COUNT(*) FROM runs WHERE config_id = ? AND status = 'queued'", cid,
		).Scan(&live)
		if err != nil {
			detail(w, 500, err.Error())
			return
		}
		if live > 0 {
			detail(w, 409, "already live")
			return
		}
	}
	if s.mode == "racy" {
		time.Sleep(20 * time.Millisecond) // widen the check-then-insert window
	}
	res, err := s.db.Exec("INSERT INTO runs (config_id, status) VALUES (?, 'queued')", cid)
	if err != nil {
		detail(w, 500, err.Error())
		return
	}
	id, _ := res.LastInsertId()
	x, err := s.readRun(id)
	if err != nil {
		detail(w, 500, err.Error())
		return
	}
	writeJSON(w, 201, x)
}

func (s *server) cancelRun(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.caller(r); !ok {
		detail(w, 401, "Not authenticated")
		return
	}
	rid, err := strconv.ParseInt(r.PathValue("rid"), 10, 64)
	if err != nil {
		detail(w, 422, "bad id")
		return
	}
	x, err := s.readRun(rid)
	if err != nil {
		detail(w, 404, "Not found")
		return
	}
	if x.Status != "queued" {
		detail(w, 409, "already final")
		return
	}
	if s.bug != "nocancel" {
		_, err := s.db.Exec(
			"UPDATE runs SET status = 'cancelled' WHERE id = ? AND status = 'queued'", rid,
		)
		if err != nil {
			detail(w, 500, err.Error())
			return
		}
	}
	x, _ = s.readRun(rid)
	if s.bug == "nocancel" {
		x.Status = "cancelled" // the answer lies; the row is still queued
	}
	writeJSON(w, 200, x)
}

func (s *server) getRun(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.caller(r); !ok {
		detail(w, 401, "Not authenticated")
		return
	}
	rid, err := strconv.ParseInt(r.PathValue("rid"), 10, 64)
	if err != nil {
		detail(w, 422, "bad id")
		return
	}
	x, err := s.readRun(rid)
	if err != nil {
		detail(w, 404, "Not found")
		return
	}
	writeJSON(w, 200, x)
}

func main() {
	if os.Getenv("STARTUP_FAIL") != "" {
		fmt.Fprintln(os.Stderr, "goapp: refusing to start: STARTUP_FAIL is set")
		os.Exit(3)
	}
	port, path := os.Getenv("PORT"), os.Getenv("DB_PATH")
	if port == "" || path == "" {
		fmt.Fprintln(os.Stderr, "goapp: PORT and DB_PATH are required")
		os.Exit(2)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=busy_timeout(10000)")
	if err == nil {
		err = db.Ping()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "goapp: cannot open the database:", err)
		os.Exit(1)
	}
	db.SetMaxOpenConns(1)
	mode := os.Getenv("MODE")
	if mode == "" {
		mode = "safe"
	}
	s := &server{db: db, mode: mode, bug: os.Getenv("BUG")}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
	})
	mux.HandleFunc("POST /login", s.login)
	mux.HandleFunc("POST /configs/{cid}/runs", s.createRun)
	mux.HandleFunc("POST /runs/{rid}/cancel", s.cancelRun)
	mux.HandleFunc("GET /runs/{rid}", s.getRun)
	if err := http.ListenAndServe("127.0.0.1:"+port, mux); err != nil {
		fmt.Fprintln(os.Stderr, "goapp:", err)
		os.Exit(1)
	}
}
