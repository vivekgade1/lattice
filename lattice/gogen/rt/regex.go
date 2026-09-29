package rt

import (
	"strconv"
	"strings"
)

// Regular expressions of compiled T2 procedures (plan 5a Task 9).
//
// A pattern is compiled by the Go emitter exactly as the Python target compiles it at
// run time -- re.compile(pattern, re.ASCII) -- into CPython's SRE code
// (lattice/gogen/regex.py), and that code is run here by a port of CPython's matching
// engine (Modules/_sre/sre_lib.h: SRE(match), SRE(search), SRE(count), SRE(charset),
// SRE(at)) and of the loops of Modules/_sre/sre.c (findall, split, sub). The results
// -- which match, every group's span, the empty-match rules of findall/split/sub --
// are therefore Python's. Offsets are code points: the engine runs over the runes.

// SRE opcodes, AT codes and categories (Lib/re/_constants.py, MAGIC 20230612).
const (
	opFAILURE            uint32 = 0
	opSUCCESS            uint32 = 1
	opANY                uint32 = 2
	opANY_ALL            uint32 = 3
	opAT                 uint32 = 6
	opBRANCH             uint32 = 7
	opCATEGORY           uint32 = 8
	opCHARSET            uint32 = 9
	opBIGCHARSET         uint32 = 10
	opIN                 uint32 = 13
	opINFO               uint32 = 14
	opJUMP               uint32 = 15
	opLITERAL            uint32 = 16
	opMARK               uint32 = 17
	opMAX_UNTIL          uint32 = 18
	opMIN_UNTIL          uint32 = 19
	opNOT_LITERAL        uint32 = 20
	opNEGATE             uint32 = 21
	opRANGE              uint32 = 22
	opREPEAT             uint32 = 23
	opREPEAT_ONE         uint32 = 24
	opMIN_REPEAT_ONE     uint32 = 26
	opIN_IGNORE          uint32 = 31
	opLITERAL_IGNORE     uint32 = 32
	opNOT_LITERAL_IGNORE uint32 = 33

	atBEGINNING        uint32 = 0
	atBEGINNING_LINE   uint32 = 1
	atBEGINNING_STRING uint32 = 2
	atBOUNDARY         uint32 = 3
	atNON_BOUNDARY     uint32 = 4
	atEND              uint32 = 5
	atEND_LINE         uint32 = 6
	atEND_STRING       uint32 = 7

	catDIGIT         uint32 = 0
	catNOT_DIGIT     uint32 = 1
	catSPACE         uint32 = 2
	catNOT_SPACE     uint32 = 3
	catWORD          uint32 = 4
	catNOT_WORD      uint32 = 5
	catLINEBREAK     uint32 = 6
	catNOT_LINEBREAK uint32 = 7

	// reMaxRepeat is _sre.MAXREPEAT: an unbounded repeat's maximum.
	reMaxRepeat uint32 = 4294967295
)

// Regex is a compiled pattern: its SRE code, its number of capturing groups and their
// names, or the exception compiling it raises in Python (every use raises it).
type Regex struct {
	Pattern         string
	Code            []uint32
	Groups          int
	Names           map[string]int
	ErrKind, ErrMsg string
}

// NewRegex is a pattern compiled by lattice.gogen.regex.compile_regex.
func NewRegex(pattern string, code []uint32, groups int, names map[string]int) *Regex {
	return &Regex{Pattern: pattern, Code: code, Groups: groups, Names: names}
}

// BadRegex is a pattern whose compilation raises kind(msg) in Python.
func BadRegex(pattern, kind, msg string) *Regex {
	return &Regex{Pattern: pattern, ErrKind: kind, ErrMsg: msg}
}

func (re *Regex) check() {
	if re.ErrKind != "" {
		Raise(re.ErrKind, re.ErrMsg)
	}
}

// ---- the engine ----------------------------------------------------------------------

type reRepeat struct {
	count   int
	pc      int // the REPEAT's arguments: <skip> <min> <max> item
	lastPtr int // -1: none (NULL)
	prev    *reRepeat
}

// reState is SRE_STATE for one string.
type reState struct {
	code                []uint32
	s                   []rune
	end                 int
	start, ptr          int
	marks               []int
	lastmark, lastindex int
	repeat              *reRepeat
	matchAll            bool
	mustAdvance         bool
	ctxs                []reCtx // SRE(match)'s context stack
	data                []int   // its data stack (saved marks)
}

func newState(re *Regex, s []rune) *reState {
	st := &reState{code: re.Code, s: s, end: len(s), marks: make([]int, 2*re.Groups+2)}
	st.reset()
	return st
}

// reset is state_reset.
func (st *reState) reset() {
	st.lastmark, st.lastindex = -1, -1
	st.repeat = nil
}

func isWordASCII(r rune) bool {
	return r < 128 && (r == '_' || ('0' <= r && r <= '9') || ('a' <= r && r <= 'z') || ('A' <= r && r <= 'Z'))
}

func isSpaceASCII(r rune) bool {
	return r == ' ' || ('\t' <= r && r <= '\r')
}

func lowerASCII(r rune) rune {
	if 'A' <= r && r <= 'Z' {
		return r + ('a' - 'A')
	}
	return r
}

// category is sre_category for the ASCII categories.
func category(cat uint32, r rune) bool {
	switch cat {
	case catDIGIT:
		return '0' <= r && r <= '9'
	case catNOT_DIGIT:
		return !('0' <= r && r <= '9')
	case catSPACE:
		return isSpaceASCII(r)
	case catNOT_SPACE:
		return !isSpaceASCII(r)
	case catWORD:
		return isWordASCII(r)
	case catNOT_WORD:
		return !isWordASCII(r)
	case catLINEBREAK:
		return r == '\n'
	case catNOT_LINEBREAK:
		return r != '\n'
	}
	panic("rt: regular expression category " + strconv.Itoa(int(cat)))
}

// at is SRE(at).
func (st *reState) at(ptr int, code uint32) bool {
	s, end := st.s, st.end
	switch code {
	case atBEGINNING, atBEGINNING_STRING:
		return ptr == 0
	case atBEGINNING_LINE:
		return ptr == 0 || s[ptr-1] == '\n'
	case atEND:
		return (end-ptr == 1 && s[ptr] == '\n') || ptr == end
	case atEND_LINE:
		return ptr == end || s[ptr] == '\n'
	case atEND_STRING:
		return ptr == end
	case atBOUNDARY, atNON_BOUNDARY:
		that := ptr > 0 && isWordASCII(s[ptr-1])
		this := ptr < end && isWordASCII(s[ptr])
		if code == atBOUNDARY {
			return this != that
		}
		return this == that
	}
	panic("rt: regular expression AT code " + strconv.Itoa(int(code)))
}

// charset is SRE(charset): is ch in the set starting at code[pc]?
func (st *reState) charset(pc int, ch uint32) bool {
	code := st.code
	ok := true
	for {
		op := code[pc]
		pc++
		switch op {
		case opFAILURE:
			return !ok
		case opLITERAL:
			if ch == code[pc] {
				return ok
			}
			pc++
		case opCATEGORY:
			if category(code[pc], rune(ch)) {
				return ok
			}
			pc++
		case opCHARSET:
			if ch < 256 && code[pc+int(ch/32)]&(1<<(ch&31)) != 0 {
				return ok
			}
			pc += 256 / 32
		case opRANGE:
			if code[pc] <= ch && ch <= code[pc+1] {
				return ok
			}
			pc += 2
		case opNEGATE:
			ok = !ok
		case opBIGCHARSET:
			count := int(code[pc])
			pc++
			block := -1
			if ch < 0x10000 {
				// 256 block indices, one byte each, packed little-endian in 64 codes
				i := ch >> 8
				block = int((code[pc+int(i/4)] >> (8 * (i % 4))) & 0xFF)
			}
			pc += 256 / 4
			if block >= 0 {
				bit := uint32(block)*256 + (ch & 255)
				if code[pc+int(bit/32)]&(1<<(ch&31)) != 0 {
					return ok
				}
			}
			pc += count * (256 / 32)
		default:
			return false
		}
	}
}

// count is SRE(count): how many times the single-character item at code[pc] matches
// from st.ptr, at most maxcount times.
func (st *reState) count(pc int, maxcount uint32) int {
	code, s := st.code, st.s
	ptr := st.ptr
	end := st.end
	if int64(maxcount) < int64(end-ptr) && maxcount != reMaxRepeat {
		end = ptr + int(maxcount)
	}
	switch code[pc] {
	case opIN:
		for ptr < end && st.charset(pc+2, uint32(s[ptr])) {
			ptr++
		}
	case opANY:
		for ptr < end && s[ptr] != '\n' {
			ptr++
		}
	case opANY_ALL:
		ptr = end
	case opLITERAL:
		chr := code[pc+1]
		for ptr < end && uint32(s[ptr]) == chr {
			ptr++
		}
	case opLITERAL_IGNORE:
		chr := code[pc+1]
		for ptr < end && uint32(lowerASCII(s[ptr])) == chr {
			ptr++
		}
	case opNOT_LITERAL:
		chr := code[pc+1]
		for ptr < end && uint32(s[ptr]) != chr {
			ptr++
		}
	case opNOT_LITERAL_IGNORE:
		chr := code[pc+1]
		for ptr < end && uint32(lowerASCII(s[ptr])) != chr {
			ptr++
		}
	default:
		// a single-character item of another kind: match it repeatedly
		start := st.ptr
		for st.ptr < end {
			if !st.match(pc, st.ptr, false) {
				break
			}
		}
		return st.ptr - start
	}
	return ptr - st.ptr
}

// ---- SRE(match) with an explicit context stack ---------------------------------------------
//
// CPython's SRE(match) never recurses on the C stack: a nested match (DO_JUMP) pushes a
// context on a heap data stack and jumps back to `entrance`; when the nested match
// returns, the parent resumes at the label its `jump` code names. This is the same
// machine: reCtx is SRE(match_context), the jump codes are CPython's, and st.data is
// the data stack MARK_PUSH / LAST_PTR_PUSH save to. Depth is bounded by memory only,
// as in Python (`(?:ab)*` over millions of characters nests one context per repeat).

const (
	jNONE uint8 = iota
	jMAX_UNTIL_1
	jMAX_UNTIL_2
	jMAX_UNTIL_3
	jMIN_UNTIL_1
	jMIN_UNTIL_2
	jMIN_UNTIL_3
	jREPEAT
	jREPEAT_ONE_1
	jREPEAT_ONE_2
	jMIN_REPEAT_ONE
	jBRANCH
)

// reCtx is SRE(match_context): where a match is (pc, ptr), the state an operator keeps
// across its nested matches, and the label to resume the parent at (jump).
type reCtx struct {
	pc, ptr             int32 // for an operator waiting on a nested match: its arguments
	count               int32
	lastmark, lastindex int32
	lastPtr             int32 // LAST_PTR_PUSH
	marksAt             int32 // MARK_PUSH: where the saved marks are in st.data (-1: none)
	dataBase            int32 // st.data's length when the context was pushed
	chr                 uint32
	rep                 *reRepeat
	jump                uint8 // how the parent resumes when this context returns
	toplevel            bool
}

// markPush is MARK_PUSH(lastmark): marks[0..lastmark] saved on the data stack.
func (st *reState) markPush(c *reCtx) {
	if c.lastmark < 0 {
		c.marksAt = -1
		return
	}
	c.marksAt = int32(len(st.data))
	st.data = append(st.data, st.marks[:c.lastmark+1]...)
}

// markPop is MARK_POP / MARK_POP_KEEP: the saved marks restored (they stay saved; the
// context's data is dropped when it returns).
func (st *reState) markPop(c *reCtx) {
	if c.lastmark >= 0 && c.marksAt >= 0 {
		copy(st.marks, st.data[c.marksAt:int(c.marksAt)+int(c.lastmark)+1])
	}
}

// match is SRE(match): does the code at pc match at ptr? On success st.ptr is where
// the match ends. toplevel: the SUCCESS of the whole pattern (fullmatch's end check and
// the empty-match rule of the scanning loops apply to it).
func (st *reState) match(pc, ptr int, toplevel bool) bool {
	base := len(st.ctxs)
	st.ctxs = append(st.ctxs, reCtx{pc: int32(pc), ptr: int32(ptr), toplevel: toplevel,
		dataBase: int32(len(st.data))})
	ret, resume := false, false
	for {
		i := len(st.ctxs) - 1
		var push bool
		if resume {
			push, ret = st.resume(i, ret)
		} else {
			push, ret = st.run(i)
		}
		if push {
			resume = false
			continue
		}
		// the context returns ret: drop it and its data, resume its parent
		st.data = st.data[:st.ctxs[i].dataBase]
		st.ctxs = st.ctxs[:i]
		if i == base {
			return ret
		}
		resume = true
	}
}

// jumpTo is DO_JUMP: the context at i waits (resumed at jump) on a nested match of
// the code at pc from ptr.
func (st *reState) jumpTo(i int, jump uint8, pc, ptr int) (bool, bool) {
	c := &st.ctxs[i]
	c.jump = jump
	st.ctxs = append(st.ctxs, reCtx{pc: int32(pc), ptr: int32(ptr), toplevel: st.ctxs[i].toplevel,
		dataBase: int32(len(st.data))})
	return true, false
}

// run runs the context at i from its pc and ptr (SRE(match)'s `entrance` and dispatch
// loop) until it returns or jumps.
func (st *reState) run(i int) (bool, bool) {
	code, s, end := st.code, st.s, st.end
	c := &st.ctxs[i]
	pc, ptr, toplevel := int(c.pc), int(c.ptr), c.toplevel
	if code[pc] == opINFO {
		// <INFO> <skip> <flags> <min> ...: too few characters left cannot match
		if code[pc+3] != 0 && uint64(end-ptr) < uint64(code[pc+3]) {
			return false, false
		}
		pc += int(code[pc+1]) + 1
	}
	for {
		op := code[pc]
		pc++
		switch op {
		case opMARK:
			g := int(code[pc])
			if g&1 == 1 {
				st.lastindex = g/2 + 1
			}
			if g > st.lastmark {
				for j := st.lastmark + 1; j < g; j++ {
					st.marks[j] = -1
				}
				st.lastmark = g
			}
			st.marks[g] = ptr
			pc++

		case opLITERAL:
			if ptr >= end || uint32(s[ptr]) != code[pc] {
				return false, false
			}
			pc++
			ptr++

		case opNOT_LITERAL:
			if ptr >= end || uint32(s[ptr]) == code[pc] {
				return false, false
			}
			pc++
			ptr++

		case opSUCCESS:
			if toplevel && ((st.matchAll && ptr != end) || (st.mustAdvance && ptr == st.start)) {
				return false, false
			}
			st.ptr = ptr
			return false, true

		case opAT:
			if !st.at(ptr, code[pc]) {
				return false, false
			}
			pc++

		case opCATEGORY:
			if ptr >= end || !category(code[pc], s[ptr]) {
				return false, false
			}
			pc++
			ptr++

		case opANY:
			if ptr >= end || s[ptr] == '\n' {
				return false, false
			}
			ptr++

		case opANY_ALL:
			if ptr >= end {
				return false, false
			}
			ptr++

		case opIN:
			if ptr >= end || !st.charset(pc+1, uint32(s[ptr])) {
				return false, false
			}
			pc += int(code[pc])
			ptr++

		case opLITERAL_IGNORE:
			if ptr >= end || uint32(lowerASCII(s[ptr])) != code[pc] {
				return false, false
			}
			pc++
			ptr++

		case opNOT_LITERAL_IGNORE:
			if ptr >= end || uint32(lowerASCII(s[ptr])) == code[pc] {
				return false, false
			}
			pc++
			ptr++

		case opIN_IGNORE:
			if ptr >= end || !st.charset(pc+1, uint32(lowerASCII(s[ptr]))) {
				return false, false
			}
			pc += int(code[pc])
			ptr++

		case opJUMP, opINFO:
			pc += int(code[pc])

		case opFAILURE:
			return false, false

		case opBRANCH:
			// <BRANCH> <0=skip> code <JUMP> ... <NULL>
			c.pc, c.ptr = int32(pc), int32(ptr)
			c.lastmark, c.lastindex = int32(st.lastmark), int32(st.lastindex)
			c.marksAt = -1
			if st.repeat != nil {
				st.markPush(c)
			}
			return st.branchNext(i)

		case opREPEAT_ONE:
			// <REPEAT_ONE> <skip> <1=min> <2=max> item <SUCCESS> tail
			min := int64(code[pc+1])
			if min > int64(end-ptr) {
				return false, false
			}
			st.ptr = ptr
			count := st.count(pc+3, code[pc+2])
			c = &st.ctxs[i] // count may have run nested matches (the stack may have moved)
			ptr += count
			if int64(count) < min {
				return false, false
			}
			next := pc + int(code[pc])
			if code[next] == opSUCCESS && ptr == end && !(toplevel && st.mustAdvance && ptr == st.start) {
				st.ptr = ptr
				return false, true
			}
			c.pc, c.ptr, c.count = int32(pc), int32(ptr), int32(count)
			c.lastmark, c.lastindex = int32(st.lastmark), int32(st.lastindex)
			c.marksAt = -1
			if st.repeat != nil {
				st.markPush(c)
			}
			if code[next] == opLITERAL {
				// the tail starts with a literal: skip positions it cannot match at
				c.chr = code[next+1]
				return st.repeatOneLiteral(i)
			}
			return st.repeatOne(i)

		case opMIN_REPEAT_ONE:
			// <MIN_REPEAT_ONE> <skip> <1=min> <2=max> item <SUCCESS> tail
			min := code[pc+1]
			if int64(min) > int64(end-ptr) {
				return false, false
			}
			st.ptr = ptr
			count := 0
			if min != 0 {
				n := st.count(pc+3, min)
				c = &st.ctxs[i]
				if n < int(min) {
					return false, false
				}
				count = n
				ptr += count
			}
			next := pc + int(code[pc])
			if code[next] == opSUCCESS &&
				!(toplevel && ((st.matchAll && ptr != end) || (st.mustAdvance && ptr == st.start))) {
				st.ptr = ptr
				return false, true
			}
			c.pc, c.ptr, c.count = int32(pc), int32(ptr), int32(count)
			c.lastmark, c.lastindex = int32(st.lastmark), int32(st.lastindex)
			c.marksAt = -1
			if st.repeat != nil {
				st.markPush(c)
			}
			return st.minRepeatOne(i)

		case opREPEAT:
			// <REPEAT> <skip> <1=min> <2=max> item <UNTIL> tail
			rep := &reRepeat{count: -1, pc: pc, lastPtr: -1, prev: st.repeat}
			c.rep = rep
			st.repeat = rep
			return st.jumpTo(i, jREPEAT, pc+int(code[pc]), ptr)

		case opMAX_UNTIL:
			rep := st.repeat
			if rep == nil {
				panic("rt: regular expression MAX_UNTIL outside a repeat")
			}
			c.rep, c.pc, c.ptr = rep, int32(pc), int32(ptr)
			count := rep.count + 1
			c.count = int32(count)
			min, max := code[rep.pc+1], code[rep.pc+2]
			if int64(count) < int64(min) {
				// not enough matches
				rep.count = count
				return st.jumpTo(i, jMAX_UNTIL_1, rep.pc+3, ptr)
			}
			if (int64(count) < int64(max) || max == reMaxRepeat) && ptr != rep.lastPtr {
				// we may have enough matches, but if we can match another item, do so
				rep.count = count
				c.lastmark, c.lastindex = int32(st.lastmark), int32(st.lastindex)
				st.markPush(c)
				c.lastPtr = int32(rep.lastPtr) // zero-width match protection
				rep.lastPtr = ptr
				return st.jumpTo(i, jMAX_UNTIL_2, rep.pc+3, ptr)
			}
			return st.maxUntilTail(i)

		case opMIN_UNTIL:
			rep := st.repeat
			if rep == nil {
				panic("rt: regular expression MIN_UNTIL outside a repeat")
			}
			c.rep, c.pc, c.ptr = rep, int32(pc), int32(ptr)
			count := rep.count + 1
			c.count = int32(count)
			if int64(count) < int64(code[rep.pc+1]) {
				rep.count = count
				return st.jumpTo(i, jMIN_UNTIL_1, rep.pc+3, ptr)
			}
			// see if the tail matches
			st.repeat = rep.prev
			c.lastmark, c.lastindex = int32(st.lastmark), int32(st.lastindex)
			c.marksAt = -1
			if st.repeat != nil {
				st.markPush(c)
			}
			return st.jumpTo(i, jMIN_UNTIL_2, pc, ptr)

		default:
			panic("rt: regular expression opcode " + strconv.Itoa(int(op)) + " is not supported")
		}
	}
}

// restoreLast is LASTMARK_RESTORE (and MARK_POP_KEEP when marks were pushed).
func (st *reState) restoreLast(c *reCtx) {
	st.markPop(c)
	st.lastmark, st.lastindex = int(c.lastmark), int(c.lastindex)
}

// branchNext tries BRANCH's next alternative (pc: its <skip>).
func (st *reState) branchNext(i int) (bool, bool) {
	code, s, end := st.code, st.s, st.end
	c := &st.ctxs[i]
	ptr := int(c.ptr)
	for pc := int(c.pc); code[pc] != 0; pc += int(code[pc]) {
		if code[pc+1] == opLITERAL && (ptr >= end || uint32(s[ptr]) != code[pc+2]) {
			continue
		}
		if code[pc+1] == opIN && (ptr >= end || !st.charset(pc+3, uint32(s[ptr]))) {
			continue
		}
		c.pc = int32(pc)
		return st.jumpTo(i, jBRANCH, pc+1, ptr)
	}
	return false, false
}

// repeatOne is REPEAT_ONE's general case: the tail tried from the longest run down.
func (st *reState) repeatOne(i int) (bool, bool) {
	c := &st.ctxs[i]
	if int64(c.count) < int64(st.code[c.pc+1]) {
		return false, false
	}
	return st.jumpTo(i, jREPEAT_ONE_2, int(c.pc)+int(st.code[c.pc]), int(c.ptr))
}

// repeatOneLiteral is REPEAT_ONE when the tail starts with the literal c.chr.
func (st *reState) repeatOneLiteral(i int) (bool, bool) {
	c := &st.ctxs[i]
	min := int64(st.code[c.pc+1])
	for int64(c.count) >= min && (int(c.ptr) >= st.end || uint32(st.s[c.ptr]) != c.chr) {
		c.ptr--
		c.count--
	}
	if int64(c.count) < min {
		return false, false
	}
	return st.jumpTo(i, jREPEAT_ONE_1, int(c.pc)+int(st.code[c.pc]), int(c.ptr))
}

// minRepeatOne tries MIN_REPEAT_ONE's tail after c.count items.
func (st *reState) minRepeatOne(i int) (bool, bool) {
	c := &st.ctxs[i]
	max := st.code[c.pc+2]
	if max == reMaxRepeat || int64(c.count) <= int64(max) {
		return st.jumpTo(i, jMIN_REPEAT_ONE, int(c.pc)+int(st.code[c.pc]), int(c.ptr))
	}
	return false, false
}

// maxUntilTail is MAX_UNTIL's "cannot match more repeated items here; make sure the
// tail matches".
func (st *reState) maxUntilTail(i int) (bool, bool) {
	c := &st.ctxs[i]
	st.repeat = c.rep.prev
	return st.jumpTo(i, jMAX_UNTIL_3, int(c.pc), int(c.ptr))
}

// resume continues the context at i after its nested match returned ret (the jump
// labels of SRE(match)).
func (st *reState) resume(i int, ret bool) (bool, bool) {
	c := &st.ctxs[i]
	switch c.jump {
	case jBRANCH:
		if ret {
			return false, true
		}
		if c.marksAt >= 0 {
			st.markPop(c)
		}
		st.lastmark, st.lastindex = int(c.lastmark), int(c.lastindex)
		c.pc += int32(st.code[c.pc])
		return st.branchNext(i)

	case jREPEAT_ONE_1, jREPEAT_ONE_2:
		if ret {
			return false, true
		}
		if c.marksAt >= 0 {
			st.markPop(c)
		}
		st.lastmark, st.lastindex = int(c.lastmark), int(c.lastindex)
		c.ptr--
		c.count--
		if c.jump == jREPEAT_ONE_1 {
			return st.repeatOneLiteral(i)
		}
		return st.repeatOne(i)

	case jMIN_REPEAT_ONE:
		if ret {
			return false, true
		}
		if c.marksAt >= 0 {
			st.markPop(c)
		}
		st.lastmark, st.lastindex = int(c.lastmark), int(c.lastindex)
		st.ptr = int(c.ptr)
		if st.count(int(c.pc)+3, 1) == 0 {
			return false, false
		}
		c = &st.ctxs[i] // count may have run nested matches
		c.ptr++
		c.count++
		return st.minRepeatOne(i)

	case jREPEAT:
		st.repeat = c.rep.prev
		return false, ret

	case jMAX_UNTIL_1, jMIN_UNTIL_1:
		if ret {
			return false, true
		}
		c.rep.count = int(c.count) - 1
		return false, false

	case jMAX_UNTIL_2:
		c.rep.lastPtr = int(c.lastPtr)
		if ret {
			return false, true
		}
		st.restoreLast(c)
		c.rep.count = int(c.count) - 1
		return st.maxUntilTail(i)

	case jMAX_UNTIL_3:
		st.repeat = c.rep
		return false, ret

	case jMIN_UNTIL_2:
		tailRepeat := st.repeat
		st.repeat = c.rep
		if ret {
			return false, true
		}
		if tailRepeat != nil && c.marksAt >= 0 {
			st.markPop(c)
		}
		st.lastmark, st.lastindex = int(c.lastmark), int(c.lastindex)
		rep := c.rep
		max := st.code[rep.pc+2]
		if (int64(c.count) >= int64(max) && max != reMaxRepeat) || int(c.ptr) == rep.lastPtr {
			return false, false
		}
		rep.count = int(c.count)
		c.lastPtr = int32(rep.lastPtr) // zero-width match protection
		rep.lastPtr = int(c.ptr)
		return st.jumpTo(i, jMIN_UNTIL_3, rep.pc+3, int(c.ptr))

	case jMIN_UNTIL_3:
		c.rep.lastPtr = int(c.lastPtr)
		if ret {
			return false, true
		}
		c.rep.count = int(c.count) - 1
		return false, false
	}
	panic("rt: regular expression engine: unknown jump")
}

// search is SRE(search) from st.start (the general case: the prefix and charset fast
// paths of CPython find the same match).
func (st *reState) search() bool {
	code := st.code
	ptr, end := st.start, st.end
	if ptr > end {
		return false
	}
	pc := 0
	if code[0] == opINFO {
		if code[3] != 0 && uint64(end-ptr) < uint64(code[3]) {
			return false
		}
		pc = 1 + int(code[1])
	}
	st.ptr = ptr
	status := st.match(pc, ptr, true)
	st.mustAdvance = false
	if !status && code[pc] == opAT && (code[pc+1] == atBEGINNING || code[pc+1] == atBEGINNING_STRING) {
		st.start, st.ptr = end, end
		return false
	}
	for !status && ptr < end {
		ptr++
		st.lastmark, st.lastindex = -1, -1
		st.start, st.ptr = ptr, ptr
		status = st.match(pc, ptr, false)
	}
	return status
}

// ---- Match -----------------------------------------------------------------------------

// Match is a re.Match: the spans of the groups (code points; -1 when a group did not
// participate).
type Match struct {
	re    *Regex
	runes []rune
	marks []int // 2 * (groups + 1)
}

// newMatch is pattern_new_match.
func newMatch(re *Regex, st *reState) *Match {
	marks := make([]int, 2*(re.Groups+1))
	marks[0], marks[1] = st.start, st.ptr
	for i, j := 0, 0; i < re.Groups; i, j = i+1, j+2 {
		if j+1 <= st.lastmark && st.marks[j] >= 0 && st.marks[j+1] >= 0 {
			marks[j+2], marks[j+3] = st.marks[j], st.marks[j+1]
		} else {
			marks[j+2], marks[j+3] = -1, -1
		}
	}
	return &Match{re: re, runes: st.s, marks: marks}
}

func (m *Match) span(g int) (int, int) { return m.marks[2*g], m.marks[2*g+1] }

func (m *Match) slice(g int) Opt[string] {
	a, b := m.span(g)
	if a < 0 {
		return None[string]()
	}
	return Some(string(m.runes[a:b]))
}

// Group0 is m.group().
func (m *Match) Group0() string { return m.slice(0).V }

// Group is m.group(i): IndexError "no such group" for a bad number.
func (m *Match) Group(i int64) Opt[string] {
	if i < 0 || i > int64(m.re.Groups) {
		Raise("IndexError", "no such group")
	}
	return m.slice(int(i))
}

// GroupName is m.group(name).
func (m *Match) GroupName(name string) Opt[string] {
	i, ok := m.re.Names[name]
	if !ok {
		Raise("IndexError", "no such group")
	}
	return m.slice(i)
}

// Start is m.start() (a code-point offset).
func (m *Match) Start() int64 { return int64(m.marks[0]) }

// End is m.end().
func (m *Match) End() int64 { return int64(m.marks[1]) }

// PyRepr is repr(m): CPython formats the match with %.50R, its repr cut to 50 code
// points (the closing quote included in the count, so a long match loses it).
func (m *Match) PyRepr() string {
	r := []rune(ReprStr(m.Group0()))
	if len(r) > 50 {
		r = r[:50]
	}
	return "<re.Match object; span=(" + strconv.Itoa(m.marks[0]) + ", " + strconv.Itoa(m.marks[1]) +
		"), match=" + string(r) + ">"
}

// ---- the functions ---------------------------------------------------------------------

// Scan charges a regular expression's input its length (T2Fuel.scan).
func Scan(f *Fuel, s string) string {
	f.Grow(Len(s))
	return s
}

func (re *Regex) matchAt0(s string, all bool) Opt[*Match] {
	re.check()
	st := newState(re, []rune(s))
	st.matchAll = all
	st.start, st.ptr = 0, 0
	if !st.match(0, 0, true) {
		return None[*Match]()
	}
	return Some(newMatch(re, st))
}

// ReMatch is re.match(pattern, s).
func ReMatch(re *Regex, s string) Opt[*Match] { return re.matchAt0(s, false) }

// ReFullmatch is re.fullmatch(pattern, s).
func ReFullmatch(re *Regex, s string) Opt[*Match] { return re.matchAt0(s, true) }

// ReSearch is re.search(pattern, s).
func ReSearch(re *Regex, s string) Opt[*Match] {
	re.check()
	st := newState(re, []rune(s))
	if !st.search() {
		return None[*Match]()
	}
	return Some(newMatch(re, st))
}

// scan runs the loop of findall / finditer / split / sub: each match, then the next
// search starts where it ended, a match there not allowed to be empty when this one
// was (must_advance). yield returns false to stop.
func (re *Regex) scan(runes []rune, yield func(st *reState) bool) {
	st := newState(re, runes)
	st.start = 0
	for st.start <= st.end {
		st.reset()
		st.ptr = st.start
		if !st.search() {
			return
		}
		if !yield(st) {
			return
		}
		st.mustAdvance = st.ptr == st.start
		st.start = st.ptr
	}
}

// finditer is re.finditer(pattern, s).
func (re *Regex) finditer(s string) []*Match {
	re.check()
	var out []*Match
	re.scan([]rune(s), func(st *reState) bool {
		out = append(out, newMatch(re, st))
		return true
	})
	return out
}

// stateSlice is state_getslice: group g (>= 1) of the current match, ok false when it
// did not participate.
func (st *reState) stateSlice(g int) (string, bool) {
	i := 2 * (g - 1)
	if i >= st.lastmark || st.marks[i] < 0 || st.marks[i+1] < 0 {
		return "", false
	}
	return string(st.s[st.marks[i]:st.marks[i+1]]), true
}

// ReFindall is re.findall(pattern, s) for a pattern of at most one group: the whole
// match, or group 1 ("" when it did not participate).
func ReFindall(re *Regex, s string) *List[string] {
	re.check()
	if re.Groups > 1 {
		panic("rt: ReFindall of a pattern with several groups (ReFindallGroups)")
	}
	out := []string{}
	re.scan([]rune(s), func(st *reState) bool {
		if re.Groups == 0 {
			out = append(out, string(st.s[st.start:st.ptr]))
		} else {
			g, _ := st.stateSlice(1)
			out = append(out, g)
		}
		return true
	})
	return &List[string]{Items: out}
}

// ReFindallGroups is re.findall(pattern, s) for a pattern of several groups: each
// match's groups ("" for one that did not participate), a tuple per match.
func ReFindallGroups(re *Regex, s string) [][]string {
	re.check()
	out := [][]string{}
	re.scan([]rune(s), func(st *reState) bool {
		item := make([]string, re.Groups)
		for g := 1; g <= re.Groups; g++ {
			item[g-1], _ = st.stateSlice(g)
		}
		out = append(out, item)
		return true
	})
	return out
}

// ReSplit is re.split(pattern, s) for a pattern without groups.
func ReSplit(re *Regex, s string) *List[string] {
	re.check()
	if re.Groups > 0 {
		panic("rt: ReSplit by a pattern with groups (ReSplitGroups)")
	}
	runes := []rune(s)
	out := []string{}
	last := 0
	re.scan(runes, func(st *reState) bool {
		out = append(out, string(runes[last:st.start]))
		last = st.ptr
		return true
	})
	out = append(out, string(runes[last:]))
	return &List[string]{Items: out}
}

// ReSplitGroups is re.split(pattern, s) for a pattern with groups: after each
// segment, the match's groups (None for one that did not participate).
func ReSplitGroups(re *Regex, s string) *List[Opt[string]] {
	re.check()
	runes := []rune(s)
	out := []Opt[string]{}
	last := 0
	re.scan(runes, func(st *reState) bool {
		out = append(out, Some(string(runes[last:st.start])))
		for g := 1; g <= re.Groups; g++ {
			if v, ok := st.stateSlice(g); ok {
				out = append(out, Some(v))
			} else {
				out = append(out, None[string]())
			}
		}
		last = st.ptr
		return true
	})
	out = append(out, Some(string(runes[last:])))
	return &List[Opt[string]]{Items: out}
}

// Template is a compiled re.sub replacement: Lits[0] Group[0] Lits[1] ... Lits[n]
// (a group that did not participate adds nothing), or the exception compiling it
// raises in Python.
type Template struct {
	Repl            string
	Lits            []string
	Groups          []int
	ErrKind, ErrMsg string
}

// ReSub is re.sub(pattern, repl, s), charged as T2Fuel.sub: the input's length, then
// the most it can grow to (each match replaced by repl, whose group references expand
// to at most the longest match), before it runs.
func ReSub(f *Fuel, re *Regex, t *Template, s string) string {
	Scan(f, s)
	re.check()
	runes := []rune(s)
	var matches []*Match
	longest := 0
	re.scan(runes, func(st *reState) bool {
		matches = append(matches, newMatch(re, st))
		if w := st.ptr - st.start; w > longest {
			longest = w
		}
		return true
	})
	if longest < 1 {
		longest = 1
	}
	f.Grow(int64(len(runes)) + int64(len(matches))*Len(t.Repl)*int64(longest))
	if t.ErrKind != "" {
		Raise(t.ErrKind, t.ErrMsg)
	}
	var b strings.Builder
	i := 0
	for _, m := range matches {
		a, e := m.span(0)
		if i < a {
			b.WriteString(string(runes[i:a]))
		}
		b.WriteString(t.Lits[0])
		for k, g := range t.Groups {
			if g >= len(m.marks)/2 {
				Raise("IndexError", "no such group")
			}
			if v := m.slice(g); v.Ok {
				b.WriteString(v.V)
			}
			b.WriteString(t.Lits[k+1])
		}
		i = e
	}
	if i < len(runes) {
		b.WriteString(string(runes[i:]))
	}
	return b.String()
}
