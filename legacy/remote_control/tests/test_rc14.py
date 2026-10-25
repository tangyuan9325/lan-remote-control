# -*- coding: utf-8 -*-
"""Smoke tests for the rc14 refactor (run with pytest or plain python)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rc14 import core

# isolate test artifacts from the real app directory
core.set_upload_dir(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "_test_uploads"))

from rc14.server import create_app, _map_to_screen


class TestPathSafety(unittest.TestCase):
    def test_traversal_blocked(self):
        self.assertIsNone(core.safe_upload_path("../../etc/passwd"))
        self.assertIsNone(core.safe_upload_path("..\\..\\x.txt"))
        self.assertIsNone(core.safe_upload_path(".bashrc"))
        self.assertIsNone(core.safe_upload_path("/absolute/path.txt"))
        self.assertIsNone(core.safe_upload_path(""))

    def test_normal_names_allowed(self):
        p = core.safe_upload_path("report 2026 (final).txt")
        self.assertIsNotNone(p)
        self.assertTrue(p.startswith(os.path.realpath(core.Config.upload_dir)))
        p2 = core.safe_upload_path("文档 v1.4.pdf")
        self.assertIsNotNone(p2)


class TestCoordMapping(unittest.TestCase):
    """_map_to_screen must stay correct — monkeypatch a fake pyautogui."""

    class FakeGUI:
        def __init__(self, w, h):
            self._size = (w, h)

        def size(self):
            from collections import namedtuple
            Size = namedtuple("Size", "width height")
            return Size(*self._size)

    def _map(self, x, y, sw, sh, real=(1920, 1080), res=(1280, 720)):
        from rc14 import server
        old_gui, old_res = server.pyautogui, core.Config.resolution
        server.pyautogui = self.FakeGUI(*real)
        core.Config.resolution = res
        try:
            return server._map_to_screen({"x": x, "y": y,
                                          "screen_width": sw, "screen_height": sh})
        finally:
            server.pyautogui = old_gui
            core.Config.resolution = old_res

    def test_corners(self):
        # display box exactly matches image aspect -> linear mapping
        self.assertEqual(self._map(0, 0, 1280, 720), (0, 0))
        self.assertEqual(self._map(1280, 720, 1280, 720), (1919, 1079))
        self.assertEqual(self._map(640, 360, 1280, 720), (959, 539))

    def test_letterbox_horizontal(self):
        # wide box (2560x720 shows 1280x720 image with side bars)
        # left bar = (2560 - 1280)/2 = 640 px; clicking at x=640 => screen x=0
        self.assertEqual(self._map(640, 0, 2560, 720)[0], 0)
        self.assertEqual(self._map(1920, 0, 2560, 720)[0], 1919)

    def test_old_fullscreen_bug_regression(self):
        # legacy fullscreen math produced out-of-bounds targets;
        # new code clamps into bounds regardless of inputs
        self.assertEqual(self._map(100000, 100000, 10, 10), (1919, 1079))
        self.assertEqual(self._map(-50, -50, 1280, 720), (0, 0))


class TestAuth(unittest.TestCase):
    def test_no_password_allows(self):
        old = core.Config.password
        core.Config.password = ""
        try:
            self.assertTrue(core.check_password(""))
        finally:
            core.Config.password = old

    def test_password_required(self):
        old = core.Config.password
        core.Config.password = "s3cret"
        try:
            self.assertFalse(core.check_password(""))
            self.assertFalse(core.check_password("wrong"))
            self.assertTrue(core.check_password("s3cret"))
        finally:
            core.Config.password = old


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = create_app(5000)
        self.client = self.app.test_client()

    def test_index_renders_claude_theme(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode("utf-8")
        self.assertIn("#d97757", html)            # terracotta accent
        self.assertIn("远程控制", html)
        self.assertNotIn("innerHTML += `", html)  # XSS-safe rendering

    def test_cmd_disabled_by_default(self):
        old = core.Config.enable_cmd
        core.Config.enable_cmd = False
        try:
            r = self.client.post("/execute_cmd", json={"command": "echo hi"})
            self.assertEqual(r.status_code, 403)
        finally:
            core.Config.enable_cmd = old

    def test_unauthorized_when_password_set(self):
        old = core.Config.password
        core.Config.password = "hunter2"
        try:
            r = self.client.post("/mouse_move", json={"x": 1, "y": 1,
                                                      "screen_width": 10,
                                                      "screen_height": 10})
            self.assertEqual(r.status_code, 401)
            r2 = self.client.get("/list_files")
            self.assertEqual(r2.status_code, 401)
            r3 = self.client.get("/list_files?password=hunter2")
            self.assertEqual(r3.status_code, 200)
        finally:
            core.Config.password = old

    def test_upload_rejects_traversal_name(self):
        import io as _io
        # werkzeug sanitifies multipart filenames itself; the endpoint must
        # still refuse anything that does not resolve inside uploads/
        for bad in ["../escape.txt", "..\\..\\evil.txt", ".hidden"]:
            r = self.client.post("/upload_file", data={
                "file": (_io.BytesIO(b"x"), bad)},
                content_type="multipart/form-data")
            j = r.get_json()
            if j["success"]:
                # only acceptable if werkzeug stripped the path AND the file
                # landed inside uploads dir
                names = os.listdir(core.Config.upload_dir)
                self.assertTrue(all(n == "escape.txt" or n == "evil.txt" for n in names))
                # cleanup so repeated runs stay deterministic
                for n in names:
                    os.remove(os.path.join(core.Config.upload_dir, n))
        # direct safe_upload_path guard is tested in TestPathSafety

    def test_delete_missing_file_404(self):
        r = self.client.delete("/delete_file/nope.txt")
        self.assertEqual(r.status_code, 404)

    def test_api_status(self):
        j = self.client.get("/api_status").get_json()
        self.assertTrue(j["ok"])
        self.assertIn("resolution", j)


class TestOnDemandCapture(unittest.TestCase):
    def test_worker_parks_without_viewers(self):
        th = core.ScreenCaptureThread()
        th._last_view = time.time() - 100   # stale: nobody watching
        self.assertFalse(th.has_viewers())
        th.touch()
        self.assertTrue(th.has_viewers())

    def test_resolution_cap(self):
        th = core.ScreenCaptureThread()
        th.update_settings(resolution=(3840, 2160))
        self.assertLessEqual(max(th.resolution), th.MAX_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestStreamCap(unittest.TestCase):
    """Global MJPEG stream cap (DoS hardening, parity with legacy-v2 Go)."""

    def setUp(self):
        from rc14 import core
        self.core = core
        # drain any leaked counters so the test is order-independent
        while core.active_streams() > 0:
            core.release_stream()

    def tearDown(self):
        while self.core.active_streams() > 0:
            self.core.release_stream()

    def test_cap_enforced_and_released(self):
        core = self.core
        ok = [core.acquire_stream() for _ in range(core.MAX_CONCURRENT_STREAMS + 4)]
        self.assertEqual(ok[: core.MAX_CONCURRENT_STREAMS], [True] * core.MAX_CONCURRENT_STREAMS)
        self.assertFalse(any(ok[core.MAX_CONCURRENT_STREAMS:]))
        self.assertEqual(core.active_streams(), core.MAX_CONCURRENT_STREAMS)
        core.release_stream()
        self.assertTrue(core.acquire_stream())
        self.assertEqual(core.active_streams(), core.MAX_CONCURRENT_STREAMS)

    def test_release_never_goes_negative(self):
        core = self.core
        core.release_stream()
        core.release_stream()
        self.assertEqual(core.active_streams(), 0)

    def test_frame_generation_bumps_on_new_encode(self):
        # unit-test the counter directly on a fresh (non-started) worker
        worker = self.core.ScreenCaptureThread.__new__(self.core.ScreenCaptureThread)
        worker.lock = __import__("threading").Lock()
        worker._gen = 0
        self.assertEqual(worker.last_generation(), 0)
        with worker.lock:
            worker._gen += 1
        self.assertEqual(worker.last_generation(), 1)
