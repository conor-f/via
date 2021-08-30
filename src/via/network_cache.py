import os
import pickle

from networkx.classes.multidigraph import MultiDiGraph

from via.settings import VERSION
from via.constants import NETWORK_CACHE_DIR
from via import logger


def is_within(bbox, larger):
    return all([
        bbox['north'] < larger['north'],
        bbox['south'] > larger['south'],
        bbox['east'] < larger['east'],
        bbox['west'] > larger['west'],
    ])


class SingleNetworkCache():
    # TODO: split these in a grid of lat / lng 0.5 by the first gps of
    # the upper right or something

    def __init__(self, network_type: str):
        self.network_type = network_type
        self.loaded = False
        self.data = []
        self.last_save_len = -1

    def get(self, journey, poly=True) -> MultiDiGraph:
        if not self.loaded:
            self.load()

        if not poly:
            for net in self.data:
                if is_within(journey.bbox, net['bbox']):
                    logger.debug(f'{journey.gps_hash}: Using a larger network rather than generating')
                    return net['network']

        for net in self.data:
            if journey.gps_hash == net['hash']:
                return net['network']

        return None

    def set(self, journey, network: MultiDiGraph):
        self.data.append({
            'hash': journey.gps_hash,
            'bbox': journey.bbox,
            'network': network
        })
        self.save()

    def save(self):
        if any([
            not os.path.exists(self.fp),
            len(self.data) > self.last_save_len and self.last_save_len >= 0
        ]):
            with open(self.fp, 'wb') as f:
                pickle.dump(self.data, f)

    def load(self):
        if not os.path.exists(self.fp):
            os.makedirs(
                os.path.dirname(self.fp),
                exist_ok=True
            )
            self.save()

        with open(self.fp, 'rb') as f:
            self.data = pickle.load(f)
        self.loaded = True
        self.last_save_len = len(self.data)

    @property
    def dir(self) -> str:
        return os.path.join(NETWORK_CACHE_DIR, VERSION)

    @property
    def fp(self) -> str:
        return os.path.join(self.dir, f'{self.network_type}_cache.pickle')


class NetworkCache():

    def __init__(self):
        self.network_caches = {}

    def get(self, key: str, journey, poly=True) -> MultiDiGraph:
        if key not in self.network_caches:
            self.network_caches[key] = SingleNetworkCache(key)
        return self.network_caches[key].get(journey, poly=poly)

    def set(self, key: str, journey, network: MultiDiGraph):
        if key not in self.network_caches:
            self.network_caches[key] = SingleNetworkCache(key)
        self.network_caches[key].set(journey, network)


network_cache = NetworkCache()
