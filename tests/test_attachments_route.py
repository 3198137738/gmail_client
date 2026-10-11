"""附件下载：路径限定在附件目录内，不能读到 secrets 等文件。"""
from __future__ import annotations

from tests.helpers import login


def test_path_traversal_rejected(client):
    login(client)
    for path in (
        "/attachments/1/inbox/1/files/..%2F..%2F..%2F..%2Fsecrets%2Ffernet.key",
        "/attachments/1/inbox/1/inline/..%2F..%2F..%2F..%2Fsecrets%2Ffernet.key",
        "/attachments/1/evil/1/files/a.txt",
    ):
        resp = client.get(path)
        assert resp.status_code in (400, 404), path


def test_attachments_require_login(client):
    resp = client.get("/attachments/1/inbox/1/files/a.txt", follow_redirects=False)
    assert resp.status_code == 303


def _put_file(account_id: int, kind: str, uid: int, sub: str, name: str, data: bytes = b"x"):
    from app.config import ATTACHMENTS_DIR

    path = ATTACHMENTS_DIR / str(account_id) / kind / str(uid) / sub / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_inline_html_disguised_as_image_is_forced_download(client):
    """攻击者把 HTML 伪装成内嵌图片（filename=a.html），不能在本站域名下渲染。"""
    login(client)
    _put_file(901, "inbox", 1, "inline", "a.html", b"<script>alert(1)</script>")
    _put_file(901, "inbox", 1, "inline", "b.svg", b"<svg onload=alert(1)></svg>")
    for name in ("a.html", "b.svg"):
        resp = client.get(f"/attachments/901/inbox/1/inline/{name}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-disposition"].startswith("attachment")
        assert "sandbox" in resp.headers["content-security-policy"]


def test_inline_png_is_shown_inline_with_sandbox_csp(client):
    login(client)
    _put_file(902, "inbox", 1, "inline", "pic.png", b"\x89PNG\r\n")
    resp = client.get("/attachments/902/inbox/1/inline/pic.png")
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["content-disposition"].startswith("inline")
    assert "sandbox" in resp.headers["content-security-policy"]


def test_file_with_declared_html_type_not_rendered_inline(client):
    from app.database import session_scope
    from app.models import Attachment
    from tests.helpers import create_account, create_message

    login(client)
    acc = create_account()
    mid = create_message(acc, uid=77)
    path = _put_file(acc, "inbox", 77, "files", "evil.txt", b"<script>alert(1)</script>")
    with session_scope() as db:
        db.add(Attachment(
            message_id=mid, filename="evil.txt", display_name="evil.txt",
            mime_type="text/html", size=10, file_path=str(path), is_inline=False,
            public_url=f"/attachments/{acc}/inbox/77/files/evil.txt",
        ))
    resp = client.get(f"/attachments/{acc}/inbox/77/files/evil.txt?download=false")
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-disposition"].startswith("attachment")
