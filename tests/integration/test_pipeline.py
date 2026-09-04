# =============================================================
# OWNER: AARON
# =============================================================
import pytest
import numpy as np
import os
import time


class TestEndToEndPipeline:
    """
    Integration tests for the full pipeline.
    Requires running Docker Compose environment.
    """

    API_URL = os.getenv("API_URL", "http://localhost:8000")

    def test_health_endpoint(self):
        import requests
        response = requests.get(f"{self.API_URL}/health", timeout=10)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_process_and_retrieve_results(self):
        import requests

        # Submit scene
        response = requests.post(
            f"{self.API_URL}/process",
            json={"scene_id": "TEST_INTEGRATION_001"},
            timeout=10
        )
        assert response.status_code == 200
        assert response.json()["status"] in ["queued", "cached"]

        # Poll for results
        max_wait = 60
        start = time.time()

        while time.time() - start < max_wait:
            status_resp = requests.get(
                f"{self.API_URL}/status/TEST_INTEGRATION_001",
                timeout=10
            )
            status = status_resp.json()["status"]

            if status == "completed":
                break
            time.sleep(2)

        assert status == "completed", f"Timeout waiting for results"

    def test_metrics_endpoint(self):
        import requests
        response = requests.get(f"{self.API_URL}/metrics", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert "total_scenes_processed" in data
        assert "avg_processing_time_seconds" in data
