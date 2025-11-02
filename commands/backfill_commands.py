import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands

from services.backfill_service import BackfillService
from utils import admin_only

logger = logging.getLogger(__name__)


def register_backfill_commands(tree: app_commands.CommandTree, backfill_service: BackfillService):
    """Register all backfill-related commands to the command tree"""

    @app_commands.command(
        name="backfill", description="Manually backfill messages for a date range"
    )
    @app_commands.describe(
        from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
        to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
        guild_id="Specific guild ID to backfill (optional, defaults to current server)",
        channel_ids="Comma-separated channel IDs to backfill (optional, defaults to all channels)",
    )
    @app_commands.default_permissions(administrator=True)
    @admin_only
    async def backfill_messages(
        interaction: discord.Interaction,
        from_date: str,
        to_date: str = None,
        guild_id: str = None,
        channel_ids: str = None,
    ):
        """Manually backfill messages for a date range"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Parse dates
            from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )

            if to_date:
                to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            else:
                to_datetime = datetime.now(timezone.utc)

            # Validate date range
            if from_datetime >= to_datetime:
                await interaction.followup.send(
                    "❌ From date must be before to date!", ephemeral=True
                )
                return

            # Determine guild
            if guild_id:
                try:
                    guild = interaction.client.get_guild(int(guild_id))
                    if not guild:
                        await interaction.followup.send(
                            f"❌ Guild with ID {guild_id} not found!", ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.followup.send(
                        "❌ Invalid guild ID format!", ephemeral=True
                    )
                    return
            else:
                guild = interaction.guild
                if not guild:
                    await interaction.followup.send(
                        "❌ This command must be used in a server or provide a guild_id!",
                        ephemeral=True,
                    )
                    return

            # Parse channel IDs if provided
            target_channels = []
            if channel_ids:
                try:
                    parsed_ids = [int(cid.strip()) for cid in channel_ids.split(",")]
                    for cid in parsed_ids:
                        channel = guild.get_channel(cid)
                        if channel and isinstance(channel, discord.TextChannel):
                            target_channels.append(channel)
                        else:
                            await interaction.followup.send(
                                f"⚠️ Warning: Channel ID {cid} not found or is not a text channel. Skipping.",
                                ephemeral=True,
                            )
                            logger.warning(
                                f"Channel {cid} not found in guild {guild.name}"
                            )

                    if not target_channels:
                        await interaction.followup.send(
                            "❌ No valid channels found!", ephemeral=True
                        )
                        return
                except ValueError:
                    await interaction.followup.send(
                        "❌ Invalid channel ID format! Use comma-separated numbers (e.g., 123456789,987654321)",
                        ephemeral=True,
                    )
                    return
            else:
                target_channels = guild.text_channels

            # Send status update
            channel_list = ", ".join([f"#{c.name}" for c in target_channels[:5]])
            if len(target_channels) > 5:
                channel_list += f" and {len(target_channels) - 5} more"

            await interaction.followup.send(
                f"🔄 Starting backfill for **{guild.name}**\n"
                f"📅 From: `{from_datetime.date()}`\n"
                f"📅 To: `{to_datetime.date()}`\n"
                f"📺 Channels: {channel_list}\n"
                f"⏳ This may take a while...\n\n"
                f"**Status updates will be logged to the console.**",
                ephemeral=True,
            )

            logger.info(
                f"Manual backfill started by {interaction.user.name} for guild {guild.name} "
                f"from {from_datetime} to {to_datetime} - {len(target_channels)} channels"
            )

            # Perform backfill with progress tracking
            success_messages = 0
            failed_messages = 0
            channels_processed = 0
            last_update_time = asyncio.get_event_loop().time()

            for channel in target_channels:
                channel_success, channel_failed = await backfill_service.backfill_channel(
                    channel, from_datetime, to_datetime
                )
                success_messages += channel_success
                failed_messages += channel_failed
                channels_processed += 1

                # Send periodic updates every 5 minutes
                current_time = asyncio.get_event_loop().time()
                if current_time - last_update_time > 300:  # 5 minutes
                    try:
                        await interaction.followup.send(
                            f"📊 Progress Update:\n"
                            f"✅ Channels: {channels_processed}/{len(target_channels)}\n"
                            f"📝 Messages: {success_messages:,} succeeded, {failed_messages:,} failed",
                            ephemeral=True,
                        )
                        last_update_time = current_time
                    except discord.errors.HTTPException as e:
                        logger.warning(f"Failed to send progress update: {e}")

            # Send completion message
            try:
                await interaction.followup.send(
                    f"✅ Backfill complete for **{guild.name}**!\n"
                    f"📺 Channels processed: **{channels_processed}**\n"
                    f"📊 Successfully logged: **{success_messages:,}** messages\n"
                    f"❌ Failed: **{failed_messages:,}** messages",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(
                    f"Failed to send completion message (token likely expired): {e}"
                )

            logger.info(
                f"Manual backfill completed for guild {guild.name} - "
                f"Channels: {channels_processed}, Success: {success_messages}, Failed: {failed_messages}"
            )

        except ValueError:
            try:
                await interaction.followup.send(
                    "❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(f"Failed to send error message (token expired): {e}")
        except Exception as e:
            logger.error(f"Error in backfill command: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Error during backfill: {e}", ephemeral=True
                )
            except discord.errors.HTTPException:
                logger.error(f"Failed to send error message (token expired): {e}")

    @app_commands.command(
        name="backfill_channels", description="Backfill messages for specific channels"
    )
    @app_commands.describe(
        from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
        to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
        channel="First channel to backfill",
        channel2="Second channel (optional)",
        channel3="Third channel (optional)",
        channel4="Fourth channel (optional)",
        channel5="Fifth channel (optional)",
    )
    @app_commands.default_permissions(administrator=True)
    @admin_only
    async def backfill_channels(
        interaction: discord.Interaction,
        from_date: str,
        channel: discord.TextChannel,
        to_date: str = None,
        channel2: discord.TextChannel = None,
        channel3: discord.TextChannel = None,
        channel4: discord.TextChannel = None,
        channel5: discord.TextChannel = None,
    ):
        """Backfill messages for specific channels"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Parse dates
            from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )

            if to_date:
                to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            else:
                to_datetime = datetime.now(timezone.utc)

            # Validate date range
            if from_datetime >= to_datetime:
                await interaction.followup.send(
                    "❌ From date must be before to date!", ephemeral=True
                )
                return

            # Collect all specified channels
            target_channels = [channel]
            for ch in [channel2, channel3, channel4, channel5]:
                if ch is not None:
                    target_channels.append(ch)

            # Send status
            channel_list = ", ".join([f"#{c.name}" for c in target_channels])
            await interaction.followup.send(
                f"🔄 Starting backfill for **{interaction.guild.name}**\n"
                f"📅 From: `{from_datetime.date()}`\n"
                f"📅 To: `{to_datetime.date()}`\n"
                f"📺 Channels: {channel_list}\n"
                f"⏳ This may take a while...",
                ephemeral=True,
            )

            logger.info(
                f"Channel backfill started by {interaction.user.name} for {len(target_channels)} channels "
                f"from {from_datetime} to {to_datetime}"
            )

            # Perform backfill
            success_messages, failed_messages = await backfill_service.backfill_channels(
                target_channels, from_datetime, to_datetime
            )

            # Send completion
            try:
                await interaction.followup.send(
                    f"✅ Backfill complete!\n"
                    f"📺 Channels: **{len(target_channels)}**\n"
                    f"📊 Messages logged: **{success_messages:,}**\n"
                    f"❌ Failed: **{failed_messages:,}**",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(f"Failed to send completion message: {e}")

            logger.info(
                f"Channel backfill completed - "
                f"Channels: {len(target_channels)}, Success: {success_messages}, Failed: {failed_messages}"
            )

        except ValueError:
            try:
                await interaction.followup.send(
                    "❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(f"Failed to send error message: {e}")
        except Exception as e:
            logger.error(f"Error in backfill_channels command: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Error during backfill: {e}", ephemeral=True
                )
            except discord.errors.HTTPException:
                logger.error(f"Failed to send error message (token expired): {e}")

    @app_commands.command(
        name="backfill_categories",
        description="Backfill messages for entire channel categories/groups",
    )
    @app_commands.describe(
        from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
        to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
        category="First category to backfill",
        category2="Second category (optional)",
        category3="Third category (optional)",
        category4="Fourth category (optional)",
        category5="Fifth category (optional)",
    )
    @app_commands.default_permissions(administrator=True)
    @admin_only
    async def backfill_categories(
        interaction: discord.Interaction,
        from_date: str,
        category: discord.CategoryChannel,
        to_date: str = None,
        category2: discord.CategoryChannel = None,
        category3: discord.CategoryChannel = None,
        category4: discord.CategoryChannel = None,
        category5: discord.CategoryChannel = None,
    ):
        """Backfill messages for entire channel categories"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Parse dates
            from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )

            if to_date:
                to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            else:
                to_datetime = datetime.now(timezone.utc)

            # Validate date range
            if from_datetime >= to_datetime:
                await interaction.followup.send(
                    "❌ From date must be before to date!", ephemeral=True
                )
                return

            # Collect all specified categories
            target_categories = [category]
            for cat in [category2, category3, category4, category5]:
                if cat is not None:
                    target_categories.append(cat)

            # Perform backfill
            success_messages, failed_messages, target_channels = (
                await backfill_service.backfill_categories(
                    target_categories, from_datetime, to_datetime
                )
            )

            if not target_channels:
                await interaction.followup.send(
                    "❌ No text channels found in the specified categories!",
                    ephemeral=True,
                )
                return

            # Send status
            category_info = [
                f"**{cat.name}** ({len([ch for ch in cat.channels if isinstance(ch, discord.TextChannel)])} channels)"
                for cat in target_categories
            ]
            categories_list = ", ".join(category_info)

            await interaction.followup.send(
                f"✅ Backfill complete for **{interaction.guild.name}**!\n"
                f"📁 Categories: {categories_list}\n"
                f"📺 Total channels: **{len(target_channels)}**\n"
                f"📊 Messages logged: **{success_messages:,}**\n"
                f"❌ Failed: **{failed_messages:,}**",
                ephemeral=True,
            )

            logger.info(
                f"Category backfill completed - "
                f"Categories: {len(target_categories)}, Channels: {len(target_channels)}, "
                f"Success: {success_messages}, Failed: {failed_messages}"
            )

        except ValueError:
            try:
                await interaction.followup.send(
                    "❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(f"Failed to send error message: {e}")
        except Exception as e:
            logger.error(f"Error in backfill_categories command: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Error during backfill: {e}", ephemeral=True
                )
            except discord.errors.HTTPException:
                logger.error(f"Failed to send error message (token expired): {e}")

    @app_commands.command(
        name="backfill_all",
        description="Backfill messages for ALL guilds (use with caution!)",
    )
    @app_commands.describe(
        from_date="Start date in YYYY-MM-DD format (e.g., 2022-01-01)",
        to_date="End date in YYYY-MM-DD format (optional, defaults to now)",
    )
    @app_commands.default_permissions(administrator=True)
    @admin_only
    async def backfill_all_guilds(
        interaction: discord.Interaction, from_date: str, to_date: str = None
    ):
        """Backfill messages for ALL guilds the bot is in"""
        try:
            # Parse dates
            from_datetime = datetime.strptime(from_date, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )

            if to_date:
                to_datetime = datetime.strptime(to_date, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            else:
                to_datetime = datetime.now(timezone.utc)

            # Validate date range
            if from_datetime >= to_datetime:
                await interaction.response.send_message(
                    "❌ From date must be before to date!", ephemeral=True
                )
                return

            # Send confirmation message
            await interaction.response.send_message(
                f"⚠️ **WARNING**: This will backfill **{len(interaction.client.guilds)}** guilds!\n"
                f"📅 From: `{from_datetime.date()}`\n"
                f"📅 To: `{to_datetime.date()}`\n\n"
                f"Click the button below to confirm within 30 seconds.",
                view=BackfillConfirmView(
                    interaction.user, from_datetime, to_datetime, backfill_service
                ),
                ephemeral=True,
            )

        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format! Use: YYYY-MM-DD (e.g., 2022-01-01)",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            logger.error(f"Error in backfill_all command: {e}", exc_info=True)

    # Register commands
    tree.add_command(backfill_messages)
    tree.add_command(backfill_channels)
    tree.add_command(backfill_categories)
    tree.add_command(backfill_all_guilds)


class BackfillConfirmView(discord.ui.View):
    """View with confirmation button for backfill_all"""

    def __init__(
        self,
        user: discord.User,
        from_datetime: datetime,
        to_datetime: datetime,
        backfill_service: BackfillService,
    ):
        super().__init__(timeout=30.0)
        self.user = user
        self.from_datetime = from_datetime
        self.to_datetime = to_datetime
        self.backfill_service = backfill_service

    @discord.ui.button(label="✅ Confirm Backfill", style=discord.ButtonStyle.danger)
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.user:
            await interaction.response.send_message(
                "❌ Only the command user can confirm!", ephemeral=True
            )
            return

        # Disable the button
        button.disabled = True
        await interaction.response.edit_message(view=self)

        # Start backfill
        await interaction.followup.send(
            "🔄 Starting backfill for all guilds...", ephemeral=True
        )

        total_success, total_failed = await self.backfill_service.backfill_all_guilds(
            interaction.client.guilds, self.from_datetime
        )

        await interaction.followup.send(
            f"✅ Backfill complete for **all {len(interaction.client.guilds)} guilds**!\n"
            f"📊 Total messages logged: **{total_success:,}**\n"
            f"❌ Total failed: **{total_failed:,}**",
            ephemeral=True,
        )

    async def on_timeout(self):
        # Disable all buttons on timeout
        for item in self.children:
            item.disabled = True
