import pytest

from cyrene.observability.powerpoint_performance_benchmark import (
    PowerPointBenchmarkConfig,
    render_markdown,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_powerpoint_benchmark_reuses_revision_across_model_free_rounds():
    revision = 0
    next_slide = 300
    slides = {}
    calls = []

    async def caller(method, args):
        nonlocal revision, next_slide
        calls.append((method, dict(args)))
        if method == "ppt.get_context":
            return {"status": "success", "revision": revision}
        if method == "ppt.create_slide":
            assert args["expectedRevision"] == revision
            next_slide += 1
            slide_id = str(next_slide)
            slides[slide_id] = args["slideSpec"]["title"]
            revision += 1
            return {"status": "applied", "revision": revision, "slideId": slide_id}
        if method == "ppt.list_shapes":
            return {
                "status": "success",
                "revision": revision,
                "slideId": args["slideId"],
                "shapes": [{"id": "2", "ref": "title", "text": slides[args["slideId"]]}],
            }
        if method == "ppt.apply_batch":
            assert args["expectedRevision"] == revision
            slides[args["slideId"]] = args["operations"][0]["text"]
            revision += 1
            return {"status": "applied", "revision": revision}
        if method == "ppt.read_text":
            return {
                "status": "success",
                "revision": revision,
                "text": [{"ref": "title", "text": slides[args["slideId"]]}],
            }
        if method == "ppt.delete_slide":
            assert args["expectedRevision"] == revision
            slides.pop(args["slideId"])
            revision += 1
            return {"status": "applied", "revision": revision}
        raise AssertionError(method)

    report = await run_benchmark(
        config=PowerPointBenchmarkConfig(
            rounds=3,
            strategies=("stage", "element"),
            session_id="session-1",
        ),
        caller=caller,
    )

    assert report["environment"]["real_llm_calls"] is False
    assert report["environment"]["continuous_session"] is True
    assert report["quality"]["preserved"] is True
    assert [item["completed_rounds"] for item in report["results"]] == [3, 3]
    assert all(item["steps"]["create"]["p50_ms"] >= 0 for item in report["results"])
    assert len([item for item in calls if item[0] == "ppt.create_slide"]) == 6
    assert not slides
    assert "| Strategy |" in render_markdown(report)
