"""
userbot.py — Telethon-based userbot manager.

Responsibilities:
  • OTP + 2FA login flow driven by the admin via the Telegram bot
  • Persist session to disk so it survives restarts
  • Forward / send large files (>50 MB) that the Bot API cannot handle
"""

import logging
import os
import tempfile
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    PhoneNumberInvalidError,
)
from telethon.tl.types import InputPeerUser

from config import Config

logger = logging.getLogger("apk_bot.userbot")


class UserbotManager:
    """
    Wraps a Telethon client with an async login flow
    suitable for being driven by Telegram bot callbacks.
    """

    def __init__(self):
        self._client:      Optional[TelegramClient] = None
        self._phone:       Optional[str]             = None
        self._phone_hash:  Optional[str]             = None
        self._connected:   bool                      = False

    # ─────────────────── Connection state ──────────────────────────────────
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def get_me(self) -> str:
        """Return a human-readable label for the connected account."""
        if not self.is_connected():
            return "Not connected"
        try:
            me = await self._client.get_me()
            name = " ".join(filter(None, [me.first_name, me.last_name]))
            return f"{name} (@{me.username})" if me.username else name
        except Exception as e:
            logger.error(f"get_me failed: {e}")
            return "Unknown"

    # ─────────────────── Session restore ───────────────────────────────────
    async def try_restore(self):
        """
        Called at startup.  If a saved session file exists, reconnect
        automatically without asking for OTP again.
        """
        session_path = Config.SESSION_FILE + ".session"
        if not os.path.exists(session_path):
            logger.info("No saved userbot session found.")
            return

        try:
            client = TelegramClient(
                Config.SESSION_FILE,
                Config.API_ID,
                Config.API_HASH,
            )
            await client.connect()
            if await client.is_user_authorized():
                self._client    = client
                self._connected = True
                me = await self.get_me()
                logger.info(f"Userbot restored: {me}")
            else:
                await client.disconnect()
                logger.warning("Saved session exists but is not authorised — ignoring.")
        except Exception as e:
            logger.error(f"Session restore failed: {e}")

    # ─────────────────── Login flow ────────────────────────────────────────
    async def start_login(self, phone: str) -> dict:
        """
        Step 1 — Send OTP to the phone number.
        Returns {"ok": True} or {"error": "..."}.
        """
        try:
            client = TelegramClient(
                Config.SESSION_FILE,
                Config.API_ID,
                Config.API_HASH,
            )
            await client.connect()

            # Store temporarily; will be replaced on successful auth
            self._client = client
            self._phone  = phone

            result = await client.send_code_request(phone)
            self._phone_hash = result.phone_code_hash
            logger.info(f"OTP sent to {phone}")
            return {"ok": True}

        except PhoneNumberInvalidError:
            return {"error": "Invalid phone number format."}
        except Exception as e:
            logger.error(f"start_login error: {e}")
            return {"error": str(e)}

    async def submit_code(self, code: str) -> dict:
        """
        Step 2 — Submit the OTP.
        Returns {"ok": True}, {"need_2fa": True}, or {"error": "..."}.
        """
        if not self._client or not self._phone or not self._phone_hash:
            return {"error": "Login not started. Use start_login first."}

        try:
            await self._client.sign_in(
                phone=self._phone,
                code=code,
                phone_code_hash=self._phone_hash,
            )
            self._connected = True
            logger.info("Userbot signed in successfully (no 2FA).")
            return {"ok": True}

        except SessionPasswordNeededError:
            return {"need_2fa": True}

        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
            return {"error": "Invalid or expired code. Try logging in again."}

        except Exception as e:
            logger.error(f"submit_code error: {e}")
            return {"error": str(e)}

    async def submit_2fa(self, password: str) -> dict:
        """
        Step 3 (optional) — Submit 2FA password.
        Returns {"ok": True} or {"error": "..."}.
        """
        if not self._client:
            return {"error": "Login not started."}

        try:
            await self._client.sign_in(password=password)
            self._connected = True
            logger.info("Userbot signed in with 2FA.")
            return {"ok": True}

        except PasswordHashInvalidError:
            return {"error": "Incorrect 2FA password."}

        except Exception as e:
            logger.error(f"submit_2fa error: {e}")
            return {"error": str(e)}

    # ─────────────────── Disconnect ────────────────────────────────────────
    async def disconnect(self):
        """Gracefully close the Telethon session."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.error(f"Disconnect error: {e}")
            finally:
                self._client    = None
                self._connected = False
                logger.info("Userbot disconnected.")

    # ─────────────────── File delivery ─────────────────────────────────────
    async def send_local_file(
        self,
        to_user_id: int,
        file_path: str,
        caption: str = "",
    ) -> bool:
        """
        Send a file that already exists on disk via Telethon.
        Used for APKs >50 MB that the Bot API cannot send.
        """
        if not self.is_connected():
            logger.error("send_local_file: userbot not connected.")
            return False
        try:
            await self._client.send_file(
                to_user_id,
                file_path,
                caption=caption,
                parse_mode="html",
            )
            logger.info(f"Sent {file_path} to {to_user_id} via userbot.")
            return True
        except Exception as e:
            logger.error(f"send_local_file failed: {e}")
            return False

    async def forward_file(
        self,
        bot,                   # aiogram Bot instance (to download the file)
        file_id: str,
        to_user_id: int,
        caption: str = "",
    ) -> bool:
        """
        Download a file via Bot API and re-upload it via Telethon to bypass
        the Bot API's 50 MB download/upload limit for end users.

        Returns True on success, False on failure.
        """
        if not self.is_connected():
            logger.error("forward_file called but userbot is not connected.")
            return False

        tmp_path: Optional[str] = None
        try:
            # 1. Download the file from Telegram servers via aiogram
            file_info = await bot.get_file(file_id)
            tmp_fd, tmp_path = tempfile.mkstemp(suffix="_apk_delivery")
            os.close(tmp_fd)

            await bot.download_file(file_info.file_path, destination=tmp_path)
            logger.info(f"Downloaded {file_id} to {tmp_path}")

            # 2. Send via Telethon to the user
            await self._client.send_file(
                to_user_id,
                tmp_path,
                caption=caption,
                parse_mode="html",
            )
            logger.info(f"Sent {tmp_path} to user {to_user_id} via userbot.")
            return True

        except Exception as e:
            logger.error(f"forward_file failed: {e}")
            return False

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
