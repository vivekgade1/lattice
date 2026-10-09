package main

import (
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func setup(db *gorm.DB) *gin.Engine {
	r := gin.Default()
	h := &Handler{db: db}
	api := r.Group("/api")
	api.POST("/bad/create-then-validate", h.BadCreateThenValidate)
	api.POST("/bad/write-then-not-found", h.BadWriteThenNotFound)
	api.POST("/bad/helper-commits", h.BadHelperCommits)
	api.POST("/bad/helper-error", h.BadHelperError)
	api.POST("/bad/commit-then-refuse", h.BadCommitThenRefuse)
	api.POST("/bad/loop", h.BadLoop)
	api.POST("/bad/respond-helper", h.BadRespondHelper)
	api.POST("/bad/transaction-then-refuse", h.BadTransactionThenRefuse)
	api.POST("/bad/db-inside-transaction", h.BadDBInsideTransaction)
	api.POST("/bad/exec", h.BadExec)
	api.POST("/bad/delete-then-abort", h.BadDeleteThenAbort)
	api.POST("/ok/check-first", h.OkCheckFirst)
	api.POST("/ok/transaction", h.OkTransaction)
	api.POST("/ok/conditional-update", h.OkConditionalUpdate)
	api.POST("/ok/write-error", h.OkWriteError)
	api.POST("/ok/begin-rollback", h.OkBeginRollback)
	api.POST("/ok/branch", h.OkBranch)
	api.POST("/ok/db-error-helper", h.OkDBErrorHelper)
	api.POST("/ok/validate-helper", h.OkValidateHelper)
	api.POST("/ok/create-helper", h.OkCreateHelper)
	api.POST("/ok/rows-affected-chain", h.OkRowsAffectedChain)
	api.POST("/ok/validate-all-first", h.OkValidateAllFirst)
	api.POST("/ok/success-only", h.OkSuccessOnly)
	api.POST("/ok/save-error-wrapped", h.OkSaveErrorWrapped)
	api.POST("/ok/request-tx", h.OkRequestTx)
	api.POST("/ok/repo-with-tx", h.OkRepoWithTx)
	api.POST("/bad/helper-outside-tx", h.BadHelperOutsideTx)
	api.POST("/ok/reload-failure", h.OkReloadFailure)
	api.POST("/ok/closure-over-tx", h.OkClosureOverTx)
	return r
}

func main() {}
