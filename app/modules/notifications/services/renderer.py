"""
Notification template renderer.

Replaces {{variable}} placeholders with values from the context dict.
Case-insensitive keys, graceful fallback to empty string for missing vars.

Supported variables:
    payer_name, amount_due, amount_paid, balance, due_date,
    account_no, description, collection_name, currency,
    psp_ref, transaction_date, phone
"""
import re
from typing import Any, Dict


_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, context: Dict[str, Any]) -> str:
    """
    Render a template string by substituting {{variable}} placeholders.

    >>> render("Hi {{payer_name}}, you owe {{amount_due}}", {"payer_name": "Kamau", "amount_due": "12,000"})
    'Hi Kamau, you owe 12,000'
    """
    normalised = {k.lower(): str(v) if v is not None else "" for k, v in context.items()}

    def replace(match: re.Match) -> str:
        return normalised.get(match.group(1).lower(), "")

    return _PATTERN.sub(replace, template)


def build_context(
    payer_name: str = "",
    amount_due: float = 0,
    amount_paid: float = 0,
    balance: float = 0,
    due_date: str = "",
    account_no: str = "",
    description: str = "",
    collection_name: str = "",
    currency: str = "KES",
    psp_ref: str = "",
    transaction_date: str = "",
    phone: str = "",
    **extra,
) -> Dict[str, Any]:
    """
    Build a standard rendering context dict.
    Pass **extra to forward any tenant-specific meta fields.
    """
    ctx = {
        "payer_name":       payer_name,
        "amount_due":       f"{amount_due:,.2f}",
        "amount_paid":      f"{amount_paid:,.2f}",
        "balance":          f"{balance:,.2f}",
        "due_date":         due_date,
        "account_no":       account_no,
        "description":      description,
        "collection_name":  collection_name,
        "currency":         currency,
        "psp_ref":          psp_ref,
        "transaction_date": transaction_date,
        "phone":            phone,
    }
    ctx.update(extra)
    return ctx
