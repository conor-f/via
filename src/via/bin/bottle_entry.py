import argparse
import threading

import bottle

from via import logger
from via.pull_journeys import pull_journeys

from via.api import *
from via.api.info import *
from via.api.journeys import *


def update_journeys():
    logger.info('Pulling journeys')
    pull_journeys()
    threading.Timer(60 * 60, update_journeys).start()


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--port',
        dest='port',
        default=8080
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        dest='debug'
    )
    parser.add_argument(
        '--reloader',
        action='store_true',
        dest='reloader'
    )
    args = parser.parse_args()

    update_journeys()

    bottle.debug(args.debug)
    bottle.run(
        host='0.0.0.0',
        port=args.port,
        debug=args.debug,
        reloader=args.reloader
    )


if __name__ == '__main__':
    main()
