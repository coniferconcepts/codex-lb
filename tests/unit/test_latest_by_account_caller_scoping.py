from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage.types import BucketModelAggregate, RequestActivityAggregate
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.accounts.repository import AccountRequestUsageSummary, AccountsRepository
from app.modules.accounts.service import AccountsService
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _make_usage_entry(entry_id: int, account_id: str, window: str, used_percent: float) -> UsageHistory:
    return UsageHistory(
        id=entry_id,
        account_id=account_id,
        recorded_at=datetime(2026, 1, 1),
        window=window,
        used_percent=used_percent,
        window_minutes=300 if window == "primary" else 10080,
    )


class _AccountsRepositorySpy(AccountsRepository):
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts
        self._session = None

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def list_request_usage_summary_by_account(
        self,
        account_ids: list[str] | None = None,
    ) -> dict[str, AccountRequestUsageSummary]:
        del account_ids
        return {}

    async def additional_quota_routing_policy_overrides(self) -> dict[str, str]:
        return {}


class _UsageRepositorySpy(UsageRepository):
    def __init__(self) -> None:
        self.latest_calls: list[tuple[str | None, list[str] | None]] = []

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        self.latest_calls.append((window, list(account_ids) if account_ids is not None else None))
        return {}


class _AdditionalUsageRepositorySpy(AdditionalUsageRepository):
    def __init__(self) -> None:
        self.list_quota_keys_calls: list[list[str] | None] = []
        self.latest_calls: list[tuple[str | None, str | None, list[str] | None]] = []

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        del since
        self.list_quota_keys_calls.append(list(account_ids) if account_ids is not None else None)
        return ["codex_spark"]

    async def latest_by_account(
        self,
        quota_key: str | None = None,
        window: str | None = None,
        *,
        limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        del limit_name, since
        self.latest_calls.append((quota_key, window, list(account_ids) if account_ids is not None else None))
        return {}


class _DashboardRepositorySpy(DashboardRepository):
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = accounts
        self.latest_calls: list[tuple[str, list[str] | None]] = []
        account_id = accounts[0].id
        self._latest_usage = {
            "primary": {account_id: _make_usage_entry(1, account_id, "primary", 20.0)},
            "secondary": {account_id: _make_usage_entry(2, account_id, "secondary", 40.0)},
            "monthly": {account_id: _make_usage_entry(3, account_id, "monthly", 60.0)},
        }

    async def list_accounts(self) -> list[Account]:
        return list(self._accounts)

    async def latest_usage_by_account(
        self,
        window: str,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        self.latest_calls.append((window, list(account_ids) if account_ids is not None else None))
        return self._latest_usage[window]

    async def latest_limit_warmups_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        return {}

    async def aggregate_conversations_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketConversationAggregate]:
        del since, bucket_seconds
        return []

    async def bulk_usage_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
    ) -> dict[str, list[UsageHistory]]:
        del account_ids, window, since
        return {}

    async def aggregate_logs_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
    ) -> list[BucketModelAggregate]:
        del since, bucket_seconds
        return []

    async def aggregate_activity_since(self, since: datetime) -> RequestActivityAggregate:
        del since
        return RequestActivityAggregate(
            request_count=0,
            error_count=0,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd=0.0,
        )

    async def aggregate_activity_between(self, since: datetime, until: datetime) -> RequestActivityAggregate:
        del since, until
        return RequestActivityAggregate(
            request_count=0,
            error_count=0,
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cost_usd=0.0,
        )

    async def top_error_between(self, since: datetime, until: datetime) -> str | None:
        del since, until
        return None

    async def earliest_activity_at(self) -> datetime | None:
        return None

    async def latest_additional_recorded_at(self) -> datetime | None:
        return None


@pytest.mark.asyncio
async def test_accounts_service_forwards_loaded_account_ids_to_all_latest_usage_reads() -> None:
    accounts = [_make_account("acc_a"), _make_account("acc_b")]
    accounts_repo = _AccountsRepositorySpy(accounts)
    usage_repo = _UsageRepositorySpy()
    additional_repo = _AdditionalUsageRepositorySpy()
    service = AccountsService(accounts_repo, usage_repo, additional_repo)

    await service.list_accounts()

    expected_ids = ["acc_a", "acc_b"]
    assert usage_repo.latest_calls == [
        ("primary", expected_ids),
        ("secondary", expected_ids),
        ("monthly", expected_ids),
    ]
    assert additional_repo.list_quota_keys_calls == [expected_ids]
    assert additional_repo.latest_calls == [
        ("codex_spark", "primary", expected_ids),
        ("codex_spark", "secondary", expected_ids),
    ]


@pytest.mark.asyncio
async def test_dashboard_service_forwards_loaded_account_ids_for_both_windows() -> None:
    accounts = [_make_account("acc_dashboard")]
    repo = _DashboardRepositorySpy(accounts)
    service = DashboardService(repo)

    await service.get_overview()

    expected_ids = ["acc_dashboard"]
    assert repo.latest_calls == [
        ("primary", expected_ids),
        ("secondary", expected_ids),
        ("monthly", expected_ids),
    ]
