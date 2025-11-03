import logging
from typing import Optional

import discord
from discord import app_commands

from services.consent_service import ConsentService, ConsentLevel
from services.backfill_service import BackfillService
from utils import bot_owner_only

logger = logging.getLogger(__name__)


def register_admin_commands(
    tree: app_commands.CommandTree,
    consent_service: Optional[ConsentService] = None,
    backfill_service: Optional[BackfillService] = None,
):
    """Register all admin-related commands to the command tree"""

    @app_commands.command(name="sync", description="Sync slash commands (owner only)")
    @app_commands.describe(
        scope="Sync scope: 'global' for all servers, 'guild' for current server only"
    )
    @bot_owner_only
    async def sync_commands(interaction: discord.Interaction, scope: str = "guild"):
        """Sync slash commands to Discord"""

        try:
            await interaction.response.defer(ephemeral=True)

            if scope.lower() == "global":
                # Sync globally (takes up to 1 hour to propagate)
                synced = await interaction.client.tree.sync()
                await interaction.followup.send(
                    f"✅ Synced {len(synced)} commands globally.\n"
                    f"⏳ May take up to 1 hour to appear in all servers.",
                    ephemeral=True,
                )
                logger.info(
                    f"Commands synced globally by {interaction.user.name}: {len(synced)} commands"
                )

            elif scope.lower() == "guild":
                # Sync to current guild (instant)
                if not interaction.guild:
                    await interaction.followup.send(
                        "❌ This command must be used in a server for guild sync!",
                        ephemeral=True,
                    )
                    return

                interaction.client.tree.copy_global_to(guild=interaction.guild)
                synced = await interaction.client.tree.sync(guild=interaction.guild)
                await interaction.followup.send(
                    f"✅ Synced {len(synced)} commands to **{interaction.guild.name}**.\n"
                    f"Commands should appear immediately.",
                    ephemeral=True,
                )
                logger.info(
                    f"Commands synced to guild {interaction.guild.name} by {interaction.user.name}: {len(synced)} commands"
                )

            else:
                await interaction.followup.send(
                    "❌ Invalid scope! Use 'global' or 'guild'.", ephemeral=True
                )

        except Exception as e:
            await interaction.followup.send(
                f"❌ Error syncing commands: {e}", ephemeral=True
            )
            logger.error(f"Error in sync command: {e}", exc_info=True)

    @app_commands.command(
        name="backfill_user",
        description="Manually backfill messages for a specific user based on their consent",
    )
    @app_commands.describe(
        user="The user to backfill messages for",
        force_full="Force full backfill from join date (overrides consent settings)",
    )
    @app_commands.default_permissions(administrator=True)
    @bot_owner_only
    async def backfill_user(
        interaction: discord.Interaction,
        user: discord.User,
        force_full: bool = False,
    ):
        """Manually trigger backfill for a specific user based on their consent"""
        await interaction.response.defer(ephemeral=True)

        if not consent_service or not backfill_service:
            await interaction.followup.send(
                "❌ Consent or backfill service not available!", ephemeral=True
            )
            return

        if not interaction.guild:
            await interaction.followup.send(
                "❌ This command must be used in a server!", ephemeral=True
            )
            return

        try:
            # Check user's consent
            consent_record = await consent_service.get_user_consent(
                interaction.guild.id, user.id
            )

            if not consent_record:
                await interaction.followup.send(
                    f"❌ **{user.display_name}** has not given consent for data collection.\n"
                    f"They need to use `/consent` first before backfilling can be done.",
                    ephemeral=True,
                )
                return

            if not consent_record.get("consent_active", False):
                await interaction.followup.send(
                    f"❌ **{user.display_name}** has revoked their consent.\n"
                    f"They need to give consent again using `/consent` before backfilling.",
                    ephemeral=True,
                )
                return

            # Get consent details
            consent_level = ConsentLevel(consent_record.get("consent_level", 0))
            backfill_historical = consent_record.get("backfill_historical", False)
            # Check both new and old field names for backward compatibility
            backfill_from_date = consent_record.get(
                "backfill_from_date"
            ) or consent_record.get("user_joined_at")
            consented_at = consent_record.get("consented_at")

            # Determine backfill time range
            if force_full and backfill_from_date:
                after = backfill_from_date
                backfill_type = "Full historical backfill (FORCED)"
            elif backfill_historical and backfill_from_date:
                after = backfill_from_date
                backfill_type = "Full historical backfill (from guild creation)"
            elif consented_at:
                after = consented_at
                backfill_type = "Partial backfill (from consent date)"
            else:
                await interaction.followup.send(
                    f"❌ Cannot determine backfill date range for **{user.display_name}**.\n"
                    f"Missing consent or join date information.",
                    ephemeral=True,
                )
                return

            # Send status update
            await interaction.followup.send(
                f"🔄 Starting backfill for **{user.display_name}** in **{interaction.guild.name}**\n\n"
                f"📊 **Consent Level:** {consent_level.name} (Level {consent_level.value})\n"
                f"📅 **Backfill Type:** {backfill_type}\n"
                f"📅 **Starting from:** <t:{int(after.timestamp())}:F>\n\n"
                f"⏳ This may take a while depending on message history...\n\n"
                f"**Progress will be logged to the console.**",
                ephemeral=True,
            )

            logger.info(
                f"Manual user backfill started by {interaction.user.name} for user {user.name} ({user.id}) "
                f"in guild {interaction.guild.name} from {after} (Type: {backfill_type})"
            )

            # Perform backfill
            (
                success_messages,
                failed_messages,
            ) = await backfill_service.backfill_user_messages(
                guild=interaction.guild,
                user_id=user.id,
                after=after,
                before=None,
            )

            # Send completion message
            try:
                await interaction.followup.send(
                    f"✅ Backfill complete for **{user.display_name}**!\n\n"
                    f"📊 **Successfully logged:** {success_messages:,} messages\n"
                    f"❌ **Failed:** {failed_messages:,} messages\n\n"
                    f"The backfill process has been completed based on their consent settings.",
                    ephemeral=True,
                )
            except discord.errors.HTTPException as e:
                logger.error(
                    f"Failed to send completion message (token likely expired): {e}"
                )

            logger.info(
                f"Manual user backfill completed for user {user.name} ({user.id}) "
                f"in guild {interaction.guild.name} - Success: {success_messages}, Failed: {failed_messages}"
            )

        except Exception as e:
            logger.error(f"Error in backfill_user command: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    f"❌ Error during user backfill: {e}", ephemeral=True
                )
            except discord.errors.HTTPException:
                logger.error(f"Failed to send error message (token expired): {e}")

    # Register commands
    tree.add_command(sync_commands)
    if consent_service and backfill_service:
        tree.add_command(backfill_user)
