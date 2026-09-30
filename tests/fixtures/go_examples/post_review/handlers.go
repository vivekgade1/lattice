package main

import (
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"unicode/utf8"

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
	mux.HandleFunc("POST /products/{product_id}/reviews", func(w http.ResponseWriter, r *http.Request) {
		user, ok := k.Authenticate(w, r, "Customer")
		if !ok {
			return
		}
		var problems []string
		productID, ok := boundedInt(r.PathValue("product_id"), 1, 1000000)
		if !ok {
			problems = append(problems, "product_id: an integer 1-1000000")
		}
		var in struct {
			Rating *int64  `json:"rating"`
			Body   *string `json:"body"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			problems = append(problems, "body: a JSON object")
		} else {
			if in.Rating == nil || *in.Rating < 1 || *in.Rating > 5 {
				problems = append(problems, "rating: an integer 1-5")
			}
			if in.Body == nil || utf8.RuneCountInString(*in.Body) < 1 || utf8.RuneCountInString(*in.Body) > 500 {
				problems = append(problems, "body: a string of 1-500 characters")
			}
		}
		if len(problems) > 0 {
			invalid(w, problems...)
			return
		}
		var found int
		err := k.DB.QueryRow("SELECT 1 FROM products WHERE id = ?", productID).Scan(&found)
		if errors.Is(err, sql.ErrNoRows) {
			fail(w, http.StatusNotFound, "not_found", "product")
			return
		}
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		var bought int
		err = k.DB.QueryRow(
			"SELECT COUNT(*) FROM order_items i JOIN orders o ON o.id = i.order_id"+
				" WHERE i.product_id = ? AND (o.customer_id = ? OR ? = 'Admin')",
			productID, user.ID, user.Role,
		).Scan(&bought)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		if bought == 0 {
			fail(w, http.StatusForbidden, "forbidden", "product not ordered")
			return
		}
		var existing int
		err = k.DB.QueryRow(
			"SELECT COUNT(*) FROM reviews"+
				" WHERE product_id = ? AND (customer_id = ? OR ? = 'Admin')",
			productID, user.ID, user.Role,
		).Scan(&existing)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		if existing > 0 {
			fail(w, http.StatusConflict, "conflict", "already reviewed")
			return
		}
		res, err := k.DB.Exec(
			"INSERT INTO reviews (product_id, customer_id, rating, body) VALUES (?, ?, ?, ?)",
			productID, user.ID, *in.Rating, *in.Body,
		)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		id, err := res.LastInsertId()
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		rows, err := k.DB.Query("SELECT * FROM reviews WHERE id = ?", id)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		review, err := scanRows(rows)
		if err != nil {
			platformkit.WriteError(w, err)
			return
		}
		writeJSON(w, http.StatusCreated, review[0])
	})
}
