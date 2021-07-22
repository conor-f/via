import os
import threading
import time

from bike import logger
from bike.utils import (
    sleep_until,
    timing
)
from bike.constants import (
    STAGED_DATA_DIR,
    SENT_DATA_DIR
)
from bike.models.journey import Journey
from bike.gps import GPSInterface
from bike.acceleration import AcceleratorInterface


class BikeClient():

    def __init__(self):
        logger.debug('Init Client')
        self.gps_interface = GPSInterface()
        self.acceleration_interface = AcceleratorInterface()

        self.journey = Journey()

        self.saver()

    def saver(self):
        logger.debug('Triggered client saver')

        # TODO: create this in the spec
        os.makedirs(STAGED_DATA_DIR, exist_ok=True)
        os.makedirs(SENT_DATA_DIR, exist_ok=True)

        self.journey.save()

        threading.Timer(
            60,
            self.saver
        ).start()

    @sleep_until
    @timing
    def capture(self):
        self._data.append(
            {
                'acc': self.acceleration_interface.get_acceleration(),
                'gps': self.gps_interface.get_lat_lng(),
                'time': time.monotonic()
            }
        )

    def run(self):
        logger.info('Starting client')
        while True:
            self.capture()
