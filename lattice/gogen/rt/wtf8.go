package rt

import "unicode/utf8"

// A Python str may hold a lone surrogate (json.loads("\"\\ud800\"")); UTF-8 cannot. Such
// a str is held in a Go string as WTF-8: the surrogate's 3-byte form (ED A0 80 ..
// ED BF BF), which Go's own decoding reads as invalid bytes. pyRunes and wtf8 convert
// exactly; every valid UTF-8 string is its own WTF-8.

// pyRunes is s's code points, a lone surrogate included.
func pyRunes(s string) []rune {
	out := make([]rune, 0, len(s))
	for i := 0; i < len(s); {
		if i+2 < len(s) && s[i] == 0xED && s[i+1] >= 0xA0 && s[i+1] <= 0xBF && s[i+2]&0xC0 == 0x80 {
			out = append(out, rune(0xD000|(rune(s[i+1])&0x3F)<<6|rune(s[i+2])&0x3F))
			i += 3
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		out = append(out, r)
		i += size
	}
	return out
}

// wtf8 is the code points rs as a string (a lone surrogate in its 3-byte form).
func wtf8(rs []rune) string {
	b := make([]byte, 0, len(rs))
	for _, r := range rs {
		if r >= 0xD800 && r < 0xE000 {
			b = append(b, 0xED, byte(0x80|(r>>6)&0x3F), byte(0x80|r&0x3F))
			continue
		}
		b = utf8.AppendRune(b, r)
	}
	return string(b)
}
