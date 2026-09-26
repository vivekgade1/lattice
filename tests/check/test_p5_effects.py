import pytest

from lattice.check import check_source

LOAD = 'raw = load("orders.csv", schema=Orders)\n'


def errors(profile, src):
    return [d for d in check_source(src, profile).diagnostics if d.severity == "error"]


def codes(profile, src):
    return [d.code for d in errors(profile, src)]


def test_write_assigned_L502_once(profile, program):
    src = program(LOAD + 'x = write(raw, "out.parquet")\n')
    (d,) = errors(profile, src)
    assert d.code == "L502" and d.span.line == 5
    assert "returns nothing" in d.message


@pytest.mark.parametrize(
    "body",
    [
        '    y = load("orders.csv", schema=Orders)\n    z = where(y, col("qty") > 1)\n',
        '    z = where(t, col("qty") > 1)\n    y = write(z, "out.csv")\n',
    ],
)
def test_io_inside_step_L503(profile, program, body):
    ret = "    return z\n"
    src = program(
        "@step\ndef f(t: Table[Orders]) -> Table:\n"
        + body
        + ret
        + LOAD
        + 'r = f(raw)\nwrite(r, "out.parquet")\n'
    )
    assert codes(profile, src) == ["L503"]


@pytest.mark.parametrize(
    "path", ["/tmp/orders.csv", "../orders.csv", "data/../../orders.csv", "a\\\\orders.csv", ""]
)
def test_load_path_L501(profile, program, path):
    src = program(f'raw = load("{path}", schema=Orders)\nwrite(raw, "out.parquet")\n')
    (d,) = errors(profile, src)
    assert d.code == "L501" and "relative" in d.message


@pytest.mark.parametrize("path", ["/tmp/out.parquet", "../out.csv", "C:/out.csv"])
def test_write_path_L501(profile, program, path):
    assert codes(profile, program(LOAD + f'write(raw, "{path}")\n')) == ["L501"]


def test_relative_subdir_ok(profile, program):
    src = program('raw = load("data/orders.csv", schema=Orders)\nwrite(raw, "out/o.csv")\n')
    assert check_source(src, profile).diagnostics == ()


def test_load_extension_L505(profile, program):
    src = program('raw = load("orders.parquet", schema=Orders)\nwrite(raw, "out.parquet")\n')
    assert codes(profile, src) == ["L505"]


@pytest.mark.parametrize("path", ["out.txt", "out", "out.json"])
def test_write_extension_L505(profile, program, path):
    assert codes(profile, program(LOAD + f'write(raw, "{path}")\n')) == ["L505"]


def test_no_write_L504(profile, program):
    src = program(LOAD + 'x = where(raw, col("qty") > 1)\ny = where(x, col("qty") < 5)\n')
    ds = errors(profile, src)
    assert [d.code for d in ds] == ["L504"] and "never writes" in ds[0].message


def test_empty_program_L504(profile):
    assert codes(profile, "# lattice: v1\n") == ["L504"]


def test_two_writes_ok(profile, program):
    src = program(LOAD + 'write(raw, "a.csv")\nwrite(raw, "b.parquet")\n')
    assert check_source(src, profile).diagnostics == ()
