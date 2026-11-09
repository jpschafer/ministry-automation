"""Entry point: python -m sexton --config config.yaml [--dry-run] [--once]"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .alerts import Alerter, Healthcheck
from .config import ConfigError, load_config
from .core import CoreClient
from .facebook import GRAPH_API_VERSION, GraphClient
from .http import Http
from .log import configure_logging, event
from .reconcile import Reconciler, describe

log = logging.getLogger("sexton")


def parse_args(argv):
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(prog="sexton", description=__doc__)
    parser.add_argument("--config", default="/config/config.yaml", help="path to the YAML config")
    parser.add_argument(
        "--dry-run", action="store_true", help="log intended actions; make no mutating calls"
    )
    parser.add_argument("--once", action="store_true", help="run a single tick and exit")
    return parser.parse_args(argv)


def main(argv=None):
    """Runs the reconcile loop until SIGTERM/SIGINT."""
    args = parse_args(argv)
    configure_logging()
    try:
        config = load_config(args.config)
    except (ConfigError, OSError) as err:
        event(log, logging.CRITICAL, "config_error", error=str(err))
        return 2

    http = Http(dry_run=args.dry_run)
    alerter = Alerter(http, config.ntfy_url, config.ntfy_token)
    healthcheck = Healthcheck(http, config.healthcheck_url)
    reconciler = Reconciler(config, CoreClient(config.core, http), GraphClient(http), alerter)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    event(
        log,
        logging.INFO,
        "start",
        dry_run=args.dry_run,
        graph_api=GRAPH_API_VERSION,
        inputs=[i.name for i in config.inputs],
    )
    while not stop.is_set():
        try:
            ok = reconciler.tick()
            alerter.resolved("unexpected")
        except Exception as err:  # pylint: disable=broad-exception-caught
            ok = False
            log.exception("tick_crashed")
            alerter.problem("unexpected", "Sexton: unexpected error", describe(err))
        healthcheck.ping(ok)
        if args.once:
            break
        stop.wait(config.tick_seconds)
    event(log, logging.INFO, "stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
