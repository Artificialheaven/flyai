"""SMTP 邮件发送：注册验证码 / 找回密码 / 管理员测试发信。

仅用标准库 ``smtplib`` + ``email.message``，不引入第三方依赖。
QQ 邮箱：``smtp.qq.com:465``（SSL，推荐）或 ``:587``（STARTTLS）；
密码填「授权码」（QQ 邮箱设置 → 账户 → 开启 SMTP 服务后生成），不是 QQ 密码。
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注
    from .config import EmailSettings

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HEADER_BREAK = re.compile(r"[\r\n]+")

# 用途 → 邮件文案里的中文标签
PURPOSE_LABELS = {"register": "注册", "reset": "密码重置"}

SMTP_TIMEOUT = 15


class MailError(Exception):
    """邮件发送失败。message 面向用户，不包含授权码等机密。"""


def sanitize_header(value: str) -> str:
    """去掉邮件头里的换行，防止头注入。"""
    return _HEADER_BREAK.sub(" ", value or "").strip()


def normalize_email(raw: str) -> str:
    """规范邮箱：去空白、转小写并做基本格式校验；非法抛 MailError。"""
    email = (raw or "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL_RE.match(email):
        raise MailError("邮箱格式不正确")
    return email


def _sender(cfg: "EmailSettings") -> str:
    addr = sanitize_header(cfg.from_address or cfg.username)
    if not addr:
        raise MailError("未配置发件地址")
    return addr


def build_code_mail(
    cfg: "EmailSettings", code: str, purpose: str, ttl_seconds: int
) -> Tuple[str, str]:
    """构造验证码邮件的 (主题, 正文)。"""
    label = PURPOSE_LABELS.get(purpose, "验证")
    minutes = max(1, int(round(int(ttl_seconds or 600) / 60.0)))
    subject = "FlyAI %s验证码" % label
    text = (
        "你正在使用 FlyAI 进行%s。\n\n"
        "验证码：%s\n"
        "有效期：%d 分钟\n\n"
        "如果这不是你本人的操作，请忽略本邮件。\n"
        "—— FlyAI"
    ) % (label, code, minutes)
    return subject, text


class Mailer:
    """无状态发信器；阻塞调用，异步路由请放到线程池执行。"""

    def send(
        self,
        cfg: "EmailSettings",
        to: str,
        subject: str,
        text: str,
    ) -> None:
        """同步发送纯文本邮件；失败抛 MailError。"""
        recipient = normalize_email(to)
        sender = _sender(cfg)
        username = (cfg.username or "").strip()
        password = cfg.password or ""
        if not username or not password:
            raise MailError("SMTP 账号或授权码未配置")

        msg = EmailMessage()
        msg["Subject"] = sanitize_header(subject)
        msg["From"] = formataddr(
            (sanitize_header(cfg.from_name or "FlyAI"), sender)
        )
        msg["To"] = recipient
        msg["Date"] = formatdate(localtime=True)
        msg.set_content(text, charset="utf-8")

        context = ssl.create_default_context()
        conn = None
        try:
            if cfg.encryption == "ssl":
                conn = smtplib.SMTP_SSL(
                    cfg.host,
                    int(cfg.port),
                    timeout=SMTP_TIMEOUT,
                    context=context,
                )
            else:
                conn = smtplib.SMTP(
                    cfg.host, int(cfg.port), timeout=SMTP_TIMEOUT
                )
                conn.ehlo()
                if cfg.encryption == "starttls":
                    conn.starttls(context=context)
                    conn.ehlo()
            conn.login(username, password)
            conn.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            raise MailError("SMTP 认证失败（QQ 邮箱请使用授权码）")
        except (smtplib.SMTPConnectError, socket.gaierror, ConnectionRefusedError):
            raise MailError("无法连接 SMTP 服务器")
        except ssl.SSLError:
            raise MailError("无法建立加密连接（请检查端口与加密方式）")
        except socket.timeout:
            raise MailError("连接超时")
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
            raise MailError("收件人或发件人被拒绝")
        except smtplib.SMTPException as exc:
            logger.warning("SMTP 发送失败: %s", type(exc).__name__)
            raise MailError("邮件发送失败")
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一为可读错误
            logger.warning("邮件发送异常: %s", type(exc).__name__)
            raise MailError("邮件发送失败")
        finally:
            if conn is not None:
                try:
                    conn.quit()
                except Exception:  # noqa: BLE001 - 连接已断开时忽略
                    pass

    def send_code(
        self,
        cfg: "EmailSettings",
        to: str,
        code: str,
        purpose: str,
    ) -> None:
        subject, text = build_code_mail(
            cfg, code, purpose, getattr(cfg, "code_ttl_seconds", 600)
        )
        self.send(cfg, to, subject, text)

    def send_test(self, cfg: "EmailSettings", to: str) -> None:
        subject = "FlyAI 邮件服务测试"
        text = (
            "这是一封来自 FlyAI 的测试邮件。\n\n"
            "SMTP 服务器：%s:%s（%s）\n"
            "如果你收到本邮件，说明邮件服务配置可用。\n"
            "—— FlyAI"
        ) % (cfg.host, cfg.port, cfg.encryption)
        self.send(cfg, to, subject, text)
