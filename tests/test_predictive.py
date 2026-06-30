import unittest
from unittest.mock import MagicMock
from sre_daemon import MetricsCollector

class TestPredictiveScalingAdvisor(unittest.TestCase):
    def test_calculate_trend_line(self):
        collector = MetricsCollector(MagicMock())
        slope = collector.calculate_trend_line([10, 20, 30, 40, 50])
        self.assertEqual(slope, 10.0)

    def test_predict_scaling_advisor(self):
        collector = MetricsCollector(MagicMock())
        self.assertTrue(collector.predict_scaling_advisor())

    def test_trigger_predictive_warning(self):
        collector = MetricsCollector(MagicMock())
        collector.trigger_predictive_warning(90.0, 85.0)
