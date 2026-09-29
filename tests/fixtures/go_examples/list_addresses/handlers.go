package main

import (
	"database/sql"
	"encoding/json"
	"math"
	"net/http"
	"strconv"

	"platformkit"
)

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, status int, name, detail string) {
	writeJSON(w, status, map[string]any{"error": name, "detail": detail})
}

func invalid(w http.ResponseWriter, detail ...string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusUnprocessableEntity)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": "validation", "detail": detail})
}

func boundedInt(s string, lo, hi int64) (int64, bool) {
	n, err := strconv.ParseInt(s, 10, 64)
	return n, err == nil && lo <= n && n <= hi
}

func scanRows(rows *sql.Rows) ([]map[string]any, error) {
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		row := map[string]any{}
		for i, col := range cols {
			row[col] = vals[i]
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

func Register(mux *http.ServeMux, k *platformkit.Kit) {
	mux.HandleFunc("GET /addresses", func(w http.ResponseWriter, r *http.Request) {
		user, ok := k.Authenticate(w, r, "Customer")
		if !ok {
			return
		}
		query := r.URL.Query()
		limit, offset := int64(20), int64(0)
		var problems []string
		if query.Has("limit") {
			if limit, ok = boundedInt(query.Get("limit"), 1, 50); !ok {
				problems = append(problems, "limit: an integer 1-50")
			}
		}
		if query.Has("offset") {
			if offset, ok = boundedInt(query.Get("offset"), 0, math.MaxInt64); !ok {
				problems = append(problems, "offset: an integer >= 0")
			}
		}
		if len(problems) > 0 {
			invalid(w, problems...)
			return
		}
		rows, err := k.DB.Query(
			"SELECT id, customer_id, line1, city, zip FROM addresses"+
				" WHERE customer_id = ? OR ? = 'Admin' ORDER BY id LIMIT ? OFFSET ?",
			user.ID, user.Role, limit, offset,
		)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		items, err := scanRows(rows)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"items": items, "limit": limit, "offset": offset})
	})
}
