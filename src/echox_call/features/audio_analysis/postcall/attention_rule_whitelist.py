"""Whitelist matching for selecting alternate postcall attention rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AttentionRuleWhitelist:
    enabled: bool
    names: frozenset[str]
    phones: frozenset[str]

    def matches_job(self, job: Any) -> bool:
        if not self.enabled:
            return False

        names = _candidate_names(job)
        if self.names and any(name in self.names for name in names):
            return True

        phones = _candidate_phones(job)
        return bool(self.phones and any(phone in self.phones for phone in phones))


def load_attention_rule_whitelist(path: Path) -> AttentionRuleWhitelist:
    if not path.exists():
        return AttentionRuleWhitelist(enabled=False, names=frozenset(), phones=frozenset())

    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        return AttentionRuleWhitelist(enabled=False, names=frozenset(), phones=frozenset())

    names = {_normalize_name(value) for value in _configured_values(config, "names")}
    phones = {_normalize_phone(value) for value in _configured_values(config, "phones")}
    for item in config.get("people") or []:
        if not isinstance(item, dict):
            continue
        names.add(_normalize_name(item.get("name")))
        phones.add(_normalize_phone(item.get("phone")))

    return AttentionRuleWhitelist(
        enabled=bool(config.get("enabled", True)),
        names=frozenset(item for item in names if item),
        phones=frozenset(item for item in phones if item),
    )


def _configured_values(config: dict[str, Any], key: str) -> list[Any]:
    values = config.get(key) or []
    return values if isinstance(values, list) else []


def _candidate_names(job: Any) -> set[str]:
    raw_payload = _raw_payload(job)
    return {
        name
        for name in (
            _normalize_name(getattr(job, "bjrmc", None)),
            _normalize_name(raw_payload.get("bjrmc")),
        )
        if name
    }


def _candidate_phones(job: Any) -> set[str]:
    raw_payload = _raw_payload(job)
    return {
        phone
        for phone in (
            _normalize_phone(getattr(job, "bjdh", None)),
            _normalize_phone(getattr(job, "lxdh", None)),
            _normalize_phone(raw_payload.get("bjdh")),
            _normalize_phone(raw_payload.get("lxdh")),
        )
        if phone
    }


def _raw_payload(job: Any) -> dict[str, Any]:
    value = getattr(job, "raw_payload", None)
    return value if isinstance(value, dict) else {}


def _normalize_name(value: Any) -> str:
    return str(value or "").strip()


def _normalize_phone(value: Any) -> str:
    phone = str(value or "").strip()
    for char in (" ", "-", "(", ")", "\t", "\n"):
        phone = phone.replace(char, "")
    if phone.startswith("+86") and len(phone) > 11:
        phone = phone[3:]
    if phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    return phone
