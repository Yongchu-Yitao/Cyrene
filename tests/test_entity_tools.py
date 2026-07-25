import pytest


@pytest.mark.asyncio
async def test_entity_tools_expose_ids_and_support_safe_delete_resolution(tmp_path):
    from cyrene.runtime.database import init_db
    from cyrene.tool_impl.entity.store import create_entity, get_entity
    from cyrene.tool_impl.entity.delete_entity import _tool_delete_entity
    from cyrene.tool_impl.entity.list_entities import _tool_list_entities
    from cyrene.tool_impl.entity.query_entities import _tool_query_entities
    from cyrene.tool_impl.entity.track_entity import _tool_track_entity

    db_path = str(tmp_path / "entities.db")
    await init_db(db_path)

    duplicate_a = await create_entity(
        db_path,
        type="fact",
        title="用户本人照片识别",
        content="第一条内容",
    )
    duplicate_b = await create_entity(
        db_path,
        type="fact",
        title="用户本人照片识别",
        content="第二条内容",
    )
    unique = await create_entity(
        db_path,
        type="fact",
        title="用户形象识别",
        content="唯一内容",
    )

    listed = await _tool_list_entities({"status": "active"}, None, 0, db_path, None)
    assert duplicate_a["id"] in listed
    assert duplicate_b["id"] in listed
    assert unique["id"] in listed

    queried = await _tool_query_entities({"q": "用户本人照片识别"}, None, 0, db_path, None)
    assert duplicate_a["id"] in queried
    assert duplicate_b["id"] in queried

    tracked = await _tool_track_entity(
        {"type": "fact", "title": "新建实体", "content": "完整 ID 返回"},
        None,
        0,
        db_path,
        None,
    )
    assert "ID: " in tracked
    assert len(tracked.rsplit("ID: ", 1)[1].rstrip("）")) == 36

    ambiguous = await _tool_delete_entity(
        {"title": "用户本人照片识别", "permanent": True},
        None,
        0,
        db_path,
        None,
    )
    assert "多条事务" in ambiguous
    assert duplicate_a["id"] in ambiguous
    assert duplicate_b["id"] in ambiguous
    assert await get_entity(db_path, duplicate_a["id"]) is not None
    assert await get_entity(db_path, duplicate_b["id"]) is not None

    deleted_by_prefix = await _tool_delete_entity(
        {"id": duplicate_a["id"][:8], "permanent": True},
        None,
        0,
        db_path,
        None,
    )
    assert duplicate_a["id"] in deleted_by_prefix
    assert await get_entity(db_path, duplicate_a["id"]) is None

    deleted_by_title = await _tool_delete_entity(
        {"title": "用户形象识别", "permanent": True},
        None,
        0,
        db_path,
        None,
    )
    assert unique["id"] in deleted_by_title
    assert await get_entity(db_path, unique["id"]) is None
