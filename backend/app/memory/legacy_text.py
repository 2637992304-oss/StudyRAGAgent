"""Deterministic legacy projection and cutover/undo compatibility."""

from sqlalchemy import select

from app.db.models import MemoryItem, MemoryPreference
from app.memory import service as legacy
from app.memory import text_service as text
from app.memory.text_schema import TextMemoryInput


def content(key, value):
    if key == "response_language":
        return {"zh": "默认使用中文讲解。", "en": "默认使用英文讲解。"}[value]
    if key == "code_language":
        return f"默认使用{value}编写代码示例。"
    if key == "explanation_detail":
        return {
            "brief": "默认保持讲解简短。",
            "standard": "默认使用适中的讲解详细程度。",
            "detailed": "默认详细讲解。",
        }[value]
    return {
        "true": "解题时先给提示，再根据用户需要展开答案。",
        "false": "解题时可以直接展开答案，无需先给提示。",
    }[value]


def check_cutover(db, owner):
    if legacy.state(db, owner, "text:engine") is not None:
        return
    for old in db.scalars(
        select(MemoryPreference).where(MemoryPreference.owner_id == owner)
    ):
        item = db.get(MemoryItem, old.id)
        if (
            not item
            or item.version != old.version
            or item.content != content(old.key, old.value)
            or (old.deleted and item.status != "deleted")
        ):
            legacy.fail(
                409, "旧版记忆在复制后发生变化，请在授权维护窗口完成增量转换再启用"
            )


def undo_legacy(db, owner, op, version):
    if op.status == "undone":
        return legacy.operation_data(op)
    item = text.owned(db, owner, op.data["preference_id"])
    if item.version != version or version != op.data["after"]["version"]:
        legacy.fail()
    before = op.data["before"]
    if before and before.get("sources"):
        legacy.fail(
            409, "旧版操作缺少历史来源指纹，无法安全撤销；可在管理入口明确重新保存"
        )
    if before is None or before.get("deleted"):
        text.delete_item(db, owner, item.id, item.version, "legacy-undo:" + op.id)
    else:
        text.apply_manual_batch(
            db,
            owner,
            [
                {
                    "id": item.id,
                    "version": item.version,
                    "input": TextMemoryInput(
                        content=content(before["key"], before["value"]),
                        course_id=None
                        if before["scope"] == "global"
                        else before["scope"].removeprefix("course:"),
                        use_mode="always",
                    ),
                }
            ],
            "legacy-undo:" + op.id,
        )
    op.status = "undone"
    db.flush()
    return legacy.operation_data(op)
