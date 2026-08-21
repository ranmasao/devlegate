from devlegate.cli import build_parser, main


def test_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "--help"])
    try:
        main()
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert "usage: devlegate" in output


def test_version(capsys):
    parser = build_parser()
    try:
        parser.parse_args(["--version"])
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert output.startswith("devlegate ")
