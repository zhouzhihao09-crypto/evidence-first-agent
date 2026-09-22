import click
from click.testing import CliRunner


def run_cli(args):
    from evidence_first.cli.main import main as cli_main
    runner = CliRunner()
    return runner.invoke(cli_main, args)
