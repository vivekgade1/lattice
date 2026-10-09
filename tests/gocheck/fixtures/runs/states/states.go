// Package states: the run states, in their own package.
package states

type Status string

const (
	Queued    Status = "queued"
	Running   Status = "running"
	Succeeded        = Status("succeeded")
	Cancelled Status = "cancelled"
)

// Live are the states a run can be cancelled from (wrongly including succeeded).
var Live = []Status{Queued, Running, Succeeded}

const ByIDAndState = "id = ? AND status IN ?"
