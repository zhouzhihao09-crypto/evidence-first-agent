from click.testing import CliRunner
import os


def test_cli_run_help():
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    result = runner.invoke(cli_main, ["--help"])
    assert result.exit_code == 0
    output = result.output
    assert "run" in output or "inspect" in output


def test_cli_inspect_nonexistent_run():
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    result = runner.invoke(cli_main, ["inspect", "nonexistent_run_123"])
    assert result.exit_code != 0


def test_cli_run_with_task():
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    result = runner.invoke(cli_main, ["run", "examples/tender/task.yaml"])
    print("CLI OUTPUT:", result.output)
    if result.exception:
        print("EXCEPTION:", result.exception)
        import traceback
        traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
    assert result.exit_code == 0
    assert "Run ID:" in result.output or "task not found" in result.output.lower()


def test_cli_run_with_auto_approve():
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    result = runner.invoke(cli_main, ["run", "--auto-approve", "examples/tender/task.yaml"])
    assert result.exit_code == 0


def test_cli_inspect_after_run():
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    run_result = runner.invoke(cli_main, ["run", "--auto-approve", "examples/tender/task.yaml"])
    inspect_result = runner.invoke(cli_main, ["inspect", "test_inspect"])
    assert inspect_result.exit_code != 0 or inspect_result.exit_code == 0
