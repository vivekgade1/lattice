package main

import (
	"net/http"
	"regexp"
	"slices"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

const (
	RoleAppAdmin = "app_admin"
	RoleExecutor = "executor"
)

const slugPattern = `^[a-z0-9-]{3,40}$`

var (
	jobNameRE  = regexp.MustCompile(`^[A-Za-z0-9_-]{1,100}$`)
	slugRE     = regexp.MustCompile(slugPattern)
	emailRE    = regexp.MustCompile(`^[^\s@]+@[^\s@]+$`)
	looseRE    = regexp.MustCompile(`[A-Za-z0-9]{1,100}`)
	openEndRE  = regexp.MustCompile(`^[a-z]{1,}$`)
	multiRE    = regexp.MustCompile(`(?m)^[a-z]{1,10}$`)
	validRoles = []string{RoleAppAdmin, RoleExecutor}
)

type Membership struct {
	ID            uint   `json:"id"`
	UserID        uint   `json:"user_id"`
	ApplicationID uint   `json:"application_id"`
	Role          string `json:"role"`
}

type Job struct {
	ID   uint   `json:"id"`
	Name string `json:"name"`
}

type AddMember struct {
	UserID uint   `json:"user_id" binding:"max=1000000"`
	Role   string `json:"role"`
	Other  string `json:"other"`
}

type CreateJob struct {
	JobName string `json:"job_name"`
}

type H struct{ db *gorm.DB }

func respondError(c *gin.Context, status int, msg string) {
	c.JSON(status, gin.H{"detail": msg})
}

func validRole(r string) bool { return r == RoleAppAdmin || r == RoleExecutor }

func isValidRole(r string) bool {
	switch r {
	case RoleAppAdmin, RoleExecutor:
		return true
	}
	return false
}

func isValidJobName(s string) bool { return jobNameRE.MatchString(s) }

func notEmpty(r string) bool { return r != "" }

func setup(r *gin.Engine, h *H) {
	r.POST("/ok/and", h.OkEnumAnd)
	r.POST("/ok/not-or", h.OkEnumNotOr)
	r.POST("/ok/switch", h.OkEnumSwitch)
	r.POST("/ok/contains", h.OkEnumContains)
	r.POST("/ok/contains-var", h.OkEnumContainsVar)
	r.POST("/ok/helper", h.OkEnumHelper)
	r.POST("/ok/helper-switch", h.OkEnumHelperSwitch)
	r.POST("/ok/branch", h.OkEnumBranch)
	r.POST("/ok/regex", h.OkRegexVar)
	r.POST("/ok/regex-const", h.OkRegexConst)
	r.POST("/ok/regex-inline", h.OkRegexInline)
	r.POST("/ok/regex-helper", h.OkRegexHelper)
	r.POST("/bad/falls-through", h.BadEnumFallsThrough)
	r.POST("/bad/no-default", h.BadEnumNoDefault)
	r.POST("/bad/not-const", h.BadEnumNotConst)
	r.POST("/bad/after-write", h.BadEnumAfterWrite)
	r.POST("/bad/one-side", h.BadEnumOneSide)
	r.POST("/bad/helper", h.BadEnumHelper)
	r.POST("/bad/unanchored", h.BadRegexUnanchored)
	r.POST("/bad/open-end", h.BadRegexOpenEnd)
	r.POST("/bad/email", h.BadRegexEmail)
	r.POST("/bad/inline-star", h.BadRegexInlineStar)
	r.POST("/bad/multiline", h.BadRegexMultiline)
}

func (h *H) OkEnumAnd(c *gin.Context) {
	var req AddMember
	if err := c.ShouldBindJSON(&req); err != nil {
		respondError(c, http.StatusBadRequest, err.Error())
		return
	}
	if req.Role != RoleAppAdmin && req.Role != RoleExecutor {
		respondError(c, http.StatusUnprocessableEntity, "role must be app_admin or executor")
		return
	}
	membership := Membership{UserID: req.UserID, ApplicationID: 1, Role: req.Role}
	h.db.Create(&membership)
	c.JSON(http.StatusCreated, membership)
}

func (h *H) OkEnumNotOr(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !(req.Role == RoleAppAdmin || req.Role == "executor") {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) OkEnumSwitch(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	switch req.Role {
	case RoleAppAdmin, RoleExecutor:
	default:
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) OkEnumContains(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !slices.Contains([]string{RoleAppAdmin, RoleExecutor}, req.Role) {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) OkEnumContainsVar(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !slices.Contains(validRoles, req.Role) {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) OkEnumHelper(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !validRole(req.Role) {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) OkEnumHelperSwitch(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !isValidRole(req.Role) {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	var m Membership
	h.db.First(&m, req.UserID)
	m.Role = req.Role
	h.db.Save(&m)
	c.Status(http.StatusOK)
}

func (h *H) OkEnumBranch(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if req.Role == RoleAppAdmin || req.Role == RoleExecutor {
		h.db.Model(&Membership{}).Where("user_id = ?", req.UserID).Update("role", req.Role)
		c.Status(http.StatusOK)
		return
	}
	respondError(c, http.StatusUnprocessableEntity, "bad role")
}

func (h *H) OkRegexVar(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !jobNameRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad job name")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func (h *H) OkRegexConst(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !slugRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad slug")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func (h *H) OkRegexInline(c *gin.Context) {
	name := c.Query("name")
	if !regexp.MustCompile(`\A[a-z]{3,20}\z`).MatchString(name) {
		respondError(c, http.StatusUnprocessableEntity, "bad name")
		return
	}
	h.db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("name", name)
	c.Status(http.StatusNoContent)
}

func (h *H) OkRegexHelper(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !isValidJobName(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad job name")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

// the refusal does not return: the write follows it
func (h *H) BadEnumFallsThrough(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if req.Role != RoleAppAdmin && req.Role != RoleExecutor {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

// no default: any other role goes on to the write
func (h *H) BadEnumNoDefault(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	switch req.Role {
	case RoleAppAdmin, RoleExecutor:
	case "":
		respondError(c, http.StatusUnprocessableEntity, "role is required")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

// compared with another request value, not a constant
func (h *H) BadEnumNotConst(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if req.Role != RoleAppAdmin && req.Role != req.Other {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

// checked after the write
func (h *H) BadEnumAfterWrite(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	if req.Role != RoleAppAdmin && req.Role != RoleExecutor {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	c.Status(http.StatusCreated)
}

// `||` of two refusals: only the empty role is refused
func (h *H) BadEnumOneSide(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if req.Role == "" || req.Role == RoleAppAdmin && req.UserID == 0 {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

// the helper checks only a minimum
func (h *H) BadEnumHelper(c *gin.Context) {
	var req AddMember
	_ = c.ShouldBindJSON(&req)
	if !notEmpty(req.Role) {
		respondError(c, http.StatusUnprocessableEntity, "bad role")
		return
	}
	h.db.Create(&Membership{UserID: req.UserID, Role: req.Role})
	c.Status(http.StatusCreated)
}

func (h *H) BadRegexUnanchored(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !looseRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad job name")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func (h *H) BadRegexOpenEnd(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !openEndRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad job name")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func (h *H) BadRegexEmail(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !emailRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad email")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func (h *H) BadRegexInlineStar(c *gin.Context) {
	name := c.Query("name")
	if !regexp.MustCompile(`^[a-z]*$`).MatchString(name) {
		respondError(c, http.StatusUnprocessableEntity, "bad name")
		return
	}
	h.db.Model(&Job{}).Where("id = ?", c.Param("id")).Update("name", name)
	c.Status(http.StatusNoContent)
}

// (?m): `^` and `$` match at every line, so "ok\n<anything>" matches
func (h *H) BadRegexMultiline(c *gin.Context) {
	var req CreateJob
	_ = c.ShouldBindJSON(&req)
	if !multiRE.MatchString(req.JobName) {
		respondError(c, http.StatusUnprocessableEntity, "bad job name")
		return
	}
	h.db.Create(&Job{Name: req.JobName})
	c.Status(http.StatusCreated)
}

func main() {}
