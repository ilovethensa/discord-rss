import asyncio
import sqlite3
import discord
from discord import app_commands
from discord.ext import tasks
import feedparser
from datetime import datetime
import os

GUILD_ID = os.getenv("GUILD_ID")
REFRESH_INTERVAL = 500
TOKEN = os.getenv("DISCORD_TOKEN")

# Constants for message types
INFO = "info"
WARNING = "warning"
ERROR = "error"
SUCCESS = "success"

# Constants for database setup
DB_NAME = "main.db"
TABLES = {
    "sent_messages": "(identifier TEXT PRIMARY KEY, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)",
    "rss_feeds": "(url TEXT PRIMARY KEY)",
    "bot_config": "(key TEXT PRIMARY KEY, value TEXT)",
}


def log(message, message_type=INFO):
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[ {current_date} ] {message}"
    colored_message = {
        WARNING: f"\033[93m{formatted_message}\033[0m",
        ERROR: f"\033[91m{formatted_message}\033[0m",
        SUCCESS: f"\033[92m{formatted_message}\033[0m",
    }.get(message_type, formatted_message)
    print(colored_message)


class DatabaseManager:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name)
        self.c = self.conn.cursor()  # Initialize the cursor
        self.create_tables()

    def create_tables(self):
        for table, columns in TABLES.items():
            self.c.execute(f"CREATE TABLE IF NOT EXISTS {table} {columns}")

    def execute_query(self, query, *args):
        return self.c.execute(query, args)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class RSSBot(discord.Client):
    def __init__(self, guild_id, db_manager):
        super().__init__(intents=discord.Intents.default())
        self.guild_id = guild_id
        self.db_manager = db_manager
        self.synced = False

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await tree.sync(guild=discord.Object(id=self.guild_id))
            self.synced = True
        log(f"We have logged in as {self.user}.", SUCCESS)
        await asyncio.gather(refresh_rss(), refresh_task.start())

    async def on_disconnect(self):
        log("Bot is disconnecting. Closing database connection.", ERROR)
        self.db_manager.close()


async def refresh_rss():
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if setup_completed:
        # Obtain the connection from the DatabaseManager instance
        conn = client.db_manager.conn
        channel = client.get_channel(channel_id)
        for rss_url in rss_feed_urls:
            log(f"Checking: {rss_url}", INFO)
            feed = feedparser.parse(rss_url)
            latest_entries = feed.entries[:5]

            for entry in latest_entries:
                message_content = f"**{entry.title}**\n{entry.link}"
                message_id = entry.link

                if not conn.execute(
                    "SELECT * FROM sent_messages WHERE identifier=?", (message_id,)
                ).fetchone():
                    message = await channel.send(message_content)
                    thread = await message.create_thread(
                        name=entry.title, auto_archive_duration=60
                    )
                    conn.execute(
                        "INSERT INTO sent_messages (identifier) VALUES (?)",
                        (message_id,),
                    )
                    conn.commit()
        log("refreshing feeds: DONE", INFO)


def get_setup_data():
    # Obtain the connection from the DatabaseManager instance
    conn = client.db_manager.conn

    setup_completed = conn.execute(
        "SELECT value FROM bot_config WHERE key=?", ("setup_completed",)
    ).fetchone()
    setup_completed = setup_completed[0] == "True" if setup_completed else False

    channel_id = (
        int(
            conn.execute(
                "SELECT value FROM bot_config WHERE key=?", ("channel_id",)
            ).fetchone()[0]
        )
        if setup_completed
        else 1
    )

    refresh_interval = (
        int(
            conn.execute(
                "SELECT value FROM bot_config WHERE key=?", ("refresh_interval",)
            ).fetchone()[0]
        )
        if conn.execute(
            "SELECT value FROM bot_config WHERE key=?", ("refresh_interval",)
        ).fetchone()
        else REFRESH_INTERVAL
    )

    rss_feed_urls = [
        feed[0] for feed in conn.execute("SELECT url FROM rss_feeds").fetchall()
    ]

    return setup_completed, channel_id, refresh_interval, rss_feed_urls


client = RSSBot(GUILD_ID, DatabaseManager(DB_NAME))
tree = app_commands.CommandTree(client)


@tasks.loop(seconds=REFRESH_INTERVAL)
async def refresh_task():
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="refresh",
    description="Manually refreshes RSS feeds.",
)
async def refresh_feeds(interaction: discord.Interaction):
    """
    Manually refreshes RSS feeds.
    """
    log("Triggered manual refresh", INFO)
    await refresh_rss()
    await interaction.response.send_message("Manually refreshed RSS feeds.")


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="setup",
    description="Set up the bot to track RSS feeds in a specific channel.",
)
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
            f"Setup already done, to reset the bot please delete {DB_NAME}"
        )
        return

    if not channel_id or not rss_url:
        await interaction.response.send_message(
            "Please provide the necessary arguments:\n`!setup <channel_id> <rss_url>`"
        )
        return

    channel_id, setup_completed = channel_id, True

    client.db_manager.c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("channel_id", str(channel_id)),
    )
    client.db_manager.c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("setup_completed", "True"),
    )

    client.db_manager.c.execute(
        "INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,)
    )
    client.db_manager.conn.commit()

    setup_completed, _, refresh_interval, rss_feed_urls = get_setup_data()

    await interaction.response.send_message(
        f"Setup completed! Channel ID set to {channel_id} and first RSS feed added: {rss_url}"
    )
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="add_feed",
    description="Add a new RSS feed to the list.",
)
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

    client.db_manager.c.execute(
        "INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,)
    )
    client.db_manager.conn.commit()

    await interaction.response.send_message(f"Added new RSS feed: {rss_url}")
    log(f"Added feed {rss_url}", INFO)
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="list_feed",
    description="List all added RSS feeds.",
)
async def list_feed(interaction: discord.Interaction):
    """
    List all added RSS feeds.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `/setup` command."
        )
        return

    if not rss_feed_urls:
        await interaction.response.send_message("No feeds added yet.")
        return

    feed_list = "\n".join(rss_feed_urls)
    log("Listed feeds!", INFO)
    await interaction.response.send_message(f"List of RSS feeds:\n{feed_list}")


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="remove_feed",
    description="Remove an RSS feed from the list.",
)
async def remove_feed(interaction: discord.Interaction, rss_url: str):
    """
    Remove an RSS feed from the list.

    Parameters:
    - rss_url: The URL of the RSS feed to remove.
    """
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `/setup` command."
        )
        return

    if not rss_url:
        await interaction.response.send_message(
            "Please provide the RSS feed URL:\n`/remove_feed <rss_url>`"
        )
        return

    if rss_url not in rss_feed_urls:
        await interaction.response.send_message(
            f"The provided RSS feed URL is not in the list."
        )
        return

    client.db_manager.c.execute("DELETE FROM rss_feeds WHERE url=?", (rss_url,))
    client.db_manager.conn.commit()

    await interaction.response.send_message(f"Removed RSS feed: {rss_url}")
    log(f"Removed feed {rss_url}", INFO)
    await refresh_rss()


@tree.command(
    guild=discord.Object(id=GUILD_ID),
    name="print_config",
    description="Prints all values from the configuration",
)
async def print_config(interaction: discord.Interaction):
    """
    Print all values from bot_config.
    """
    c = client.db_manager.conn
    setup_completed, channel_id, refresh_interval, rss_feed_urls = get_setup_data()
    if not setup_completed:
        await interaction.response.send_message(
            "Please complete the setup using the `/setup` command."
        )
        return

    config_values = client.db_manager.c.execute("SELECT * FROM bot_config").fetchall()

    if not config_values:
        await interaction.response.send_message("No values found in bot_config.")
        return

    config_output = "\n".join([f"{key}: {value}" for key, value in config_values])
    await interaction.response.send_message(f"Values from bot_config:\n{config_output}")


client.run(TOKEN)
