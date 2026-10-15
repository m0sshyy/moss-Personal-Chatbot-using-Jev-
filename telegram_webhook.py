"""
Tells Telegram where to deliver your bot's messages.

    .venv\\Scripts\\python telegram_webhook.py set https://<your-cloud-run-address>
        -> messages go to the cloud bot (24/7)
    .venv\\Scripts\\python telegram_webhook.py commands
        -> updates the menu Telegram shows when you type "/"
    .venv\\Scripts\\python telegram_webhook.py info
        -> shows where messages currently go, and any delivery errors
    .venv\\Scripts\\python telegram_webhook.py delete
        -> stop sending to the cloud (e.g. to run the bot on your PC again;
           starting MosshyBot.py on the PC also does this automatically)

Only one place can receive the messages at a time: the cloud or your PC.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import Bot


async def main() -> None:
    load_dotenv()
    command = sys.argv[1] if len(sys.argv) > 1 else "info"
    async with Bot(os.environ["TELEGRAM_BOT_TOKEN"]) as bot:
        if command == "set":
            url = sys.argv[2].rstrip("/") + "/telegram"
            await bot.set_webhook(url, secret_token=os.environ["TELEGRAM_WEBHOOK_SECRET"],
                                  allowed_updates=["message", "callback_query"])
            print(f"Telegram now delivers messages to {url}")
        if command in ("set", "commands"):
            # The menu Telegram shows when you type "/" (the list lives in MosshyBot.py).
            from MosshyBot import COMMANDS
            await bot.set_my_commands(COMMANDS)
            print(f"Command menu updated ({len(COMMANDS)} commands)")
        elif command == "delete":
            await bot.delete_webhook()
            print("Webhook removed. Run MosshyBot.py on your PC to receive messages there.")
        info = await bot.get_webhook_info()
        print(f"Delivering to: {info.url or '(nothing - the PC bot polls for messages)'}")
        print(f"Messages waiting: {info.pending_update_count}")
        if info.last_error_message:
            print(f"Last delivery error: {info.last_error_message} ({info.last_error_date})")


asyncio.run(main())
