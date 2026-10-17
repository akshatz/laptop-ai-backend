"""Audit Status Code and Chat Soft Delete: both patch Open WebUI classes in place."""

from unittest.mock import AsyncMock

from conftest import fresh_patch_targets, load_function


async def run_audited(middleware_cls, status, scope_extra=None):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = []

    async def send(message):
        sent.append(message)

    await middleware_cls(app)({"type": "http", **(scope_extra or {})}, None, send)
    return sent


async def test_audit_entries_get_the_response_status():
    targets = fresh_patch_targets()
    load_function("audit_status_code").Event()

    sent = await run_audited(targets.AuditLoggingMiddleware, 404)

    assert targets.AuditLoggingMiddleware.logged == [{"response_status_code": 404}]
    assert sent[0]["status"] == 404  # the response itself is passed through unchanged


async def test_audit_keeps_a_status_open_webui_already_recorded():
    targets = fresh_patch_targets()
    load_function("audit_status_code").Event()
    await run_audited(targets.AuditLoggingMiddleware, 404, {"status_from_open_webui": 201})
    assert targets.AuditLoggingMiddleware.logged == [{"response_status_code": 201}]


def test_audit_patch_installs_once():
    targets = fresh_patch_targets()
    load_function("audit_status_code").Event()
    patched = targets.AuditLoggingMiddleware.__call__
    load_function("audit_status_code").Event()
    assert targets.AuditLoggingMiddleware.__call__ is patched


def soft_delete():
    targets = fresh_patch_targets()
    module = load_function("chat_soft_delete")
    module._archive = AsyncMock()
    module.Event()
    return module, targets


async def test_deleted_chats_are_archived_first():
    module, targets = soft_delete()
    chats = targets.ChatTable()

    await chats.delete_chat_by_id("c1")
    await chats.delete_chat_by_id_and_user_id("c2", "u1")
    await chats.delete_chats_by_user_id_and_folder_id("u1", "f1")

    assert [c.args for c in module._archive.await_args_list] == [
        ("c.id = :id", {"id": "c1"}),
        ("c.id = :id AND c.user_id = :uid", {"id": "c2", "uid": "u1"}),
        ("c.user_id = :uid AND c.folder_id = :fid", {"uid": "u1", "fid": "f1"}),
    ]
    assert [c[0] for c in targets.ChatTable.calls] == [
        "delete_chat_by_id", "delete_chat_by_id_and_user_id", "delete_chats_by_user_id_and_folder_id",
    ]


async def test_deleting_a_whole_user_leaves_archiving_to_user_cleanup():
    module, targets = soft_delete()
    await targets.UsersTable().delete_user_by_id("u1")
    module._archive.assert_not_awaited()
    assert targets.ChatTable.calls == [("delete_chats_by_user_id", "u1")]  # still deleted

    await targets.ChatTable().delete_chats_by_user_id("u1")  # "delete all chats" by the user
    module._archive.assert_awaited_once_with("c.user_id = :uid", {"uid": "u1"})


async def test_a_failed_archive_still_deletes_the_chat():
    targets = fresh_patch_targets()
    load_function("chat_soft_delete").Event()  # real _archive; the database refuses in tests
    assert await targets.ChatTable().delete_chat_by_id("c1") is True
    assert targets.ChatTable.calls == [("delete_chat_by_id", "c1")]
