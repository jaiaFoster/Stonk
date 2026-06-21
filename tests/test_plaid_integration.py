"""
tests/test_plaid_integration.py — Plaid dual-path broker integration tests.

Verifies:
- Plaid config vars present
- DB migration adds plaid columns
- store_plaid_tokens / get_plaid_access_token round-trip
- PlaidCredentialProvider registered in get_provider()
- Plaid normalization functions (holdings → ASA shape)
- Option quantity ÷ 100 normalization
- Account type classification
- Personalization broker_type dispatch (Plaid vs Robinhood)
- Redaction service includes PLAID_SECRET
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "users_plaid.db")
    with patch("app.config.USERS_DB_PATH", db_path):
        from app.db.users import init_db
        init_db()
    return db_path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestPlaidConfig:
    def test_plaid_config_vars_exist(self):
        from app import config
        assert hasattr(config, "PLAID_CLIENT_ID")
        assert hasattr(config, "PLAID_SECRET")
        assert hasattr(config, "PLAID_ENV")
        assert hasattr(config, "PLAID_REFRESH_ON_EVERY_RUN")

    def test_plaid_env_default(self):
        from app import config
        assert config.PLAID_ENV in ("sandbox", "development", "production")


# ---------------------------------------------------------------------------
# DB migration
# ---------------------------------------------------------------------------

class TestPlaidMigration:
    def test_plaid_columns_exist(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
        conn.close()
        assert "plaid_access_token_encrypted" in cols
        assert "plaid_item_id" in cols


# ---------------------------------------------------------------------------
# Credential storage round-trip
# ---------------------------------------------------------------------------

def _insert_test_user(db_path, username="testuser"):
    """Insert a minimal test user via raw SQL (avoids bcrypt dependency)."""
    import secrets as _secrets
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (username, password_hash, api_key, broker_type) VALUES (?, ?, ?, ?)",
        (username, "fake_hash", _secrets.token_hex(16), "robinhood"),
    )
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return user_id


class TestPlaidCredentialStorage:
    def test_store_and_retrieve(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import store_plaid_tokens, get_plaid_access_token
            with patch("app.db.users.encrypt_credential", side_effect=lambda v: "ENC:" + v):
                with patch("app.db.users.decrypt_credential", side_effect=lambda v: v.replace("ENC:", "")):
                    user_id = _insert_test_user(temp_db, "plaid_test")
                    store_plaid_tokens(user_id, "access-sandbox-abc123", "item-xyz")
                    retrieved = get_plaid_access_token(user_id)
                    assert retrieved == "access-sandbox-abc123"

                    conn = sqlite3.connect(temp_db)
                    row = conn.execute("SELECT broker_type, plaid_item_id FROM users WHERE id=?", (user_id,)).fetchone()
                    conn.close()
                    assert row[0] == "plaid"
                    assert row[1] == "item-xyz"

    def test_no_token_returns_none(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.db.users import get_plaid_access_token
            with patch("app.db.users.decrypt_credential", side_effect=lambda v: v):
                user_id = _insert_test_user(temp_db, "no_plaid")
                assert get_plaid_access_token(user_id) is None


# ---------------------------------------------------------------------------
# Provider registration
# ---------------------------------------------------------------------------

class TestProviderRegistration:
    def test_get_provider_plaid(self):
        from app.services.broker_provider import BrokerCredentialProvider, PlaidCredentialProvider
        provider = BrokerCredentialProvider.get_provider("plaid")
        assert isinstance(provider, PlaidCredentialProvider)
        assert provider.broker_type() == "plaid"

    def test_get_provider_robinhood_still_works(self):
        from app.services.broker_provider import BrokerCredentialProvider, RobinhoodCredentialProvider
        provider = BrokerCredentialProvider.get_provider("robinhood")
        assert isinstance(provider, RobinhoodCredentialProvider)

    def test_get_provider_unknown_raises(self):
        from app.services.broker_provider import BrokerCredentialProvider
        with pytest.raises(ValueError):
            BrokerCredentialProvider.get_provider("schwab")


# ---------------------------------------------------------------------------
# Normalization functions
# ---------------------------------------------------------------------------

class TestPlaidNormalization:
    def test_normalize_holding(self):
        from app.services.broker_provider import _normalize_plaid_holding
        holding = {"quantity": 10, "cost_basis": 1000, "institution_value": 1200}
        security = {"ticker_symbol": "AAPL", "close_price": 120}
        account = {"account_id": "acct_123", "subtype": "brokerage"}
        result = _normalize_plaid_holding(holding, security, account)
        assert result["ticker"] == "AAPL"
        assert result["quantity"] == 10
        assert result["avg_cost"] == 100.0
        assert result["current_price"] == 120.0
        assert result["market_value"] == 1200.0
        assert result["account_type"] == "Individual"
        assert result["account_number"] == "acct_123"
        assert result["_broker"] == "plaid"

    def test_normalize_holding_pnl(self):
        from app.services.broker_provider import _normalize_plaid_holding
        holding = {"quantity": 5, "cost_basis": 500, "institution_value": 600}
        security = {"ticker_symbol": "GOOG", "close_price": 120}
        account = {"account_id": "a1", "subtype": "ira"}
        result = _normalize_plaid_holding(holding, security, account)
        assert result["unrealized_pnl_pct"] == 20.0
        assert result["account_type"] == "Traditional IRA"

    def test_normalize_option_quantity_divided_by_100(self):
        """Plaid option quantity = shares (contracts × 100). Must divide by 100."""
        from app.services.broker_provider import _normalize_plaid_option
        holding = {"quantity": 200, "cost_basis": 500, "security_id": "sec1"}
        security = {
            "type": "derivative",
            "option_contract": {
                "underlying_security_ticker": "TSLA",
                "contract_type": "call",
                "strike_price": 250.0,
                "expiration_date": "2025-08-15",
            }
        }
        account = {"account_id": "acct_1", "subtype": "brokerage"}
        result = _normalize_plaid_option(holding, security, account)
        assert result["quantity"] == "2.0"
        assert result["chain_symbol"] == "TSLA"
        assert result["type"] == "call"
        assert result["strike_price"] == "250.0"
        assert result["expiration_date"] == "2025-08-15"
        assert result["_plaid_raw_quantity"] == 200
        assert result["_broker"] == "plaid"

    def test_normalize_option_single_contract(self):
        from app.services.broker_provider import _normalize_plaid_option
        holding = {"quantity": 100, "cost_basis": 300}
        security = {
            "option_contract": {
                "underlying_security_ticker": "SPY",
                "contract_type": "put",
                "strike_price": 450.0,
                "expiration_date": "2025-09-19",
            }
        }
        account = {"account_id": "a2", "subtype": "roth"}
        result = _normalize_plaid_option(holding, security, account)
        assert result["quantity"] == "1.0"
        assert result["type"] == "put"
        assert result["_source_account_type"] == "Roth IRA"


# ---------------------------------------------------------------------------
# Account type classification
# ---------------------------------------------------------------------------

class TestPlaidAccountClassification:
    def test_known_types(self):
        from app.services.broker_provider import _classify_plaid_account_type
        cases = {
            "roth": "Roth IRA",
            "ira": "Traditional IRA",
            "roth 401k": "Roth 401k",
            "401k": "401k",
            "brokerage": "Individual",
        }
        for subtype, expected in cases.items():
            assert _classify_plaid_account_type({"subtype": subtype}) == expected

    def test_unknown_type_titlecased(self):
        from app.services.broker_provider import _classify_plaid_account_type
        assert _classify_plaid_account_type({"subtype": "hsa"}) == "Hsa"

    def test_empty_subtype(self):
        from app.services.broker_provider import _classify_plaid_account_type
        assert _classify_plaid_account_type({}) == "Unknown"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

class TestRedactionIncludesPlaid:
    def test_plaid_secret_in_known_secrets_names(self):
        from app.services.redaction_service import known_secrets
        names_str = str(known_secrets)
        from app.services import redaction_service
        import inspect
        source = inspect.getsource(redaction_service.known_secrets)
        assert "PLAID_SECRET" in source
        assert "PLAID_CLIENT_ID" in source


# ---------------------------------------------------------------------------
# Personalization dispatch
# ---------------------------------------------------------------------------

class TestPersonalizationBrokerDispatch:
    def test_plaid_user_no_token_returns_no_creds(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.services.personalization import run_personalization
            user = {"id": 1, "broker_type": "plaid"}
            result = run_personalization(1, user)
            assert result["reason"] == "no_broker_credentials"

    def test_robinhood_user_no_creds_returns_no_creds(self, temp_db):
        with patch("app.config.USERS_DB_PATH", temp_db):
            from app.services.personalization import run_personalization
            user = {"id": 1, "broker_type": "robinhood"}
            result = run_personalization(1, user)
            assert result["reason"] == "no_broker_credentials"
