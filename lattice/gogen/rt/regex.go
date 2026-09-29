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

// saveMarks is MARK_PUSH(lastmark): a copy of marks[0..lastmark].
func (st *reState) saveMarks(lastmark int) []int {
	if lastmark < 0 {
		return nil
	}
	return append([]int(nil), st.marks[:lastmark+1]...)
}

// restoreMarks is MARK_POP / MARK_POP_KEEP.
func (st *reState) restoreMarks(saved []int) {
	copy(st.marks, saved)
}

// match is SRE(match): does the code at pc match at ptr? On success st.ptr is where
// the match ends. toplevel: the SUCCESS of the whole pattern (fullmatch's end check and
// the empty-match rule of the scanning loops apply to it).
func (st *reState) match(pc, ptr int, toplevel bool) bool {
	code, s, end := st.code, st.s, st.end
	if code[pc] == opINFO {
		// <INFO> <skip> <flags> <min> ...: too few characters left cannot match
		if code[pc+3] != 0 && uint64(end-ptr) < uint64(code[pc+3]) {
			return false
		}
		pc += int(code[pc+1]) + 1
	}
	for {
		op := code[pc]
		pc++
		switch op {
		case opMARK:
			i := int(code[pc])
			if i&1 == 1 {
				st.lastindex = i/2 + 1
			}
			if i > st.lastmark {
				for j := st.lastmark + 1; j < i; j++ {
					st.marks[j] = -1
				}
				st.lastmark = i
			}
			st.marks[i] = ptr
			pc++

		case opLITERAL:
			if ptr >= end || uint32(s[ptr]) != code[pc] {
				return false
			}
			pc++
			ptr++

		case opNOT_LITERAL:
			if ptr >= end || uint32(s[ptr]) == code[pc] {
				return false
			}
			pc++
			ptr++

		case opSUCCESS:
			if toplevel && ((st.matchAll && ptr != end) || (st.mustAdvance && ptr == st.start)) {
				return false
			}
			st.ptr = ptr
			return true

		case opAT:
			if !st.at(ptr, code[pc]) {
				return false
			}
			pc++

		case opCATEGORY:
			if ptr >= end || !category(code[pc], s[ptr]) {
				return false
			}
			pc++
			ptr++

		case opANY:
			if ptr >= end || s[ptr] == '\n' {
				return false
			}
			ptr++

		case opANY_ALL:
			if ptr >= end {
				return false
			}
			ptr++

		case opIN:
			if ptr >= end || !st.charset(pc+1, uint32(s[ptr])) {
				return false
			}
			pc += int(code[pc])
			ptr++

		case opLITERAL_IGNORE:
			if ptr >= end || uint32(lowerASCII(s[ptr])) != code[pc] {
				return false
			}
			pc++
			ptr++

		case opNOT_LITERAL_IGNORE:
			if ptr >= end || uint32(lowerASCII(s[ptr])) == code[pc] {
				return false
			}
			pc++
			ptr++

		case opIN_IGNORE:
			if ptr >= end || !st.charset(pc+1, uint32(lowerASCII(s[ptr]))) {
				return false
			}
			pc += int(code[pc])
			ptr++

		case opJUMP, opINFO:
			pc += int(code[pc])

		case opFAILURE:
			return false

		case opBRANCH:
			// <BRANCH> <0=skip> code <JUMP> ... <NULL>
			lastmark, lastindex := st.lastmark, st.lastindex
			var saved []int
			inRepeat := st.repeat != nil
			if inRepeat {
				saved = st.saveMarks(lastmark)
			}
			for ; code[pc] != 0; pc += int(code[pc]) {
				if code[pc+1] == opLITERAL && (ptr >= end || uint32(s[ptr]) != code[pc+2]) {
					continue
				}
				if code[pc+1] == opIN && (ptr >= end || !st.charset(pc+3, uint32(s[ptr]))) {
					continue
				}
				if st.match(pc+1, ptr, toplevel) {
					return true
				}
				if inRepeat {
					st.restoreMarks(saved)
				}
				st.lastmark, st.lastindex = lastmark, lastindex
			}
			return false

		case opREPEAT_ONE:
			// <REPEAT_ONE> <skip> <1=min> <2=max> item <SUCCESS> tail
			min := int64(code[pc+1])
			if min > int64(end-ptr) {
				return false
			}
			st.ptr = ptr
			count := st.count(pc+3, code[pc+2])
			ptr += count
			if int64(count) < min {
				return false
			}
			next := pc + int(code[pc])
			if code[next] == opSUCCESS && ptr == end && !(toplevel && st.mustAdvance && ptr == st.start) {
				st.ptr = ptr
				return true
			}
			lastmark, lastindex := st.lastmark, st.lastindex
			var saved []int
			inRepeat := st.repeat != nil
			if inRepeat {
				saved = st.saveMarks(lastmark)
			}
			if code[next] == opLITERAL {
				// the tail starts with a literal: skip positions it cannot match at
				chr := code[next+1]
				for {
					for int64(count) >= min && (ptr >= end || uint32(s[ptr]) != chr) {
						ptr--
						count--
					}
					if int64(count) < min {
						break
					}
					if st.match(next, ptr, toplevel) {
						return true
					}
					if inRepeat {
						st.restoreMarks(saved)
					}
					st.lastmark, st.lastindex = lastmark, lastindex
					ptr--
					count--
				}
			} else {
				for int64(count) >= min {
					if st.match(next, ptr, toplevel) {
						return true
					}
					if inRepeat {
						st.restoreMarks(saved)
					}
					st.lastmark, st.lastindex = lastmark, lastindex
					ptr--
					count--
				}
			}
			return false

		case opMIN_REPEAT_ONE:
			// <MIN_REPEAT_ONE> <skip> <1=min> <2=max> item <SUCCESS> tail
			min, max := code[pc+1], code[pc+2]
			if int64(min) > int64(end-ptr) {
				return false
			}
			st.ptr = ptr
			count := 0
			if min != 0 {
				n := st.count(pc+3, min)
				if n < int(min) {
					return false
				}
				count = n
				ptr += count
			}
			next := pc + int(code[pc])
			if code[next] == opSUCCESS &&
				!(toplevel && ((st.matchAll && ptr != end) || (st.mustAdvance && ptr == st.start))) {
				st.ptr = ptr
				return true
			}
			lastmark, lastindex := st.lastmark, st.lastindex
			var saved []int
			inRepeat := st.repeat != nil
			if inRepeat {
				saved = st.saveMarks(lastmark)
			}
			for max == reMaxRepeat || int64(count) <= int64(max) {
				if st.match(next, ptr, toplevel) {
					return true
				}
				if inRepeat {
					st.restoreMarks(saved)
				}
				st.lastmark, st.lastindex = lastmark, lastindex
				st.ptr = ptr
				if st.count(pc+3, 1) == 0 {
					break
				}
				ptr++
				count++
			}
			return false

		case opREPEAT:
			// <REPEAT> <skip> <1=min> <2=max> item <UNTIL> tail
			rep := &reRepeat{count: -1, pc: pc, lastPtr: -1, prev: st.repeat}
			st.repeat = rep
			ok := st.match(pc+int(code[pc]), ptr, toplevel)
			st.repeat = rep.prev
			return ok

		case opMAX_UNTIL:
			rep := st.repeat
			if rep == nil {
				panic("rt: regular expression MAX_UNTIL outside a repeat")
			}
			count := rep.count + 1
			min, max := code[rep.pc+1], code[rep.pc+2]
			if int64(count) < int64(min) {
				// not enough matches
				rep.count = count
				if st.match(rep.pc+3, ptr, toplevel) {
					return true
				}
				rep.count = count - 1
				return false
			}
			if (int64(count) < int64(max) || max == reMaxRepeat) && ptr != rep.lastPtr {
				// we may have enough matches, but if we can match another item, do so
				rep.count = count
				lastmark, lastindex := st.lastmark, st.lastindex
				saved := st.saveMarks(lastmark)
				savedLast := rep.lastPtr
				rep.lastPtr = ptr
				ok := st.match(rep.pc+3, ptr, toplevel)
				rep.lastPtr = savedLast
				if ok {
					return true
				}
				st.restoreMarks(saved)
				st.lastmark, st.lastindex = lastmark, lastindex
				rep.count = count - 1
			}
			// cannot match more repeated items here; make sure the tail matches
			st.repeat = rep.prev
			ok := st.match(pc, ptr, toplevel)
			st.repeat = rep
			return ok

		case opMIN_UNTIL:
			rep := st.repeat
			if rep == nil {
				panic("rt: regular expression MIN_UNTIL outside a repeat")
			}
			count := rep.count + 1
			min, max := code[rep.pc+1], code[rep.pc+2]
			if int64(count) < int64(min) {
				rep.count = count
				if st.match(rep.pc+3, ptr, toplevel) {
					return true
				}
				rep.count = count - 1
				return false
			}
			// see if the tail matches
			st.repeat = rep.prev
			lastmark, lastindex := st.lastmark, st.lastindex
			var saved []int
			if st.repeat != nil {
				saved = st.saveMarks(lastmark)
			}
			ok := st.match(pc, ptr, toplevel)
			tailRepeat := st.repeat
			st.repeat = rep
			if ok {
				return true
			}
			if tailRepeat != nil {
				st.restoreMarks(saved)
			}
			st.lastmark, st.lastindex = lastmark, lastindex
			if (int64(count) >= int64(max) && max != reMaxRepeat) || ptr == rep.lastPtr {
				return false
			}
			rep.count = count
			savedLast := rep.lastPtr
			rep.lastPtr = ptr
			ok = st.match(rep.pc+3, ptr, toplevel)
			rep.lastPtr = savedLast
			if ok {
				return true
			}
			rep.count = count - 1
			return false

		default:
			panic("rt: regular expression opcode " + strconv.Itoa(int(op)) + " is not supported")
		}
	}
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

// PyRepr is repr(m).
func (m *Match) PyRepr() string {
	return "<re.Match object; span=(" + strconv.Itoa(m.marks[0]) + ", " + strconv.Itoa(m.marks[1]) +
		"), match=" + ReprStr(m.Group0()) + ">"
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

// ReFindall is re.findall(pattern, s) for a pattern of at most one group: the whole
// match, or group 1 ("" when it did not participate).
func ReFindall(re *Regex, s string) *List[string] {
	re.check()
	if re.Groups > 1 {
		panic("rt: findall of a pattern with several groups (L892)")
	}
	out := []string{}
	re.scan([]rune(s), func(st *reState) bool {
		if re.Groups == 0 {
			out = append(out, string(st.s[st.start:st.ptr]))
		} else if 0 < st.lastmark && st.marks[0] >= 0 && st.marks[1] >= 0 {
			out = append(out, string(st.s[st.marks[0]:st.marks[1]]))
		} else {
			out = append(out, "")
		}
		return true
	})
	return &List[string]{Items: out}
}

// ReSplit is re.split(pattern, s) for a pattern without groups.
func ReSplit(re *Regex, s string) *List[string] {
	re.check()
	if re.Groups > 0 {
		panic("rt: split by a pattern with groups (L892)")
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
