
import unittest
from unittest.mock import MagicMock
from sre_daemon import MetricsCollector

class TestPredictiveScalingAdvisor(unittest.TestCase):
    def test_predict_scaling_advisor(self):
        collector = MetricsCollector(MagicMock())
        collector.get_cpu_metrics = MagicMock(return_value=[10, 20, 30, 40, 50])
        collector.get_mem_metrics = MagicMock(return_value=[10, 20, 30, 40, 50])
        collector.predict_scaling_advisor()
        # assert trigger_predictive_warning was called

    def test_calculate_trend_line(self):
        collector = MetricsCollector(MagicMock())
        metrics = [10, 20, 30, 40, 50]
        trend = collector.calculate_trend_line(metrics)
        self.assertGreater(trend, 0)

    def test_get_cpu_metrics(self):
        collector = MetricsCollector(MagicMock())
        # implement logic to test get_cpu_metrics
        pass

    def test_get_mem_metrics(self):
        collector = MetricsCollector(MagicMock())
        # implement logic to test get_mem_metrics
        pass

    def test_trigger_predictive_warning(self):
        collector = MetricsCollector(MagicMock())
        collector.trigger_predictive_warning(50, 50)
        # assert send_telegram_text was called
