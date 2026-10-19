package app

import (
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// rich builds a status with an ErrorInfo detail, the way a team that wants machine-readable reasons does.
func rich(c codes.Code, reason string) error {
	st := status.New(c, reason)
	if withInfo, err := st.WithDetails(&errdetails.ErrorInfo{Reason: reason}); err == nil {
		st = withInfo
	}
	return st.Err()
}

func detailed(reason string) error {
	st := status.New(codes.NotFound, reason)
	if withInfo, err := st.WithDetails(&errdetails.ErrorInfo{Reason: reason}); err == nil {
		st = withInfo
	}
	return st.Err()
}

func split(reason string) error {
	s, _ := status.New(codes.Aborted, reason).WithDetails(&errdetails.ErrorInfo{Reason: reason})
	return s.Err()
}
