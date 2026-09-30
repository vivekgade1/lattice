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
	mux.HandleFunc("PATCH /products/{product_id}/stock", func(w http.ResponseWriter, r *http.Request) {
		if _, ok := k.Authenticate(w, r, "Admin"); !ok {
			return
		}
		var problems []string
		productID, ok := boundedInt(r.PathValue("product_id"), 1, math.MaxInt64)
		if !ok {
			problems = append(problems, "product_id: an integer >= 1")
		}
		var in struct {
			Stock *int64 `json:"stock"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			problems = append(problems, "body: a JSON object")
		} else if in.Stock == nil || *in.Stock < 0 || *in.Stock > 10000 {
			problems = append(problems, "stock: an integer 0-10000")
		}
		if len(problems) > 0 {
			invalid(w, problems...)
			return
		}
		res, err := k.DB.Exec("UPDATE products SET stock = ? WHERE id = ?", *in.Stock, productID)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		if n, err := res.RowsAffected(); err != nil || n == 0 {
			fail(w, http.StatusNotFound, "not_found", "product")
			return
		}
		rows, err := k.DB.Query("SELECT id, name, stock FROM products WHERE id = ?", productID)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		product, err := scanRows(rows)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, product[0])
	})
}
