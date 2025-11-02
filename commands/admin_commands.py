import logging

import discord
from discord import app_commands

from utils import bot_owner_only

logger = logging.getLogger(__name__)


def register_admin_commands(tree: app_commands.CommandTree):
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

    # Register commands
    tree.add_command(sync_commands)
