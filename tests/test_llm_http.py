"""Tests for the OpenAI-compatible LLM client against a fake local server.

No internet or API key needed: a tiny HTTP server on localhost pretends to be
the provider and returns success, "429 too many requests" and daily-limit
errors, so the retry and rate-limit logic can be checked.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from rtl_agent.llm.base import DailyLimitReached, RequestTooLarge
from rtl_agent.llm.openai_compat import OpenAICompatibleProvider


class FakeProvider(BaseHTTPRequestHandler):
    script: list = []          # list of (status, body, headers)
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeProvider.requests.append({"body": body, "auth": self.headers.get("Authorization")})
        status, payload, headers = FakeProvider.script.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def ok(text="hello", pt=100, ct=20):
    return (200, {"model": "m", "choices": [{"message": {"content": text}}],
                  "usage": {"prompt_tokens": pt, "completion_tokens": ct}}, {})


class TestOpenAICompatible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeProvider)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def cfg(self, **kw):
        base = dict(base_url=self.url, model="m", api_key_env="RTL_AGENT_TEST_KEY", temperature=0.1,
                    max_output_tokens=50, tokens_per_minute=None, tokens_per_day=None, request_timeout_s=10,
                    max_retries=3, extra_params={"reasoning_effort": "low"})
        base.update(kw)
        return SimpleNamespace(**base)

    def setUp(self):
        FakeProvider.script, FakeProvider.requests = [], []
        self.slept = []

    def provider(self, **kw):
        return OpenAICompatibleProvider(self.cfg(**kw), sleep=self.slept.append)

    def test_success_and_usage(self):
        FakeProvider.script = [ok("hi", 100, 20)]
        r = self.provider().complete([{"role": "user", "content": "x"}])
        self.assertEqual((r.text, r.total_tokens), ("hi", 120))
        self.assertEqual(FakeProvider.requests[0]["body"]["reasoning_effort"], "low")

    def test_unsupported_setting_is_dropped(self):
        """Switching to a model without reasoning_effort (e.g. a Llama model on Groq) must not stop the run."""
        FakeProvider.script = [(400, {"error": {"message": "`reasoning_effort` is not supported with this model",
                                                "type": "invalid_request_error", "param": "reasoning_effort"}}, {}),
                               ok("first"), ok("second")]
        p = self.provider()
        self.assertEqual(p.complete([{"role": "user", "content": "x"}]).text, "first")
        self.assertEqual(p.complete([{"role": "user", "content": "x"}]).text, "second")
        self.assertIn("reasoning_effort", FakeProvider.requests[0]["body"])
        self.assertNotIn("reasoning_effort", FakeProvider.requests[1]["body"])
        self.assertNotIn("reasoning_effort", FakeProvider.requests[2]["body"])    # remembered for later calls
        self.assertIn("temperature", FakeProvider.requests[1]["body"])            # other settings kept

    def test_other_400_is_still_an_error(self):
        from rtl_agent.llm.base import LLMError
        FakeProvider.script = [(400, {"error": {"message": "messages must not be empty"}}, {})]
        with self.assertRaises(LLMError):
            self.provider().complete([{"role": "user", "content": "x"}])

    def test_429_then_success(self):
        FakeProvider.script = [(429, {"error": {"message": "Rate limit reached: tokens per minute (TPM)"}},
                                {"retry-after": "7"}), ok()]
        r = self.provider().complete([{"role": "user", "content": "x"}])
        self.assertEqual(r.text, "hello")
        self.assertIn(7.0, self.slept)

    def test_daily_limit(self):
        FakeProvider.script = [(429, {"error": {"message": "Rate limit reached on tokens per day (TPD)"}}, {})]
        with self.assertRaises(DailyLimitReached):
            self.provider().complete([{"role": "user", "content": "x"}])

    def test_request_larger_than_minute_limit(self):
        with self.assertRaises(RequestTooLarge):
            self.provider(tokens_per_minute=60).complete([{"role": "user", "content": "x" * 400}])

    def test_waits_for_minute_window(self):
        clock = [1000.0]
        p = OpenAICompatibleProvider(self.cfg(tokens_per_minute=300), sleep=lambda s: (self.slept.append(s),
                                     clock.__setitem__(0, clock[0] + s)), clock=lambda: clock[0])
        FakeProvider.script = [ok(pt=150, ct=50), ok(pt=150, ct=50)]
        p.complete([{"role": "user", "content": "x" * 400}])
        p.complete([{"role": "user", "content": "x" * 400}])
        self.assertTrue(self.slept and self.slept[0] > 50)   # had to wait for the window to clear

    def test_daily_budget_is_checked_before_sending(self):
        FakeProvider.script = [ok(pt=900, ct=50)]
        p = self.provider(tokens_per_day=1000)
        p.complete([{"role": "user", "content": "x"}])
        with self.assertRaises(DailyLimitReached):
            p.complete([{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
