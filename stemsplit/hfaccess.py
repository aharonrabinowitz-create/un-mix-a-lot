"""Hugging Face access for SAM Audio, in plain words.

SAM Audio's weights are gated by Meta: a person needs a free Hugging Face account, has to request access on
each model page, and needs a token. This module checks where someone is in that process and saves the token
(through huggingface_hub's own login, so SAM Audio finds it without any extra setup)."""
from __future__ import annotations

import os
from pathlib import Path

SIZES = ("small", "base", "large")
TOKEN_PAGE = "https://huggingface.co/settings/tokens/new?tokenType=read"


def model_id(size: str) -> str:
    return f"facebook/sam-audio-{size}"


def model_page(size: str) -> str:
    return f"https://huggingface.co/{model_id(size)}"


def _mock() -> str:
    return os.environ.get("STEMSPLIT_MOCK_HF", "")  # tests only: none / ok / pending / badtoken


def get_token() -> str | None:
    if _mock():
        return None if _mock() == "none" else "hf_mock"
    try:
        from huggingface_hub import get_token as _gt
        return _gt() or None
    except Exception:
        return None


def is_cached(size: str) -> bool:
    """True when this model's weights are already on the PC (then no account is needed to run it)."""
    try:
        from huggingface_hub import constants
        snaps = Path(constants.HF_HUB_CACHE) / ("models--facebook--sam-audio-" + size) / "snapshots"
        return snaps.is_dir() and any(any(d.iterdir()) for d in snaps.iterdir() if d.is_dir())
    except Exception:
        return False


def needs_setup(size: str) -> bool:
    return not is_cached(size) and get_token() is None


def save_token(token: str) -> None:
    """Check the token with Hugging Face and keep it on this PC. Raises ValueError with a plain message."""
    token = (token or "").strip()
    if not token:
        raise ValueError("Paste your Hugging Face token first.")
    if _mock():
        return
    try:
        from huggingface_hub import login
        login(token=token, add_to_git_credential=False)
    except Exception as e:
        msg = str(e)
        if any(k in msg for k in ("401", "Invalid", "invalid", "Unauthorized")):
            raise ValueError("Hugging Face didn't accept that token. Check you copied all of it.") from e
        raise ValueError("Couldn't reach Hugging Face to check the token. Check your internet and try again.") from e


def check(size: str) -> str:
    """One of: ok, pending, notoken, badtoken, offline."""
    if _mock():
        return {"none": "notoken"}.get(_mock(), _mock())
    if is_cached(size):
        return "ok"
    tok = get_token()
    if not tok:
        return "notoken"
    try:
        from huggingface_hub import HfApi
        from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
    except Exception:
        return "offline"
    try:
        HfApi().auth_check(model_id(size), token=tok)
        return "ok"
    except GatedRepoError:
        return "pending"
    except RepositoryNotFoundError:
        return "badtoken"
    except HfHubHTTPError as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        return "badtoken" if code == 401 else ("pending" if code == 403 else "offline")
    except Exception:
        return "offline"


WORDS = {
    "ok": "Approved. You're ready to use it.",
    "pending": "Not approved yet. Request access on the model page, then wait for Meta's approval email.",
    "notoken": "No token saved yet.",
    "badtoken": "Hugging Face didn't accept the saved token. Make a new one and save it.",
    "offline": "Couldn't reach Hugging Face. Check your internet.",
}


def is_access_error(e: Exception) -> bool:
    m = str(e)
    return any(k in m for k in ("401", "403", "gated", "restricted", "Access to model", "denied access"))


SETUP_HINT = "SAM Audio needs a free Hugging Face account first. Open Settings, then the Model tab, to set it up."
