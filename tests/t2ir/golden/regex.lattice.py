import re


@procedure
def regex(s: Str) -> Str:
    if re.fullmatch(r"[a-z]+", s) is not None:
        return s
    m = re.search(r"\d+", s)
    parts = re.split(r",", s)
    found = re.findall(r"[A-Z]", s)
    cleaned = re.sub(r"\s+", " ", s)
    head = "none" if m is None else "some"
    return head + cleaned + str(len(parts) + len(found))
