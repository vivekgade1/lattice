package app

import (
	"strconv"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// AppError is a refusal with a stable application code; its gRPC code is looked up.
type AppError struct {
	Code   string
	Field  string
	Status codes.Code
}

func (e *AppError) Error() string { return e.Code }

// GRPCStatus answers the mapped code, with details when they attach.
func (e *AppError) GRPCStatus() *status.Status {
	st := status.New(e.Status, e.Code)
	if withDetails, err := st.WithDetails(); err == nil {
		return withDetails
	}
	return st
}

// codeStatus maps each stable application code to its gRPC code.
var codeStatus = map[string]codes.Code{
	"not_found":         codes.NotFound,
	"taken":             codes.AlreadyExists,
	"validation_failed": codes.InvalidArgument,
	"busy":              codes.ResourceExhausted,
}

// fail looks the code up; a code the table does not list is Unknown.
func fail(code string) *AppError {
	st, ok := codeStatus[code]
	if !ok {
		st = codes.Unknown
	}
	return &AppError{Code: code, Status: st}
}

// failField is fail with the offending field.
func failField(code, field string) *AppError {
	e := fail(code)
	e.Field = field
	return e
}

// invalid is a validation failure on one field.
func invalid(field string) *AppError { return failField("validation_failed", field) }

// pick parses a numeric code: the code is chosen at run time (the fallback is not the only value).
func pick(name string) *AppError {
	n, err := strconv.Atoi(name)
	c := codes.Code(n)
	if err != nil {
		c = codes.Unknown
	}
	return &AppError{Code: name, Status: c}
}

// pick2 is pick with the code from a multi-value call.
func pick2(name string) *AppError {
	c, err := parse(name)
	if err != nil {
		c = codes.Unknown
	}
	return &AppError{Code: name, Status: c}
}

func parse(name string) (codes.Code, error) {
	n, err := strconv.Atoi(name)
	return codes.Code(n), err
}
