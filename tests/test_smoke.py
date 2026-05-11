import os
import unittest

os.environ.setdefault("AUTO_ENRICH_COMPANIES", "0")

from fastapi.testclient import TestClient

from company_app.main import app


class SmokeTests(unittest.TestCase):
    def test_healthz_ok(self):
        client = TestClient(app)

        response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
