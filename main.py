from discord.ext.commands import Bot
from discord.ext import tasks, commands
import discord
import feedparser
import sqlite3
import os

TOKEN = os.getenv("DISCORD_TOKEN")
intents = discord.Intents.default()
intents.message_content = True

client = Bot(command_prefix="!", intents=intents)

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


# Check if setup is completed from the database
setup_completed, CHANNEL_ID, refresh_interval, RSS_FEED_URLS = get_setup_data()


@client.event
async def on_ready():
    print(f"We have logged in as {client.user.name}")
    refresh_task.start()


@client.event
async def on_disconnect():
    print("Bot is disconnecting. Closing database connection.")
    conn.close()  # You happy now, okko?


async def refresh_rss():
    setup_completed, channel_id, _, rss_feed_urls = get_setup_data()
    if setup_completed:
        channel = client.get_channel(channel_id)
        for rss_url in rss_feed_urls:
            print(f"Checking: {rss_url}")
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
        print("refreshing feeds: DONE")


@tasks.loop(seconds=refresh_interval)
async def refresh_task():
    await refresh_rss()


@client.command(description="Manually refresh RSS feeds.")
@commands.has_permissions(administrator=True)
async def refresh_feeds(ctx):
    """
    Manually refreshes RSS feeds.
    """
    await refresh_rss()
    await ctx.send("Manually refreshed RSS feeds.")


@client.command(description="Set up the bot to track RSS feeds in a specific channel.")
@commands.has_permissions(administrator=True)
async def setup(ctx, channel_id: int = None, rss_url: str = None):
    """
    Set up the bot to track RSS feeds in a specific channel.

    Parameters:
    - channel_id: The ID of the channel where RSS feed updates will be posted.
    - rss_url: The URL of the RSS feed to track.
    """
    global CHANNEL_ID, setup_completed, RSS_FEED_URLS, refresh_interval
    if setup_completed:
        await ctx.send(f"Setup already done, to reset the bot please delete main.db")
        return

    if channel_id is None or rss_url is None:
        await ctx.send(
            "Please provide the necessary arguments:\n`!setup <channel_id> <rss_url>`"
        )
        return

    CHANNEL_ID, setup_completed = channel_id, True

    c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("channel_id", str(CHANNEL_ID)),
    )
    c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("setup_completed", "True"),
    )

    c.execute("INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,))
    conn.commit()

    setup_completed, _, refresh_interval, RSS_FEED_URLS = get_setup_data()

    await ctx.send(
        f"Setup completed! Channel ID set to {channel_id} and first RSS feed added: {rss_url}"
    )
    await refresh_rss()


@client.command(description="Add a new RSS feed to the list.")
@commands.has_permissions(administrator=True)
async def add_feed(ctx, rss_url: str):
    """
    Add a new RSS feed to the list.

    Parameters:
    - rss_url: The URL of the RSS feed to add.
    """
    global RSS_FEED_URLS, setup_completed
    if not setup_completed:
        await ctx.send("Please complete the setup using the `!setup` command.")
        return

    if not rss_url:
        await ctx.send("Please provide the RSS feed URL:\n`!add_feed <rss_url>`")
        return

    c.execute("INSERT OR IGNORE INTO rss_feeds (url) VALUES (?)", (rss_url,))
    conn.commit()

    setup_completed, _, _, RSS_FEED_URLS = get_setup_data()

    await ctx.send(f"Added new RSS feed: {rss_url}")
    await refresh_rss()


@client.command(description="List all added RSS feeds.")
@commands.has_permissions(administrator=True)
async def list_feeds(ctx):
    """
    List all added RSS feeds.
    """
    global RSS_FEED_URLS, setup_completed
    if not setup_completed:
        await ctx.send("Please complete the setup using the `!setup` command.")
        return

    if not RSS_FEED_URLS:
        await ctx.send("No feeds added yet.")
        return

    feed_list = "\n".join(RSS_FEED_URLS)
    await ctx.send(f"List of RSS feeds:\n{feed_list}")


@client.command(description="Remove an RSS feed from the list.")
@commands.has_permissions(administrator=True)
async def remove_feed(ctx, rss_url: str):
    """
    Remove an RSS feed from the list.

    Parameters:
    - rss_url: The URL of the RSS feed to remove.
    """
    global RSS_FEED_URLS, setup_completed
    if not setup_completed:
        await ctx.send("Please complete the setup using the `!setup` command.")
        return

    if not rss_url:
        await ctx.send("Please provide the RSS feed URL:\n`!remove_feed <rss_url>`")
        return

    if rss_url not in RSS_FEED_URLS:
        await ctx.send(f"The provided RSS feed URL is not in the list.")
        return

    c.execute("DELETE FROM rss_feeds WHERE url=?", (rss_url,))
    conn.commit()

    setup_completed, _, _, RSS_FEED_URLS = get_setup_data()

    await ctx.send(f"Removed RSS feed: {rss_url}")
    await refresh_rss()


@client.command(description="Print all values from bot_config.")
@commands.has_permissions(administrator=True)
async def print_config(ctx):
    """
    Print all values from bot_config.
    """
    global setup_completed, CHANNEL_ID, RSS_FEED_URLS, refresh_interval
    if not setup_completed:
        await ctx.send("Please complete the setup using the `!setup` command.")
        return

    config_values = c.execute("SELECT * FROM bot_config").fetchall()

    if not config_values:
        await ctx.send("No values found in bot_config.")
        return

    config_output = "\n".join([f"{key}: {value}" for key, value in config_values])
    await ctx.send(f"Values from bot_config:\n{config_output}")


@client.command(description="Set the time between RSS feed refreshes.")
@commands.has_permissions(administrator=True)
async def set_refresh_interval(ctx, seconds: int):
    """
    Set the time between RSS feed refreshes.

    Parameters:
    - seconds: The time interval in seconds between RSS feed refreshes.
    """
    global refresh_task
    if seconds <= 0:
        await ctx.send("Please provide a positive value for the refresh interval.")
        return

    refresh_task.change_interval(seconds=seconds)

    # Save the refresh interval in the database
    c.execute(
        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
        ("refresh_interval", str(seconds)),
    )
    conn.commit()

    setup_completed, _, refresh_interval, _ = get_setup_data()

    await ctx.send(f"Refresh interval set to {seconds} seconds.")


client.run(TOKEN)
