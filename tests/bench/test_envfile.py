from pathlib import Path

from bench.envfile import load_env_file


def test_loads_keys_and_ignores_comments_blanks_and_quotes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LATTICE_T1", raising=False)
    monkeypatch.delenv("LATTICE_T2", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\n\nLATTICE_T1=abc\nexport LATTICE_T2="q v"\nnot a pair\n')

    assert load_env_file(env) == {"LATTICE_T1": "abc", "LATTICE_T2": "q v"}
    import os

    assert os.environ["LATTICE_T1"] == "abc" and os.environ["LATTICE_T2"] == "q v"


def test_shell_env_wins_and_empty_values_are_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LATTICE_T3", "from-shell")
    monkeypatch.delenv("LATTICE_T4", raising=False)
    env = tmp_path / ".env"
    env.write_text("LATTICE_T3=from-file\nLATTICE_T4=\n")

    assert load_env_file(env) == {}
    import os

    assert os.environ["LATTICE_T3"] == "from-shell" and "LATTICE_T4" not in os.environ


def test_missing_file_is_a_no_op(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env") == {}
