package svc

import (
	"context"
	"crypto/rand"
	"encoding/hex"

	sessionsv1 "example.com/sessionsapp/gen/sessions/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

// Sessions serves sessions.v1.Sessions. Stub is left to the embedded Unimplemented server.
type Sessions struct {
	sessionsv1.UnimplementedSessionsServer
	DB *gorm.DB
}

func internalErr(err error) error {
	return status.Error(codes.Internal, "internal error: "+err.Error())
}

func (s *Sessions) Login(ctx context.Context, req *sessionsv1.LoginRequest) (*sessionsv1.LoginResponse, error) {
	if req.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}
	var u User
	if err := s.DB.Where("name = ? AND password = ?", req.Name, req.Password).First(&u).Error; err != nil {
		return nil, status.Error(codes.Unauthenticated, "bad credentials")
	}
	raw := make([]byte, 8)
	if _, err := rand.Read(raw); err != nil {
		return nil, internalErr(err)
	}
	sess := Session{Token: "s-" + hex.EncodeToString(raw), UserID: u.ID}
	if err := s.DB.Create(&sess).Error; err != nil {
		return nil, internalErr(err)
	}
	return &sessionsv1.LoginResponse{Token: sess.Token}, nil
}

// SignOut revokes the caller's session: its token answers UNAUTHENTICATED afterwards.
func (s *Sessions) SignOut(ctx context.Context, req *sessionsv1.SignOutRequest) (*sessionsv1.SignOutResponse, error) {
	sess, err := session(ctx)
	if err != nil {
		return nil, err
	}
	if err := s.DB.Delete(&Session{}, "token = ?", sess.Token).Error; err != nil {
		return nil, internalErr(err)
	}
	return &sessionsv1.SignOutResponse{}, nil
}

// DeleteMe deletes the caller's account and every session of it.
func (s *Sessions) DeleteMe(ctx context.Context, req *sessionsv1.DeleteMeRequest) (*sessionsv1.DeleteMeResponse, error) {
	sess, err := session(ctx)
	if err != nil {
		return nil, err
	}
	if err := s.DB.Delete(&Session{}, "user_id = ?", sess.UserID).Error; err != nil {
		return nil, internalErr(err)
	}
	if err := s.DB.Delete(&User{}, sess.UserID).Error; err != nil {
		return nil, internalErr(err)
	}
	return &sessionsv1.DeleteMeResponse{}, nil
}

func (s *Sessions) Secret(ctx context.Context, req *sessionsv1.SecretRequest) (*sessionsv1.SecretResponse, error) {
	if _, err := session(ctx); err != nil {
		return nil, err
	}
	return &sessionsv1.SecretResponse{Message: "the cake is a lie"}, nil
}

func (s *Sessions) Health(ctx context.Context, req *sessionsv1.HealthRequest) (*sessionsv1.HealthResponse, error) {
	return &sessionsv1.HealthResponse{Ok: true}, nil
}
