from app.mail.domain import (
    MailAccount,
    MailAttachment,
    MailDraft,
    MailMessage,
    MailObservation,
    MailOperationTask,
    MailThread,
)
from app.mail.current_provider import CurrentImapSmtpMailProvider
from app.mail.fake_provider import FakeMailProvider
from app.mail.harness import MailHarness
from app.mail.provider import MailProvider, MailProviderCapabilities, MailProviderResponse
from app.mail.provider_contract import run_mail_provider_contract

__all__ = [
    "CurrentImapSmtpMailProvider",
    "FakeMailProvider",
    "MailAccount",
    "MailAttachment",
    "MailDraft",
    "MailHarness",
    "MailMessage",
    "MailObservation",
    "MailOperationTask",
    "MailProvider",
    "MailProviderCapabilities",
    "MailProviderResponse",
    "MailThread",
    "run_mail_provider_contract",
]
