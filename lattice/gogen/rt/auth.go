package rt

import (
	"math"
	"strconv"
	"strings"
)

// Caller is the authenticated caller: the users row's key (ID), its role, whether the
// role is the admin role, and its row (Row in Endpoint.UserColumns order; Cols by name).
type Caller struct {
	ID      any
	Role    string
	IsAdmin bool
	Row     []any
	Cols    map[string]any
}

// IsAdminInt is the caller's is_admin as the SQL binds it (:__is_admin).
func (c *Caller) IsAdminInt() int64 {
	if c != nil && c.IsAdmin {
		return 1
	}
	return 0
}

// Token is the prelude's _token: the bearer token of an Authorization header (the
// scheme in any case, surrounding whitespace ignored), or ok false.
func Token(header string, present bool) (string, bool) {
	if !present {
		return "", false
	}
	scheme, token, _ := strings.Cut(Strip(header, nil), " ")
	token = Strip(token, nil)
	if Lower(scheme) != "bearer" || token == "" {
		return "", false
	}
	return token, true
}

// pyTruth is Python's truth value of a stored value.
func pyTruth(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case int64:
		return x != 0
	case float64:
		return x != 0
	case string:
		return x != ""
	case []byte:
		return len(x) > 0
	case bool:
		return x
	}
	return true
}

// pyStr is str() of a stored value.
func pyStr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case int64:
		return strconv.FormatInt(x, 10)
	case float64:
		if math.IsInf(x, 0) || math.IsNaN(x) {
			return pyFloatName(x)
		}
		return ReprFloat(x)
	case string:
		return x
	case []byte:
		return "b" + ReprStr(string(x))
	}
	return ""
}

func indexOfCol(cols []string, name string) int {
	for i, c := range cols {
		if c == name {
			return i
		}
	}
	return -1
}

// callerRole is the caller's role: the endpoint's fixed role (NoRoles), else from the
// admin flag column, else the role column.
func callerRole(ep *Endpoint, row []any) string {
	if ep.FixedRole != nil {
		return *ep.FixedRole
	}
	if ep.AdminFlag != nil {
		if pyTruth(row[indexOfCol(ep.UserColumns, *ep.AdminFlag)]) {
			return ep.AdminRole
		}
		return ep.FlagRole
	}
	col := ep.RoleColumn
	if col == "" {
		col = "role"
	}
	return pyStr(row[indexOfCol(ep.UserColumns, col)])
}

// authenticate is the prelude's authenticate: the caller by bearer token (one
// statement): 401 if unknown, 500 if ambiguous (several rows), the inactive status if
// the active column is not true, 403 if the role may not call the endpoint (the admin
// role may call every endpoint). It panics with the error answer.
func (a *App) authenticate(ep *Endpoint, db *dbConn, token string) *Caller {
	rows := db.query(ep.AuthSQL, token).Rows
	s := &a.Settings
	if len(rows) == 0 {
		panic(NewErrorResp(401, "unauthorized", strOrNil(s.UnauthorizedDetail)))
	}
	if len(rows) > 1 { // never pick one of them
		msg := ep.Ambiguous
		if msg == "" {
			msg = "error R311: ambiguous caller"
		}
		panic(&Failure{Msg: msg})
	}
	row := rows[0]
	if s.ActiveColumn != nil && !pyTruth(row[indexOfCol(ep.UserColumns, *s.ActiveColumn)]) {
		status := s.InactiveStatus
		if status == 0 {
			status = 400
		}
		detail := s.InactiveDetail
		if detail == "" {
			detail = "Inactive user"
		}
		panic(NewErrorResp(status, statusCodes[status], detail))
	}
	role := callerRole(ep, row)
	if !(ep.Auth == "Public" || role == ep.Auth || role == ep.AdminRole) {
		panic(NewErrorResp(403, "forbidden", strOrNil(s.ForbiddenDetail)))
	}
	cols := make(map[string]any, len(row))
	for i, c := range ep.UserColumns {
		if i < len(row) {
			cols[c] = row[i]
		}
	}
	return &Caller{
		ID:      row[indexOfCol(ep.UserColumns, ep.UserKey)],
		Role:    role,
		IsAdmin: role == ep.AdminRole,
		Row:     row,
		Cols:    cols,
	}
}

var statusCodes = map[int]string{400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found"}

func strOrNil(s *string) any {
	if s == nil {
		return nil
	}
	return *s
}
