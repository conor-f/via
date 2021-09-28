import hashlib
import statistics
from numbers import Number
from operator import itemgetter

import reverse_geocoder
from shapely.geometry import (
    MultiPoint,
    Point
)

from via.settings import MIN_ACC_SCORE
from via import logger
from via.place_cache import place_cache
from via.models.generic import (
    GenericObject,
    GenericObjects
)
from via.models.gps import GPSPoint
from via.utils import angle_between_slopes


class Context(object):

    def set_context(self, pre=None, post=None):
        if not self.is_context_populated:
            self.context_pre = pre
            self.context_post = post

    def get_slope_incoming(self, mode='near'):
        """

        :kwarg mode: near or far - how much context should be used
        """
        if mode == 'far':
            return self.context_pre[0].gps.slope_between(self.gps)
        elif mode == 'near':
            return self.context_pre[-1].gps.slope_between(self.gps)
        raise ValueError('Mode %s not recognised' % (mode))

    def get_slope_outgoing(self, mode='near'):
        """

        :kwarg mode: near or far - how much context should be used
        """
        if mode == 'far':
            return self.gps.slope_between(self.context_post[-1].gps)
        elif mode == 'near':
            return self.gps.slope_between(self.context_post[0].gps)
        raise ValueError('Mode %s not recognised' % (mode))

    def get_slope_around_point(self, mode='near'):
        """
        Get the slope of the few points around the point.

        Useful in getting a slope associated with the slope so we can
        compare it to the slope of a road

        :kwarg mode: near or far - how much context should be used
        """
        if mode == 'far':
            return self.context_pre[0].gps.slope_between(
                self.context_post[-1]
            )
        elif mode == 'near':
            return self.context_pre[-1].gps.slope_between(
                self.context_post[0]
            )

        raise ValueError('Mode %s not recognised' % (mode))

    def get_in_out_angle(self, mode='near'):
        """
        :kwarg mode: near or far - how much context should be used
        """
        # TODO: Rename this function, ew
        return angle_between_slopes(
            self.get_slope_incoming(mode=mode),
            self.get_slope_outgoing(mode=mode),
            absolute=True
        )

    @property
    def is_context_populated(self):
        return self.context_pre != [] and self.context_post != []

    @property
    def dist_to_earliest(self):
        pass

    @property
    def dist_to_latest(self):
        pass

    def serialize(self, include_time=True):
        return {
            'pre': [p.serialize(include_time=include_time, include_context=False) for p in self.pre],
            'post': [p.serialize(include_time=include_time, include_context=False) for p in self.post]
        }


class FramePoint(GenericObject, Context):
    """
    Data which snaps to the gps giving something like
    {gps: (1, 2), acc: [1,2,3], time: 1}

    Rather than
    {gps: (1, 2), acc: 1, time: 1}
    {gps: (1, 2), acc: 2, time: 2}
    {gps: (1, 2), acc: 3, time: 3}
    """

    def __init__(self, time, gps, acceleration):
        """

        :param time:
        :param gps: GPSPoint or dict serialization of GPSPoint
        :param acceneration:
        """

        self.context_pre = []
        self.context_post = []

        super().__init__()

        self.time = time
        self.gps = GPSPoint.parse(gps)

        if isinstance(acceleration, list):
            self.acceleration = [acc for acc in acceleration if acc >= MIN_ACC_SCORE]
        else:
            if acceleration is None:
                acceleration = 0
            assert isinstance(acceleration, Number)
            self.acceleration = [acc for acc in [acceleration] if acc >= MIN_ACC_SCORE]

    def get_best_edge(self, edges, graph=None, mode=None):
        """

        :kwarg mode: strategy to use to get the best edge
        """

        default_mode = 'nearest'
        if mode is None:
            mode = default_mode

        if mode == 'nearest':
            return sorted(edges, key=itemgetter(1))[0]
        elif mode == 'matching_angle':
            if not self.is_context_populated:
                logger.debug('Cannot use mode \'%s\' as no point context is set, using mode \'%s\'', mode, default_mode)
                # can probably warn if there's no post AND no pre, that would show there was no context ever set on the journey?
                return self.get_best_edge(edges, mode='nearest')
            if not graph:
                logger.warning('graph not supplied to get_best_edge and mode \'%s\' was selected. Defaulting to mode \'%s\'', mode, default_mode)
                return self.get_best_edge(edges, mode=default_mode)

            edge_node_data = []
            slope_around = self.get_slope_around_point()

            for edge in edges:
                try:
                    origin = graph.nodes[edge[0][0]]
                    dst = graph.nodes[edge[0][1]]
                    data = {
                        'edge': edge,
                        'origin': GPSPoint(origin['y'], origin['x']),
                        'dst': GPSPoint(dst['y'], dst['x']),
                    }
                    data['slope'] = data['origin'].slope_between(
                        data['dst']
                    )
                    data['angle_between'] = angle_between_slopes(
                        slope_around,
                        data['slope'],
                        absolute=True
                    )
                    edge_node_data.append(
                        data
                    )
                except Exception as ex:
                    logger.warning('Could not get edge data: %s: %s', edge, ex)

            return sorted(
                edge_node_data,
                key=lambda x: x['angle_between']
            )[0]['edge']
        elif mode == 'angle_nearest':
            # TODO: if there's only a diff of x, use the nearest or something, should probably weight
            # TODO: reuse a load from matching_angle
            # A hybrid of matching angle and nearest
            raise NotImplementedError()
        elif mode == 'sticky':
            # Try to stick to previous road if it makes sense
            # Might want to be sticky on top of some other mode? Not important now
            raise NotImplementedError()
        else:
            logger.warning('Can not use mode \'%s\' to get best edge as that is not recognised. Defaulting to mode \'%s\'', mode, default_mode)
            return self.get_best_edge(edges, mode=default_mode)

    def append_acceleration(self, acc):
        if isinstance(acc, list):
            for item in acc:
                self.append_acceleration(item)
        elif isinstance(acc, type(None)):
            return
        else:
            if acc >= MIN_ACC_SCORE:
                self.acceleration.append(acc)

    @staticmethod
    def parse(obj):
        if isinstance(obj, FramePoint):
            return obj

        if isinstance(obj, dict):
            return FramePoint(
                obj.get('time', None),
                obj['gps'],
                [acc for acc in obj['acc'] if acc >= MIN_ACC_SCORE]
            )
        else:
            raise NotImplementedError(
                'Can\'t parse Point from type %s' % (type(obj))
            )

    def speed_between(self, point):
        metres_per_second = None
        distance = self.distance_from(point.gps)
        if distance != 0:
            time_diff = point.time - self.time
            metres_per_second = distance / time_diff
        return metres_per_second

    def distance_from(self, point):
        """

        :param point: GPSPoint or tuple of (lat, lng) or Frame object
        :rtype: float
        :return: Distance between points in metres
        """
        if isinstance(point, FramePoint):
            point = point.gps
        return self.gps.distance_from(point)

    @property
    def is_complete(self):
        """
        Does the point contain all expected data
        """
        return isinstance(self.time, float) and self.gps.is_populated and self.acceleration != []

    @property
    def road_quality(self):
        if self.acceleration == []:
            return 0
        try:
            return int(statistics.mean(self.acceleration) * 100)
        except:
            logger.warning(f'Could not calculate road quality from: {self.acceleration}. Defauling to 0')
            return 0

    def serialize(self, include_time=True, include_context=True):
        data = {
            'gps': self.gps.serialize(),
            'acc': list(self.acceleration),
        }
        if include_time:
            data['time'] = round(self.time, 2)
        if include_context:
            data['context'] = {
                'pre': [p.serialize(include_time=include_time, include_context=False) for p in self.context_pre],
                'post': [p.serialize(include_time=include_time, include_context=False) for p in self.context_post],
            }
        return data

    @property
    def gps_hash(self):
        return self.gps.content_hash

    @property
    def content_hash(self):
        return hashlib.md5(
            str(hash(tuple(self.acceleration)) + hash(tuple(self.gps.point)) + hash(self.time)).encode()
        ).hexdigest()


class FramePoints(GenericObjects):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('child_class', FramePoint)
        super().__init__(*args, **kwargs)

    @property
    def most_northern(self):
        return max([frame.gps.lat for frame in self])

    @property
    def most_southern(self):
        return min([frame.gps.lat for frame in self])

    @property
    def most_eastern(self):
        return max([frame.gps.lng for frame in self])

    @property
    def most_western(self):
        return min([frame.gps.lng for frame in self])

    @property
    def data_quality(self):
        """
        Get the percentage of frames that are good. Should
        automatically disregard journeys with low data quality

        :rtype: float
        :return: The percent between 0 and 1
        """
        return 1  # TODO

    @property
    def origin(self):
        """

        :rtype: via.models.Frame
        :return: The first frame of the journey
        """
        return self[0]

    @property
    def destination(self):
        """

        :rtype: via.models.Frame
        :return: The last frame of the journey
        """
        return self[-1]

    @property
    def duration(self):
        """

        :rtype: float
        :return: The number of seconds the journey took
        """
        return self.destination.time - self.origin.time

    @property
    def direct_distance(self):
        """

        :rtype: float
        :return: distance from origin to destination in metres
        """
        return self[0].distance_from(self[-1])

    def serialize(self, include_time=False, include_context=True):
        return [frame.serialize(include_time=include_time, include_context=include_context) for frame in self]

    def get_multi_points(self):
        """
        Get a shapely.geometry.MultiPoint of all the points
        """
        unique_points = []
        prev = None
        for frame in self:
            if frame.gps.is_populated:
                if prev is not None:
                    if prev.gps.lat != frame.gps.lat:
                        unique_points.append(
                            Point(
                                frame.gps.lng,
                                frame.gps.lat
                            )
                        )

            prev = frame

        return MultiPoint(
            unique_points
        )

    @property
    def gps_hash(self):
        return hashlib.md5(
            str([
                point.gps.content_hash for point in self
            ]).encode()
        ).hexdigest()

    @property
    def content_hash(self):
        return hashlib.md5(
            str([
                point.content_hash for point in self
            ]).encode()
        ).hexdigest()

    @property
    def country(self):
        """
        Get what country this journey started in

        :return: a two letter country code
        :rtype: str
        """
        return reverse_geocoder.search(
            (
                self.origin.gps.lat,
                self.origin.gps.lng
            )
        )[0]['cc']

    def is_in_place(self, place_name: str):
        """
        Get if a journey is entirely within the bounds of some place.
        Does this by rect rather than polygon so it isn't exact but mainly
        to be used to focus on a single city.

        :param place_name: An osmnx place name. For example "Dublin, Ireland"
            To see if the place name is valid try graph_from_place(place).
            Might be good to do that in here and throw an ex if it's not found
        """
        try:
            place_bounds = place_cache.get(place_name)
            return all([
                self.most_northern < place_bounds['north'],
                self.most_southern > place_bounds['south'],
                self.most_eastern < place_bounds['east'],
                self.most_western > place_bounds['west']
            ])
        except ValueError:
            return False
