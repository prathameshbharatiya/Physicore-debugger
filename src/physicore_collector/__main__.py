"""`python -m physicore_collector` == the `physicore-collector` command. Used to start the
detached supervisor with the exact interpreter that is running `capture start`."""

from .cli import app

if __name__ == "__main__":
    app(prog_name="physicore-collector")
