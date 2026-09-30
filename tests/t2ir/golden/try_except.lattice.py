@procedure
def parse(xs: list[Str]) -> Int:
    total = 0
    for x in xs:
        try:
            total += int(x)
        except ValueError:
            total -= 1
        except (KeyError, IndexError) as err:
            total = 0
        else:
            total += 1
    return total
