"""SES account checks and identity verification for the email setup UI.

Everything outward (verification emails, test sends) happens only when a
person clicks a button; reads are cached briefly so pages can poll them.
Nothing here raises for AWS problems: callers get `None`/`False` plus a reason.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from mcp_tools import handlers

ACCOUNT_TTL = 60.0
IDENTITIES_TTL = 20.0
# SES v2 management APIs (CreateEmailIdentity, ...) are limited to 1 request
# per second per account; stay under it.
CREATE_IDENTITY_INTERVAL = 1.1
# Verification emails sent per POST; the UI calls again for the rest.
CREATE_BATCH = 10

SES_CONSOLE_ACCOUNT_URL = "https://console.aws.amazon.com/ses/home?region={region}#/account"

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_sleep: Callable[[float], None] = time.sleep


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _cached(key: str, ttl: float, load: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = load()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _ses() -> Any:
    # Through the handlers' factory so tests patch one place for all SES use.
    return handlers._ses_client(quick=True)


def _aws_client(service: str) -> Any:
    import boto3
    from botocore.config import Config

    config = Config(retries={"max_attempts": 1}, connect_timeout=3, read_timeout=5)
    return boto3.client(service, region_name=handlers._ses_region(), config=config)


def region() -> str:
    return handlers._ses_region() or "us-east-1"


def error_code(exc: Exception) -> str:
    return handlers._aws_error_code(exc) or type(exc).__name__


def error_text(exc: Exception) -> str:
    """`Code: message` as SES/IAM report it (e.g. MessageRejected: Email address is not verified...)."""
    code = handlers._aws_error_code(exc)
    response = getattr(exc, "response", None)
    message = ""
    if isinstance(response, dict):
        message = (response.get("Error") or {}).get("Message") or ""
    if code:
        return f"{code}: {message}".rstrip(": ")
    return f"{type(exc).__name__}: {exc}"[:300]


def _no_credentials(exc: Exception) -> bool:
    return type(exc).__name__ in {"NoCredentialsError", "PartialCredentialsError", "NoRegionError"} or (
        "credential" in type(exc).__name__.lower()
    )


def _unavailable_reason(exc: Exception) -> str:
    if _no_credentials(exc):
        return "The server has no AWS credentials (run `aws login` and restart the API)."
    return f"Couldn't reach SES: {error_text(exc)}"


# --- account -----------------------------------------------------------------------


def account_status() -> dict[str, Any]:
    def load() -> dict[str, Any]:
        base = {
            "region": region(),
            "console_url": SES_CONSOLE_ACCOUNT_URL.format(region=region()),
        }
        try:
            info = _ses().get_account()
        except Exception as exc:
            return {
                **base,
                "sandbox": None,
                "sending_enabled": None,
                "max_24h": None,
                "max_rate": None,
                "sent_last_24h": None,
                "reason": _unavailable_reason(exc),
            }
        quota = info.get("SendQuota") or {}
        sandbox = not info.get("ProductionAccessEnabled", False)
        return {
            **base,
            "sandbox": sandbox,
            "sending_enabled": bool(info.get("SendingEnabled", False)),
            "max_24h": quota.get("Max24HourSend"),
            "max_rate": quota.get("MaxSendRate"),
            "sent_last_24h": quota.get("SentLast24Hours"),
            "enforcement": info.get("EnforcementStatus"),
            "reason": (
                "SES sandbox: mail is delivered only to verified addresses."
                if sandbox
                else "Production access: any recipient can receive mail."
            ),
        }

    return _cached("account", ACCOUNT_TTL, load)


# --- identities ----------------------------------------------------------------------


def _identity_map() -> dict[str, Any]:
    """{identity -> {"verified": bool, "status": str}} for the whole account, or {"_error": reason}."""

    def load() -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            ses = _ses()
            token = None
            while True:
                kwargs: dict[str, Any] = {"PageSize": 1000}
                if token:
                    kwargs["NextToken"] = token
                page = ses.list_email_identities(**kwargs)
                for item in page.get("EmailIdentities") or []:
                    name = str(item.get("IdentityName") or "").lower()
                    if name:
                        out[name] = {
                            "verified": bool(item.get("SendingEnabled"))
                            and item.get("VerificationStatus", "SUCCESS") == "SUCCESS",
                            "status": item.get("VerificationStatus") or "UNKNOWN",
                        }
                token = page.get("NextToken")
                if not token:
                    return out
        except Exception as exc:
            return {"_error": _unavailable_reason(exc)}

    return _cached("identities", IDENTITIES_TTL, load)


def address_statuses(addresses: Iterable[str]) -> dict[str, Any]:
    """Per-address SES status: verified (address or its domain), pending, failed or not_requested.

    Returns {"available": bool, "reason": str|None, "statuses": {addr: status}}.
    """
    identities = _identity_map()
    if "_error" in identities:
        return {"available": False, "reason": identities["_error"], "statuses": {}}
    statuses: dict[str, str] = {}
    for address in addresses:
        addr = address.strip().lower()
        domain = addr.rsplit("@", 1)[-1]
        own, dom = identities.get(addr), identities.get(domain)
        if (own and own["verified"]) or (dom and dom["verified"]):
            statuses[addr] = "verified"
        elif own and own["status"] in {"PENDING", "NOT_STARTED"}:
            statuses[addr] = "pending"
        elif own and own["status"] in {"FAILED", "TEMPORARY_FAILURE"}:
            statuses[addr] = "failed"
        else:
            statuses[addr] = "not_requested"
    return {"available": True, "reason": None, "statuses": statuses}


def unverified(addresses: Iterable[str]) -> list[str] | None:
    """Addresses SES would reject in the sandbox; None if that can't be told."""
    result = address_statuses(addresses)
    if not result["available"]:
        return None
    return sorted(a for a, s in result["statuses"].items() if s != "verified")


class SesActionError(Exception):
    """A person-triggered SES action failed. `status` is the HTTP status to return."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


def _action_error(exc: Exception) -> SesActionError:
    code = error_code(exc)
    if _no_credentials(exc):
        return SesActionError(400, "no_credentials", _unavailable_reason(exc))
    if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
        return SesActionError(403, "access_denied", f"AWS refused: {error_text(exc)}")
    return SesActionError(502, "ses_error", error_text(exc))


def request_verification(address: str) -> dict[str, Any]:
    """CreateEmailIdentity: SES emails `address` a verification link.

    AlreadyExists returns the current status instead of failing.
    Raises SesActionError for credential/permission/SES problems.
    """
    address = address.strip().lower()
    try:
        _ses().create_email_identity(EmailIdentity=address)
        outcome = "sent"
    except Exception as exc:
        if error_code(exc) != "AlreadyExistsException":
            raise _action_error(exc) from exc
        outcome = "already_exists"
    finally:
        with _lock:
            _cache.pop("identities", None)
    status = address_statuses([address])["statuses"].get(address, "pending")
    if outcome == "already_exists" and status == "verified":
        message = f"{address} is already verified."
    elif outcome == "already_exists":
        message = (
            f"A verification email was already sent to {address}. Check that inbox (and spam) and "
            "click the AWS link; links expire after 24 hours."
        )
    else:
        message = f"SES sent a verification email to {address}. Click the link in it, then refresh."
    return {"address": address, "outcome": outcome, "status": status, "message": message}


def request_verifications(addresses: list[str], limit: int = CREATE_BATCH) -> dict[str, Any]:
    """Verification emails for every address that isn't verified or pending, `limit` per call."""
    current = address_statuses(addresses)
    if not current["available"]:
        raise SesActionError(400, "unavailable", current["reason"])
    todo = [a for a in addresses if current["statuses"].get(a) in {"not_requested", "failed"}]
    batch, rest = todo[:limit], todo[limit:]
    requested, failed = [], []
    for i, address in enumerate(batch):
        if i:
            _sleep(CREATE_IDENTITY_INTERVAL)
        try:
            _ses().create_email_identity(EmailIdentity=address)
            requested.append(address)
        except Exception as exc:
            if error_code(exc) == "AlreadyExistsException":
                requested.append(address)
                continue
            err = _action_error(exc)
            if err.status in {400, 403}:  # no point trying the rest
                with _lock:
                    _cache.pop("identities", None)
                raise err from exc
            failed.append({"address": address, "error": str(err)})
    with _lock:
        _cache.pop("identities", None)
    return {
        "requested": requested,
        "failed": failed,
        "remaining": len(rest),
        "already_verified": sum(1 for s in current["statuses"].values() if s == "verified"),
        "already_pending": sum(1 for s in current["statuses"].values() if s == "pending"),
    }


# --- permissions -----------------------------------------------------------------------

PERMISSION_ACTIONS = {"send": "ses:SendEmail", "create_identity": "ses:CreateEmailIdentity"}


def _role_arn(caller_arn: str) -> str:
    """arn:aws:sts::A:assumed-role/Role/session -> arn:aws:iam::A:role/Role (else unchanged)."""
    if ":assumed-role/" not in caller_arn:
        return caller_arn
    prefix, rest = caller_arn.split(":assumed-role/", 1)
    role = rest.split("/", 1)[0]
    return prefix.replace(":sts:", ":iam:") + f":role/{role}"


def permissions() -> dict[str, Any]:
    def load() -> dict[str, Any]:
        out: dict[str, Any] = {key: None for key in PERMISSION_ACTIONS}
        try:
            caller = _aws_client("sts").get_caller_identity()["Arn"]
        except Exception as exc:
            return {**out, "principal": None, "reason": _unavailable_reason(exc)}
        principal = _role_arn(caller)
        iam = _aws_client("iam")
        if principal != caller:
            # A role with a path (role/path/Name) needs its real ARN.
            try:
                principal = iam.get_role(RoleName=principal.rsplit("/", 1)[1])["Role"]["Arn"]
            except Exception:
                pass
        try:
            result = iam.simulate_principal_policy(
                PolicySourceArn=principal, ActionNames=list(PERMISSION_ACTIONS.values())
            )
        except Exception as exc:
            return {
                **out,
                "principal": principal,
                "reason": (
                    f"Couldn't check IAM permissions ({error_code(exc)}). "
                    "Use “Send test email” to find out for sure."
                ),
            }
        decisions = {r.get("EvalActionName"): r.get("EvalDecision") for r in result.get("EvaluationResults") or []}
        for key, action in PERMISSION_ACTIONS.items():
            decision = decisions.get(action)
            out[key] = None if decision is None else decision == "allowed"
        return {
            **out,
            "principal": principal,
            "reason": (
                "From IAM's policy simulator. Organization-level policies (SCPs) aren't included, "
                "so a test send is the final proof."
            ),
        }

    return _cached("permissions", ACCOUNT_TTL, load)
