import sqlite3
import discord
from discord import app_commands
from discord.ext import tasks
import feedparser
from datetime import datetime
import os

GUILD_ID = 1234  # REPLACE ME


TOKEN = os.getenv("DISCORD_TOKEN")


def log(message, message_type="info"):
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[ {current_date} ] {message}"

    if message_type == "warning":
        colored_message = f"\033[93m{formatted_message}\033[0m"  # Yellow
    elif message_type == "error":
        colored_message = f"\033[91m{formatted_message}\033[0m"  # Red
    elif message_type == "success":
        colored_message = f"\033[92m{formatted_message}\033[0m"  # Green
    else:
        colored_message = formatted_message

    print(colored_message)


class aclient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.synced = (
            False  # we use this so the bot doesn't sync commands more than once
        )

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:  # check if slash commands have been synced
            await tree.sync(
                guild=discord.Object(id=GUILD_ID)
            )  # guild specific: leave blank if global (global registration can take 1-24 hours)
            self.synced = True
        log(f"We have logged in as {self.user}.", "success")
        await refresh_rss()


conn = sqlite3.connect("main.db")
c = conn.cursor()

c.execute(
    """CREATE TABLE IF NOT EXISTS sent_messages
             (identifier TEXT PRIMARY KEY, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
)

c.execute(
    """CREATE TABLE IF NOT EXISTS rss_feeds
             (url TEXT PRIMARY KEY)"""
)

c.execute(
    """CREATE TABLE IF NOT EXISTS bot_config
             (key TEXT PRIMARY KEY, value TEXT)"""
)


def get_setup_data():
    setup_completed = c.execute(
        "SELECT value FROM bot_config WHERE key=?", ("setup_completed",)
    ).fetchone()
    setup_completed = setup_completed[0] == "True" if setup_completed else False

    channel_id = (
        int(
            c.execute(
                "SELECT value FROM bot_config WHERE key=?", ("channel_id",)
            ).fetchone()[0]
        )
        if setup_completed
        else 1
    )

    refresh_interval = (
        int(
            c.execute(
                "SELECT value FROM bot_config WHERE key=?", ("refresh_interval",)
            ).fetchone()[0]
        )
        if c.execute(
            "SELECT value FROM bot_config WHERE key=?", ("refresh_interval",)
        ).fetchone()
        else 500
    )

    rss_feed_urls = [
        feed[0] for feed in c.execute("SELECT url FROM rss_feeds").fetchall()
    ]

    return setup_completed, channel_id, refresh_interval, rss_feed_urls


async def refresh_rss():
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if setup_completed:
        channel = client.get_channel(channel_id)
        for rss_url in rss_feed_urls:
            log(f"Checking: {rss_url}", "info")
            feed = feedparser.parse(rss_url)
            latest_entries = feed.entries[:5]

            for entry in latest_entries:
                message_content = f"**{entry.title}**\n{entry.link}"
                message_id = entry.link

                if not c.execute(
                    "SELECT * FROM sent_messages WHERE identifier=?", (message_id,)
                ).fetchone():
                    message = await channel.send(message_content)
                    thread = await message.create_thread(
                        name=entry.title, auto_archive_duration=60
                    )
                    c.execute(
                        "INSERT INTO sent_messages (identifier) VALUES (?)",
                        (message_id,),
                    )
                    conn.commit()
        log("refreshing feeds: DONE", "info")


client = aclient()
tree = app_commands.CommandTree(client)


@tasks.loop(seconds=500)
async def refresh_task():
    await refresh_rss()


@client.event
async def on_disconnect():
    log("Bot is disconnecting. Closing database connection.", "error")
    conn.close()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="refresh",
    description="Manually refreshes RSS feeds.",
)  # guild specific slash command
async def refresh_feeds(interaction: discord.Interaction):
    """
    Manually refreshes RSS feeds.
    """
    log("Triggered manual refresh", "info")
    await refresh_rss()
    await interaction.response.send_message("Manually refreshed RSS feeds.")


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="setup",
    description="Set up the bot to track RSS feeds in a specific channel.",
)  # guild specific slash command
async def setup(interaction: discord.Interaction, channel_id: str, rss_url: str):
    """
    Set up the bot to track RSS feeds in a specific channel.

    Parameters:
    - channel_id: The ID of the channel where RSS feed updates will be posted.
    - rss_url: The URL of the RSS feed to track.
    """
    setup_completed, _, refresh_interval, rss_feed_urls = get_setup_data()
    if setup_completed:
        await interaction.response.send_message(
            f"Setup already done, to reset the bot please delete main.db"
        )
        return

    if channel_id is None or rss_url is None:
        await interaction.response.send_message(
            "Please provide the necessary arguments:\n`!setup <channel_id> <rss_url>`"
        )
        return

    channel_id, setup_completed = channel_id, True

    c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("channel_id", str(channel_id)),
    )
    c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("setup_completed", "True"),
    )

    c.execute("INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,))
    conn.commit()

    setup_completed, _, refresh_interval, rss_feed_urls = get_setup_data()

    await interaction.response.send_message(
        f"Setup completed! Channel ID set to {channel_id} and first RSS feed added: {rss_url}"
    )
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="add_feed",
    description="Add a new RSS feed to the list.",
)  # guild specific slash command
async def add_feed(interaction: discord.Interaction, rss_url: str):
    """
    Add a new RSS feed to the list.

    Parameters:
    - rss_url: The URL of the RSS feed to add.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `/setup` command."
        )
        return

    if not rss_url:
        await interaction.response.send_message(
            "Please provide the RSS feed URL:\n`/add_feed <rss_url>`"
        )
        return

    c.execute("INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,))
    conn.commit()

    await interaction.response.send_message(f"Added new RSS feed: {rss_url}")
    log(f"Added feed {rss_url}", "info")
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="list_feed",
    description="List all added RSS feeds.",
)  # guild specific slash command
async def list_feed(interaction: discord.Interaction):
    """
    List all added RSS feeds.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `!setup` command."
        )
        return

    if not rss_feed_urls:
        await interaction.response.send_message("No feeds added yet.")
        return

    feed_list = "\n".join(rss_feed_urls)
    log("Listed feeds!", "info")
    await interaction.response.send_message(f"List of RSS feeds:\n{feed_list}")


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="remove_feed",
    description="Remove an RSS feed from the list.",
)  # guild specific slash command
async def remove_feed(interaction: discord.Interaction, rss_url: str):
    """
    Remove an RSS feed from the list.

    Parameters:
    - rss_url: The URL of the RSS feed to remove.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `!setup` command."
        )
        return

    if not rss_url:
        await interaction.response.send_message(
            "Please provide the RSS feed URL:\n`!remove_feed <rss_url>`"
        )
        return

    if rss_url not in rss_feed_urls:
        await interaction.response.send_message(
            f"The provided RSS feed URL is not in the list."
        )
        return

    c.execute("DELETE FROM rss_feeds WHERE url=?", (rss_url,))
    conn.commit()

    await interaction.response.send_message(f"Removed RSS feed: {rss_url}")
    log(f"Removed feed {rss_url}", "info")
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="print_config",
    description="Prints all values from the configuration",
)  # guild specific slash command
async def print_config(interaction: discord.Interaction):
    """
    Print all values from bot_config.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `!setup` command."
        )
        return

    config_values = c.execute("SELECT * FROM bot_config").fetchall()

    if not config_values:
        await interaction.response.send_message("No values found in bot_config.")
        return

    config_output = "\n".join([f"{key}: {value}" for key, value in config_values])
    await interaction.response.send_message(f"Values from bot_config:\n{config_output}")


client.run(TOKEN)
