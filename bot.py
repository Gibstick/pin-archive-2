import mimetypes
import os
import pickle
import sys
from typing import Any, Dict, Type

import discord
from discord.ext import commands
from dotenv import load_dotenv
from discord_slash.utils.manage_commands import create_option,create_permission
from discord_slash import SlashCommand,cog_ext
from discord_slash.model import SlashCommandOptionType,SlashCommandPermissionType

DEFAULT_REACTS = 7
load_dotenv()
DiscordContext = Type[commands.Context]


permission = {int(os.getenv("GUILD_ID")):[create_permission(int(os.getenv("ADMIN_ID")), SlashCommandPermissionType.ROLE, True)]}
def guild_save_config(
    config_path: str, guild_id: int, key: str, value: Any
) -> None:
    """Save a config value for a guild.

    - config_path: path to the config directory
    - guild_id: id as integer
    - key: string key of the config value to save
    - value: value of the config value to save
    """
    directory = os.path.join(config_path, str(guild_id))
    os.makedirs(directory, exist_ok=True)
    filename = os.path.join(directory, key)
    print(f"Saving config {config_path} {guild_id} {key} {value}")
    with open(filename, "wb+") as f:
        pickle.dump(value, f)


def guild_read_config(config_path: str, guild_id: int, key: str) -> Any:
    """Read a config value for a guild."""
    filename = os.path.join(config_path, str(guild_id), key)
    try:
        with open(filename, "rb") as f:
            return pickle.load(f)
    except FileNotFoundError:
        return None


def already_pinned(message: discord.Message) -> bool:
    """Check if the message is already pinned."""
    # To more easily support custom reactions in the future, just check if
    # there is any reaction at all from the bot.
    already_pinned = discord.utils.get(message.reactions, me=True)
    return already_pinned is not None


async def maybe_unpin(message: discord.Message) -> None:
    """Unpin a message from a channel if we're at the 50-message limit."""
    pins = await message.channel.pins()
    if len(pins) > 48:  # some leeway
        await pins[-1].unpin()


async def react_as_pinned(message: discord.Message) -> None:
    """React to the message to indicate that it is pinned already."""
    # TODO: Custom reaction support
    try:
        await message.add_reaction("📌")
    except discord.HTTPException as e:
        # If for some reason reactions are full and the pushpin isn't present,
        # just react on the first reaction in the message.
        if message.reactions:
            await message.add_reaction(message.reactions[0].emoji)
        else:
            print("Unable to react?")
            print(e)


async def get_message_by_id(
    channel: discord.TextChannel, message_id: int
) -> discord.Message:
    try:
        message = await channel.fetch_message(message_id)
        return message
    except discord.NotFound:
        print(f"Message {message_id} not found")
        return
    except discord.Forbidden:
        return


class MainCog(commands.Cog):
    def __init__(self, bot: discord.Client, config_path: str):
        self.bot = bot
        self.config_path = config_path
        self.config_cache: Dict[str, Any] = {}
        self.converter = commands.MessageConverter()
        self.webhook_adapter = discord.RequestsWebhookAdapter()

    def read_config(self, guild: discord.Guild, key: str) -> Any:
        """Read a config value for a guild at the given key."""
        if guild.id not in self.config_cache:
            self.config_cache[guild.id] = {}

        try:
            return self.config_cache[guild.id][key]
        except KeyError:
            value = guild_read_config(self.config_path, guild.id, key)
            self.config_cache[guild.id][key] = value
            return value

    def save_config(self, guild: discord.Guild, key: str, value: Any) -> None:
        """Save a config value for a guild with the given key-value.'

        Anything pickleable can be saved."""
        if guild.id not in self.config_cache:
            self.config_cache[guild.id] = {}

        self.config_cache[guild.id][key] = value
        guild_save_config(self.config_path, guild.id, key, value)

    def get_react_count(self, guild: discord.Guild) -> int:
        """Get the reaction count threshold for a given guild."""
        val = self.read_config(guild, "reaction_count")
        if val is None:
            return DEFAULT_REACTS
        return val

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        print("Ready!")

    async def archive_message(self, message: discord.Message) -> None:
        """Forwards a message to the archive channel."""

        channel_id = self.read_config(message.guild, "archive_channel")
        if channel_id is None:
            await message.channel.send(
                "Bot not initialized. Use +init <pin archive channel> "
                "to initialize."
            )
            return

        name = message.author.display_name
        avatar_url = message.author.avatar_url
        server = message.guild.id
        message_url = (
            "https://discordapp.com/channels/"
            f"{server}/{message.channel.id}/{message.id}"
        )

        webhook = self.read_config(message.guild, "webhook_url")

        if not webhook:
            print("No webhook???")
            return

        webhook = discord.Webhook.from_url(
            webhook, adapter=self.webhook_adapter
        )

        embed = discord.Embed(
            url=message_url,
            description=message.content,
            timestamp=message.created_at,
            color=0x7289DA,
        )
        embed.set_author(name=name, url=message_url, icon_url=avatar_url)
        embed.set_footer(text=f"Sent in {message.channel.name}")
        attachments = message.attachments

        if message.embeds:
            thumbnail = message.embeds[0].thumbnail
            if thumbnail.url:
                # If the thumbnail URL appears in the message, we can directly
                # set it as the image of the embed
                if thumbnail.url and thumbnail.url in message.content:
                    embed.set_image(url=thumbnail.url)
                # Otherwise, it's not direct link to an image, so we set it as
                # the thumbnail of the embed instead
                else:
                    embed.set_thumbnail(url=thumbnail.url)
        elif attachments:
            # Set the first image attachment as the embed image
            for attachment in attachments:
                guess = mimetypes.guess_type(attachment.filename)[0]
                if guess and guess.startswith("image/"):
                    embed.set_image(url=attachment.url)
                    break

        # Add links to attachments as extra fields
        for attachment in attachments:
            embed.add_field(name="🔗", value=attachment.url)

        # Heuristic: if the embed URL is in the message content already,
        # don't create an embed
        embeds = [embed] + (
            [
                embed
                for embed in message.embeds
                if embed.url is discord.Embed.Empty
                or embed.url not in message.content
            ]
            or []
        )

        webhook_message = {
            "content": f"[Message from {name}]({message_url})",
            "wait": False,
            "embeds": embeds,
        }

        webhook.send(**webhook_message)

    @cog_ext.cog_slash(name="init",
        description="Initialize the bot with the given pin-archive channel.",
        options=[create_option(
                 name="pin_channel",
                 description="This is pin archive channel",
                 option_type=SlashCommandOptionType.CHANNEL,
                 required=True)],
        default_permission=False,
        permissions=permission)
    async def init(
        self,
        ctx: DiscordContext,
        pin_channel: discord.TextChannel,
    ) -> None:
        """Initialize the bot with the given pin-archive channel."""
        print(ctx.channel.permissions_for(
            ctx.author
        ).administrator)
        if not ctx.channel.permissions_for(
            ctx.author
        ).administrator:
            return
        guild = ctx.guild

        self.save_config(guild, "archive_channel", pin_channel.id)

        # Create webhook and save it
        old_webhook_url = self.read_config(guild, "webhook_url")
        if old_webhook_url:
            old_webhook = discord.Webhook.from_url(
                old_webhook_url, adapter=self.webhook_adapter
            )
            old_webhook.delete()

        webhook = await pin_channel.create_webhook(
            name="Pin Archive 2 Webhook",
            reason="+init command for pin archiver",
        )

        self.save_config(guild, "webhook_url", webhook.url)

        await ctx.send(
            f"Set archive channel to #{pin_channel} and created webhook"
        )

    @cog_ext.cog_slash(name="archive",
        description="Archive a message",
        default_permission=False,
        permissions=permission)
    async def archive(
        self, ctx: DiscordContext, message: discord.Message
    ) -> None:
        """Archive a message.

        The message gets converted using discord.MessageConverter."""
        if not ctx.channel.permissions_for(
            ctx.author
        ).manage_messages:
            return
        await ctx.defer()
        try:
            message = await self.converter.convert(ctx,message)
        except commands.CommandError as e:
            print(e)
            await ctx.send("Message not found")
            return

        await self.archive_message(message)
        # Respond to interaction
        await ctx.send("Message added to pin-archive",hidden=True)

    @cog_ext.cog_slash(name="setreactcount",
        description="Set the reaction count threshold.",
        options=[create_option(
                 name="count",
                 description="Reaction count",
                 option_type=SlashCommandOptionType.INTEGER,
                 required=True)],
        default_permission=False,
        permissions=permission)
    async def setreactcount(self, ctx: DiscordContext, count: int) -> None:
        """Set the reaction count threshold."""
        if not ctx.channel.permissions_for(
            ctx.author
        ).manage_messages:
            return

        self.save_config(ctx.guild, "reaction_count", count)
        await ctx.send(f"Set reaction count to {count} :pushpin:")

    @cog_ext.cog_slash(name="getreactcount",
        description="Get the reaction count threshold.")
    async def getreactcount(self, ctx: DiscordContext) -> None:
        """Get the reaction count threshold."""
        count = self.get_react_count(ctx.guild)
        await ctx.send(f"Reaction count is {count} :pushpin:")

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, raw_reaction: discord.RawReactionActionEvent
    ) -> None:
        # TODO: Custom reaction support

        channel = self.bot.get_channel(raw_reaction.channel_id)
        guild = channel.guild
        # Skip reactions in the archive channel
        if raw_reaction.channel_id == self.read_config(
            guild, "archive_channel"
        ):
            return

        message_id = raw_reaction.message_id
        message = await get_message_by_id(channel, message_id)
        reaction = discord.utils.get(message.reactions, emoji="📌")
        if reaction is None:
            return

        if already_pinned(message):
            return

        if reaction.count >= self.get_react_count(message.guild):
            await maybe_unpin(message)
            await message.pin()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for the system pins_add message and copy the pinned message
        to the archive channel."""
        if message.type != discord.MessageType.pins_add:
            return
        if message.channel.id == self.read_config(
            message.guild, "archive_channel"
        ):
            return

        reference = message.reference

        channel = self.bot.get_channel(reference.channel_id)
        message = await channel.fetch_message(reference.message_id)

        await maybe_unpin(message)
        await react_as_pinned(message)
        await self.archive_message(message)


def main() -> None:
    load_dotenv()
    config_path = os.getenv("PIN_ARCHIVE_CONFIG_PATH", "config")
    prefix = os.getenv("PIN_ARCHIVE_PREFIX", "+")
    token = os.getenv("PIN_ARCHIVE_TOKEN", None)

    if not token:
        print("Set PIN_ARCHIVE_TOKEN")
        sys.exit(1)

    os.makedirs(config_path, exist_ok=True)

    intents = discord.Intents(
        guild_messages=True, guild_reactions=True, guilds=True
    )

    bot = commands.Bot(command_prefix=prefix, intents=intents)
    slash = SlashCommand(bot, sync_commands=True,sync_on_cog_reload=True,override_type=True,
        debug_guild=os.getenv("GUILD_ID"))
    bot.add_cog(MainCog(bot, config_path))
    bot.run(token)


if __name__ == "__main__":
    main()
