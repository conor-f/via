import json
import statistics
import os
from collections import defaultdict

import geopandas as gpd

from shapely.ops import cascaded_union
from shapely.geometry import MultiPoint, Point

import requests

import osmnx as ox
import networkx as nx

from bike import logger
from bike.utils import (
    window,
    get_combined_id,
    get_edge_colours
)
from bike.nearest_node import nearest_node
from bike.constants import (
    POLY_POINT_BUFFER,
    STAGED_DATA_DIR,
    SENT_DATA_DIR
)
from bike.settings import (
    EXCLUDE_METRES_BEGIN_AND_END,
    MINUTES_TO_CUT,
    TRANSPORT_TYPE,
    SUSPENSION,
    DELETE_ON_SEND,
    UPLOAD_URL,
    UPLOAD_EXCLUDE_TIME
)
from bike.models.frame import (
    Frame,
    Frames
)
from bike.edge_cache import get_edge_data


class Journey(Frames):

    def __init__(self, *args, **kwargs):
        """

        :kwarg is_culled: If the journey is culled or not
        :kwarg transport_type: What transport type being used, defaults
            to settings.TRANSPORT_TYPE
        :kwarg suspension: If using suspension or not, defaults
            to settings.SUSPENSION
        """
        kwargs.setdefault('child_class', Frame)
        super().__init__(*args, **kwargs)

        self.is_culled = kwargs.get('is_culled', False)
        self.is_sent = kwargs.get('is_sent', False)

        self.transport_type = kwargs.get('transport_type', TRANSPORT_TYPE)
        self.suspension = kwargs.get('suspension', SUSPENSION)

        self.network_type = 'bike'

        self.included_journeys = []

        self.last_gps = None

    def append(self, thing):
        frame = Frame.parse(thing)
        if frame.gps.lat is None and frame.gps.lng is None:
            if self.last_gps is None:
                return
            frame.gps = self.last_gps
        else:
            self.last_gps = frame.gps

        super().append(frame)

    @staticmethod
    def parse(objs):
        if isinstance(objs, Journey):
            return objs
        elif isinstance(objs, dict):
            return Journey(
                **objs
            )

        # TODO: do this from an object serialization
        raise NotImplementedError(
            'Can\'t parse journey from type %s' % (type(objs))
        )

    @staticmethod
    def from_file(filepath: str):
        """
        Given a file get a Journey object

        :param filepath: Path to a saved journey file
        :rtype: bike.models.journey.Journey
        """
        logger.debug('Loading journey from %s', filepath)
        with open(filepath, 'r') as journey_file:
            return Journey(
                **json.loads(journey_file.read())
            )

    def get_indirect_distance(self, n_seconds=10):
        """

        :param n_seconds: use the location every n seconds as if the
            location is calculated too frequently the distance
            travelled could be artificially inflated
        :rtype: float
        :return: distance travelled in metres
        """
        previous_used_frame = None
        distances = []

        for frame in self:
            if previous_used_frame is None:
                previous_used_frame = frame
            elif frame.time >= previous_used_frame.time + n_seconds:
                distances.append(
                    previous_used_frame.distance_from(
                        frame
                    )
                )
                previous_used_frame = frame

        return sum(distances)

    def get_avg_speed(self, n_seconds=30):
        """

        :param n_seconds: use the location every n seconds as if the
                        location is calculated too frequently the distance
                        travelled could be artificially inflated
        :rtype: float
        :return: avg speed in metres per second
        """
        return self.get_indirect_distance(n_seconds=n_seconds) / self.duration

    def serialize(self, minimal=False, exclude_time=False):
        data = {
            'uuid': str(self.uuid),
            'data': super().serialize(exclude_time=exclude_time),
            'transport_type': self.transport_type,
            'suspension': self.suspension,
            'is_culled': self.is_culled,
            'is_sent': self.is_sent
        }

        if minimal is False:
            data.update(
                {
                    'direct_distance': self.direct_distance,
                    'indirect_distance': {
                        1: self.get_indirect_distance(n_seconds=1),
                        5: self.get_indirect_distance(n_seconds=5),
                        10: self.get_indirect_distance(n_seconds=10),
                        30: self.get_indirect_distance(n_seconds=30)
                    },
                    'data_quality': self.data_quality,
                    'duration': self.duration,
                    'avg_speed': self.get_avg_speed()
                }
            )

        return data

    def cull_distance(self):
        """
        Remove the start and end of the journey, the start to remove
        will be until you are x metres away, the end to remove will be the
        last time you are x metres away from the destination
        """
        first_frame_away_idx = None
        last_frame_away_idx = None

        for idx, frame in enumerate(self):
            if frame.distance_from(self.origin) > EXCLUDE_METRES_BEGIN_AND_END:
                first_frame_away_idx = idx
                break

        for idx, frame in enumerate(reversed(self._data)):
            if frame.distance_from(self.destination) > EXCLUDE_METRES_BEGIN_AND_END:
                last_frame_away_idx = len(self) - idx
                break

        if any(
            [
                first_frame_away_idx is None,
                last_frame_away_idx is None
            ]
        ):
            raise ValueError(
                'Not a long enough journey to get any meaningful data from'
            )

        self._data = self[first_frame_away_idx:last_frame_away_idx]

    def cull_time(self, origin_time, destination_time):
        """
        Remove the start and end of a journey, the start to remove will be
        the first x minutes, the end to remove will be the last x minutes
        """
        if MINUTES_TO_CUT != 0:
            min_time = origin_time
            max_time = destination_time

            tmp_frames = Frames()
            for frame in self:
                if any([
                    frame.time < min_time + (60 * MINUTES_TO_CUT),
                    frame.time > max_time - (60 * MINUTES_TO_CUT)
                ]):
                    continue
                tmp_frames.append(frame)

            self._data = tmp_frames

    def cull(self):
        """
        Remove the start and end of the journey by time and distance for the
        sake of removing possible identifiable data
        """
        logger.debug('Culling %s', self.uuid)

        assert self.is_culled is False

        origin_time = self.origin.time
        destination_time = self.destination.time

        orig_frame_count = len(self)

        self.cull_distance()
        self.cull_time(origin_time, destination_time)

        new_frame_count = len(self)

        self.is_culled = True

        logger.info(
            'Culled %s kept %s %% frames',
            self.uuid,
            (new_frame_count / orig_frame_count) * 100
        )

    def save(self):
        logger.info('Saving %s', self.uuid)

        assert self.is_culled is False

        filepath = os.path.join(STAGED_DATA_DIR, str(self.uuid) + '.json')

        os.makedirs(
            os.path.dirname(filepath),
            exist_ok=True
        )

        with open(filepath, 'w') as journey_file:
            json.dump(
                self.serialize(minimal=True),
                journey_file
            )

    def post_send(self):
        if len(self.included_journeys) != 0:
            # since this is a mega journey, we should set all included to be saved
            for journey in self.included_journeys:
                journey.post_send()
        else:
            filepath = os.path.join(STAGED_DATA_DIR, str(self.uuid) + '.json')
            if DELETE_ON_SEND:
                os.remove(filepath)
                logger.debug('Deleted: %s', filepath)
            else:
                sent_filepath = os.path.join(SENT_DATA_DIR, str(self.uuid) + '.json')

                os.makedirs(
                    os.path.dirname(sent_filepath),
                    exist_ok=True
                )

                os.rename(
                    filepath,
                    sent_filepath
                )
                logger.debug('Moved %s -> %s', filepath, sent_filepath)

    def send(self):
        logger.info('Sending %s', self.uuid)

        if not self.is_culled:
            logger.info('Forcing a cull on send')
            self.cull()

        try:
            requests.post(
                url=UPLOAD_URL,
                json=self.serialize(exclude_time=UPLOAD_EXCLUDE_TIME)
            )
        except requests.exceptions.RequestException as ex:
            logger.error('Failed to send: %s', self.uuid)
            logger.error(ex)
        else:
            self.post_send()

    def plot_route(
        self,
        apply_condition_colour=False,
        use_closest_edge_from_base=False,
        colour_map_name='plasma_r',
        plot_kwargs={}
    ):
        """

        :kwarg apply_condition_colour: This is just a random colour for
            the moment as a jumping off point for when I come back to it
        :kwarg use_closest_from_base: For each point on the actual route,
            for each node use the closest node from the original base graph
            the route is being drawn on
        :kwarg colour_map_name:
        :kwarg plot_kwargs: A dict of kwargs to pass to whatever plot is
            being done
        """
        base = self.graph
        if apply_condition_colour:

            if use_closest_edge_from_base:
                edge_colours = get_edge_colours(
                    base,
                    colour_map_name,
                    edge_map=self.edge_quality_map
                )

            else:
                base.add_nodes_from(self.route_graph.nodes(data=True))
                base.add_edges_from(self.route_graph.edges(data=True))

                edge_colours = get_edge_colours(
                    base,
                    colour_map_name,
                    key_name='avg_road_quality'
                )

            ox.plot_graph(
                base,
                edge_color=edge_colours,
                **plot_kwargs
            )
        else:
            if use_closest_edge_from_base:
                route = self.closest_route
            else:
                base.add_nodes_from(self.route_graph.nodes(data=True))
                base.add_edges_from(self.route_graph.edges(data=True))
                route = self.route

            ox.plot_graph_route(
                base,
                route,
                **plot_kwargs
            )

    @property
    def edge_quality_map(self):
        """
        Get a map between edge_hash and road quality of the road. edge_map
        being edge_id and road quality being something that hasn't been
        defined yet TODO

        :rtype: dict
        """
        return {
            edge_id: {
                'avg': int(
                    statistics.mean(
                        [edge['avg_road_quality'] for edge in single_edge_data]
                    )
                ),
                'count': len(single_edge_data)
            } for edge_id, single_edge_data in self.edge_data.items()
        }

    @property
    def edge_data(self):
        """

        :rtype: dict
        :return: {edge_id: [{edge_data}, {edge_data}]}
        """

        # FIXME: This uses nodes to get edges, should use nearest edge
        # unless very close to a node. Factor in direction and all that

        data = defaultdict(list)
        bounding_graph = self.graph
        route_graph = self.route_graph

        for (our_origin, our_destination) in window(self, window_size=2):

            their_origin, their_destination = nearest_node.get(
                bounding_graph,
                [our_origin, our_destination]
            )

            data[get_combined_id(their_origin, their_destination)].append(
                get_edge_data(
                    route_graph,
                    our_origin.uuid,
                    our_destination.uuid
                )
            )

        return data

    @property
    def closest_route(self):
        """
        Get the route but instead of creating new nodes from our journey
        data use the closest nodes on the bounding graph

        :rtype: list
        :return: list of node ids along the path
        """
        # TODO: option to do this by edges
        # ...Maybe unless it's x metres within the radius of a node cause
        # it would probably get confused if too close and only doing by edge
        bounding_graph = self.graph

        return [
            nearest_node.get(
                bounding_graph,
                [frame]
            )[0] for frame in self
        ]

    @property
    def route(self):
        """
        Get a list of nodes representing the journey.

        Only useful once route_graph is merged with a more detailed graph
        """
        return list(self.route_graph._node.keys())

    @property
    def route_graph(self):
        """
        Get a graph of the journey without snapping to closest node / edge
        """
        graph = nx.Graph()

        combined_edge_data = defaultdict(list)

        for (origin, destination) in window(self, window_size=2):
            edge_id = get_combined_id(origin.uuid, destination.uuid)

            graph.add_node(
                origin.uuid,
                **{'x': origin.gps.lng, 'y': origin.gps.lat}
            )
            graph.add_node(
                destination.uuid,
                **{'x': destination.gps.lng, 'y': destination.gps.lat}
            )

            distance = origin.distance_from(destination)
            combined_edge_data[edge_id].append(
                {
                    'origin': origin,
                    'destination': destination,
                    'distance': distance,
                    'road_quality': origin.road_quality,
                    # TODO: other bits, speed / elevation maybe?
                }
            )

        merged_edge_data = {}
        for shared_id, values in combined_edge_data.items():
            merged_edge_data[shared_id] = {
                'origin': values[0]['origin'],
                'destination': values[0]['destination'],
                'distance': values[0]['distance'],
                'avg_road_quality': statistics.mean([val['road_quality'] for val in values]),
                'max_road_quality': max([val['road_quality'] for val in values])
            }

        for shared_id, values in merged_edge_data.items():
            graph.add_edge(
                values['origin'].uuid,
                values['destination'].uuid,
                length=values['distance'],
                avg_road_quality=values['avg_road_quality'],
                max_road_quality=values['max_road_quality']
            )

        return graph

    @property
    def bounding_graph(self):
        logger.debug(
            'Plotting bounding graph (n,s,e,w) (%s, %s, %s, %s)',
            self.most_northern,
            self.most_southern,
            self.most_eastern,
            self.most_western
        )
        return ox.graph_from_bbox(
            self.most_northern,
            self.most_southern,
            self.most_eastern,
            self.most_western,
            network_type=self.network_type,
            simplify=True
        )

    @property
    def graph(self):
        """
        Get a graph of the journey but excluding nodes far away from the route

        :rtype: networkx.classes.multidigraph.MultiDiGraph
        """
        points = MultiPoint(
            [
                Point(frame.gps.lng, frame.gps.lat) for frame in self
            ]
        )
        buf = points.buffer(POLY_POINT_BUFFER, cap_style=3)
        boundary = gpd.GeoSeries(cascaded_union(buf))

        return ox.graph_from_polygon(
            boundary.geometry[0],
            network_type=self.network_type,
            simplify=True
        )
