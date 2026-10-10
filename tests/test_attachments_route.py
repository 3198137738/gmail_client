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
