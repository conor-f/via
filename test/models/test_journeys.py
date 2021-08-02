import json

from mock import patch

from unittest import TestCase

from bike.models.journey import Journey
from bike.models.journeys import Journeys
from bike.models.frame import Frame


class JourneysTest(TestCase):

    def setUp(self):
        with open('test/resources/dublin_route.json') as json_file:
            self.test_data = json.load(json_file)

        self.test_journey = Journey()
        self.test_journey.save()
        print(self.test_journey.uuid)
        for d in self.test_data:
            self.test_journey.append(
                Frame(
                    d['time'],
                    {'lat': d['lat'], 'lng': d['lng']},
                    ()  # acceleration, don't really care at the mo
                )
            )

        self.test_journeys_single = Journeys()
        self.test_journeys_single.append(self.test_journey)

        with open('test/resources/dublin_route.json') as json_file:
            self.test_data = json.load(json_file)

        self.test_journey = Journey()
        self.test_journey.save()
        print(self.test_journey.uuid)
        for d in self.test_data:
            self.test_journey.append(
                Frame(
                    d['time'],
                    {'lat': d['lat'], 'lng': d['lng']},
                    ()  # acceleration, don't really care at the mo
                )
            )

        self.test_journeys = Journeys()
        self.test_journeys.append(self.test_journey)
        self.test_journeys.append(self.test_journey)

    def test_most_whatever(self):
        self.assertEqual(
            self.test_journeys.most_northern,
            53.3588887
        )
        self.assertEqual(
            self.test_journeys.most_southern,
            53.332599
        )
        self.assertEqual(
            self.test_journeys.most_eastern,
            -6.2523619
        )
        self.assertEqual(
            self.test_journeys.most_western,
            -6.2661022
        )

    def test_get_mega_journeys(self):
        journeys = self.test_journeys.get_mega_journeys()
        self.assertEqual(
            list(journeys.keys()),
            ['mountain_True']
        )
        self.assertEqual(
            len(journeys['mountain_True']._data),
            (len(self.test_journey._data) * 2)
        )
        self.assertEqual(
            journeys['mountain_True'].transport_type,
            'mountain'
        )
        self.assertEqual(
            journeys['mountain_True'].suspension,
            True
        )

    @patch('bike.models.journeys.UPLOAD_PARTIAL', True)
    @patch('bike.models.partial.PARTIAL_SPLIT_INTO', 20)
    @patch('bike.models.journeys.MIN_JOURNEYS_UPLOAD_PARTIALS', 1)
    @patch('bike.models.partial.Partial.send')
    @patch('bike.models.journeys.Journeys._send_partials')
    def test_send_partials_called(self, mock_send_partials, mock_partial_send):
        self.test_journeys.send()
        self.assertTrue(mock_send_partials.called)

    @patch('bike.models.journeys.UPLOAD_PARTIAL', True)
    @patch('bike.models.partial.PARTIAL_SPLIT_INTO', 20)
    @patch('bike.models.journeys.MIN_JOURNEYS_UPLOAD_PARTIALS', 1)
    @patch('bike.models.partial.Partial.send')
    def test_send_partial_enough_journeys(self, mock_partial_send):
        self.test_journeys_single._send_partials()
        self.assertTrue(mock_partial_send.called)

    @patch('bike.models.journeys.UPLOAD_PARTIAL', True)
    @patch('bike.models.partial.PARTIAL_SPLIT_INTO', 20)
    @patch('bike.models.journeys.MIN_JOURNEYS_UPLOAD_PARTIALS', 2)
    @patch('bike.models.partial.Partial.send')
    def test_send_partial_not_enough_journeys(self, mock_partial_send):
        with self.assertRaises(Exception):
            self.test_journeys_single._send_partials()

    def test_edge_quality_map(self):
        self.assertEqual(
            len(self.test_journeys_single.edge_quality_map),
            77
        )
