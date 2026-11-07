"""Tests for app.detect — the render/call-model/parse/annotate pipeline. No network,
no real model calls; StubModelClient/FailingModelClient stand in for the model."""

from __future__ import annotations

import importlib
import inspect

import pytest

import app.detect as detect_module
from app.detect import MAX_PAGES, PageResult, detect, rebuild_pages

from .helpers import FailingModelClient, StubModelClient, make_pdf_bytes, ok_response


class TestMaxPagesEnvVar:
    def test_falls_back_to_25_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEMO_APP_MAX_PAGES", raising=False)
        reloaded = importlib.reload(detect_module)
        try:
            assert reloaded.MAX_PAGES == 25
        finally:
            importlib.reload(detect_module)

    def test_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_APP_MAX_PAGES", "3")
        reloaded = importlib.reload(detect_module)
        try:
            assert reloaded.MAX_PAGES == 3
        finally:
            monkeypatch.delenv("DEMO_APP_MAX_PAGES", raising=False)
            importlib.reload(detect_module)


class TestThreadsEnvVar:
    def test_falls_back_to_4_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEMO_APP_THREADS", raising=False)
        reloaded = importlib.reload(detect_module)
        try:
            assert reloaded.DEFAULT_THREADS == 4
        finally:
            importlib.reload(detect_module)

    def test_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEMO_APP_THREADS", "8")
        reloaded = importlib.reload(detect_module)
        try:
            assert reloaded.DEFAULT_THREADS == 8
            assert inspect.signature(reloaded.detect).parameters["threads"].default == 8
        finally:
            monkeypatch.delenv("DEMO_APP_THREADS", raising=False)
            importlib.reload(detect_module)


class TestDetect:
    def test_returns_one_lightweight_metadata_entry_per_page(self) -> None:
        pages_meta = detect(
            make_pdf_bytes(2), client=StubModelClient(), model="test/model", max_px=200
        )

        assert len(pages_meta) == 2
        for meta in pages_meta:
            assert set(meta) == {
                "page",
                "status",
                "error",
                "dropped",
                "complete",
                "boxes",
                "elapsed_seconds",
            }
            assert meta["status"] == "ok"
            assert len(meta["boxes"]) == 1
            assert meta["elapsed_seconds"] >= 0

    def test_metadata_never_carries_image_bytes(self) -> None:
        # The whole point of streaming pages out via on_page rather than accumulating
        # them: detect()'s own return value must stay small regardless of page count,
        # or a large run's memory scales with page count again. See detect()'s docstring.
        pages_meta = detect(
            make_pdf_bytes(3), client=StubModelClient(), model="test/model", max_px=200
        )
        for meta in pages_meta:
            assert "original_png" not in meta
            assert "label_layers" not in meta

    def test_on_page_receives_the_full_result_including_images(self) -> None:
        streamed: list[PageResult] = []
        detect(
            make_pdf_bytes(1),
            client=StubModelClient(),
            model="test/model",
            max_px=200,
            on_page=streamed.append,
        )

        assert len(streamed) == 1
        assert streamed[0].original_png
        assert streamed[0].label_layers.keys() == {"elevation"}
        assert streamed[0].colors.keys() == {"elevation"}

    def test_pages_are_returned_sorted_regardless_of_completion_order(self) -> None:
        pages_meta = detect(
            make_pdf_bytes(5),
            client=StubModelClient(),
            model="test/model",
            max_px=200,
            threads=5,
        )

        assert [meta["page"] for meta in pages_meta] == [1, 2, 3, 4, 5]

    def test_a_page_with_no_boxes_has_no_overlay_layers(self) -> None:
        streamed: list[PageResult] = []
        detect(
            make_pdf_bytes(1),
            client=StubModelClient(respond=lambda: ok_response("[]")),
            model="test/model",
            max_px=200,
            on_page=streamed.append,
        )

        assert streamed[0].boxes == []
        assert streamed[0].label_layers == {}
        assert streamed[0].colors == {}

    def test_a_failed_page_is_reported_not_raised(self) -> None:
        pages_meta = detect(
            make_pdf_bytes(1),
            client=FailingModelClient("network exploded"),
            model="test/model",
            max_px=200,
        )

        assert pages_meta[0]["status"] == "failed"
        assert "network exploded" in pages_meta[0]["error"]
        assert pages_meta[0]["boxes"] == []

    def test_rejects_a_pdf_over_the_page_cap(self) -> None:
        with pytest.raises(ValueError, match=str(MAX_PAGES)):
            detect(
                make_pdf_bytes(MAX_PAGES + 1),
                client=StubModelClient(),
                model="test/model",
                max_px=200,
            )

    def test_rejects_a_pdf_with_no_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # PyMuPDF itself refuses to save a real zero-page PDF ("cannot save with zero
        # pages"), so there's no fixture file to build for this — fake the open() call
        # instead of chasing an unconstructable one.
        class _EmptyDocument:
            page_count = 0

            def __enter__(self) -> "_EmptyDocument":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        monkeypatch.setattr("app.detect.pymupdf.open", lambda **_kwargs: _EmptyDocument())

        with pytest.raises(ValueError, match="no pages"):
            detect(b"irrelevant", client=StubModelClient(), model="test/model", max_px=200)


class TestRebuildPages:
    def test_reproduces_the_same_images_as_the_live_run(self) -> None:
        # rebuild_pages() exists so a history entry can be replayed without spending on
        # a fresh model call — its whole value proposition is that it's indistinguishable
        # from what was actually served the first time.
        pdf_bytes = make_pdf_bytes(1)
        streamed: list[PageResult] = []
        detect(
            pdf_bytes,
            client=StubModelClient(),
            model="test/model",
            max_px=200,
            on_page=streamed.append,
        )
        live = streamed[0]

        pages_meta = [
            {
                "page": live.page,
                "status": live.status,
                "error": live.error,
                "dropped": live.dropped,
                "complete": live.complete,
                "boxes": live.boxes,
                "elapsed_seconds": live.elapsed_seconds,
            }
        ]
        rebuilt = rebuild_pages(pdf_bytes, 200, pages_meta)

        assert rebuilt[0].original_png == live.original_png
        assert rebuilt[0].label_layers.keys() == live.label_layers.keys()
        assert rebuilt[0].colors == live.colors
        # Carried through from the stored meta, not recomputed — rebuilding is a local
        # re-render with no model call, so its own timing isn't the number worth showing.
        assert rebuilt[0].elapsed_seconds == live.elapsed_seconds

    def test_a_failed_pages_original_image_is_still_rebuilt(self) -> None:
        # Rendering doesn't depend on the model call having succeeded — only the
        # (absent) boxes do.
        pages_meta = [
            {
                "page": 1,
                "status": "failed",
                "error": "boom",
                "dropped": 0,
                "complete": False,
                "boxes": [],
            }
        ]

        rebuilt = rebuild_pages(make_pdf_bytes(1), 200, pages_meta)

        assert rebuilt[0].status == "failed"
        assert rebuilt[0].original_png
        assert rebuilt[0].label_layers == {}

    def test_pages_are_returned_sorted_regardless_of_completion_order(self) -> None:
        pages_meta = [
            {"page": p, "status": "ok", "error": None, "dropped": 0, "complete": True, "boxes": []}
            for p in [3, 1, 2]
        ]

        rebuilt = rebuild_pages(make_pdf_bytes(3), 200, pages_meta, threads=3)

        assert [result.page for result in rebuilt] == [1, 2, 3]
