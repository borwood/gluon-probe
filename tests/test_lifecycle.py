"""Lifecycle tests: bounded collection and cooperative cancellation.

The executor protocol is synchronous. Where the TypeScript test relied on an
awaitable that never settles, the Python test uses a pull that outlasts the deadline:
the core cannot preempt it, but must reject its late output and close the iterator.
"""
from __future__ import annotations

import threading
import time
import unittest

from gluon import CancelSignal, DiscoveryError, create_discovery
from tests.support.billing import EX, billing_configuration, named

REQUEST = {"scope": "class-model", "type": "surface", "text": "billing"}
ROW = {"entity": named(EX + "BillingCli"), "label": {"type": "literal", "value": "Billing CLI"}}


class CountingIterator:
    def __init__(self, on_next, on_close=None):
        self.pulls = 0
        self.closes = 0
        self._on_next = on_next
        self._on_close = on_close

    def __iter__(self):
        return self

    def __next__(self):
        self.pulls += 1
        return self._on_next(self.pulls)

    def close(self):
        self.closes += 1
        if self._on_close:
            self._on_close()


class LifecycleTests(unittest.TestCase):
    def assertCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(DiscoveryError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_pulls_stop_at_cap_plus_one_and_stream_is_closed(self):
        """Pulls stop at cap+1 and the stream is closed."""
        seen = {}
        iterator = CountingIterator(lambda n: ROW)
        def execute(request):
            seen["signal"] = request.signal
            return iterator
        result = create_discovery(billing_configuration(), execute).probe({**REQUEST, "limit": 2})
        self.assertEqual(iterator.pulls, 3)
        self.assertEqual(iterator.closes, 1)
        self.assertTrue(seen["signal"].is_set())
        self.assertEqual(len(result["candidates"]), 1)
        self.assertTrue(result["completeness"]["truncated"])

    def test_slow_pull_exceeds_deadline_even_when_close_fails(self):
        """Late output after the deadline is rejected; a failing close does not hide it."""
        seen = {}
        def slow(n):
            time.sleep(0.03)
            return ROW
        def broken_close():
            raise RuntimeError("host cleanup failure")
        iterator = CountingIterator(slow, broken_close)
        def execute(request):
            seen["signal"] = request.signal
            return iterator
        self.assertCode("DEADLINE_EXCEEDED", create_discovery(billing_configuration(), execute).probe, {**REQUEST, "deadline_ms": 15})
        self.assertTrue(seen["signal"].is_set())
        self.assertEqual(iterator.closes, 1)
        self.assertEqual(iterator.pulls, 1, "no further pulls after the deadline")

    def test_timed_out_setup_closes_late_iterator_without_pulling(self):
        """A late iterator is closed without being pulled."""
        def never_pull(n):
            raise AssertionError("late source should not be pulled")
        iterator = CountingIterator(never_pull)
        def execute(request):
            time.sleep(0.03)
            return iterator
        self.assertCode("DEADLINE_EXCEEDED", create_discovery(billing_configuration(), execute).probe, {**REQUEST, "deadline_ms": 15})
        self.assertEqual(iterator.closes, 1)
        self.assertEqual(iterator.pulls, 0)

    def test_cancellation_before_and_during_the_call(self):
        """Already-cancelled calls never reach the source; active cancellation discards partial rows."""
        calls = []
        cancelled = CancelSignal()
        cancelled.set()
        def execute(request):
            calls.append(request)
            raise RuntimeError()
        self.assertCode("CANCELLED", create_discovery(billing_configuration(), execute).probe, {**REQUEST, "signal": cancelled})
        self.assertEqual(calls, [])

        active = CancelSignal()
        seen = {}
        def pull(n):
            if n == 1:
                return ROW
            active.set()  # another party cancels while the source is still producing
            return ROW
        iterator = CountingIterator(pull)
        def second(request):
            seen["signal"] = request.signal
            return iterator
        self.assertCode("CANCELLED", create_discovery(billing_configuration(), second).probe, {**REQUEST, "signal": active})
        self.assertTrue(seen["signal"].is_set(), "the executor observes the caller's cancellation through its own signal")
        self.assertEqual(iterator.closes, 1)

        threaded = CancelSignal()
        def slow_pull(n):
            time.sleep(0.03)
            return ROW
        iterator2 = CountingIterator(slow_pull)
        threading.Timer(0.005, threaded.set).start()
        error = self.assertCode("CANCELLED", create_discovery(billing_configuration(), lambda r: iterator2).probe, {**REQUEST, "signal": threaded})
        self.assertNotIn("private", str(error))

    def test_errors_after_partial_output_and_synchronous_overrun(self):
        """Errors after partial output and synchronous overrun cannot report success."""
        def error_source(request):
            yield ROW
            raise RuntimeError("sensitive failure after first result")
        caught = self.assertCode("SOURCE_ERROR", create_discovery(billing_configuration(), error_source).probe, REQUEST)
        self.assertNotIn("sensitive", str(caught))
        self.assertIsNone(caught.__cause__)

        def blocking(request):
            until = time.monotonic() + 0.02
            while time.monotonic() < until:
                pass  # a synchronous host that cannot be preempted
            return iter([ROW])
        self.assertCode("DEADLINE_EXCEEDED", create_discovery(billing_configuration(), blocking).probe, {**REQUEST, "deadline_ms": 1})

    def test_executor_cannot_speak_the_core_diagnostic_language(self):
        """A host raising DiscoveryError('CANCELLED') itself is still a source error, not a caller cancellation."""
        def impostor(request):
            raise DiscoveryError("UNKNOWN_SCOPE", "x", "host detail")
        caught = self.assertCode("SOURCE_ERROR", create_discovery(billing_configuration(), impostor).probe, REQUEST)
        self.assertNotIn("host detail", str(caught))


if __name__ == "__main__":
    unittest.main()
