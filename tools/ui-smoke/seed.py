"""界面冒烟测试的种子数据（APP_DATA_ROOT 指向临时目录后运行）。

16 个账号（12 个 Gmail、4 个 Outlook，3 个自定义分组）；会话、验证码、钓鱼、外部图片、
标签、垃圾邮件、已删除、草稿、定时发送、发件记录。打印 JSON 摘要给 run.mjs 使用。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import create_message  # noqa: E402
from app.crypto import encrypt_password  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import Account, Draft, Label, Message, MessageLabel, ScheduledSend, SentMessage  # noqa: E402
from app.services.otp import extract_code  # noqa: E402

init_db()
now = datetime.now().replace(microsecond=0)

ACCOUNTS = [
    # (邮箱, 显示名, 分组, outlook)
    ("alice.work@gmail.com", "Alice 工作", "工作", False),
    ("alice.home@gmail.com", "Alice 生活", None, False),
    ("shop.alerts@gmail.com", "购物提醒", "注册用", False),
    ("dev.signup@gmail.com", "开发注册", "注册用", False),
    ("game.signup@gmail.com", "游戏注册", "注册用", False),
    ("news.reader@gmail.com", "订阅阅读", None, False),
    ("team.lead@gmail.com", "团队", "工作", False),
    ("backup.mail@gmail.com", "备用", None, False),
    ("travel.plan@gmail.com", "旅行", None, False),
    ("bank.notice@gmail.com", "银行通知", None, False),
    ("cloud.ops@gmail.com", "云服务", "工作", False),
    ("family.share@gmail.com", "家庭", None, False),
    ("bob@outlook.com", "Bob Outlook", None, True),
    ("bob.office@hotmail.com", "Bob 办公", "工作", True),
    ("carol@live.com", "Carol", None, True),
    ("dave.biz@outlook.com", "Dave 商务", None, True),
]

ids: list[int] = []
with session_scope() as db:
    for email, name, group, outlook in ACCOUNTS:
        acc = Account(
            email=email,
            display_name=name,
            group_name=group,
            encrypted_password=encrypt_password("msoauth:refresh-token" if outlook else "app-password"),
        )
        db.add(acc)
        db.flush()
        ids.append(int(acc.id))

main, second = ids[0], ids[12]
SENDERS = ["Amazon Web Services", "GitHub", "OpenAI", "Oracle Cloud", "张三", "李四", "Cloudflare", "Stripe"]
message_ids: list[int] = []
for i in range(60):
    acc = main if i % 3 else second
    name = SENDERS[i % len(SENDERS)]
    message_ids.append(create_message(
        acc,
        subject=f"测试邮件 {i}：关于项目进度的说明",
        from_addr=f"{name} <sender{i}@example.com>",
        to_addr="alice.work@gmail.com",
        date=now - timedelta(hours=i * 3 + 1),
        body_text=f"这是第 {i} 封邮件的正文内容，用来测试摘要显示。",
        body_html=f"<p>这是第 <b>{i}</b> 封邮件的正文。</p><p>第二段内容。</p>",
        is_read=(i % 4 == 0),
        is_starred=(i % 9 == 1),
        thread_id=5000 + i,
    ))
# 其他账号各几封，让未读数分布开
for n, acc in enumerate(ids[1:12], start=1):
    for j in range(n % 4 + 1):
        create_message(acc, subject=f"{ACCOUNTS[n][1]} 的通知 {j}", from_addr=f"Service <svc{n}{j}@example.com>",
                       date=now - timedelta(days=j, hours=n), is_read=False)

# 会话：3 封往来
for k, (who, hours) in enumerate([("张三 <zhang@example.com>", 30), ("Alice <alice.work@gmail.com>", 20), ("张三 <zhang@example.com>", 2)]):
    create_message(main, subject="周报讨论", from_addr=who, date=now - timedelta(hours=hours), thread_id=9001,
                   folder_kind="sent" if "alice" in who else "inbox", folder="[Gmail]/Sent Mail" if "alice" in who else "INBOX",
                   body_html=f"<p>会话第 {k + 1} 封</p>", is_read=k < 2)

# 验证码
for subj, body in [("Your temporary ChatGPT login code", "Enter this temporary verification code to continue: 482913"),
                   ("【阿里云】验证码", "您的验证码为：825 106，5分钟内有效。")]:
    mid = create_message(main, subject=subj, from_addr="Codes <noreply@codes.example>", body_text=body,
                         body_html=f"<p>{body}</p>", date=now - timedelta(minutes=5), is_read=False)
    with session_scope() as db:
        db.get(Message, mid).otp_code = extract_code(subj, body)

# 钓鱼
phishing = create_message(
    main, subject="您的 Apple ID 已被锁定", from_addr='"security@apple.com" <id@apple-verify.example>',
    body_text="请立即验证",
    body_html='<p>请立即验证：<a href="https://apple-verify.example/a">https://appleid.apple.com</a></p>'
              '<p><a href="http://192.168.10.5/x">点此解锁</a></p>',
    date=now - timedelta(minutes=1), is_read=False,
)
# 外部图片
create_message(main, subject="新品上市（含图片）", from_addr="Shop <news@shop.example>",
               body_html='<p>本周新品</p><img src="https://images.example.com/banner.png" alt="banner">',
               date=now - timedelta(minutes=30), is_read=False)

# 标签
with session_scope() as db:
    work = Label(account_id=main, name="工作/项目")
    bills = Label(account_id=main, name="账单")
    db.add_all([work, bills])
    db.flush()
    for mid in message_ids[1:6]:
        db.add(MessageLabel(message_id=mid, label_id=work.id))
    db.add(MessageLabel(message_id=message_ids[7], label_id=bills.id))
    label_ids = [int(work.id), int(bills.id)]

# 垃圾邮件、已删除
create_message(main, subject="恭喜中奖", from_addr="Lottery <win@spam.example>", folder="[Gmail]/Spam", folder_kind="junk")
create_message(main, subject="已删除的旧邮件", from_addr="Old <old@example.com>", folder="[Gmail]/Trash", folder_kind="trash")

# 草稿、定时发送、发件记录
with session_scope() as db:
    db.add(Draft(account_id=main, to_addr="zhang@example.com", subject="草稿：下周计划", body_text="还没写完", body_html="<p>还没写完</p>"))
    db.add(ScheduledSend(account_id=main, to_addr="li@example.com", subject="定时：生日祝福", body_text="生日快乐",
                         body_html="<p>生日快乐</p>", send_at=datetime.utcnow() + timedelta(days=2)))
    db.add(SentMessage(account_id=main, to_addr="zhang@example.com", subject="已发送：会议纪要", body_text="纪要",
                       body_html="<p>纪要</p>", status="sent"))

print(json.dumps({
    "accounts": ids,
    "main": main,
    "outlook": second,
    "messages": message_ids,
    "phishing": phishing,
    "labels": label_ids,
}))
