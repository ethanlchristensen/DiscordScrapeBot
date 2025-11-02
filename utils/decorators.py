import logging
from functools import wraps
from typing import Callable

import discord

from services import ConfigService

logger = logging.getLogger(__name__)


def admin_only(func: Callable):
    """
    Decorator to restrict slash commands to admin users only.

    Checks if the interaction user's ID matches the adminId in the config.
    If not, sends an error message and prevents command execution.

    Usage:
        @app_commands.command(name="example", description="Admin only command")
        @admin_only
        async def example_command(interaction: discord.Interaction):
            await interaction.response.send_message("You are an admin!")
    """

    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        # Load config
        config = ConfigService().load()

        # Debug logging for type checking
        user_id = interaction.user.id
        admin_id = config.adminId

        logger.debug(
            f"Admin check: user_id={user_id} (type: {type(user_id).__name__}), "
            f"admin_id={admin_id} (type: {type(admin_id).__name__})"
        )

        # Ensure both are integers for comparison
        try:
            admin_id = int(admin_id)
        except (ValueError, TypeError):
            logger.error(
                f"Invalid adminId in config: {admin_id} (type: {type(admin_id).__name__})"
            )
            await interaction.response.send_message(
                "❌ Configuration error: Invalid admin ID. Please contact the bot owner.",
                ephemeral=True,
            )
            return

        # Check if user is admin
        if user_id != admin_id:
            logger.warning(
                f"Unauthorized access attempt by {interaction.user.name} "
                f"({user_id}) to command '{func.__name__}'. Admin ID is {admin_id}."
            )
            await interaction.response.send_message(
                "❌ You are not authorized to use this command. Only the bot admin can use this.",
                ephemeral=True,
            )
            return

        # User is admin, execute the command
        logger.info(
            f"Admin {interaction.user.name} ({user_id}) executing command '{func.__name__}'"
        )
        return await func(interaction, *args, **kwargs)

    return wrapper


def bot_owner_only(func: Callable):
    """
    Decorator to restrict slash commands to the Discord bot owner only.

    Checks if the interaction user's ID matches the bot's owner ID from Discord.
    This is different from admin_only which uses the config file.

    Usage:
        @app_commands.command(name="example", description="Owner only command")
        @bot_owner_only
        async def example_command(interaction: discord.Interaction):
            await interaction.response.send_message("You are the bot owner!")
    """

    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        # Get bot owner from Discord
        app_info = await interaction.client.application_info()

        # Check if user is bot owner
        if interaction.user.id != app_info.owner.id:
            logger.warning(
                f"Unauthorized access attempt by {interaction.user.name} "
                f"({interaction.user.id}) to owner-only command '{func.__name__}'"
            )
            await interaction.response.send_message(
                "❌ You are not authorized to use this command. Only the bot owner can use this.",
                ephemeral=True,
            )
            return

        # User is bot owner, execute the command
        logger.info(
            f"Bot owner {interaction.user.name} ({interaction.user.id}) executing command '{func.__name__}'"
        )
        return await func(interaction, *args, **kwargs)

    return wrapper
