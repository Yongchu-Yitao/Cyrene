import asyncio
import io
import json
import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent.plugin import PluginContext
from conftest import frontend_module_source


class FakeWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, **_kwargs):
        self.closed = True


@pytest.mark.asyncio
async def test_office_bridge_serializes_parallel_requests_per_session():
    from agent.plugin.plugin_impl.cyrene_office.protocol import expected_handshake
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeService

    service = OfficeBridgeService()
    socket = FakeWebSocket()
    session = await service.register(socket, {
        "host": "powerpoint",
        "document": {"name": "demo.pptx"},
        **expected_handshake(),
    })

    first = asyncio.create_task(service.call(session.session_id, "ppt.apply_batch", {"expectedRevision": 0}))
    await asyncio.sleep(0)
    second = asyncio.create_task(service.call(session.session_id, "ppt.delete_slide", {"expectedRevision": 0}))
    await asyncio.sleep(0)
    assert len(socket.sent) == 1

    first_request = socket.sent[0]
    service.receive(session, {
        "type": "response",
        "id": first_request["id"],
        "ok": True,
        "result": {"status": "applied", "revision": 1},
    })
    await first
    await asyncio.sleep(0)
    assert len(socket.sent) == 2

    second_request = socket.sent[1]
    service.receive(session, {
        "type": "response",
        "id": second_request["id"],
        "ok": True,
        "result": {"status": "applied", "revision": 2},
    })
    assert (await second)["revision"] == 2


@pytest.mark.asyncio
async def test_office_bridge_routes_one_live_session_and_tracks_revision():
    from agent.plugin.plugin_impl.cyrene_office.protocol import expected_handshake
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeService

    service = OfficeBridgeService()
    socket = FakeWebSocket()
    session = await service.register(socket, {
        "host": "powerpoint",
        "revision": 4,
        "document": {"name": "demo.pptx"},
        "capabilities": {"powerPointApi18": True},
        **expected_handshake(),
    })

    call = asyncio.create_task(service.call(None, "ppt.get_context", {}))
    await asyncio.sleep(0)
    request = socket.sent[0]
    service.receive(session, {
        "type": "response",
        "id": request["id"],
        "ok": True,
        "result": {"status": "success", "revision": 5},
    })

    result = await call
    assert result["status"] == "success"
    assert result["revision"] == 5
    assert result["agentKit"]["compatible"] is True
    assert service.list_sessions()[0]["revision"] == 5
    await service.close()
    assert socket.closed is True


@pytest.mark.asyncio
async def test_office_bridge_publishes_live_connection_selection_and_revision(monkeypatch):
    from cyrene.observability import debug
    from agent.plugin.plugin_impl.cyrene_office.protocol import expected_handshake
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeService

    events = []

    async def capture(event, session_id=""):
        events.append(dict(event))

    monkeypatch.setattr(debug, "publish_event", capture)
    monkeypatch.setattr(debug, "publish_event_sync", lambda event, session_id="": events.append(dict(event)))
    service = OfficeBridgeService()
    socket = FakeWebSocket()
    session = await service.register(socket, {
        "host": "powerpoint",
        "document": {"name": "live.pptx"},
        **expected_handshake(),
    })
    service.receive(session, {
        "type": "event",
        "event": "selection_changed",
        "revision": 0,
        "selection": {"slideIds": ["256"], "shapes": [{"id": "2"}]},
    })
    service.receive(session, {
        "type": "event",
        "event": "revision_changed",
        "revision": 1,
    })
    await asyncio.sleep(0)

    assert [event["event"] for event in events] == [
        "connected", "selection_changed", "revision_changed",
    ]
    assert events[-1]["session"]["selection"]["slideIds"] == ["256"]
    assert events[-1]["session"]["revision"] == 1
    assert events[-1]["sessions"][0]["sessionId"] == session.session_id


@pytest.mark.asyncio
async def test_office_bridge_blocks_writes_from_an_outdated_addin_but_allows_context():
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeError, OfficeBridgeService

    service = OfficeBridgeService()
    socket = FakeWebSocket()
    session = await service.register(socket, {"host": "powerpoint", "revision": 2})
    assert session.compatible is False

    with pytest.raises(OfficeBridgeError) as captured:
        await service.call(session.session_id, "ppt.apply_batch", {"operations": []})
    assert captured.value.code == "addin_outdated"
    assert socket.sent == []


@pytest.mark.asyncio
async def test_office_bridge_requires_session_when_multiple_decks_are_open():
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeError, OfficeBridgeService

    service = OfficeBridgeService()
    await service.register(FakeWebSocket(), {"host": "powerpoint", "document": {"name": "a.pptx"}})
    await service.register(FakeWebSocket(), {"host": "powerpoint", "document": {"name": "b.pptx"}})

    with pytest.raises(OfficeBridgeError) as captured:
        service.get_session(None)

    assert captured.value.code == "session_required"
    assert len(captured.value.details["sessions"]) == 2


def test_office_gateway_generates_tls_material_manifest_and_protected_assets(monkeypatch, tmp_path):
    from cryptography import x509

    from agent.plugin.plugin_impl.cyrene_office.gateway import OfficeGatewayFiles, create_office_gateway_app
    from cyrene.runtime import settings_service

    def fake_read_public(namespace):
        if namespace == "runtime":
            return {"revision": 7, "values": {"app_language": "en"}}
        return {
            "revision": 7,
            "values": {
                "theme": "light",
                "accent": "#123456",
                "background_light": "#fefefe",
                "background_dark": "#101010",
                "text_size": "large",
            },
        }

    monkeypatch.setattr(settings_service, "read_public", fake_read_public)

    files = OfficeGatewayFiles(tmp_path / "office", port=4943)
    files.ensure()

    assert files.key_path.is_file()
    cert = x509.load_pem_x509_certificate(files.cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)

    root = ElementTree.fromstring(files.manifest_path.read_text(encoding="utf-8"))
    assert root.tag.endswith("OfficeApp")
    group = next(element for element in root.iter() if element.tag.endswith("Group"))
    assert [child.tag.rsplit("}", 1)[-1] for child in group][:3] == ["Label", "Icon", "Control"]
    manifest = files.manifest_path.read_text(encoding="utf-8")
    assert "https://localhost:4943/taskpane.html?token=" in manifest
    assert "<Version>1.3.1.0</Version>" in manifest
    assert 'id="Icon.16" DefaultValue="https://localhost:4943/assets/icon-16.png?token=' in manifest
    assert 'id="Icon.32" DefaultValue="https://localhost:4943/assets/icon-32.png?token=' in manifest
    assert 'id="Icon.80" DefaultValue="https://localhost:4943/assets/icon-80.png?token=' in manifest
    assert 'lifetime="long"' in manifest
    assert files.secret not in files.public_info(running=False)["install_command"]

    client = TestClient(create_office_gateway_app(files))
    import agent.plugin as plugin_api

    class FakeRuntime:
        async def call(self, plugin_name, args, _context):
            assert plugin_name == "PowerPointGetContext"
            return type("Call", (), {
                "success": True,
                "value": {
                    "status": "success",
                    "method": "ppt.get_context",
                    "arguments": args,
                },
                "error": "",
            })()

    fake_host = type("Host", (), {
        "runtime": FakeRuntime(),
        "services": {},
        "db_path": str(tmp_path / "cyrene.db"),
    })()
    monkeypatch.setattr(plugin_api, "active_plugin_application_host", lambda: fake_host)
    assert client.post("/benchmark/invoke", json={"method": "ppt.get_context", "arguments": {}}).status_code == 401
    benchmark_response = client.post(
        "/benchmark/invoke",
        params={"token": files.secret},
        json={"method": "ppt.get_context", "arguments": {"sessionId": "one"}},
    )
    assert benchmark_response.status_code == 200
    assert benchmark_response.json()["method"] == "ppt.get_context"
    assert client.post(
        "/benchmark/invoke",
        params={"token": files.secret},
        json={"method": "ppt.execute_officejs", "arguments": {}},
    ).status_code == 400
    assert client.get("/taskpane.html").status_code == 401
    page = client.get("/taskpane.html", params={"token": files.secret})
    assert page.status_code == 200
    assert files.secret in page.text
    assert "wb-office" not in page.text
    assert "taskpane-shell" in page.text
    assert '<img class="brand-mark" src="/assets/icon-80.png?token=' in page.text
    assert "__CYRENE_OFFICE_TOKEN__" not in page.text
    assert "/assets/cyrene-theme.css?token=" in page.text
    assert "/assets/cyrene-fonts.css?token=" in page.text
    assert "__CYRENE_OFFICE_BUILD_HASH__" not in page.text
    script = client.get("/taskpane.js", params={"token": files.secret})
    assert script.status_code == 200
    assert "__CYRENE_OFFICE_PROTOCOL_VERSION__" not in script.text
    assert "protocolVersion: Number(\"4\")" in script.text
    stylesheet = client.get("/taskpane.css", params={"token": files.secret})
    assert stylesheet.status_code == 200
    assert "__CYRENE_OFFICE_TOKEN__" not in stylesheet.text
    status_rule = stylesheet.text.split(".status-card {", 1)[1].split("}", 1)[0]
    assert "align-items: center;" in status_rule
    for size in (16, 32, 80):
        icon = client.get(f"/assets/icon-{size}.png", params={"token": files.secret})
        assert icon.status_code == 200
        assert icon.headers["content-type"].startswith("image/png")
        assert icon.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert int.from_bytes(icon.content[16:20], "big") == size
        assert int.from_bytes(icon.content[20:24], "big") == size
    assert client.get("/assets/icon-80.png").status_code == 401
    assert client.get("/assets/icon-64.png", params={"token": files.secret}).status_code == 404
    theme = client.get("/assets/cyrene-theme.css", params={"token": files.secret})
    assert theme.status_code == 200
    assert "--accent:" in theme.text
    font_styles = client.get("/assets/cyrene-fonts.css", params={"token": files.secret})
    assert font_styles.status_code == 200
    assert f"?token={files.secret}" in font_styles.text
    font = client.get("/assets/fonts/manrope-variable.woff2", params={"token": files.secret})
    assert font.status_code == 200
    assert font.headers["content-type"].startswith("font/woff2")
    assert client.get("/assets/fonts/manrope-variable.woff2").status_code == 401
    appearance = client.get("/appearance", params={"token": files.secret}).json()
    assert appearance == {
        "revision": 7,
        "values": {
            "theme": "light",
            "accent": "#123456",
            "background_light": "#fefefe",
            "background_dark": "#101010",
            "text_size": "large",
            "language": "en",
        },
    }
    assert client.get("/health").json()["service"] == "cyrene-office-gateway"




def test_semantic_slide_spec_compiles_geometry_without_model_coordinates():
    from agent.plugin.plugin_impl.cyrene_office import _shared

    params = _shared._prepare_request("ppt.create_slide", {
        "slideSpec": {
            "layout": "section-grid",
            "title": "Semantic slide",
            "sections": [
                {"heading": "One", "body": "First"},
                {"heading": "Two", "bullets": ["Second", "Third"]},
            ],
            "theme": {"accent": "#0055AA"},
        },
    })

    spec = params["slideSpec"]
    assert spec["metadata"]["semanticLayoutCompiled"] is True
    assert spec["background"]
    assert {"title", "section-1-title", "section-2-body"} <= {
        item["ref"] for item in spec["elements"]
    }
    assert all(len(item["box"]) == 4 for item in spec["elements"])
    assert all(value > 0 for item in spec["elements"] for value in item["box"][2:])


def test_semantic_image_is_never_silently_dropped_by_non_media_layout():
    from agent.plugin.plugin_impl.cyrene_office.slide_layout import compile_slide_spec

    spec = compile_slide_spec({
        "layout": "quote",
        "title": "Image-backed quote",
        "quote": "Preserve the requested visual.",
        "attribution": "Cyrene",
        "image": {"assetRef": "assets/capybara.png", "caption": "Capybara"},
    })

    assert spec["layout"] == "image-right"
    assert any(
        element.get("type") == "image"
        and element.get("assetRef") == "assets/capybara.png"
        for element in spec["elements"]
    )
    body = next(element for element in spec["elements"] if element.get("ref") == "body")
    assert "Preserve the requested visual." in body["text"]
    assert "Cyrene" in body["text"]
    assert spec["metadata"] == {
        "semanticLayoutCompiled": True,
        "requestedLayout": "quote",
        "resolvedLayout": "image-right",
        "layoutFallbackReason": "image_requires_media_layout",
    }


def test_powerpoint_canonical_request_preflight_and_image_pipeline(
    monkeypatch,
    tmp_path,
    real_pillow_modules,
):
    from PIL import Image

    from agent.plugin.plugin_impl.cyrene_office import _shared

    monkeypatch.setattr(_shared, "Image", real_pillow_modules)

    image_path = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 12), "#336699").save(image_path, format="JPEG")
    monkeypatch.setattr(_shared, "resolve_workspace_path", lambda value: tmp_path / value)
    params = _shared._prepare_request("ppt.apply_batch", {
        "sessionId": "session-1",
        "slideId": "256",
        "expectedRevision": 4,
        "idempotencyKey": "image-1",
        "operations": [{
            "op": "insert_image",
            "imagePath": "photo.jpg",
            "box": [10, 20, 300, 180],
            "ref": "hero",
        }],
    })
    assert params["sessionId"] == "session-1"
    assert params["expectedRevision"] == 4
    operation = params["operations"][0]
    assert operation["op"] == "insert_image"
    assert {"x", "y", "width", "height", "imageBase64"} <= set(operation)
    image = Image.open(io.BytesIO(__import__("base64").b64decode(operation["imageBase64"])))
    assert image.format == "PNG"

    with pytest.raises(_shared.PowerPointRequestError) as non_canonical:
        _shared._prepare_request("ppt.apply_batch", {"session_id": "session-1"})
    assert non_canonical.value.code == "non_canonical_field"

    with pytest.raises(_shared.PowerPointRequestError) as captured:
        _shared._prepare_request("ppt.apply_batch", {
            "operations": [{"op": "update_text", "text": "Missing target"}],
        })
    assert captured.value.code == "shape_target_required"
    assert captured.value.details["operationIndex"] == 0
    assert captured.value.details["field"] == "shapeRef"


@pytest.mark.asyncio
async def test_create_slides_routes_simple_pages_in_progressive_stages(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import kit

    calls = []
    progress = []

    async def fake_call(args, operation, method, **_kwargs):
        calls.append((dict(args), operation, method))
        index = len(calls)
        return json.dumps({"status": "applied", "revision": 10 + index, "slideId": str(300 + index), "stages": [{"name": "title"}]})

    monkeypatch.setattr(kit, "execute_powerpoint_request", fake_call)
    async def fake_progress(**payload):
        progress.append(payload)
    monkeypatch.setattr(kit, "publish_tool_progress", fake_progress)
    payload = json.loads(await kit._create_slides_handler({
        "expectedRevision": 10,
        "idempotencyKey": "deck",
        "commitMode": "atomic",
        "progressiveGranularity": "stage",
        "slideSpecs": [{"elements": []}, {"elements": []}],
    }))
    assert payload["status"] == "applied"
    assert payload["revision"] == 12
    assert [call[0]["expectedRevision"] for call in calls] == [10, 11]
    assert [call[0]["idempotencyKey"] for call in calls] == ["deck:slide:1", "deck:slide:2"]
    assert all(call[0]["commitMode"] == "progressive" for call in calls)
    assert all(call[0]["progressiveGranularity"] == "stage" for call in calls)
    assert [item["current"] for item in progress] == [0, 1, 2]


@pytest.mark.asyncio
async def test_create_slides_routes_template_pages_through_template_composer(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import kit

    calls = []

    async def fake_call(args, operation, method, **_kwargs):
        calls.append((dict(args), operation, method))
        return json.dumps({"status": "applied", "revision": 8, "slideId": "401"})

    monkeypatch.setattr(kit, "execute_powerpoint_request", fake_call)
    monkeypatch.setattr(kit, "publish_tool_progress", lambda **_payload: asyncio.sleep(0))
    payload = json.loads(await kit._create_slides_handler({
        "expectedRevision": 7,
        "idempotencyKey": "templated-deck",
        "slideSpecs": [{
            "templateSlideId": "256",
            "templateBindings": [
                {"shapeRef": "title", "text": "Inherited layout"},
                {"shapeRef": "unused-subtitle", "delete": True},
            ],
        }],
    }))

    assert payload["status"] == "applied"
    assert calls[0][1:] == ("ppt.create_from_template", "ppt.create_from_template")
    assert calls[0][0]["templateSlideId"] == "256"
    assert calls[0][0]["slideSpec"]["templateBindings"][1]["delete"] is True


@pytest.mark.asyncio
async def test_live_slide_spec_defaults_to_staged_preview_but_file_mode_stays_atomic(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import kit

    calls = []

    async def fake_call(args, operation, method, **kwargs):
        calls.append((dict(args), operation, method, kwargs))
        return json.dumps({"status": "applied", "revision": 1})

    monkeypatch.setattr(kit, "execute_powerpoint_request", fake_call)

    await kit._method_handler("ppt.create_slide", {
        "sessionId": "live-session",
        "expectedRevision": 0,
        "idempotencyKey": "live-slide",
        "commitMode": "atomic",
        "progressiveGranularity": "stage",
        "slideSpec": {"elements": []},
    }, PluginContext())
    await kit._method_handler("ppt.create_slide", {
        "filePath": "/tmp/deck.pptx",
        "expectedRevision": 0,
        "idempotencyKey": "file-slide",
        "commitMode": "progressive",
        "slideSpec": {"elements": []},
    }, PluginContext())

    assert calls[0][0]["commitMode"] == "progressive"
    assert calls[0][0]["progressiveGranularity"] == "stage"
    assert calls[0][3]["timeout"] == 300
    assert calls[1][0]["commitMode"] == "atomic"


@pytest.mark.asyncio
async def test_create_slides_rolls_back_completed_pages_after_later_failure(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import kit

    calls = []

    async def fake_call(args, operation, method, **_kwargs):
        calls.append((dict(args), operation, method))
        if method == "ppt.create_slide" and len([item for item in calls if item[2] == method]) == 1:
            return json.dumps({"status": "applied", "revision": 11, "slideId": "301", "undoToken": "undo-301"})
        if method == "ppt.create_slide":
            return json.dumps({"status": "error", "error_code": "office_error", "message": "stage failed"})
        if method == "ppt.get_context":
            return json.dumps({"status": "success", "revision": 11})
        if method == "ppt.delete_slide":
            return json.dumps({"status": "applied", "revision": 12, "deleted": [args["slideId"]]})
        raise AssertionError(method)

    monkeypatch.setattr(kit, "execute_powerpoint_request", fake_call)
    monkeypatch.setattr(kit, "publish_tool_progress", lambda **_payload: asyncio.sleep(0))

    payload = json.loads(await kit._create_slides_handler({
        "sessionId": "ppt-session",
        "expectedRevision": 10,
        "idempotencyKey": "deck",
        "slideSpecs": [{"elements": []}, {"elements": []}],
    }))

    assert payload["status"] == "error"
    assert payload["details"]["slideIndex"] == 1
    assert payload["details"]["rollback"] == {
        "attempted": True,
        "completed": True,
        "deletedSlides": ["301"],
        "errors": [],
        "revision": 12,
    }
    delete_call = next(item for item in calls if item[2] == "ppt.delete_slide")
    assert delete_call[0] == {
        "sessionId": "ppt-session",
        "slideId": "301",
        "expectedRevision": 11,
        "idempotencyKey": "deck:rollback:1",
    }


def test_powerpoint_addin_uses_typed_dispatch_without_eval():
    source = (Path(__file__).parents[1] / "src/agent/plugin/plugin_impl/cyrene_office/static/taskpane.js").read_text(encoding="utf-8")

    assert '"ppt.apply_batch": applyBatch' in source
    assert '"ppt.create_slide": createSlide' in source
    assert "Office.context.document.setSelectedDataAsync" in source
    assert 'isSetSupported("ImageCoercion", "1.1")' in source
    assert "slide.shapes.addPicture" not in source
    assert "slide.shapes.addImage" not in source
    assert "fill.setImage" not in source
    assert "const imageOperations =" not in source
    assert 'operationGroups(params.operations, params.progressiveGranularity || "stage")' in source
    assert "await addImageElement(context, slide, op, batch.created)" in source
    assert "media_stage_failed" in source
    assert "agentKit: agentKit" in source
    assert '"ppt.create_from_template": createFromTemplate' in source
    assert "async function applyTemplateBindings(slideId, bindings)" in source
    assert "Template pages use the" in source
    assert '"ppt.apply_slide_spec": applySlideSpec' in source
    assert '"ppt.edit_table": editTable' in source
    assert "context.sync()" in source
    assert "params.expected_revision" not in source
    assert "params.idempotency_key" not in source
    assert "resume_session_id" not in source
    assert "message.session_id" not in source
    assert "!/^title property in /i.test(candidate)" in source
    assert 'className = "status-indicator " + currentStatus.kind' in source
    assert 'className = "dot " + kind' not in source
    assert 'fetch("/appearance?token="' in source
    assert 'style.setProperty("--accent", accent)' in source
    assert "const translations = {" in source
    assert 'document.querySelectorAll("[data-i18n]")' in source
    assert "values.language" in source
    assert 'statusConnectedDetail: "Cyrene can edit this presentation in real time"' in source
    assert "const slide = slides.add(" not in source
    assert "const slide = context.presentation.slides.add(" not in source
    assert "slides.add(options || {});" in source
    assert "createdSlideId = await addSlideAndGetId(context, addOptions);" in source
    assert "await removeInheritedPlaceholders(context, slide);" in source
    assert 'code: "unresolved_placeholder"' in source
    assert "await rollbackCreatedSlide(createdSlideId);" in source
    assert "state.mutationQueue.then(executeRequest, executeRequest)" in source
    assert "if (isMutation) state.mutationInFlight = true;" in source
    assert "if (isMutation) state.mutationInFlight = false;" in source
    assert "if (isMutation) await reconcileMutationSignatures(result);" in source
    assert "try { await reconcileMutationSignatures({}); }" in source
    assert 'requireApi("1.8", "Snapshot-backed automatic rollback for batch edits")' in source
    assert "const restoredSlideIds = await restoreSlideSnapshot(automaticRollback);" in source
    assert "await rollbackCreatedSlide(slideId);" in source
    assert "forgetSlideIdempotency(slide.id);" in source
    assert "context.presentation.setSelectedSlides([target]);" in source
    assert "if (slideId) return context.presentation.slides.getItem(slideId);" in source
    assert "await focusSlide(context, slide.id);" in source
    assert 'progressiveElementGroups(preparedElements, params.progressiveGranularity || "stage")' in source
    assert "await livePreviewTick();" in source
    batch_loop = source.split("for (const group of groups)", 1)[1].split("batch.created.forEach", 1)[0]
    assert "await applyBatchOperation(context, slide, op, batch);" in batch_loop
    assert "await context.sync();" in batch_loop
    assert batch_loop.index("await applyBatchOperation") < batch_loop.index("await context.sync();") < batch_loop.index("await livePreviewTick();")
    selection_handler = source.split("Office.EventType.DocumentSelectionChanged", 1)[1].split("});", 1)[0]
    assert "if (state.mutationInFlight) return;" in selection_handler
    assert 'event: "selection_changed"' in selection_handler
    assert "selection: context.selection" in selection_handler
    assert "currentSignature !== previousSignature" in selection_handler
    assert "state.revision += 1" in selection_handler
    assert "eval(" not in source
    assert "new Function" not in source


def _write_minimal_pptx(path: Path) -> None:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
</Types>'''
    presentation = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId2"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="9144000" cy="5143500"/></p:presentation>'''
    presentation_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/></Relationships>'''
    slide = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="cyrene:title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="762000" y="457200"/><a:ext cx="7874000" cy="609600"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN"/><a:t>Old title</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
    slide_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'''
    master = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="Cyrene Master"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'''
    master_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>'''
    layout = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'''
    layout_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>'''
    theme = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Cyrene"><a:themeElements><a:clrScheme name="Cyrene"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:accent1><a:srgbClr val="2563EB"/></a:accent1></a:clrScheme><a:fontScheme name="Cyrene"><a:majorFont/><a:minorFont/></a:fontScheme><a:fmtScheme name="Cyrene"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr("ppt/slideMasters/slideMaster1.xml", master)
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        archive.writestr("ppt/theme/theme1.xml", theme)


def test_file_backend_edits_creates_and_undoes_a_real_pptx_package(
    tmp_path,
    real_pillow_modules,
):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    path = tmp_path / "deck.pptx"
    _write_minimal_pptx(path)
    engine = PptxFileEngine()

    context = engine.call("ppt.get_context", {"filePath": str(path)})
    assert context["mode"] == "file"
    assert context["revision"] == 0

    inspected = engine.call("ppt.inspect", {"filePath": str(path), "slideId": "256"})
    assert inspected["slide"]["shapes"][0]["ref"] == "title"

    edited = engine.call("ppt.apply_batch", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "edit-title", "operations": [{"op": "update_text", "shapeRef": "title", "text": "New title"}],
    })
    assert edited["status"] == "applied"
    assert edited["revision"] == 1
    assert engine.call("ppt.apply_batch", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "edit-title", "operations": [{"op": "update_text", "shapeRef": "title", "text": "New title"}],
    })["replayed"] is True

    created = engine.call("ppt.create_slide", {
        "filePath": str(path), "expectedRevision": 1, "idempotencyKey": "new-slide",
    })
    assert created["revision"] == 2
    assert len(engine.call("ppt.list_slides", {"filePath": str(path)})["slides"]) == 2

    undone = engine.call("ppt.undo_batch", {
        "filePath": str(path), "expectedRevision": 2, "idempotencyKey": "undo-new-slide", "undoToken": created["undoToken"],
    })
    assert undone["revision"] == 3
    assert len(engine.call("ppt.list_slides", {"filePath": str(path)})["slides"]) == 1

    chart = engine.call("ppt.edit_chart", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 3,
        "idempotencyKey": "chart-visual", "chartMode": "visual",
        "ref": "revenue-chart", "x": 60, "y": 120, "width": 420, "height": 220,
        "chartSpec": {"type": "column", "categories": ["Q1", "Q2"], "series": [{"name": "Revenue", "values": [12, 18]}]},
    })
    assert chart["nativeEditable"] is False
    with zipfile.ZipFile(path) as archive:
        assert any(name.startswith("ppt/media/image") for name in archive.namelist())

    native_chart = engine.call("ppt.edit_chart", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 4,
        "idempotencyKey": "chart-native", "chartMode": "native",
        "ref": "editable-chart", "x": 60, "y": 120, "width": 420, "height": 220,
        "chartSpec": {"type": "line", "categories": ["Q1", "Q2"], "series": [{"name": "Revenue", "values": [12, 18]}]},
    })
    assert native_chart["nativeEditable"] is True
    with zipfile.ZipFile(path) as archive:
        assert "ppt/charts/chart1.xml" in archive.namelist()
        workbook = archive.read("ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx")
    with zipfile.ZipFile(io.BytesIO(workbook)) as embedded:
        assert "xl/worksheets/sheet1.xml" in embedded.namelist()


def test_file_backend_reuses_digest_until_file_identity_changes(tmp_path, monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import file_engine

    path = tmp_path / "digest-cache.pptx"
    _write_minimal_pptx(path)
    real_digest = file_engine._digest
    calls: list[Path] = []

    def tracked_digest(target: Path) -> str:
        calls.append(target)
        return real_digest(target)

    monkeypatch.setattr(file_engine, "_digest", tracked_digest)
    engine = file_engine.PptxFileEngine()

    assert engine.call("ppt.get_context", {"filePath": str(path)})["revision"] == 0
    assert engine.call("ppt.inspect", {"filePath": str(path)})["revision"] == 0
    assert calls == [path]

    current = path.stat()
    os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
    assert engine.call("ppt.get_context", {"filePath": str(path)})["revision"] == 0
    assert calls == [path, path]

    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("customXml/change.txt", "external change")
    assert engine.call("ppt.get_context", {"filePath": str(path)})["revision"] == 1
    assert calls == [path, path, path]


def test_file_backend_precise_reads_persistent_refs_and_review_checks(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    path = tmp_path / "precise.pptx"
    _write_minimal_pptx(path)
    engine = PptxFileEngine()

    slide = engine.call("ppt.get_slide", {"filePath": str(path), "slideId": "256"})
    assert slide["slide"]["id"] == "256"
    assert slide["slide"]["shapes"][0]["text"] == "Old title"
    shape = engine.call("ppt.get_shape", {"filePath": str(path), "slideId": "256", "shapeRef": "title"})
    assert shape["shape"]["ref"] == "title"
    assert engine.call("ppt.read_text", {"filePath": str(path), "slideId": "256"})["text"][0]["text"] == "Old title"

    bound = engine.call("ppt.bind_shape", {
        "filePath": str(path), "slideId": "256", "shapeRef": "title", "ref": "hero-title",
        "expectedRevision": 0, "idempotencyKey": "bind-title",
    })
    assert bound["revision"] == 1
    assert engine.call("ppt.get_shape", {"filePath": str(path), "slideId": "256", "shapeRef": "hero-title"})["shape"]["ref"] == "hero-title"
    assert engine.call("ppt.check_overflow", {"filePath": str(path), "slideId": "256"})["check"] == "overflow"
    assert engine.call("ppt.check_overlap", {"filePath": str(path), "slideId": "256"})["check"] == "overlap"
    contrast = engine.call("ppt.check_contrast", {"filePath": str(path), "slideId": "256", "minimumRatio": 4.5})
    assert contrast["check"] == "contrast"
    assert contrast["unverifiableShapeIds"] == ["2"]
    background = engine.call("ppt.apply_slide_spec", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 1,
        "idempotencyKey": "background", "slideSpec": {"background": "#102030", "elements": []},
    })
    assert background["changed"] == ["256"]
    with zipfile.ZipFile(path) as archive:
        assert b'val="102030"' in archive.read("ppt/slides/slide1.xml")


def test_file_backend_groups_and_ungroups_with_the_canonical_batch_contract(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    path = tmp_path / "groups.pptx"
    _write_minimal_pptx(path)
    engine = PptxFileEngine()
    added = engine.call("ppt.apply_batch", {
        "filePath": str(path), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "add-group-members", "operations": [
            {"op": "add_shape", "ref": "left", "x": 40, "y": 120, "width": 100, "height": 80},
            {"op": "add_shape", "ref": "right", "x": 180, "y": 120, "width": 100, "height": 80},
        ],
    })
    grouped = engine.call("ppt.apply_batch", {
        "filePath": str(path), "slideId": "256", "expectedRevision": added["revision"],
        "idempotencyKey": "group-members", "operations": [
            {"op": "group_shapes", "shapeRefs": ["left", "right"], "ref": "pair"},
        ],
    })
    assert grouped["created"] == ["pair"]
    group = engine.call("ppt.get_shape", {"filePath": str(path), "slideId": "256", "shapeRef": "pair"})["shape"]
    assert group["type"] == "grpSp"
    assert (group["x"], group["y"], group["width"], group["height"]) == (40.0, 120.0, 240.0, 80.0)

    ungrouped = engine.call("ppt.apply_batch", {
        "filePath": str(path), "slideId": "256", "expectedRevision": grouped["revision"],
        "idempotencyKey": "ungroup-members", "operations": [
            {"op": "ungroup_shapes", "shapeRef": "pair"},
        ],
    })
    assert ungrouped["changed"] == ["pair"]
    refs = {shape["ref"] for shape in engine.call("ppt.get_slide", {"filePath": str(path), "slideId": "256"})["slide"]["shapes"]}
    assert {"left", "right"} <= refs


@pytest.mark.asyncio
async def test_file_backend_output_version_keeps_revision_across_multiple_created_slides(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine
    from agent.plugin.plugin_impl.cyrene_office import kit

    source = tmp_path / "source-version.pptx"
    output = tmp_path / "output-version.pptx"
    _write_minimal_pptx(source)
    payload = json.loads(await kit._create_slides_handler({
        "mode": "file",
        "filePath": str(source),
        "outputPath": str(output),
        "expectedRevision": 0,
        "idempotencyKey": "versioned-deck",
        "slideSpecs": [
            {"elements": [{"ref": "one", "type": "text", "box": [40, 40, 300, 40], "text": "One"}]},
            {"elements": [{"ref": "two", "type": "text", "box": [40, 40, 300, 40], "text": "Two"}]},
        ],
    }))
    assert payload["status"] == "applied"
    assert payload["mode"] == "file"
    assert payload["audit"]["commitMode"] == "atomic"
    assert payload["audit"]["packageWrites"] == 1
    engine = PptxFileEngine()
    assert len(engine.call("ppt.list_slides", {"filePath": str(source)})["slides"]) == 1
    assert len(engine.call("ppt.list_slides", {"filePath": str(output)})["slides"]) == 3


def test_template_creation_and_replacement_have_the_same_file_semantics(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    path = tmp_path / "template-semantics.pptx"
    _write_minimal_pptx(path)
    engine = PptxFileEngine()
    created = engine.call("ppt.create_from_template", {
        "filePath": str(path),
        "templateSlideId": "256",
        "expectedRevision": 0,
        "idempotencyKey": "from-template",
        "slideSpec": {"templateBindings": [{
            "shapeRef": "title", "text": "Inherited title",
        }], "elements": [{
            "ref": "template-caption", "type": "text", "box": [60, 320, 400, 30], "text": "Added to template",
        }]},
    })
    created_slide_id = created["created"][0]["slideId"]
    cloned = engine.call("ppt.get_slide", {"filePath": str(path), "slideId": created_slide_id})["slide"]
    assert {shape.get("text") for shape in cloned["shapes"]} >= {"Inherited title", "Added to template"}

    replaced = engine.call("ppt.replace_slide", {
        "filePath": str(path),
        "slideId": created_slide_id,
        "expectedRevision": created["revision"],
        "idempotencyKey": "replace-template-slide",
        "slideSpec": {"elements": [{
            "ref": "replacement-title", "type": "text", "box": [60, 40, 600, 60], "text": "Replacement",
        }]},
    })
    replacement = engine.call("ppt.get_slide", {"filePath": str(path), "slideId": created_slide_id})["slide"]
    assert [shape.get("text") for shape in replacement["shapes"]] == ["Replacement"]
    assert replaced["deleted"]


def test_file_backend_native_table_notes_master_layout_and_import(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    target = tmp_path / "target.pptx"
    source = tmp_path / "source.pptx"
    _write_minimal_pptx(target)
    _write_minimal_pptx(source)
    engine = PptxFileEngine()

    table = engine.call("ppt.edit_table", {
        "filePath": str(target), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "table-create", "ref": "metrics-table",
        "x": 60, "y": 130, "width": 480, "height": 150,
        "values": [["Metric", "Value"], ["Revenue", "42"]],
    })
    assert table["created"] == ["metrics-table"]
    with zipfile.ZipFile(target) as archive:
        slide_xml = ElementTree.fromstring(archive.read("ppt/slides/slide1.xml"))
        namespace = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        assert slide_xml.find(".//a:tbl", namespace) is not None
        assert "Revenue" in "".join(node.text or "" for node in slide_xml.findall(".//a:t", namespace))

    notes = engine.call("ppt.edit_notes", {
        "filePath": str(target), "slideId": "256", "expectedRevision": 1,
        "idempotencyKey": "notes-create", "text": "Explain the revenue assumptions.",
    })
    assert notes["audit"]["action"] == "edit_notes"
    with zipfile.ZipFile(target) as archive:
        notes_parts = [name for name in archive.namelist() if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")]
        assert len(notes_parts) == 1
        assert b"Explain the revenue assumptions." in archive.read(notes_parts[0])
    assert engine.call("ppt.get_slide", {"filePath": str(target), "slideId": "256"})["slide"]["notes"] == "Explain the revenue assumptions."

    master = engine.call("ppt.get_master", {"filePath": str(target)})
    assert master["capabilities"]["editViaTypedOperations"] is True
    assert master["masters"][0]["layouts"] == ["ppt/slideLayouts/slideLayout1.xml"]
    master_edit = engine.call("ppt.edit_master", {
        "filePath": str(target), "expectedRevision": 2, "idempotencyKey": "master-edit",
        "masterPart": "ppt/slideMasters/slideMaster1.xml",
        "operations": [{"op": "add_textbox", "ref": "footer", "text": "Confidential", "x": 20, "y": 380, "width": 180, "height": 20}],
    })
    assert master_edit["created"] == ["footer"]
    layout_edit = engine.call("ppt.edit_layout", {
        "filePath": str(target), "expectedRevision": 3, "idempotencyKey": "layout-edit",
        "layoutPart": "ppt/slideLayouts/slideLayout1.xml",
        "operations": [{"op": "add_textbox", "ref": "layout-label", "text": "Layout", "x": 20, "y": 20, "width": 120, "height": 30}],
    })
    assert layout_edit["created"] == ["layout-label"]
    applied_layout = engine.call("ppt.edit_layout", {
        "filePath": str(target), "slideId": "256", "expectedRevision": 4,
        "idempotencyKey": "layout-apply", "layoutId": "ppt/slideLayouts/slideLayout1.xml",
    })
    assert applied_layout["audit"]["action"] == "apply_layout"

    source_engine = PptxFileEngine()
    source_engine.call("ppt.apply_batch", {
        "filePath": str(source), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "source-title", "operations": [{"op": "update_text", "shapeRef": "title", "text": "Imported title"}],
    })
    imported = engine.call("ppt.import_slides", {
        "filePath": str(target), "presentationPath": str(source), "sourceSlideIds": ["256"],
        "targetSlideId": "256", "formatting": "KeepSourceFormatting",
        "expectedRevision": 5, "idempotencyKey": "import-source-slide",
    })
    assert imported["audit"]["slideCount"] == 1
    slides = engine.call("ppt.list_slides", {"filePath": str(target)})["slides"]
    assert len(slides) == 2
    imported_slide = engine.call("ppt.get_slide", {"filePath": str(target), "slideId": imported["created"][0]["slideId"]})
    assert any(shape["text"] == "Imported title" for shape in imported_slide["slide"]["shapes"])


def test_file_backend_replaces_one_slide_from_presentation_and_keeps_single_undo(tmp_path):
    from agent.plugin.plugin_impl.cyrene_office.file_engine import PptxFileEngine

    target = tmp_path / "replace-target.pptx"
    source = tmp_path / "replace-source.pptx"
    _write_minimal_pptx(target)
    _write_minimal_pptx(source)
    source_engine = PptxFileEngine()
    source_engine.call("ppt.apply_batch", {
        "filePath": str(source), "slideId": "256", "expectedRevision": 0,
        "idempotencyKey": "prepare-replacement", "operations": [
            {"op": "update_text", "shapeRef": "title", "text": "Replacement package"},
        ],
    })

    engine = PptxFileEngine()
    replaced = engine.call("ppt.replace_slide_ooxml", {
        "filePath": str(target), "slideId": "256", "presentationPath": str(source),
        "sourceSlideIds": ["256"], "expectedRevision": 0,
        "idempotencyKey": "replace-from-package", "confirmed": True,
    })
    assert replaced["deleted"] == ["256"]
    assert len(replaced["created"]) == 1
    replacement_id = replaced["created"][0]["slideId"]
    slide = engine.call("ppt.get_slide", {"filePath": str(target), "slideId": replacement_id})["slide"]
    assert any(shape["text"] == "Replacement package" for shape in slide["shapes"])

    undone = engine.call("ppt.undo_batch", {
        "filePath": str(target), "expectedRevision": replaced["revision"],
        "idempotencyKey": "undo-package-replacement", "undoToken": replaced["undoToken"],
    })
    restored = engine.call("ppt.get_slide", {"filePath": str(target), "slideId": "256"})["slide"]
    assert undone["revision"] == replaced["revision"] + 1
    assert any(shape["text"] == "Old title" for shape in restored["shapes"])




def test_macos_office_install_copies_manifest_to_powerpoint_wef(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_office import installation
    from agent.plugin.plugin_impl.cyrene_office.gateway import OfficeGatewayFiles

    files = OfficeGatewayFiles(tmp_path / "gateway", port=4943)
    files.ensure()
    monkeypatch.setattr(installation, "certificate_trusted", lambda *_args, **_kwargs: True)

    installed = installation.install_powerpoint_addin(
        files,
        system="Darwin",
        home=tmp_path / "home",
    )

    target = installation.powerpoint_manifest_target(system="Darwin", home=tmp_path / "home")
    assert target is not None and target.read_bytes() == files.manifest_path.read_bytes()
    assert installed["addin_installed"] is True
    assert installed["one_click_install"] is True
    assert installed["restart_powerpoint"] is True

    removed = installation.remove_powerpoint_addin(files, system="Darwin", home=tmp_path / "home")
    assert removed["addin_installed"] is False
    assert target.exists() is False


def test_windows_office_install_prepares_manifest_without_unsafe_registry_writes(monkeypatch, tmp_path):
    from agent.plugin.plugin_impl.cyrene_office import installation
    from agent.plugin.plugin_impl.cyrene_office.gateway import OfficeGatewayFiles

    files = OfficeGatewayFiles(tmp_path / "gateway", port=4943)
    files.ensure()
    monkeypatch.setattr(installation, "certificate_trusted", lambda *_args, **_kwargs: True)

    result = installation.install_powerpoint_addin(files, system="Windows", home=tmp_path / "home")

    assert result["message_code"] == "prepared_manual"
    assert result["manual_step_required"] is True
    assert result["one_click_install"] is False
    assert result["manifest_path"] == str(files.manifest_path.resolve())


def test_service_integrations_page_contains_powerpoint_install_controls():
    source = frontend_module_source("features/settings/general.jsx")

    assert 'id: "setting-office-powerpoint"' in source
    assert '"/api/settings/integrations/office/install"' in source
    assert "function install()" in source
    assert "function revealManifest(currentStatus)" in source
    assert '"settings.officeInstall"' in source
    assert '"settings.officeReinstall"' in source
    assert '"settings.officeInstalledUsageHint"' in source
    assert "wb-office-install-button" in source
    assert 'FieldRow(t("settings.officeGateway")' not in source
    assert 'onClick: removeOfficeIntegration' not in source


def test_office_integration_status_route_is_registered(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import settings_routes as office_routes

    monkeypatch.setattr(office_routes, "integration_status", lambda: {
        "running": True,
        "certificate_trusted": True,
        "addin_installed": True,
        "connected_presentations": 1,
    })
    app = FastAPI()
    office_routes.register_office_integration_routes(app)
    payload = TestClient(app).get("/api/settings/integrations/office").json()

    assert payload == {
        "running": True,
        "certificate_trusted": True,
        "addin_installed": True,
        "connected_presentations": 1,
    }


def test_disabled_office_pack_does_not_attach_routes_or_services(tmp_path):
    from agent.plugin import (
        PluginActivationState,
        PluginApplicationHost,
        PluginRegistry,
    )
    from agent.plugin.plugin_impl.cyrene_office import plugin_pack

    registry = PluginRegistry(
        include_core=False,
        activation=PluginActivationState(packs={"cyrene_office": False}),
    )
    registry.register_pack(plugin_pack, source="test")
    host = PluginApplicationHost(
        app=FastAPI(),
        registry=registry,
        bot=None,
        db_path=str(tmp_path / "app.db"),
        data_directory=tmp_path / "data",
        plugin_directory=tmp_path / "plugin_impl",
    )
    router = APIRouter()
    host.attach(router)

    assert not any(route.path.startswith("/api/settings/integrations/office") for route in router.routes)
    assert host.service("office") is None


def test_office_tool_error_is_structured_when_powerpoint_is_not_connected(monkeypatch):
    from agent.plugin.plugin_impl.cyrene_office import _shared
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeService

    monkeypatch.setattr(_shared, "get_office_bridge", lambda: OfficeBridgeService())
    monkeypatch.setattr(_shared.get_office_gateway_runtime(), "info", lambda: {"running": False, "manifest_path": "/tmp/cyrene-powerpoint-addin.xml"})

    payload = json.loads(asyncio.run(_shared.get_context_handler({}, PluginContext())))
    assert payload["status"] == "error"
    assert payload["error_code"] == "office_not_connected"
    assert "setup" in payload


def test_office_error_messages_follow_invocation_language_and_hide_raw_errors():
    from agent.plugin.plugin_impl.cyrene_office import _shared
    from agent.plugin.plugin_impl.cyrene_office.service import OfficeBridgeError

    rendered = _shared._failure(
        "ppt.context.get",
        OfficeBridgeError(
            "office_not_connected",
            "ConnectionError: private bridge detail",
        ),
        PluginContext(data={"language": "zh"}),
    )
    payload = json.loads(rendered)

    assert payload["message"] == "当前没有 PowerPoint 演示文稿连接到 Cyrene。"
    assert "private bridge detail" not in rendered
